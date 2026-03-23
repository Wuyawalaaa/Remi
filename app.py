import os
import re
import json
import glob
import uuid
import sqlite3
import difflib
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

ALL_STORES = ["Safeway", "H Mart", "99 Ranch", "Cardenas"]


def get_deals(store=None, category=None):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    today = date.today().isoformat()

    query = """
        SELECT id, store_name, product_name, quality_tags,
               original_price, sale_price, unit,
               discount_amount, discount_percentage,
               valid_from, valid_to, category,
               normalized_price, normalized_unit, deal_type, package_size, deal_conditions
        FROM deals
        WHERE valid_from <= ? AND valid_to >= ?
    """
    params = [today, today]

    if store:
        query += " AND store_name = ?"
        params.append(store)

    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY category, normalized_price ASC"

    rows = cur.execute(query, params).fetchall()
    con.close()

    deals = []
    for row in rows:
        d = dict(row)
        try:
            d["quality_tags"] = json.loads(d["quality_tags"] or "[]")
        except Exception:
            d["quality_tags"] = []
        deals.append(d)

    return deals


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
    today = date.today().isoformat()
    rows = cur.execute(
        "SELECT DISTINCT store_name FROM deals WHERE valid_from <= ? AND valid_to >= ? ORDER BY store_name",
        (today, today)
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
    today = date.today().isoformat()
    row = cur.execute("""
        SELECT * FROM deals
        WHERE original_price IS NOT NULL
          AND discount_percentage IS NOT NULL
          AND category IS NOT NULL
          AND valid_from <= ? AND valid_to >= ?
        ORDER BY scraped_at DESC LIMIT 1
    """, (today, today)).fetchone()
    con.close()
    sample = dict(row) if row else None
    if sample:
        try:
            sample["quality_tags"] = json.loads(sample["quality_tags"] or "[]")
        except Exception:
            sample["quality_tags"] = []

    return render_template("index.html", stores=ALL_STORES, categories=categories,
                           deals=all_deals, sample=sample)


@app.route("/api/deals")
def api_deals():
    store    = request.args.get("store") or None
    category = request.args.get("category") or None
    deals    = get_deals(store=store, category=category)
    return jsonify(deals)


@app.route("/api/deal-crop")
def deal_crop():
    """Render a full PDF page as JPEG. The page is identified by the OCR text search."""
    import fitz
    import io

    try:
        page_num = int(request.args.get("page", 1))
    except ValueError:
        page_num = 1

    base      = os.path.dirname(__file__)
    pdf_files = sorted(glob.glob(f"{base}/flyers/safeway/*.pdf"), reverse=True)
    if not pdf_files:
        return "No PDF found", 404

    doc      = fitz.open(pdf_files[0])
    page_num = max(1, min(page_num, len(doc)))
    page     = doc[page_num - 1]

    # Render at 1.3x — large enough to read text, keeps file size reasonable
    pix = page.get_pixmap(matrix=fitz.Matrix(1.3, 1.3))
    doc.close()

    img_bytes = io.BytesIO(pix.tobytes("jpeg", jpg_quality=72))
    return send_file(img_bytes, mimetype="image/jpeg",
                     max_age=3600, as_attachment=False)


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
    ocr_deals   = ocr_data["deals"]

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

        ocr_price = deal.get("sale_price")
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
            "sale_price":     parsed.get("sale_price"),
            "original_price": parsed.get("original_price"),
            "unit":           parsed.get("unit"),
            "category":       parsed.get("category"),
            "quality_tags":   parsed.get("quality_tags", []),
        }

        # Stage 3 – Database record (after normalization)
        row = cur.execute(
            "SELECT sale_price, unit, normalized_price, normalized_unit "
            "FROM deals WHERE product_name = ?",
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
    today = date.today().isoformat()

    con_dq = sqlite3.connect(DB_PATH)
    con_dq.row_factory = sqlite3.Row
    cur_dq = con_dq.cursor()

    cat_rows = cur_dq.execute("""
        SELECT category, COUNT(*) AS cnt FROM deals
        WHERE valid_from <= ? AND valid_to >= ?
        GROUP BY category
    """, (today, today)).fetchall()
    cat_counts   = {(r["category"] or "__null__"): r["cnt"] for r in cat_rows}
    null_count   = cat_counts.get("__null__", 0)
    total_current = sum(cat_counts.values())
    null_rate    = round(null_count / total_current * 100, 1) if total_current else 100

    cat_grid = [(cat, cat_counts.get(cat, 0)) for cat in ALL_CATS]

    null_items_rows = cur_dq.execute("""
        SELECT product_name, sale_price, unit FROM deals
        WHERE category IS NULL AND valid_from <= ? AND valid_to >= ?
        ORDER BY product_name
    """, (today, today)).fetchall()
    null_items = [dict(r) for r in null_items_rows]

    latest_dates = cur_dq.execute("""
        SELECT valid_from, valid_to FROM deals ORDER BY valid_from DESC LIMIT 1
    """).fetchone()
    data_fresh = bool(latest_dates and latest_dates["valid_to"] >= today)

    # BOGO stats from deals JSON (pre-import state)
    bogo_normalized = cur_dq.execute("""
        SELECT COUNT(*) FROM deals
        WHERE discount_percentage = 50.0 AND unit = 'ea'
        AND original_price IS NOT NULL
        AND valid_from <= ? AND valid_to >= ?
    """, (today, today)).fetchone()[0]

    con_dq.close()

    cat_active = sum(1 for cat in ALL_CATS if cat_counts.get(cat, 0) > 0)
    core_ok    = all(cat_counts.get(c, 0) > 0 for c in ["produce", "meat", "dairy", "beverages"])

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
                           null_rate=null_rate,
                           total_current=total_current,
                           cat_active=cat_active,
                           bogo_normalized=bogo_normalized,
                           cardenas_raw_sample=cardenas_raw_sample)


