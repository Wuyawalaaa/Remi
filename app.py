import os
import re
import json
import glob
import uuid
import sqlite3

import pipeline_status
import difflib
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from flask import Flask, render_template, request, jsonify, send_file
from dotenv import load_dotenv
from database_utils import ensure_pipeline_schema

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
ensure_pipeline_schema()

def all_stores():
    """Stores that actually have published data. Derived, not declared -- the
    hardcoded list here used display names while the rest of the pipeline used
    slugs, and that mismatch is what hid four months of failures."""
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT DISTINCT store_name FROM batches WHERE status='published' ORDER BY store_name"
    ).fetchall()
    con.close()
    return [r[0] for r in rows]

_BLOCKED_EXACT_NAMES = {
    "monthly credit",
    "points for discounts",
    "deals",
    "products",
    "delivery",
    "exclusive brands",
    "points",
}

_BLOCKED_GENERIC_ONE_WORD = {
    "fresh",
    "regular",
    "roast",
    "marinated",
}


def _published_batch_predicate(alias):
    return f"EXISTS (SELECT 1 FROM batches b WHERE b.id = {alias}.batch_id AND b.status = 'published')"


def _package_label(deal):
    package_amount = deal.get("package_amount")
    per_unit_volume = deal.get("per_unit_volume")

    if package_amount and per_unit_volume:
        return f"{package_amount} x {per_unit_volume}"
    if per_unit_volume:
        return str(per_unit_volume)
    if package_amount and package_amount != 1:
        return f"{package_amount} ct"
    return None


def _prepare_deal_record(deal):
    """Fill small display-friendly helpers without reintroducing legacy schema names."""
    if not deal.get("product_name"):
        deal["product_name"] = deal.get("product") or deal.get("brand") or "Unknown item"
    deal["package_label"] = _package_label(deal)
    return deal


def _deal_block_reason(deal):
    name = (deal.get("product_name") or "").strip()
    lowered = name.lower()
    category = (deal.get("category") or "").strip().lower()
    package_label = (deal.get("package_label") or "").strip()
    deal_type = (deal.get("deal_type") or "").strip().lower()
    conditions = (deal.get("deal_conditions") or "").strip().lower()
    quality_tags = deal.get("quality_tags") or []

    if lowered in _BLOCKED_EXACT_NAMES:
        return "promo copy / loyalty marketing"

    if (
        lowered in _BLOCKED_GENERIC_ONE_WORD
        and not category
        and not package_label
        and not quality_tags
        and not deal_type
    ):
        return "generic modifier without product identity"

    if any(token in lowered for token in ("points", "credit", "delivery", "exclusive brand")):
        if not category and not package_label and not quality_tags:
            return "non-product membership / service row"

    if not name or lowered in {"unknown item", "-", "—"}:
        return "missing product name"

    if not category and not package_label and not quality_tags and not deal_type and not conditions:
        words = re.findall(r"[a-zA-Z]+", lowered)
        if len(words) <= 1:
            return "too little product detail"

    return None


def _filter_displayable_deals(deals):
    visible = []
    blocked = []
    for deal in deals:
        prepared = _prepare_deal_record(dict(deal))
        try:
            prepared["quality_tags"] = json.loads(prepared["quality_tags"] or "[]")
        except Exception:
            prepared["quality_tags"] = prepared.get("quality_tags") or []
        reason = _deal_block_reason(prepared)
        if reason:
            prepared["block_reason"] = reason
            blocked.append(prepared)
        else:
            visible.append(prepared)
    return visible, blocked


def get_deals(store=None, category=None):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    query = """
        SELECT id, store_name, product_name, quality_tags,
               original_price, price_flyer, unit_flyer,
               package_amount, per_unit_volume,
               discount_amount, discount_percentage,
               valid_from, valid_to, category,
               normalized_price, unit_norm, deal_type, deal_conditions,
               promotion_group_id, price_source, flyer_image_url, scraped_at
        FROM deals
        WHERE {published_only}
    """
    query = query.format(published_only=_published_batch_predicate("deals"))
    params = []

    if store:
        query += " AND store_name = ?"
        params.append(store)

    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY category, normalized_price IS NULL, normalized_price ASC"

    rows = cur.execute(query, params).fetchall()
    con.close()

    visible, _ = _filter_displayable_deals(rows)
    return visible


