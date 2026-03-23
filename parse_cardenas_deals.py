import os
import re
import json
import logging
from pathlib import Path
from datetime import datetime
from collections import Counter

# Reuse CATEGORY_MAP and helpers from the shared parse module
import sys
sys.path.insert(0, os.path.dirname(__file__))
from parse_safeway_deals import (
    validate_deal, enrich_deal, normalize_category, to_float
)

FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "cardenas")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


# ── Google taxonomy L2 → canonical category ───────────────────────────────────

GOOGLE_L2_MAP = {
    "beverages":                    "beverages",
    "juice":                        "beverages",
    "soda":                         "beverages",
    "water":                        "beverages",
    "coffee":                       "beverages",
    "tea":                          "beverages",
    "beer":                         "alcohol",
    "wine":                         "alcohol",
    "spirits":                      "alcohol",
    "produce":                      "produce",
    "fresh vegetables":             "produce",
    "fresh fruit":                  "produce",
    "herbs & spices (fresh)":       "produce",
    "meat":                         "meat",
    "beef":                         "meat",
    "pork":                         "meat",
    "poultry":                      "meat",
    "lamb":                         "meat",
    "game meat":                    "meat",
    "seafood":                      "seafood",
    "fish":                         "seafood",
    "shrimp":                       "seafood",
    "shellfish":                    "seafood",
    "dairy":                        "dairy",
    "cheese":                       "dairy",
    "milk":                         "dairy",
    "butter":                       "dairy",
    "eggs":                         "dairy",
    "cream":                        "dairy",
    "yogurt":                       "dairy",
    "bread":                        "bakery",
    "bakery":                       "bakery",
    "pastries":                     "bakery",
    "tortillas":                    "bakery",
    "deli":                         "deli",
    "deli meats":                   "deli",
    "prepared meals":               "prepared foods",
    "soups & stews":                "prepared foods",
    "frozen":                       "frozen",
    "frozen foods":                 "frozen",
    "ice cream":                    "frozen",
    "frozen vegetables":            "frozen",
    "frozen meat":                  "frozen",
    "snacks":                       "snacks",
    "chips":                        "snacks",
    "crackers":                     "snacks",
    "candy":                        "snacks",
    "cookies":                      "snacks",
    "nuts":                         "snacks",
    "condiments":                   "pantry",
    "sauces":                       "pantry",
    "canned goods":                 "pantry",
    "pasta":                        "pantry",
    "rice":                         "pantry",
    "beans":                        "pantry",
    "baking":                       "pantry",
    "cooking oils":                 "pantry",
    "cereal":                       "pantry",
    "breakfast foods":              "pantry",
    "household":                    "household",
    "cleaning supplies":            "household",
    "laundry":                      "household",
    "paper goods":                  "household",
    "personal care":                "personal care",
    "hair care":                    "personal care",
    "oral care":                    "personal care",
    "skin care":                    "personal care",
    "health":                       "health",
    "vitamins":                     "health",
    "medicine":                     "health",
    "baby":                         "baby",
    "baby food":                    "baby",
    "diapers":                      "baby",
    "flowers":                      "floral",
    "plants":                       "floral",
}


def google_category_to_canonical(item_categories: dict) -> str:
    """Map Flipp's Google taxonomy to our canonical category."""
    if not item_categories:
        return None
    # Try L3, L2, L1 in order — most specific first
    for level in ("l3", "l2", "l1"):
        node = item_categories.get(level) or {}
        name = (node.get("category_name") or "").lower().strip()
        if name in GOOGLE_L2_MAP:
            return GOOGLE_L2_MAP[name]
    # Fallback: try CATEGORY_MAP from parse_safeway_deals
    for level in ("l3", "l2", "l1"):
        node = item_categories.get(level) or {}
        name = (node.get("category_name") or "").lower().strip()
        result = normalize_category(name)
        if result:
            return result
    return None


# ── Price parsing ──────────────────────────────────────────────────────────────

_X_FOR_RE   = re.compile(r"^(\d+)\s*(?:FOR|/)", re.IGNORECASE)
_LB_RE      = re.compile(r"\bLB\b|/LB\b", re.IGNORECASE)
_OZ_RE      = re.compile(r"\b(?:FL\.?\s*)?OZ\b", re.IGNORECASE)
# Matches strings that are purely a size/count with no real product word
# e.g. "48 oz", "5 Ct", "5 Lb Bag", "375 mL", "30 Pk, 12 oz Cans"
_SIZE_ONLY_RE = re.compile(
    r"^[\d\s\.,]*(oz|lb|ct|pk|ml|liter|kg|g|fl\.?\s*oz|bag|pack|cans?|bottles?|liters?)"
    r"(\s*[\d\s\.,]*(oz|lb|ct|pk|ml|liter|kg|g|bag|pack|cans?|bottles?|liters?))*\s*$",
    re.IGNORECASE
)


