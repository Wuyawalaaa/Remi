import os
import re
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
from parse_compound_deals import expand_compound_deal

FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "safeway")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)



def split_pages(raw_text: str) -> dict:
    """Split _raw.txt into {page_num: text} dict."""
    pages = {}
    current_page = None
    lines = []
    for line in raw_text.split("\n"):
        m = re.match(r"^--- PAGE (\d+) ---$", line.strip())
        if m:
            if current_page is not None:
                pages[current_page] = "\n".join(lines)
            current_page = int(m.group(1))
            lines = []
        elif current_page is not None:
            lines.append(line)
    if current_page is not None:
        pages[current_page] = "\n".join(lines)
    return pages


def extract_page_deals(page_text: str, page_num: int) -> list:
    """Parse the JSON array from one page's OCR output."""
    text = page_text.strip()
    if not text:
        return []

    # Strip markdown fences if the model added them anyway
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    start = text.find("[")
    end   = text.rfind("]")
    if start == -1 or end == -1:
        log.warning(f"  Page {page_num}: no JSON array found, skipping")
        return []

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        log.warning(f"  Page {page_num}: JSON parse error: {e}")
        return []



CATEGORY_MAP = {
    # canonical passthrough
    "produce":        "produce",
    "meat":           "meat",
    "seafood":        "seafood",
    "dairy":          "dairy",
    "bakery":         "bakery",
    "deli":           "deli",
    "frozen":         "frozen",
    "pantry":         "pantry",
    "beverages":      "beverages",
    "snacks":         "snacks",
    "prepared foods": "prepared foods",
    "household":      "household",
    "personal care":  "personal care",
    "health":         "health",
    "baby":           "baby",
    "alcohol":        "alcohol",
    "floral":         "floral",
    "condiments":     "condiments",
    # variants → canonical
    "baked goods":          "bakery",
    "candy":                "snacks",
    "chocolate":            "snacks",
    "nuts":                 "snacks",
    "chips":                "snacks",
    "asian snacks":         "snacks",
    "ice cream":            "frozen",
    "frozen foods":         "frozen",
    "frozen vegetables":    "frozen",
    "household cleaning":   "household",
    "laundry":              "household",
    "cleaning":             "household",
    "paper goods":          "household",
    "kitchenware":          "household",
    "cookware":             "household",
    "hair care":            "personal care",
    "feminine hygiene":     "personal care",
    "shaving":              "personal care",
    "allergy":              "health",
    "sleep aid":            "health",
    "probiotics":           "health",
    "vitamins":             "health",
    "medicine":             "health",
    "pharmacy":             "health",
    "pain relief":          "health",
    "supplements":          "health",
    "poultry":              "meat",
    "chicken":              "meat",
    "pork":                 "meat",
    "beef":                 "meat",
    "lunch meat":           "deli",
    "flowers":              "floral",
    "plants":               "floral",
    "drinks":               "beverages",
    "breakfast":            "pantry",
    "baking needs":         "pantry",
    "spices":               "pantry",
    "canned goods":         "pantry",
    "sauces":               "condiments",
    "dressings":            "condiments",
    "prepared meals":       "prepared foods",
    "prepared food":        "prepared foods",
    "ready to eat":         "prepared foods",
}


def normalize_category(cat) -> str:
    if not cat:
        return None
    return CATEGORY_MAP.get(cat.lower().strip(), None)


def validate_deal(deal: dict) -> bool:
    """Warn about incomplete deals. Always returns True — no deals are dropped."""
    if not deal.get("product_name"):
        log.warning(f"[MISSING product_name] {deal}")
    if deal.get("sale_price") is None and not deal.get("deal_type"):
        log.warning(f"[MISSING price+deal_type] {deal.get('product_name')} | store={deal.get('store_name')}")
    return True




def to_float(val):
    """Coerce a price value to float, return None if not possible."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None



def enrich_deal(deal: dict, valid_from: str, valid_to: str) -> dict:
    """Map fields, fix unit outliers, pass price through — no computation."""
    deal["valid_from"] = valid_from
    deal["valid_to"]   = valid_to

    # Map product → product_name (new schema)
    if not deal.get("product_name") and deal.get("product"):
        deal["product_name"] = deal["product"]

    # Normalize unit_flyer — support both old (unit) and new (unit_flyer) OCR output
    u = deal.get("unit_flyer") or deal.get("unit")
    if u == "for":
        u = "ea"
    elif isinstance(u, str) and u.lower() == "null":
        u = None
    deal["unit_flyer"] = u

    # price_flyer — support both old (sale_price) and new field name
    deal["price_flyer"]    = to_float(deal.get("price_flyer") or deal.get("sale_price"))
    deal["original_price"] = to_float(deal.get("original_price"))
    deal["package_amount"] = deal.get("package_amount") or 1
    deal["category"]       = normalize_category(deal.get("category"))

    return deal


def parse_and_save(store_name: str, flyers_dir: str, file_prefix: str) -> str:
    """
    Shared parse logic for any store.
    Finds the latest {file_prefix}_*_raw.txt in flyers_dir, parses all pages,
    enriches and validates deals, writes {file_prefix}_YYYY-MM-DD_deals.json.
    Returns the output path.
    """
    txts = sorted(Path(flyers_dir).glob(f"{file_prefix}_*_raw.txt"), reverse=True)
    if not txts:
        raise FileNotFoundError(f"No raw OCR text found in {flyers_dir}")
    raw_path = txts[0]
    log.info(f"Merging JSON from: {raw_path}")

    # Filename pattern: {prefix}_YYYY-MM-DD_raw.txt
    stem     = raw_path.stem              # e.g. safeway_2026-02-18_raw
    date_str = stem.split("_")[1]         # 2026-02-18
    valid_from = date_str
    valid_to   = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")

    raw_text = raw_path.read_text(encoding="utf-8")
    pages    = split_pages(raw_text)
    log.info(f"Found {len(pages)} pages")

    all_deals = []
    for page_num in sorted(pages):
        deals = extract_page_deals(pages[page_num], page_num)
        log.info(f"  Page {page_num}: {len(deals)} items")
        all_deals.extend(deals)

    enriched_deals = [enrich_deal(d, valid_from, valid_to) for d in all_deals]
    expanded_deals = []
    for deal in enriched_deals:
        expanded_deals.extend(expand_compound_deal(deal, store_name))

    for d in expanded_deals:
        validate_deal(d)
    valid_deals = expanded_deals

    categories = Counter(d.get("category", "unknown") for d in valid_deals)

    output = {
        "store":       store_name,
        "valid_from":  valid_from,
        "valid_to":    valid_to,
        "parsed_at":   datetime.now().isoformat(),
        "deals_count": len(valid_deals),
        "deals":       valid_deals,
    }

    out_path = os.path.join(flyers_dir, f"{file_prefix}_{date_str}_deals.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"Saved to {out_path}")
    print(f"\n=== Parse complete ===")
    print(f"Store:                 {store_name}")
    print(f"Total deals extracted: {len(valid_deals)}")
    print(f"\nBy category:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {(cat or 'none'):<12} {count}")
    print(f"\nOutput: {out_path}")
    return out_path


def run():
    parse_and_save("Safeway", FLYERS_DIR, "safeway")


if __name__ == "__main__":
    run()