def get_ocr_snippet(product_name, raw_txt_path, page_num, context=7):
    """Extract the OCR text lines around where a product appears on a given page."""
    try:
        with open(raw_txt_path) as f:
            content = f.read()
    except FileNotFoundError:
        return None

    # Split raw text into per-page line lists
    pages = {}
    current_page = None
    for line in content.split("\n"):
        m = re.match(r"^--- PAGE (\d+) ---$", line.strip())
        if m:
            current_page = int(m.group(1))
            pages[current_page] = []
        elif current_page is not None:
            pages[current_page].append(line)

    page_lines = pages.get(page_num, [])
    if not page_lines:
        return None

    # Find the position with the most product-name words in a sliding window
    words = [w.lower() for w in product_name.split() if len(w) > 3]
    if not words:
        return "\n".join(page_lines[:12])

    best_center, best_score = 0, 0
    win = 4
    for i in range(len(page_lines)):
        window_text = " ".join(page_lines[max(0, i - win):i + win + 1]).lower()
        score = sum(1 for w in words if w in window_text)
        if score > best_score:
            best_score, best_center = score, i

    start = max(0, best_center - context)
    end   = min(len(page_lines), best_center + context + 1)
    return "\n".join(page_lines[start:end]).strip()


def find_page_for_product(product_name, raw_txt_path):
    """Return the 1-indexed PDF page where a product most likely appears."""
    try:
        with open(raw_txt_path) as f:
            content = f.read()
    except FileNotFoundError:
        return 1

    pages = {}
    current_page = None
    for line in content.split("\n"):
        m = re.match(r"^--- PAGE (\d+) ---$", line.strip())
        if m:
            current_page = int(m.group(1))
            pages[current_page] = []
        elif current_page is not None:
            pages[current_page].append(line.lower())

    words = [w.lower() for w in product_name.split() if len(w) > 3]
    best_page, best_score = 1, 0
    for pnum, lines in pages.items():
        text = " ".join(lines)
        score = sum(1 for w in words if w in text)
        if score > best_score:
            best_score, best_page = score, pnum
    return best_page


