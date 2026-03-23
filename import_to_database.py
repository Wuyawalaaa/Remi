import os
import re
import sys
import json
import logging
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH    = os.path.join(os.path.dirname(__file__), "database.db")
FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "safeway")

BASE_DIR = os.path.dirname(__file__)
STORE_DIRS = {
    "safeway":  os.path.join(BASE_DIR, "flyers", "safeway"),
    "hmart":    os.path.join(BASE_DIR, "flyers", "hmart"),
    "99ranch":  os.path.join(BASE_DIR, "flyers", "99ranch"),
    "cardenas": os.path.join(BASE_DIR, "flyers", "cardenas"),
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


def find_latest_deals_json(flyers_dir=None):
    """Find the most recent *_deals.json in the given directory (default: Safeway)."""
    search_dir = flyers_dir or FLYERS_DIR
    files = sorted(Path(search_dir).glob("*_deals.json"), reverse=True)
    if not files:
        raise FileNotFoundError(f"No deals JSON found in {search_dir}")
    return files[0]


LIQUID_CATEGORIES = {"beverages", "alcohol"}

_VOL_UNIT_RE = re.compile(
    r'^([\d.]+)\s*(ml|l|liter|litre|fl\.?oz|floz|oz|gal|qt|quart|pt|pint|lb|lbs|g|kg)s?$',
    re.IGNORECASE
)

def parse_per_unit_volume(s):
    """Parse '750ml' → (750.0, 'ml'), '16oz' → (16.0, 'oz'), '1lb' → (1.0, 'lb'). Returns (None, None) on failure."""
    if not s:
        return None, None
    m = _VOL_UNIT_RE.match(s.strip().replace(" ", ""))
    if not m:
        return None, None
    return float(m.group(1)), m.group(2).lower().rstrip("s")


def to_ml(amount, unit):
    """Convert amount in given unit to ml. Returns None if unit unrecognized."""
    conversions = {
        "ml": 1, "l": 1000, "liter": 1000, "litre": 1000,
        "floz": 29.5735, "fl.oz": 29.5735, "oz": 29.5735,
        "gal": 3785.41, "qt": 946.353, "quart": 946.353,
        "pt": 473.176, "pint": 473.176,
    }
    return round(amount * conversions[unit], 4) if unit in conversions else None


def to_lb(amount, unit):
    """Convert amount in given unit to lb. Returns None if unit unrecognized."""
    conversions = {
        "lb": 1, "lbs": 1, "oz": 1/16, "g": 1/453.592, "kg": 2.20462,
    }
    return round(amount * conversions[unit], 4) if unit in conversions else None


def get_unit_norm(category):
    """Derive target normalization unit from category."""
    cat = (category or "").lower().strip()
    if cat in LIQUID_CATEGORIES:
        return "per_ml"
    if cat in {"produce", "meat", "seafood", "dairy", "bakery", "deli",
               "frozen", "pantry", "condiments", "prepared foods"}:
        return "per_lb"
    return "per_ea"


def normalize_price(price_flyer, unit_flyer, package_amount, per_unit_volume, category):
    """
    normalized_price = price_flyer / total_content_in_unit_norm
    total_content    = package_amount × per_unit_volume (converted to unit_norm)

    If unit_flyer is already a weight/volume, use it directly (per_unit_volume ignored).
    """
    if price_flyer is None:
        return None, None

    unit_norm = get_unit_norm(category)
    cat       = (category or "").lower().strip()
    u         = (unit_flyer or "").lower().strip()
    qty       = package_amount or 1

    # ── unit_flyer is already a measurement → normalize directly ──────────────
    if u in ("lb", "lbs"):
        return round(price_flyer / qty, 4), "per_lb"
    if u == "g":
        return round(price_flyer / qty * 453.592, 4), "per_lb"
    if u == "kg":
        return round(price_flyer / qty / 2.20462, 4), "per_lb"
    if u == "oz":
        if cat in LIQUID_CATEGORIES:
            return round(price_flyer / qty / 29.5735, 4), "per_ml"
        else:
            return round(price_flyer / qty * 16, 4), "per_lb"
    if u in ("ml",):
        return round(price_flyer / qty, 4), "per_ml"
    if u in ("l", "liter", "litre"):
        return round(price_flyer / qty / 1000, 4), "per_ml"
    if u in ("fl_oz", "floz"):
        return round(price_flyer / qty / 29.5735, 4), "per_ml"
    if u == "gal":
        return round(price_flyer / qty / 3785.41, 4), "per_ml"
    if u in ("qt", "quart"):
        return round(price_flyer / qty / 946.353, 4), "per_ml"
    if u in ("pt", "pint"):
        return round(price_flyer / qty / 473.176, 4), "per_ml"
    if u == "doz":
        return round(price_flyer / qty / 12, 4), "per_ea"

    # ── unit_flyer = ea (or ct/pkg) → use per_unit_volume ─────────────────────
    if per_unit_volume:
        vol_amount, vol_unit = parse_per_unit_volume(per_unit_volume)
        if vol_amount is not None:
            if unit_norm == "per_ml":
                total_ml = to_ml(vol_amount * qty, vol_unit)
                if total_ml:
                    return round(price_flyer / total_ml, 4), "per_ml"
            elif unit_norm == "per_lb":
                total_lb = to_lb(vol_amount * qty, vol_unit)
                if total_lb:
                    return round(price_flyer / total_lb, 4), "per_lb"

    # ── ea with no volume info ─────────────────────────────────────────────────
    if u in ("ea", "each", "ct", "pkg", "box", "pk"):
        return round(price_flyer / qty, 4), "per_ea"

    log.warning(f"[UNRECOGNIZED unit_flyer] {u!r} — normalized_price null")
    return None, None


def import_deals(deals: list, store_name: str) -> dict:
    """
    Import deals into the database using a single transaction.
    Returns counts: inserted, skipped, failed.
    """
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    scraped_at = datetime.now().isoformat()

    inserted = 0
    skipped  = 0
    failed   = 0

    try:
        for deal in deals:
            try:
                price_flyer     = deal.get("price_flyer")
                unit_flyer      = deal.get("unit_flyer") or deal.get("unit")
                package_amount  = deal.get("package_amount") or 1
                per_unit_volume = deal.get("per_unit_volume")
                original_price  = deal.get("original_price")
                category        = deal.get("category")

                norm_price, norm_unit = normalize_price(
                    price_flyer, unit_flyer, package_amount, per_unit_volume, category
                )
                if norm_price is None:
                    name = deal.get("product_name") or deal.get("product")
                    log.warning(f"[NO NORM PRICE] {name} | unit_flyer={unit_flyer!r} | price_flyer={price_flyer} | per_unit_volume={per_unit_volume!r} | category={category!r}")

                # discount: compare total original vs price_flyer
                if original_price is not None and price_flyer is not None:
                    total_original      = original_price * package_amount
                    discount_amount     = round(total_original - price_flyer, 2)
                    discount_percentage = round(discount_amount / total_original * 100, 1) if total_original else None
                else:
                    discount_amount     = None
                    discount_percentage = None

                quality_tags_json = json.dumps(deal.get("quality_tags") or [])

                cur.execute("""
                    INSERT INTO deals (
                        store_name, product_name, brand, quality_tags,
                        original_price, price_flyer, unit_flyer,
                        package_amount, per_unit_volume,
                        discount_amount, discount_percentage,
                        valid_from, valid_to, category,
                        scraped_at, flyer_image_url,
                        normalized_price, unit_norm,
                        price_source, deal_type,
                        deal_conditions, promotion_group_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(product_name, store_name, valid_from) DO UPDATE SET
                        brand               = excluded.brand,
                        quality_tags        = excluded.quality_tags,
                        original_price      = excluded.original_price,
                        price_flyer         = excluded.price_flyer,
                        unit_flyer          = excluded.unit_flyer,
                        package_amount      = excluded.package_amount,
                        per_unit_volume     = excluded.per_unit_volume,
                        discount_amount     = excluded.discount_amount,
                        discount_percentage = excluded.discount_percentage,
                        valid_to            = excluded.valid_to,
                        category            = excluded.category,
                        scraped_at          = excluded.scraped_at,
                        normalized_price    = excluded.normalized_price,
                        unit_norm           = excluded.unit_norm,
                        price_source        = excluded.price_source,
                        deal_type           = excluded.deal_type,
                        deal_conditions     = excluded.deal_conditions,
                        promotion_group_id  = excluded.promotion_group_id
                """, (
                    store_name,
                    deal.get("product_name"),
                    deal.get("brand"),
                    quality_tags_json,
                    original_price,
                    price_flyer,
                    unit_flyer,
                    package_amount,
                    per_unit_volume,
                    discount_amount,
                    discount_percentage,
                    deal.get("valid_from"),
                    deal.get("valid_to"),
                    category,
                    scraped_at,
                    None,
                    norm_price,
                    norm_unit,
                    "flyer",
                    deal.get("deal_type"),
                    deal.get("deal_conditions"),
                    deal.get("promotion_group_id"),
                ))
                inserted += 1

            except sqlite3.IntegrityError as e:
                failed += 1
                log.warning(f"Failed to upsert '{deal.get('product_name')}': {e}")

            except Exception as e:
                failed += 1
                log.warning(f"Failed to insert '{deal.get('product_name')}': {e}")

        con.commit()
        log.info(f"Transaction committed: {inserted} inserted, {skipped} skipped, {failed} failed")

    except Exception as e:
        con.rollback()
        log.error(f"Transaction rolled back due to error: {e}")
        raise

    finally:
        con.close()

    return {"inserted": inserted, "skipped": skipped, "failed": failed}


def run_one(json_path):
    log.info(f"Loading: {json_path}")

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    store_name = data.get("store", "Unknown")
    deals      = data.get("deals", [])
    log.info(f"Found {len(deals)} deals for {store_name}")

    counts = import_deals(deals, store_name)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    total = cur.execute("SELECT COUNT(*) FROM deals WHERE store_name=?", (store_name,)).fetchone()[0]
    by_category = cur.execute(
        "SELECT category, COUNT(*) FROM deals WHERE store_name=? GROUP BY category ORDER BY COUNT(*) DESC",
        (store_name,)
    ).fetchall()
    con.close()

    print(f"\n=== {store_name} ===")
    print(f"Inserted:  {counts['inserted']}")
    print(f"Skipped:   {counts['skipped']}  (duplicates)")
    print(f"Failed:    {counts['failed']}")
    print(f"Total in DB: {total}")
    print(f"By category:")
    for cat, count in by_category:
        print(f"  {(cat or 'unknown'):<16} {count}")

    return counts


def run(json_path=None):
    """
    Usage:
      python import_to_database.py              # import all stores
      python import_to_database.py safeway      # import one store by name
      python import_to_database.py path/to.json # import a specific file
    """
    arg = json_path or (sys.argv[1] if len(sys.argv) > 1 else None)

    # Explicit file path
    if arg and (arg.endswith(".json") or os.sep in arg or "/" in arg):
        run_one(arg)
        return

    # Store name(s)
    if arg:
        store_key = arg.lower()
        if store_key not in STORE_DIRS:
            print(f"Unknown store '{arg}'. Known: {', '.join(STORE_DIRS)}")
            sys.exit(1)
        targets = {store_key: STORE_DIRS[store_key]}
    else:
        targets = STORE_DIRS  # all stores

    for store_key, store_dir in targets.items():
        try:
            json_file = find_latest_deals_json(store_dir)
            run_one(json_file)
        except FileNotFoundError as e:
            log.warning(f"Skipping {store_key}: {e}")


if __name__ == "__main__":
    run()
