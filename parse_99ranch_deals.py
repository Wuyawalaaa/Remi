import os
import re
import json
import logging
from pathlib import Path
from datetime import datetime
from collections import Counter

import sys
sys.path.insert(0, os.path.dirname(__file__))
from deal_fields import enrich_deal, normalize_category
from parse_taxonomy import google_category_to_canonical

FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "99ranch")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


# ── Unit normalization ─────────────────────────────────────────────────────────

def parse_unit(saleUom: str, variantName: str) -> str:
    """
    Derive a unit string from the 99 Ranch product fields.
    saleUom examples: "each", "lb", "oz"
    variantName examples: "2.4600 oz/each", "1.00 lb/each", "500g/each"
    """
    uom = (saleUom or "").lower().strip()
    if uom == "lb":
        return "lb"
    if uom == "oz":
        return "oz"
    # Check variantName for lb/oz before the slash
    if variantName:
        vn = variantName.lower()
        pre = vn.split("/")[0] if "/" in vn else vn
        if "lb" in pre:
            return "lb"
        if " oz" in pre or pre.endswith("oz"):
            return "oz"
    return "ea"


# ── Category mapping ───────────────────────────────────────────────────────────
# 99 Ranch API rarely returns category names; fall back to product name heuristics.

_NAME_CAT_RE = [
    (re.compile(r"\b(salmon|shrimp|crab|tuna|tilapia|cod|fish|seafood|prawn)\b", re.I), "seafood"),
    (re.compile(r"\b(beef|pork|chicken|lamb|turkey|steak|rib|brisket|sausage|meat)\b", re.I), "meat"),
    (re.compile(r"\b(milk|cheese|butter|cream|yogurt|egg)\b", re.I), "dairy"),
    (re.compile(r"\b(rice|noodle|pasta|flour|soy sauce|oil|vinegar|spice|seasoning|sauce|broth|stock)\b", re.I), "pantry"),
    (re.compile(r"\b(ice cream|frozen|dumpling|gyoza|edamame|mochi)\b", re.I), "frozen"),
    (re.compile(r"\b(chip|cracker|cookie|candy|snack|popcorn|jerky|nut|biscuit)\b", re.I), "snacks"),
    (re.compile(r"\b(water|juice|soda|tea|coffee|drink|beverage|milk tea|boba)\b", re.I), "beverages"),
    (re.compile(r"\b(beer|wine|sake|soju|liquor|whisky|baijiu)\b", re.I), "alcohol"),
    (re.compile(r"\b(shampoo|conditioner|lotion|cleanser|sunscreen|moisturizer|serum|mask|cream|hair|skin|beauty|makeup|toner)\b", re.I), "personal care"),
    (re.compile(r"\b(detergent|bleach|cleaner|sponge|towel|tissue|wrap|bag)\b", re.I), "household"),
    (re.compile(r"\b(vitamin|supplement|medicine|capsule|tablet|health|probiotic)\b", re.I), "health"),
    (re.compile(r"\b(tofu|mushroom|vegetable|fruit|broccoli|cabbage|spinach|onion|garlic|ginger|pepper|tomato)\b", re.I), "produce"),
    (re.compile(r"\b(bread|bun|cake|pastry|roll)\b", re.I), "bakery"),
]


def guess_category(product_name: str):
    for pattern, cat in _NAME_CAT_RE:
        if pattern.search(product_name or ""):
            return cat
    return None


# ── Product → deal ─────────────────────────────────────────────────────────────

def product_to_deal(p: dict) -> dict:
    name           = p.get("productName") or p.get("productNameEN") or ""
    original_price = p.get("price") or p.get("retailPrice")
    sale_price     = p.get("salePrice")
    saleUom        = p.get("saleUom") or ""
    variantName    = p.get("variantName") or p.get("variantNameEN") or ""

    # If sale price equals regular price, no actual discount
    if sale_price is not None and original_price is not None and sale_price >= original_price:
        original_price = None

    unit     = parse_unit(saleUom, variantName)
    category = (
        google_category_to_canonical(p.get("item_categories"), normalize_category)
        or guess_category(name)
    )

    return {
        "product_name":    name,
        "sale_price":      sale_price,
        "original_price":  original_price,
        "unit":            unit,
        "quality_tags":    [],
        "category":        category,
        "flyer_image_url": p.get("image"),
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def find_latest_raw():
    files = sorted(Path(FLYERS_DIR).glob("99ranch_*_raw.json"), reverse=True)
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
        deal = product_to_deal(p)
        deal = enrich_deal(deal, valid_from, valid_to)
        all_deals.append(deal)

    # validate_deal always returned True; the publish gate does validation now.
    valid_deals = all_deals
    skipped     = len(all_deals) - len(valid_deals)
    if skipped:
        log.warning(f"Skipped {skipped} items missing required fields")

    categories = Counter(d.get("category") or "none" for d in valid_deals)

    output = {
        "store":       "99 Ranch",
        "valid_from":  valid_from,
        "valid_to":    valid_to,
        "parsed_at":   datetime.now().isoformat(),
        "deals_count": len(valid_deals),
        "deals":       valid_deals,
    }

    out_path = os.path.join(FLYERS_DIR, f"99ranch_{valid_from}_deals.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"Saved to {out_path}")
    print(f"\n=== Parse complete ===")
    print(f"Store:   99 Ranch")
    print(f"Deals:   {len(valid_deals)}  (skipped {skipped})")
    print(f"Valid:   {valid_from} → {valid_to}")
    print(f"\nBy category:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat:<16} {count}")
    print(f"\nOutput: {out_path}")


if __name__ == "__main__":
    run()