def get_available_stores():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    rows = cur.execute(
        f"SELECT DISTINCT store_name FROM deals WHERE {_published_batch_predicate('deals')} ORDER BY store_name"
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


@app.route("/")
def index():
    all_deals  = get_deals()
    categories = sorted(set(d["category"] for d in all_deals if d["category"]))

    # Sample deal for the data inspector card — pick the most complete row
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    row = cur.execute(f"""
        SELECT * FROM deals
        WHERE original_price IS NOT NULL
          AND discount_percentage IS NOT NULL
          AND category IS NOT NULL
          AND {_published_batch_predicate('deals')}
        ORDER BY scraped_at DESC LIMIT 1
    """).fetchone()
    con.close()
    sample = dict(row) if row else None
    if sample:
        sample = _prepare_deal_record(sample)
        try:
            sample["quality_tags"] = json.loads(sample["quality_tags"] or "[]")
        except Exception:
            sample["quality_tags"] = []

    return render_template("index.html", stores=all_stores(), categories=categories,
                           status=pipeline_status.overall(DB_PATH),
                           deals=all_deals, sample=sample)


@app.route("/api/deals")
def api_deals():
    store    = request.args.get("store") or None
    category = request.args.get("category") or None
    deals    = get_deals(store=store, category=category)
    return jsonify(deals)


@app.route("/admin")
def admin():
    base = os.path.dirname(__file__)

    # Find latest files
    flipp_files = sorted(glob.glob(f"{base}/flyers/safeway/flipp_data/*.json"), reverse=True)
    ocr_files   = sorted(glob.glob(f"{base}/flyers/safeway/*_deals.json"), reverse=True)

    if not flipp_files or not ocr_files:
        return "Missing data files.", 404

    with open(flipp_files[0]) as f:
        flipp_data = json.load(f)
    with open(ocr_files[0]) as f:
        ocr_data = json.load(f)

    flipp_items = flipp_data["items"]
    ocr_deals   = [_prepare_deal_record(dict(deal)) for deal in ocr_data["deals"]]

    # ── helpers ──────────────────────────────────────────────────────────────

    def parse_flipp_price(label):
        label_lower = label.lower()
        m = re.search(r'(\d+)\s+(?:lbs?\s+)?for\s+\$(\d+\.?\d*)', label_lower)
        if m:
            qty, total = int(m.group(1)), float(m.group(2))
            unit = "lb" if "lb" in label_lower[:m.start()+10] else "ea"
            return round(total / qty, 2), unit
        if "buy 1 get 1" in label_lower or "b1g1" in label_lower:
            return None, "bogo"
        if "buy 2 get 2" in label_lower:
            return None, "b2g2"
        if re.search(r'\d+%\s*off', label_lower):
            return None, "pct_off"
        m = re.search(r'\$(\d+\.?\d*)\s*(lb|oz|ea|gal)?', label_lower)
        if m:
            return float(m.group(1)), (m.group(2) or "ea")
        return None, None

    def normalize_name(name):
        name = name.lower()
        name = re.sub(r'[®™©]', '', name)
        name = re.sub(r'\b(signature select|waterfront bistro|open nature|'
                      r'o organics|primo taglio|lucerne|member price|'
                      r'value pack|mega pack|selected varieties)\b', '', name)
        name = re.sub(r'\d+(\.\d+)?\s*(oz|lb|ct|pk|fl oz|gal)', '', name)
        name = re.sub(r'[^a-z0-9\s]', ' ', name)
        return re.sub(r'\s+', ' ', name).strip()

    def best_match(ocr_name, items, threshold=0.45):
        norm_ocr = normalize_name(ocr_name)
        best_item, best_score = None, 0
        for item in items:
            norm_f = normalize_name(item["raw_label"].split(",")[0])
            score  = difflib.SequenceMatcher(None, norm_ocr, norm_f).ratio()
            if score > best_score:
                best_score, best_item = score, item
        return (best_item, best_score) if best_score >= threshold else (None, 0)

    # ── classify each error ───────────────────────────────────────────────────

    def classify_error(ocr_price, flipp_price, flipp_label):
        label_lower = flipp_label.lower()
        ratio = ocr_price / flipp_price if flipp_price else 0
        # halved twice: e.g. 2 for $7 → correct $3.50, got $1.75 (ratio ~0.5)
        m = re.search(r'(\d+)\s+for\s+\$(\d+\.?\d*)', label_lower)
        if m and abs(ratio - 0.5) < 0.05:
            return "multi_buy_halved_twice", "「2 for $X」被多除了一次", {
                "qty": int(m.group(1)), "total": float(m.group(2)),
            }
        # neighbor swap: large absolute difference
        if abs(ocr_price - flipp_price) > 3:
            return "neighbor_price", "邻近商品价格混淆", {
                "diff": round(abs(ocr_price - flipp_price), 2),
            }
        # small diff — LLM rounding or wrong variant
        return "other", "其他解析错误", {
            "diff": round(abs((ocr_price or 0) - flipp_price), 2),
        }

    # ── run comparison ────────────────────────────────────────────────────────

    correct, wrong, bogo_skip, no_match = [], [], [], []

    for deal in ocr_deals:
        item, score = best_match(deal["product_name"], flipp_items, threshold=0.65)
        if item is None:
            no_match.append(deal["product_name"])
            continue

        flipp_price, flipp_unit = parse_flipp_price(item["raw_label"])
        if flipp_unit in ("bogo", "b2g2", "pct_off") or flipp_price is None:
            bogo_skip.append({"name": deal["product_name"], "label": item["raw_label"]})
            continue

        ocr_price = deal.get("price_flyer")
        if ocr_price and abs(ocr_price - flipp_price) < 0.02:
            correct.append({"name": deal["product_name"], "price": ocr_price, "score": round(score,2)})
        else:
            err_type, err_desc, extra = classify_error(ocr_price or 0, flipp_price, item["raw_label"])
            wrong.append({
                "name":        deal["product_name"],
                "ocr_price":   ocr_price,
                "flipp_price": flipp_price,
                "flipp_label": item["raw_label"],
                "score":       round(score, 2),
                "err_type":    err_type,
                "err_desc":    err_desc,
                "extra":       extra,
            })

    # Group wrong by error type
    from collections import defaultdict
    by_error = defaultdict(list)
    for r in wrong:
        by_error[r["err_type"]].append(r)

    # Build pipeline data for the case-study item (first) of each error type
    raw_txt_files = sorted(glob.glob(f"{base}/flyers/safeway/*_raw.txt"), reverse=True)
    raw_txt_path  = raw_txt_files[0] if raw_txt_files else None
    deal_by_name  = {d["product_name"]: d for d in ocr_deals}

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    for err_type, items in by_error.items():
        first    = items[0]
        name     = first["name"]

        # Stage 1 – OCR raw text snippet
        page_num = find_page_for_product(name, raw_txt_path) if raw_txt_path else 1
        first["extra"]["page_num"]    = page_num
        first["extra"]["ocr_snippet"] = get_ocr_snippet(name, raw_txt_path, page_num) if raw_txt_path else None

        # Stage 2 – LLM parsed deal (from _deals.json)
        parsed = deal_by_name.get(name, {})
        first["extra"]["parsed"] = {
            "price_flyer":    parsed.get("price_flyer"),
            "original_price": parsed.get("original_price"),
            "unit_flyer":     parsed.get("unit_flyer"),
            "category":       parsed.get("category"),
            "quality_tags":   parsed.get("quality_tags", []),
        }

        # Stage 3 – Database record (after normalization)
        row = cur.execute(
            f"SELECT d.price_flyer, d.unit_flyer, d.normalized_price, d.unit_norm "
            f"FROM deals d WHERE d.product_name = ? AND {_published_batch_predicate('d')} "
            "ORDER BY d.scraped_at DESC LIMIT 1",
            (name,)
        ).fetchone()
        first["extra"]["db"] = dict(row) if row else None

    con.close()

    FIXES = {
        "multi_buy_halved_twice": {
            "title": "「2 for $X」被多除了一次",
            "desc":  "LLM 把「2 for $7」先算成 $3.50，再除以 2 得到 $1.75。",
            "fix":   "在 prompt 中加强：「多件促销只除一次，例如 2 for $7 → $3.50，不是 $1.75」",
        },
        "neighbor_price": {
            "title": "邻近商品价格混淆",
            "desc":  "传单上相邻商品的价格被错误归属到了当前商品。",
            "fix":   "在 prompt 中加强：「每个商品只提取紧跟在其名称下方的价格，不要引用相邻商品的价格」",
        },
        "other": {
            "title": "其他解析误差",
            "desc":  "价格差异较小，可能是商品变体不同或匹配错误。",
            "fix":   "建立高频商品 ground truth 缓存，人工确认后固定",
        },
    }

    comparable = len(correct) + len(wrong)
    accuracy   = round(len(correct) / comparable * 100, 1) if comparable else 0

    stats = {
        "total_ocr":    len(ocr_deals),
        "total_flipp":  flipp_data["total_items"],
        "comparable":   comparable,
        "correct":      len(correct),
        "wrong":        len(wrong),
        "bogo_skip":    len(bogo_skip),
        "no_match":     len(no_match),
        "accuracy":     accuracy,
        "valid_from":   ocr_data["valid_from"],
        "valid_to":     ocr_data["valid_to"],
        "match_mode":   "fuzzy SequenceMatcher ≥ 0.65",
    }

    # ── Data quality ─────────────────────────────────────────────────────────
    ALL_CATS = [
        "produce", "meat", "seafood", "dairy", "bakery", "deli", "frozen",
        "pantry", "condiments", "beverages", "snacks", "prepared foods", "household",
        "personal care", "health", "baby", "alcohol", "floral",
    ]
    con_dq = sqlite3.connect(DB_PATH)
    con_dq.row_factory = sqlite3.Row
    cur_dq = con_dq.cursor()

    active_rows = cur_dq.execute(f"""
        SELECT id, store_name, product_name, quality_tags,
               original_price, price_flyer, unit_flyer,
               package_amount, per_unit_volume,
               discount_amount, discount_percentage,
               valid_from, valid_to, category,
               normalized_price, unit_norm, deal_type, deal_conditions,
               promotion_group_id, price_source, flyer_image_url, scraped_at
        FROM deals
        WHERE {_published_batch_predicate('deals')}
        ORDER BY store_name, product_name
    """).fetchall()
    visible_deals, blocked_deals = _filter_displayable_deals(active_rows)

    cat_counts = Counter((d.get("category") or "__null__") for d in visible_deals)
    null_count = cat_counts.get("__null__", 0)
    total_current = len(visible_deals)
    null_rate = round(null_count / total_current * 100, 1) if total_current else 100

    cat_grid = [(cat, cat_counts.get(cat, 0)) for cat in ALL_CATS]

    null_items = [
        {
            "product_name": d.get("product_name"),
            "price_flyer": d.get("price_flyer"),
            "unit_flyer": d.get("unit_flyer"),
        }
        for d in visible_deals
        if not d.get("category")
    ]

    latest_dates = cur_dq.execute(f"""
        SELECT valid_from, valid_to
        FROM deals
        WHERE {_published_batch_predicate('deals')}
        ORDER BY valid_to DESC, valid_from DESC
        LIMIT 1
    """).fetchone()
    today = date.today().isoformat()
    data_fresh = bool(latest_dates and latest_dates["valid_to"] >= today)

    # BOGO stats from deals JSON (pre-import state)
    bogo_normalized = cur_dq.execute(f"""
        SELECT COUNT(*) FROM deals
        WHERE discount_percentage = 50.0 AND unit_flyer = 'ea'
        AND original_price IS NOT NULL
        AND {_published_batch_predicate('deals')}
    """).fetchone()[0]

    cat_active = sum(1 for cat in ALL_CATS if cat_counts.get(cat, 0) > 0)
    core_ok    = all(cat_counts.get(c, 0) > 0 for c in ["produce", "meat", "dairy", "beverages"])

    published_batches = cur_dq.execute("""
        SELECT *
        FROM batches
        WHERE status = 'published'
        ORDER BY store_name
    """).fetchall()

    ops_cards = []
    for batch in published_batches:
        previous = cur_dq.execute("""
            SELECT store_name, valid_from, valid_to, row_count
            FROM batches
            WHERE store_name = ?
              AND id <> ?
            ORDER BY COALESCE(published_at, created_at) DESC
            LIMIT 1
        """, (batch["store_name"], batch["id"])).fetchone()
        last_run = cur_dq.execute("""
            SELECT step_name, status, started_at, finished_at, message
            FROM pipeline_runs
            WHERE store_name = ?
            ORDER BY id DESC
            LIMIT 1
        """, (batch["store_name"],)).fetchone()
        row_delta = None
        if previous:
            row_delta = batch["row_count"] - (previous["row_count"] or 0)

        ops_cards.append({
            "store_name": batch["store_name"],
            "published_range": f"{batch['valid_from'] or '—'} → {batch['valid_to'] or '—'}",
            "published_at": batch["published_at"] or batch["created_at"],
            "row_count": batch["row_count"],
            "warning_count": batch["warning_count"],
            "error_count": batch["error_count"],
            "row_delta": row_delta,
            "previous_range": (
                f"{previous['valid_from'] or '—'} → {previous['valid_to'] or '—'}"
                if previous else None
            ),
            "last_run_step": last_run["step_name"] if last_run else None,
            "last_run_status": last_run["status"] if last_run else "never",
            "last_run_at": (last_run["finished_at"] or last_run["started_at"]) if last_run else None,
            "last_run_message": last_run["message"] if last_run else None,
        })

    recent_runs = cur_dq.execute("""
        SELECT store_name, step_name, status, started_at, finished_at, message
        FROM pipeline_runs
        ORDER BY id DESC
        LIMIT 12
    """).fetchall()
    con_dq.close()

    readiness = [
        {
            "ok":     data_fresh,
            "label":  "Data in-date",
            "detail": f"{latest_dates['valid_from']} → {latest_dates['valid_to']}" if latest_dates else "No data",
        },
        {
            "ok":     null_rate < 15,
            "label":  "Category coverage",
            "detail": f"{null_rate}% uncategorized  ({null_count} items)",
        },
        {
            "ok":     core_ok,
            "label":  "Core categories",
            "detail": f"{cat_active} / 18 active",
        },
        {
            "ok":     stats["accuracy"] >= 70,
            "label":  "Price accuracy",
            "detail": f"{stats['accuracy']}% vs Flipp",
        },
    ]

    # Cardenas raw preview — 10 sample items before parsing
    cardenas_raw_files = sorted(glob.glob(f"{base}/flyers/cardenas/*_raw.json"), reverse=True)
    cardenas_raw_sample = []
    if cardenas_raw_files:
        with open(cardenas_raw_files[0]) as f:
            cardenas_raw = json.load(f)
        for p in cardenas_raw.get("products", [])[:10]:
            cardenas_raw_sample.append({
                "description": p.get("description") or p.get("name"),
                "pre_price_text":  p.get("pre_price_text") or "",
                "price_text":      p.get("price_text") or "",
                "post_price_text": p.get("post_price_text") or "",
                "sale_story":      p.get("sale_story") or "",
                "category_l2":     ((p.get("item_categories") or {}).get("l2") or {}).get("category_name") or "",
                "valid_from":      p.get("valid_from") or "",
                "valid_to":        p.get("valid_to") or "",
            })

    return render_template("admin.html",
                           stats=stats,
                           by_error=dict(by_error),
                           fixes=FIXES,
                           correct=correct,
                           no_match=no_match,
                           bogo_skip=bogo_skip,
                           readiness=readiness,
                           cat_grid=cat_grid,
                           null_items=null_items,
                           blocked_deals=blocked_deals,
                           null_rate=null_rate,
                           total_current=total_current,
                           cat_active=cat_active,
                           bogo_normalized=bogo_normalized,
                           cardenas_raw_sample=cardenas_raw_sample,
                           ops_cards=ops_cards,
                           recent_runs=recent_runs)


# ── Chat agent ────────────────────────────────────────────────────────────────

_chat_sessions      = {}  # session_id → [{role, content}, ...]
_chat_lists         = {}  # session_id → shopping_list
_session_last_active = {}  # session_id → datetime
_SESSION_TTL_HOURS   = 2

def _evict_stale_sessions():
    cutoff = datetime.now() - timedelta(hours=_SESSION_TTL_HOURS)
    stale = [sid for sid, t in _session_last_active.items() if t < cutoff]
    for sid in stale:
        _chat_sessions.pop(sid, None)
        _chat_lists.pop(sid, None)
        _session_last_active.pop(sid, None)

_CHAT_SYSTEM = """You are a grocery shopping assistant for families. Help users build a shopping list by assigning each item to the store with the best deal this week.

Relevant deal candidates:
{ctx}

CORE BEHAVIOR:
- You must choose exactly one mode per response: clarify, answer_question, or update_list.
- In clarify mode: ask one short clarification question and DO NOT change the shopping list.
- In answer_question mode: answer the user's follow-up question briefly and DO NOT change the shopping list.
- In update_list mode: return the full shopping list for this session — all items, not just new ones.
- Never change a previously added item unless the user explicitly requests it.
- Add ALL items the user requested in one response. Never split across turns.
- ONLY add items the user explicitly asked for. Never suggest or include extras.

MATCHING ITEMS TO DEALS:
- Each user item maps to one deal entry. Only match a deal if it is the same type of product (e.g. 'lemon' → produce, not lemon squeezer, the tool; 'eggs' → dairy, not egg mixer, the tool).
- If multiple deals match the same product type and quality level → silently pick the cheapest one. Do not ask.
- If multiple deals match genuinely different products (e.g. "pepper" matches bell pepper and ground pepper) → ask the user which one. Keep the question to one short sentence. Never list more than 2–3 options; group similar ones (e.g. various chicken cuts → just ask "Which cut — breast, drumsticks, or thighs?").
- Match to the quality level the user specifies. If unspecified, pick cheapest.
- For items where no deal is found, add under store '—' with price '' and note 'no deal found this week'.
- In the name field, use the exact product name as it appears in the flyer (e.g. "Farm Hen", not "Whole Chicken"). This helps the user find the item in-store.
- Use price only for an actual numeric price like '$8.99/ea'. Never put promo text like 'Mix & Match' in price.
- Put promo text, savings text, or deal conditions in note. If no deal matched, write 'no deal found this week'.
- Never return a matched store or numeric price together with 'no deal found this week'.

SAVINGS:
- Track total savings: sum of (original_price - price_flyer) or (discount_percentage x original_price) per item if applicable.
- Show total savings at the bottom of the list.
- Only include items where the dollar saving is calculable. Do not estimate.
- In save_amount, put the dollar amount saved per unit as a number (e.g. 1.50). Set to 0 if no deal.

RESPONSE FORMAT:
- Conversational replies for questions and clarifications.
- Keep replies concise. Do not explain your reasoning unless asked.

Respond ONLY as raw JSON (no markdown fences):
{"mode":"clarify","reply":"...","shopping_list":[...]}
or
{"mode":"answer_question","reply":"...","shopping_list":[...]}
or
{"mode":"update_list","reply":"...","shopping_list":[{"store":"Store Name","items":[{"name":"...","price":"...","note":"...","save_amount":0.00}]}]}"""


_STOP_WORDS = {
    "a", "an", "and", "any", "best", "buy", "deals", "for", "from", "get", "grocery",
    "have", "i", "im", "i'm", "item", "items", "list", "looking", "me", "need", "of",
    "on", "please", "shopping", "some", "the", "this", "to", "want", "week", "with",
}


def _chat_terms(text):
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in words if len(w) >= 3 and w not in _STOP_WORDS]


