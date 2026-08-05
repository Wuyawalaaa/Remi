import re


GOOGLE_CATEGORY_MAP = {
    "beverages": "beverages",
    "juice": "beverages",
    "soda": "beverages",
    "water": "beverages",
    "coffee": "beverages",
    "tea": "beverages",
    "sports drinks": "beverages",
    "soft drinks": "beverages",
    "mixed drinks": "alcohol",
    "beer": "alcohol",
    "wine": "alcohol",
    "spirits": "alcohol",
    "liquor & spirits": "alcohol",
    "produce": "produce",
    "fruits & vegetables": "produce",
    "fresh vegetables": "produce",
    "fresh fruit": "produce",
    "fresh & frozen fruits": "produce",
    "fresh & frozen vegetables": "produce",
    "coconuts": "produce",
    "herbs & spices (fresh)": "produce",
    "meat": "meat",
    "meat, seafood & eggs": "meat",
    "beef": "meat",
    "pork": "meat",
    "poultry": "meat",
    "lamb": "meat",
    "game meat": "meat",
    "seafood": "seafood",
    "fish": "seafood",
    "shrimp": "seafood",
    "shellfish": "seafood",
    "dairy": "dairy",
    "dairy products": "dairy",
    "cheese": "dairy",
    "milk": "dairy",
    "butter": "dairy",
    "eggs": "dairy",
    "cream": "dairy",
    "yogurt": "dairy",
    "bread": "bakery",
    "bakery": "bakery",
    "pastries": "bakery",
    "tortillas": "bakery",
    "tortillas & wraps": "bakery",
    "deli": "deli",
    "deli meats": "deli",
    "prepared meals": "prepared foods",
    "prepared foods": "prepared foods",
    "soups & stews": "prepared foods",
    "frozen": "frozen",
    "frozen foods": "frozen",
    "ice cream": "frozen",
    "ice cream & frozen yogurt": "frozen",
    "frozen desserts & novelties": "frozen",
    "frozen vegetables": "frozen",
    "frozen meat": "frozen",
    "snacks": "snacks",
    "snack foods": "snacks",
    "candy & chocolate": "snacks",
    "chips": "snacks",
    "crackers": "snacks",
    "candy": "snacks",
    "cookies": "snacks",
    "nuts": "snacks",
    "pudding & gelatin snacks": "snacks",
    "condiments": "pantry",
    "sauces": "pantry",
    "canned goods": "pantry",
    "cooking & baking ingredients": "pantry",
    "sugar & sweeteners": "pantry",
    "pasta & noodles": "pantry",
    "pasta": "pantry",
    "rice": "pantry",
    "beans": "pantry",
    "baking": "pantry",
    "cooking oils": "pantry",
    "cereal": "pantry",
    "breakfast foods": "pantry",
    "household": "household",
    "cleaning supplies": "household",
    "laundry": "household",
    "paper goods": "household",
    "disposable tableware": "household",
    "disposable plates": "household",
    "personal care": "personal care",
    "hair care": "personal care",
    "oral care": "personal care",
    "skin care": "personal care",
    "health": "health",
    "vitamins": "health",
    "medicine": "health",
    "baby": "baby",
    "baby food": "baby",
    "diapers": "baby",
    "flowers": "floral",
    "plants": "floral",
    "household supplies": "household",
    "household cleaning supplies": "household",
    "household cleaning products": "household",
    "dish detergent & soap": "household",
    "paper products": "household",
    "soap": "personal care",
    "fresh fish": "seafood",
    "frozen fruit": "frozen",
    "frozen dessert novelties": "frozen",
}

NAME_CATEGORY_PATTERNS = [
    (re.compile(r"\b(salmon|shrimp|crab|tuna|tilapia|cod|fish|seafood|prawn|ceviche|octopus|catfish)\b", re.I), "seafood"),
    (re.compile(r"\b(beef|pork|chicken|lamb|turkey|steak|rib|brisket|sausage|meat|ham|chorizo|bacon|hen|drumsticks|wings)\b", re.I), "meat"),
    (re.compile(r"\b(milk|cheese|butter|cream|yogurt|egg|queso|crema|margarina)\b", re.I), "dairy"),
    (re.compile(r"\b(rice|noodle|pasta|flour|soy sauce|oil|vinegar|spice|seasoning|sauce|broth|stock|beans?|soup|hominy|condensed milk|sugar|piloncillo)\b", re.I), "pantry"),
    (re.compile(r"\b(ice cream|frozen|dumpling|gyoza|edamame|mochi|ice pops|frozen dairy dessert|pulp)\b", re.I), "frozen"),
    (re.compile(r"\b(chip|cracker|cookie|candy|snack|popcorn|jerky|nut|biscuit|chocolate|gelatin|ferrero)\b", re.I), "snacks"),
    (re.compile(r"\b(water|juice|soda|tea|coffee|drink|beverage|milk tea|boba|refresco|coconut drinks?)\b", re.I), "beverages"),
    (re.compile(r"\b(beer|wine|sake|soju|liquor|whisky|baijiu|tequila|vodka|rum|brandy|scotch|modelo|corona|dos equis|michelob)\b", re.I), "alcohol"),
    (re.compile(r"\b(shampoo|conditioner|lotion|cleanser|sunscreen|moisturizer|serum|mask|hair gel|toothpaste|oral|skin care)\b", re.I), "personal care"),
    (re.compile(r"\b(detergent|bleach|cleaner|sponge|towel|tissue|wrap|bag|dish liquid|dish soap|paper towel|bath tissue|charcoal|foil|soap, 90 oz|paper plates?)\b", re.I), "household"),
    (re.compile(r"\b(vitamin|supplement|medicine|capsule|tablet|health|probiotic|xl-3)\b", re.I), "health"),
    (re.compile(r"\b(tofu|mushroom|vegetable|fruit|broccoli|cabbage|spinach|onion|garlic|ginger|pepper|tomato|papaya|pear|mango|avocado|banana|plantain|squash|cactus|cilantro|beets|cabbage|coconut)\b", re.I), "produce"),
    (re.compile(r"\b(bread|bun|cake|pastry|roll|croissant|bagels?|cuernitos?|tortilla|pan dulce)\b", re.I), "bakery"),
    (re.compile(r"\b(deli|prepared|rotisserie|salad|fruit bowl)\b", re.I), "prepared foods"),
]


def _clean_taxonomy_name(name):
    if not name:
        return ""
    return re.sub(r"\s+", " ", str(name).lower()).strip()


def google_category_to_canonical(item_categories, fallback_normalizer=None):
    """Map a Google/Flipp taxonomy dict onto Remi's canonical category set."""
    if not item_categories:
        return None

    for level in ("l5", "l4", "l3", "l2", "l1"):
        node = item_categories.get(level) or {}
        name = _clean_taxonomy_name(node.get("category_name"))
        if name in GOOGLE_CATEGORY_MAP:
            return GOOGLE_CATEGORY_MAP[name]

    if fallback_normalizer:
        for level in ("l5", "l4", "l3", "l2", "l1"):
            node = item_categories.get(level) or {}
            result = fallback_normalizer(_clean_taxonomy_name(node.get("category_name")))
            if result:
                return result

    return None


def guess_category_from_name(product_name):
    text = str(product_name or "")
    for pattern, category in NAME_CATEGORY_PATTERNS:
        if pattern.search(text):
            return category
    return None
