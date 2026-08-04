"""Shared deal-field normalisation. Used by every parser.

These functions lived in `parse_safeway_deals.py`, a file named after one store
that was in fact the shared library for all four — the other three parsers
imported from it. Deleting it along with the Safeway OCR path would have broken
99 Ranch and Cardenas, and nothing about the filename hinted at that.

`validate_deal` did not come along. Its own docstring read "Always returns True
— no deals are dropped", so `[d for d in deals if validate_deal(d)]` filtered
nothing while reading like a safety check. Validation now lives in publish_gate,
which is the only thing that decides whether data is fit to serve.

enrich_deal still accepts the older field names (`product`, `sale_price`, `unit`)
because parse_99ranch_deals.py still emits them.
"""
import logging

log = logging.getLogger(__name__)


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