def _deal_candidate_score(deal, terms):
    haystack = " ".join(
        str(part or "")
        for part in [
            deal.get("product_name"),
            " ".join(deal.get("quality_tags") or []),
            deal.get("category"),
            deal.get("package_label"),
            deal.get("deal_type"),
            deal.get("deal_conditions"),
        ]
    ).lower()
    if not terms:
        return 0
    score = 0
    for term in terms:
        if term in haystack:
            score += 4
        elif any(term in token for token in haystack.split()):
            score += 2
    if terms and deal.get("normalized_price") is not None:
        score += 0.25
    return score


def _format_candidate_line(deal):
    if deal.get("price_flyer") not in (None, 0):
        price = f"${deal['price_flyer']:.2f}/{deal.get('unit_flyer') or 'ea'}"
    else:
        price = ""
    parts = [
        f"store={deal.get('store_name')}",
        f"name={deal.get('product_name')}",
    ]
    if deal.get("package_label"):
        parts.append(f"package={deal['package_label']}")
    if price:
        parts.append(f"price={price}")
    if deal.get("deal_type"):
        parts.append(f"promo={deal['deal_type']}")
    if deal.get("deal_conditions"):
        parts.append(f"conditions={deal['deal_conditions']}")
    if deal.get("original_price"):
        parts.append(f"orig=${deal['original_price']:.2f}")
    return " | ".join(parts)