# ── Chat agent ────────────────────────────────────────────────────────────────

_chat_sessions      = {}  # session_id → [{role, content}, ...]
_session_last_active = {}  # session_id → datetime
_SESSION_TTL_HOURS   = 2

def _evict_stale_sessions():
    cutoff = datetime.now() - timedelta(hours=_SESSION_TTL_HOURS)
    stale = [sid for sid, t in _session_last_active.items() if t < cutoff]
    for sid in stale:
        _chat_sessions.pop(sid, None)
        _session_last_active.pop(sid, None)

_CHAT_SYSTEM = """You are a grocery shopping assistant for families. Help users build a shopping list by assigning each item to the store with the best deal this week.

This week's deals:
{ctx}

CORE BEHAVIOR:
- Maintain a complete shopping list for this session. Every response that modifies the list must return the full list — all items, not just new ones.
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
- In the note field, include savings info when available, e.g. 'save 30% · was $4.99'. If no deal matched, write 'no deal found this week'.

SAVINGS:
- Track total savings: sum of (original_price - sale_price) or (discount_percentage x original_price) per item if applicable.
- Show total savings at the bottom of the list.
- Only include items where the dollar saving is calculable. Do not estimate.
- In save_amount, put the dollar amount saved per unit as a number (e.g. 1.50). Set to 0 if no deal.

RESPONSE FORMAT:
- Conversational replies for questions and clarifications.
- Keep replies concise. Do not explain your reasoning unless asked.

Respond ONLY as raw JSON (no markdown fences):
{"reply":"...","shopping_list":[{"store":"Store Name","items":[{"name":"...","price":"...","note":"...","save_amount":0.00}]}]}"""


def _deals_context():
    rows = get_deals()
    if not rows:
        return "No active deals this week."
    out = []
    for d in rows:
        deal_type = d.get("deal_type")
        if deal_type:
            price = deal_type
        elif d.get("sale_price") not in (None, 0):
            price = f"${d['sale_price']:.2f}/{d['unit'] or 'ea'}"
        else:
            price = "deal — see store"
        orig = f" (reg ${d['original_price']:.2f})" if d.get("original_price") else ""
        pkg  = f", {d['package_size']}" if d.get("package_size") else ""
        out.append(f"{d['store_name']} | {d['product_name']}{pkg} | {price}{orig}")
    return "\n".join(out)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data       = request.get_json(force=True)
    session_id = data.get("session_id") or str(uuid.uuid4())
    msg        = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "empty message"}), 400

    _evict_stale_sessions()
    _chat_sessions.setdefault(session_id, [])
    _session_last_active[session_id] = datetime.now()
    history = _chat_sessions[session_id]
    history.append({"role": "user", "content": msg})

    try:
        from openai import OpenAI as _OAI
        client  = _OAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))
        msgs    = [{"role": "system", "content": _CHAT_SYSTEM.replace("{ctx}", _deals_context())}] + history
        resp    = client.chat.completions.create(model="google/gemini-2.0-flash-001", max_tokens=2048, messages=msgs)
        text    = resp.choices[0].message.content.strip()
    except Exception as e:
        history.pop()
        return jsonify({"error": str(e)}), 500

    history.append({"role": "assistant", "content": text})

    # Strip markdown fences if model ignores the prompt instruction
    clean = text.strip()
    clean = re.sub(r'^```(?:json)?\s*\n?', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\n?```\s*$', '', clean, flags=re.IGNORECASE).strip()

    try:
        parsed = json.loads(clean)
    except Exception:
        m = re.search(r'\{[\s\S]*\}', clean)
        try:
            parsed = json.loads(m.group()) if m else {}
        except Exception:
            parsed = {}

    return jsonify({
        "session_id":    session_id,
        "reply":         parsed.get("reply", text),
        "shopping_list": parsed.get("shopping_list", []),
    })


if __name__ == "__main__":
    app.run(debug=True, port=8080)
