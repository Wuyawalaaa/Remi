import os
import re
import json
import glob
import uuid
import sqlite3

import pipeline_status
import console_faults
import console_marks
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


def get_available_stores():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    rows = cur.execute(
        f"SELECT DISTINCT store_name FROM deals WHERE {_published_batch_predicate('deals')} ORDER BY store_name"
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


def _inspection_payload():
    """Published deals with a fault code per disagreement between the parsed
    fields and the text they were parsed from.

    Sent to the browser as JSON rather than rendered here. Server-rendering all
    653 rows with their detail panels came to 1.6 MB and ~26k nodes and the page
    stopped responding -- expand-on-demand only pays off if the hidden half is
    genuinely not built."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        """SELECT d.*, b.valid_from AS b_from, b.valid_to AS b_to
           FROM deals d JOIN batches b ON d.batch_id = b.id
           WHERE b.status = 'published'
           ORDER BY d.store_name, d.product_name"""
    )]
    con.close()

    marks = console_marks.load(DB_PATH)
    for r in rows:
        detected = [f.as_dict() for f in console_faults.detect(r)]
        r["faults"], r["dismissed"] = console_marks.apply(
            detected, marks.get(r["id"]), console_faults.CATALOG)

    keep = ("id", "store_name", "product_name", "brand", "category",
            "price_flyer", "unit_flyer", "original_price",
            "per_unit_volume", "package_amount",
            "normalized_price", "unit_norm", "deal_type", "deal_conditions",
            "b_from", "b_to", "faults", "dismissed")

    return {
        "payload": [{k: r.get(k) for k in keep} for r in rows],
        "fault_summary": console_faults.summarise(rows),
        "catalog": console_faults.CATALOG,
        "stores": sorted({r["store_name"] for r in rows}),
        "n_total": len(rows),
        "n_faulty": sum(1 for r in rows if r["faults"]),
    }


@app.route("/api/fault-mark", methods=["POST"])
def api_fault_mark():
    """Add, dismiss, or undo one fault code on one deal.

    Dismissing insists on a reason. "The code is gone" otherwise means both
    "the rule was wrong" and "the fault was real and is handled", and once those
    are mixed there is no way back to a false-positive rate."""
    d = request.get_json(force=True)
    deal_id, code, action = d.get("deal_id"), d.get("code"), d.get("action")
    if not deal_id or not code:
        return jsonify({"error": "deal_id 和 code 必填"}), 400
    try:
        if action == "clear":
            console_marks.clear(DB_PATH, deal_id, code)
        else:
            console_marks.put(DB_PATH, deal_id, code, action,
                              d.get("verdict"), d.get("note"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


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

    inspect = _inspection_payload()

    return render_template("index.html", categories=categories,
                           status=pipeline_status.overall(DB_PATH),
                           deals=all_deals, sample=sample, **inspect)


@app.route("/api/deals")
def api_deals():
    store    = request.args.get("store") or None
    category = request.args.get("category") or None
    deals    = get_deals(store=store, category=category)
    return jsonify(deals)


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