def _candidate_deals_context(message, limit=36):
    rows = get_deals()
    if not rows:
        return "No active deals this week."
    terms = _chat_terms(message)
    scored = []
    for deal in rows:
        score = _deal_candidate_score(deal, terms)
        if terms and score <= 0:
            continue
        scored.append((score, deal.get("normalized_price") is None, deal.get("normalized_price") or 999999, deal))

    if not scored:
        scored = [(0, deal.get("normalized_price") is None, deal.get("normalized_price") or 999999, deal) for deal in rows[:limit]]

    scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3].get("product_name") or ""))
    candidates = []
    seen = set()
    for _, _, _, deal in scored:
        key = (
            deal.get("store_name"),
            deal.get("product_name"),
            deal.get("package_label"),
            deal.get("price_flyer"),
            deal.get("deal_type"),
        )
        if key in seen:
            continue
        seen.add(key)
        candidates.append(_format_candidate_line(deal))
        if len(candidates) >= limit:
            break

    return "\n".join(candidates) if candidates else "No relevant deals found this week."


def _chat_transcript(history):
    lines = []
    for msg in history:
        role = "User" if msg.get("role") == "user" else "Assistant"
        lines.append(f"{role}: {msg.get('content', '')}")
    return "\n".join(lines)


def _parse_chat_json(text):
    clean = (text or "").strip()
    clean = re.sub(r'^```(?:json)?\s*\n?', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\n?```\s*$', '', clean, flags=re.IGNORECASE).strip()
    try:
        return json.loads(clean)
    except Exception:
        m = re.search(r'\{[\s\S]*\}', clean)
        if not m:
            return {}
        try:
            return json.loads(m.group())
        except Exception:
            return {}


def _looks_like_clarification(reply):
    reply = (reply or "").strip().lower()
    if not reply:
        return False
    return reply.endswith("?") or reply.startswith(("which ", "what ", "do you want", "do you mean", "is that "))


def _looks_like_info_question(message):
    message = (message or "").strip().lower()
    if not message:
        return False
    starters = (
        "what is", "what are", "what's", "whats", "how is", "how are",
        "difference", "differences", "explain", "tell me about",
    )
    return "?" in message or any(message.startswith(prefix) for prefix in starters)


def _sanitize_list_item(item):
    name = str(item.get("name") or "").strip()
    if not name:
        return None

    store = str(item.get("store") or "—").strip() or "—"
    price = str(item.get("price") or "").strip()
    note = str(item.get("note") or "").strip()
    try:
        save_amount = float(item.get("save_amount") or 0)
    except (TypeError, ValueError):
        save_amount = 0.0

    no_deal = "no deal found this week" in note.lower()
    if no_deal:
        store = "—"
        price = ""
        save_amount = 0.0

    if price and not price.startswith("$"):
        price = ""

    return {
        "store": store,
        "name": name,
        "price": price,
        "note": note,
        "save_amount": round(save_amount, 2),
    }


def _normalize_shopping_list(raw_list):
    grouped = []
    index = {}
    for group in raw_list or []:
        store = str(group.get("store") or "—").strip() or "—"
        for raw_item in group.get("items") or []:
            item = _sanitize_list_item({**raw_item, "store": store})
            if not item:
                continue
            bucket = item["store"]
            if bucket not in index:
                index[bucket] = len(grouped)
                grouped.append({"store": bucket, "items": []})
            grouped[index[bucket]]["items"].append({
                "name": item["name"],
                "price": item["price"],
                "note": item["note"],
                "save_amount": item["save_amount"],
            })
    return grouped


def _validated_chat_response(parsed, previous_list):
    reply = str(parsed.get("reply") or "").strip()
    mode = str(parsed.get("mode") or "").strip().lower()
    candidate_list = _normalize_shopping_list(parsed.get("shopping_list") or [])

    if mode not in {"clarify", "answer_question", "update_list"}:
        mode = "clarify" if _looks_like_clarification(reply) else "update_list"

    if mode == "clarify":
        return {
            "mode": "clarify",
            "reply": reply or "Which one did you mean?",
            "shopping_list": previous_list,
        }

    if mode == "answer_question":
        return {
            "mode": "answer_question",
            "reply": reply or "Here’s a quick explanation.",
            "shopping_list": previous_list,
        }

    if _looks_like_clarification(reply):
        return {
            "mode": "clarify",
            "reply": reply,
            "shopping_list": previous_list,
        }

    return {
        "mode": "update_list",
        "reply": reply or "Here are the best matches I found.",
        "shopping_list": candidate_list,
    }


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data       = request.get_json(force=True)
    session_id = data.get("session_id") or str(uuid.uuid4())
    msg        = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "empty message"}), 400

    _evict_stale_sessions()
    _chat_sessions.setdefault(session_id, [])
    _chat_lists.setdefault(session_id, [])
    _session_last_active[session_id] = datetime.now()
    history = _chat_sessions[session_id]
    previous_list = _chat_lists[session_id]
    question_hint = _looks_like_info_question(msg)
    history.append({"role": "user", "content": msg})

    try:
        from google import genai
        from google.genai import types
        from google.oauth2 import service_account

        service_account_file = os.getenv("VERTEX_SERVICE_ACCOUNT_FILE")
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

        if not service_account_file:
            raise RuntimeError("VERTEX_SERVICE_ACCOUNT_FILE not set in .env")
        if not project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT not set in .env")

        cred_path = service_account_file
        if not os.path.isabs(cred_path):
            cred_path = os.path.join(os.path.dirname(__file__), cred_path)

        credentials = service_account.Credentials.from_service_account_file(
            cred_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

        client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            credentials=credentials,
        )

        prompt = (
            f"Current shopping list JSON:\n{json.dumps(previous_list, ensure_ascii=False)}\n\n"
            f"User message type hint: {'follow-up question' if question_hint else 'shopping request'}\n\n"
            "Conversation so far:\n"
            f"{_chat_transcript(history)}\n\n"
            "Return the next assistant reply and the full current shopping list."
        )

        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_CHAT_SYSTEM.replace("{ctx}", _candidate_deals_context(msg)),
                temperature=0.2,
                maxOutputTokens=2048,
                responseMimeType="application/json",
                thinkingConfig=types.ThinkingConfig(thinkingBudget=0),
            ),
        )
        text = (resp.text or "").strip()
    except Exception as e:
        history.pop()
        return jsonify({"error": str(e)}), 500

    parsed = _parse_chat_json(text)
    validated = _validated_chat_response(parsed, previous_list)
    _chat_lists[session_id] = validated["shopping_list"]
    history.append({"role": "assistant", "content": validated["reply"]})

    return jsonify({
        "session_id":    session_id,
        "mode":          validated["mode"],
        "reply":         validated["reply"],
        "shopping_list": validated["shopping_list"],
    })


if __name__ == "__main__":
    app.run(debug=True, port=8080)
