# Granny

Bay Area grocery optimizer. Cross-store deal aggregation → multi-store route optimization → personalized shopping list. Target user: Bay Area Chinese-American families.

**Current stage:** Infra ~70% done. Deal pipeline works (Safeway primary, 97.6% OCR accuracy). Routing algo and conversation layer not started.
**PRD:** `~/Desktop/Granny_PRD_v6.md` — read before working on algo or V0+.

---

## Run

```bash
# Full pipeline (weekly, after flyer updates):
python3 download_safeway_flyer.py   # fetch PDF + Flipp ground truth
python3 ocr_safeway_flyer.py        # vision LLM → JSON per page (~90s)
python3 parse_safeway_deals.py      # enrich + validate JSON
python3 import_to_database.py       # load into SQLite

# Other stores (scripts exist, same pattern):
# download/ocr/parse: hmart, 99ranch, cardenas

python3 app.py                      # http://localhost:5000 (deals) + /admin (validation)
```

**Deps:** `flask python-dotenv pymupdf openai google-genai anthropic pillow requests`
**OCR provider:** OpenRouter → `google/gemini-2.0-flash-001` (best results; set in `.env` as `OCR_PROVIDER=openrouter`)

---

## File Map

| File | Purpose |
|------|---------|
| `download_safeway_flyer.py` | Flipp API → PDF + ground truth JSON. Idempotent. |
| `ocr_safeway_flyer.py` | PDF pages → PNG → vision LLM → `_raw.txt` (JSON per page) |
| `parse_safeway_deals.py` | `_raw.txt` → `_deals.json`. Normalizes price labels, computes discounts. |
| `import_to_database.py` | `_deals.json` → `database.db`. Normalizes units (oz→per_lb, etc). |
| `setup_database.py` | Creates schema. **Bug:** missing `price_label` in CREATE TABLE — run ALTER after. |
| `app.py` | Flask server. `/` deal browser, `/admin` validation dashboard. |
| `static/script.js` | Vanilla JS. Chip filters → `/api/deals` → render cards. |
| `0222.md` | Detailed project memory (decisions, architecture rationale). |
| `grocery-deal-proposal.md` | Original pitch (Chinese). Vision + monetization strategy. |

**Data layout:**
```
flyers/safeway/
  safeway_YYYY-MM-DD.pdf
  safeway_YYYY-MM-DD_raw.txt       ← OCR output, --- PAGE N --- markers
  safeway_YYYY-MM-DD_deals.json    ← ~196 items
  flipp_data/safeway_flipp_YYYY-MM-DD.json
temp/safeway_pages/page_NN.png     ← intermediate (2.0x res)
database.db                        ← SQLite, all stores
```

---

## DB Schema (table: `deals`)

`id, store_name, product_name, quality_tags (JSON), original_price, sale_price, unit, discount_amount, discount_percentage, valid_from, valid_to, category, scraped_at, flyer_image_url, normalized_price, normalized_unit, price_label`

Unique constraint: `(product_name, store_name, valid_from)`. Units normalize to: `per_lb / per_ea / per_gal / per_fl_oz`.

---

## Known Issues

| Issue | Fix needed |
|-------|-----------|
| `price_label` missing from `setup_database.py` CREATE TABLE | Add column; workaround: `ALTER TABLE deals ADD COLUMN price_label TEXT` |
| Flipp token + postal code hardcoded in `download_safeway_flyer.py` | Move to `.env` |
| OCR category field not normalized (model invents values) | Add fixed category list to `OCR_PROMPT` |
| No auth on `/admin` | Fine for local; add before any deployment |

---

## What's Left (Infra → V0)

- [ ] Fix `setup_database.py` schema (add `price_label`)
- [ ] Add fixed category list to OCR prompt
- [ ] Move hardcoded Flipp token to `.env`
- [ ] More stores: Costco, Whole Foods (different flyer formats)
- [ ] **Stage 1 route pre-computation** (Google Maps API, enumerate store combos) — P1 in PRD
- [ ] **Stage 2 session scoring** (cheapest store per item across route) — P8 in PRD
- [ ] **Stage 3 LLM optimization pass** (final list selection) — P9 in PRD
- [ ] V0: 10 test personas, email capture UI
- [ ] Historical price benchmarks (`item_price_history` table)

---

## Key Decisions (don't re-litigate)

- **OCR outputs structured JSON directly** (not raw text): fixes spatial price-product pairing in grid layouts. Raw-text approach failed because vision models read linearly.
- **"799" = $7.99**: grocery flyer convention (no decimal point). Breaks for non-grocery stores — use separate `OCR_PROMPT` per store type if adding appliances etc.
- **OpenRouter Gemini 2.0 Flash**: DeepSeek vision not publicly available; Novita DeepSeek-OCR-2 endpoint broken; local vLLM needs CUDA (unavailable on M3 Pro).

---

## Session Handoff

**At the end of each work session, update this file:**
- Move completed items out of "What's Left"
- Add any new known issues discovered
- Note current stage and any in-progress work
- Keep it concise — this file is for agents, not humans

_Last updated: 2026-03-18. Stage: Infra pipeline working + chat agent layer added. Routing algo not started._

## Chat Agent Layer (added 2026-03-18)

Temporary middle layer to test the deal pipeline's core value. Not V0 — no personas, no route, no auth.

**What it does:** User chats with an LLM in the web panel. Agent knows all active deals from the DB and helps the user decide where to buy each item. Outputs a shopping list grouped by store. User can keep chatting to modify it.

**New endpoint:** `POST /api/chat` — body: `{message, session_id}` → response: `{reply, shopping_list, session_id}`
- Session history stored in-memory (`_chat_sessions` dict in `app.py`)
- Session ID persisted in `localStorage` on frontend
- Model: `claude-sonnet-4-6` via Anthropic SDK
- Deals context injected fresh on every turn from `get_deals()`
- "Clear" button resets session

**Files changed:** `app.py` (added `/api/chat`, `_deals_context()`, `_CHAT_SYSTEM`), `templates/index.html` (two-column layout), `static/script.js` (chat UI logic), `static/style.css` (layout + chat styles)