def parse_price(pre_text, price_text, post_text, sale_story):
    """
    Returns (sale_price, unit, deal_type) by interpreting Flipp price fields.

    Formats observed:
      BOGO  : sale_story="BUY 1 GET 1 FREE"         → (None, "ea", "Buy 1 Get 1 Free")
      X FOR : pre="2 FOR", price="5.00"              → (2.50, "ea", "2 for $5")
      plain : pre="",      price="4.99", post="EA"   → (4.99, "ea", None)
      per lb: pre="",      price="1.49", post="/LB"  → (1.49, "lb", None)
      per oz: pre="",      price="0.99", post="/OZ"  → (0.99, "oz", None)
      empty : price=""                               → (None, None, None)
    """
    story = (sale_story or "").upper()
    pre   = (pre_text   or "").strip()
    price = (price_text or "").strip()
    post  = (post_text  or "").strip()

    # BOGO detection
    if any(kw in story for kw in ("BUY 1 GET 1", "BOGO", "B1G1", "FREE")):
        return None, "ea", "Buy 1 Get 1 Free"

    if not price:
        return None, None, None

    try:
        price_val = float(price.replace("$", "").replace(",", ""))
    except ValueError:
        return None, None, None

    # "X FOR $Y" → per-unit price, preserve deal_type so shopper knows they must buy X
    m = _X_FOR_RE.match(pre)
    if m:
        qty = int(m.group(1))
        deal_type = f"{qty} for ${price_val:.2f}".rstrip('0').rstrip('.')
        return round(price_val / qty, 2), "ea", deal_type

    # Unit from post_price_text
    post_upper = post.upper()
    if _LB_RE.search(post_upper):
        return price_val, "lb", None
    if _OZ_RE.search(post_upper):
        return price_val, "oz", None

    return price_val, "ea", None


# ── Product → deal conversion ──────────────────────────────────────────────────

def is_size_only_name(name: str) -> bool:
    """Return True if the name is just a package size with no real product word."""
    return bool(name and _SIZE_ONLY_RE.match(name.strip()))


_COMPOUND_SPLIT_RE = re.compile(r",\s*| or ", re.IGNORECASE)


def split_compound_name(name: str) -> list[str]:
    """Split 'Cilantro, Radish bunch or Green Onion' into ['Cilantro', 'Radish bunch', 'Green Onion'].
    Only splits when the result has multiple substantial parts (not size descriptors)."""
    if not name:
        return [name]
    # Only attempt split if there's a comma or ' or ' present
    if "," not in name and " or " not in name.lower():
        return [name]
    parts = [p.strip() for p in _COMPOUND_SPLIT_RE.split(name) if p.strip()]
    # If any single part is just a size descriptor, the whole thing is one product (e.g. "Red or Yellow Potatoes 5 Lb Bag")
    if any(is_size_only_name(p) for p in parts):
        return [name]
    # Require each part to be at least 2 chars and have at least one letter
    valid_parts = [p for p in parts if len(p) >= 2 and re.search(r"[a-zA-Z]", p)]
    return valid_parts if len(valid_parts) > 1 else [name]


def product_to_deal(p: dict) -> list[dict]:
    sale_price, unit, deal_type = parse_price(
        p.get("pre_price_text"),
        p.get("price_text"),
        p.get("post_price_text"),
        p.get("sale_story"),
    )
    base = {
        "sale_price":     sale_price,
        "original_price": None,
        "unit":           unit,
        "deal_type":      deal_type,
        "quality_tags":   [],
        "category":       google_category_to_canonical(p.get("item_categories")),
        "valid_from":     (p.get("valid_from") or "")[:10],
        "valid_to":       (p.get("valid_to")   or "")[:10],
    }
    raw_name = p.get("description") or p.get("name")
    names = split_compound_name(raw_name)
    return [{**base, "product_name": n} for n in names]


# ── Main ───────────────────────────────────────────────────────────────────────

def find_latest_raw():
    files = sorted(Path(FLYERS_DIR).glob("cardenas_*_raw.json"), reverse=True)
    if not files:
        raise FileNotFoundError(f"No raw JSON found in {FLYERS_DIR}")
    return files[0]


def run():
    raw_path = find_latest_raw()
    log.info(f"Parsing: {raw_path}")

    with open(raw_path, encoding="utf-8") as f:
        data = json.load(f)

    valid_from = data["valid_from"]
    valid_to   = data["valid_to"]
    products   = data["products"]
    log.info(f"Products in raw: {len(products)}")

    all_deals = []
    for p in products:
        for deal in product_to_deal(p):
            if is_size_only_name(deal.get("product_name")):
                continue
            deal = enrich_deal(deal, valid_from, valid_to)
            all_deals.append(deal)

    valid_deals = [d for d in all_deals if validate_deal(d)]
    skipped     = len(all_deals) - len(valid_deals)
    if skipped:
        log.warning(f"Skipped {skipped} items missing required fields")

    categories = Counter(d.get("category", "unknown") for d in valid_deals)

    output = {
        "store":       "Cardenas",
        "valid_from":  valid_from,
        "valid_to":    valid_to,
        "parsed_at":   datetime.now().isoformat(),
        "deals_count": len(valid_deals),
        "deals":       valid_deals,
    }

    date_str = valid_from
    out_path = os.path.join(FLYERS_DIR, f"cardenas_{date_str}_deals.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"Saved to {out_path}")
    print(f"\n=== Parse complete ===")
    print(f"Store:                 Cardenas")
    print(f"Total deals extracted: {len(valid_deals)}")
    print(f"\nBy category:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {(cat or 'none'):<12} {count}")
    print(f"\nOutput: {out_path}")


if __name__ == "__main__":
    run()
