import os
import re
import sys
import json
import logging
import sqlite3
from pathlib import Path
from datetime import datetime
from database_utils import (
    ensure_pipeline_schema,
    create_batch,
    update_batch,
    mark_batch_published,
    mark_batch_failed,
    record_pipeline_run,
)

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
    r'([\d.]+)\s*(ml|l|liter|litre|fl\.?\s*oz|floz|gal|qt|quart|pt|pint|lb|lbs|g|kg|oz|doz|ea|each|ct|count|pk|pack|box)\b',
    re.IGNORECASE
)

LIQUID_UNITS = {"ml", "l", "liter", "litre", "floz", "fl oz", "gal", "qt", "quart", "pt", "pint"}
WEIGHT_UNITS = {"lb", "lbs", "g", "kg", "oz"}
COUNT_UNITS = {"doz", "ea", "each", "ct", "count", "pk", "pack", "box"}
PACK_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*[- ]?(pack|pk|ct|count|box)\b[^0-9]{0,8}(\d+(?:\.\d+)?)\s*[- ]?\s*(ml|l|liter|litre|fl\.?\s*oz|floz|gal|qt|quart|pt|pint|lb|lbs|g|kg|oz)\.?\b',
    re.IGNORECASE
)

def parse_per_unit_volume(s):
    """Extract the first comparable quantity from package text."""
    if not s:
        return None, None
    cleaned = str(s).strip().lower().replace("-", " ")
    m = _VOL_UNIT_RE.search(cleaned)
    if not m:
        return None, None
    unit = re.sub(r"\s+", " ", m.group(2).lower())
    if unit == "fl. oz":
        unit = "fl oz"
    if unit == "lbs":
        unit = "lb"
    if unit in {"each", "count"}:
        unit = "ea"
    return float(m.group(1)), unit


def to_ml(amount, unit):
    """Convert amount in given unit to ml. Returns None if unit unrecognized."""
    conversions = {
        "ml": 1, "l": 1000, "liter": 1000, "litre": 1000,
        "floz": 29.5735, "fl oz": 29.5735, "oz": 29.5735,
        "gal": 3785.41, "qt": 946.353, "quart": 946.353,
        "pt": 473.176, "pint": 473.176,
    }
    return round(amount * conversions[unit], 4) if unit in conversions else None


def to_lb(amount, unit):
    """Convert amount in given unit to lb. Returns None if unit unrecognized."""
    conversions = {
        "lb": 1, "oz": 1/16, "g": 1/453.592, "kg": 2.20462,
    }
    return round(amount * conversions[unit], 4) if unit in conversions else None


def _normalize_unit_label(unit):
    unit = (unit or "").strip().lower().replace("_", " ")
    unit = re.sub(r"\s+", " ", unit)
    aliases = {
        "fl. oz": "fl oz",
        "fluid ounce": "fl oz",
        "fluid ounces": "fl oz",
        "liter": "l",
        "litre": "l",
        "lbs": "lb",
        "each": "ea",
        "count": "ea",
    }
    return aliases.get(unit, unit)


def infer_unit_norm(unit_flyer, per_unit_volume, category):
    """Choose the final comparison unit: ml, lb, or ea."""
    unit_flyer = _normalize_unit_label(unit_flyer)
    vol_amount, vol_unit = parse_per_unit_volume(per_unit_volume)

    if unit_flyer in LIQUID_UNITS:
        return "ml"
    if vol_unit in LIQUID_UNITS:
        return "ml"
    if (category or "").lower().strip() in LIQUID_CATEGORIES and (
        unit_flyer in {"ea", "pk", "pack", "box", "ct", ""} or vol_unit in LIQUID_UNITS
    ):
        return "ml"

    if unit_flyer in WEIGHT_UNITS:
        return "lb"
    if vol_unit in WEIGHT_UNITS:
        return "lb"

    return "ea"


def _safe_quantity(value):
    try:
        qty = float(value)
    except (TypeError, ValueError):
        return 1.0
    return qty if qty > 0 else 1.0


def extract_total_measurement(unit_flyer, package_amount, per_unit_volume):
    """
    Return (total_amount, total_unit) for the item content when possible.
    Examples:
      6 x 12 oz -> (72, "oz")
      750 ml    -> (750, "ml")
      1 lb      -> (1, "lb")
    """
    qty = _safe_quantity(package_amount)
    text = (per_unit_volume or "").replace("\u00a0", " ").replace("-", " ").lower()

    pack_matches = list(PACK_RE.finditer(text))
    if len(pack_matches) == 1:
        pack_qty = float(pack_matches[0].group(1))
        size_amount = float(pack_matches[0].group(3))
        size_unit = _normalize_unit_label(pack_matches[0].group(4))
        return pack_qty * size_amount * qty, size_unit

    vol_amount, vol_unit = parse_per_unit_volume(per_unit_volume)
    if vol_amount is not None and vol_unit:
        return vol_amount * qty, vol_unit

    unit_flyer = _normalize_unit_label(unit_flyer)
    if unit_flyer in LIQUID_UNITS | WEIGHT_UNITS | {"doz"}:
        return qty, unit_flyer

    return None, None


def extract_total_measurement_from_text(text, multiplier=1):
    """Fallback: infer total comparable content from product/deal text."""
    if not text:
        return None, None

    cleaned = str(text).replace("\u00a0", " ").replace("-", " ").lower()

    pack_matches = list(PACK_RE.finditer(cleaned))
    if len(pack_matches) == 1:
        pack_qty = float(pack_matches[0].group(1))
        size_amount = float(pack_matches[0].group(3))
        size_unit = _normalize_unit_label(pack_matches[0].group(4))
        return pack_qty * size_amount * _safe_quantity(multiplier), size_unit

    amount, unit = parse_per_unit_volume(cleaned)
    if amount is not None and unit:
        return amount * _safe_quantity(multiplier), unit

    return None, None


def normalize_price(price_flyer, unit_flyer, package_amount, per_unit_volume, category, product_name=None, deal_conditions=None):
    """
    normalized_price = total flyer price / total comparable content in unit_norm.
    unit_norm is always one of: ml, lb, ea.
    """
    if price_flyer is None:
        return None, None

    fallback_text = " ".join(part for part in [product_name, deal_conditions] if part)
    fallback_amount, fallback_unit = extract_total_measurement_from_text(fallback_text, 1)

    inferred_source = per_unit_volume
    if per_unit_volume is None and fallback_unit:
        inferred_source = fallback_text

    unit_norm = infer_unit_norm(unit_flyer, inferred_source, category)
    u = _normalize_unit_label(unit_flyer)
    total_amount, total_unit = extract_total_measurement(unit_flyer, package_amount, per_unit_volume)
    if total_amount is None and fallback_unit:
        total_amount, total_unit = fallback_amount, fallback_unit

    if unit_norm == "ml":
        if total_amount is not None and total_unit in (LIQUID_UNITS | {"oz"}):
            total_ml = to_ml(total_amount, total_unit)
            if total_ml:
                return round(price_flyer / total_ml, 4), "ml"
        if u in LIQUID_UNITS:
            total_ml = to_ml(_safe_quantity(package_amount), u)
            if total_ml:
                return round(price_flyer / total_ml, 4), "ml"
        log.warning(f"[NO LIQUID SIZE] unit_flyer={unit_flyer!r} | package_amount={package_amount!r} | per_unit_volume={per_unit_volume!r}")
        return None, "ml"

    if unit_norm == "lb":
        if total_amount is not None and total_unit in WEIGHT_UNITS:
            total_lb = to_lb(total_amount, total_unit)
            if total_lb:
                return round(price_flyer / total_lb, 4), "lb"
        if u in WEIGHT_UNITS:
            total_lb = to_lb(_safe_quantity(package_amount), u)
            if total_lb:
                return round(price_flyer / total_lb, 4), "lb"
        log.warning(f"[NO WEIGHT SIZE] unit_flyer={unit_flyer!r} | package_amount={package_amount!r} | per_unit_volume={per_unit_volume!r}")
        return None, "lb"

    if total_amount is not None and total_unit == "doz":
        total_each = total_amount * 12
        return round(price_flyer / total_each, 4), "ea"

    total_each = _safe_quantity(package_amount)
    return round(price_flyer / total_each, 4), "ea"


def import_deals(deals: list, store_name: str, batch_id: str | None = None) -> dict:
    """
    Import deals into the database using a single transaction.
    Returns counts: inserted, skipped, failed.
    """
    ensure_pipeline_schema()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    scraped_at = datetime.now().isoformat()

    inserted = 0
    skipped  = 0
    failed   = 0
    warnings = 0

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
                    , deal.get("product_name"), deal.get("deal_conditions")
                )
                if norm_price is None:
                    name = deal.get("product_name") or deal.get("product")
                    log.warning(f"[NO NORM PRICE] {name} | unit_flyer={unit_flyer!r} | price_flyer={price_flyer} | per_unit_volume={per_unit_volume!r} | category={category!r}")
                    warnings += 1

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
                        deal_conditions, promotion_group_id, batch_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        promotion_group_id  = excluded.promotion_group_id,
                        batch_id            = excluded.batch_id
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
                    batch_id,
                ))
                inserted += 1

            except sqlite3.IntegrityError as e:
                failed += 1
                log.warning(f"Failed to upsert '{deal.get('product_name')}': {e}")

            except Exception as e:
                failed += 1
                log.warning(f"Failed to insert '{deal.get('product_name')}': {e}")

        con.commit()
        log.info(f"Transaction committed: {inserted} inserted, {skipped} skipped, {failed} failed, {warnings} warnings")

    except Exception as e:
        con.rollback()
        log.error(f"Transaction rolled back due to error: {e}")
        raise

    finally:
        con.close()

    return {"inserted": inserted, "skipped": skipped, "failed": failed, "warnings": warnings}


def run_one(json_path, batch_id=None, auto_publish=True):
    ensure_pipeline_schema()
    log.info(f"Loading: {json_path}")

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    store_name = data.get("store", "Unknown")
    deals      = data.get("deals", [])
    valid_from = data.get("valid_from")
    valid_to   = data.get("valid_to")
    log.info(f"Found {len(deals)} deals for {store_name}")

    if not batch_id:
        batch_id = create_batch(
            store_name=store_name,
            valid_from=valid_from,
            valid_to=valid_to,
            source_flyer_path=str(json_path),
        )
    else:
        update_batch(
            batch_id,
            valid_from=valid_from,
            valid_to=valid_to,
            source_flyer_path=str(json_path),
        )

    started_at = datetime.now().isoformat()
    try:
        counts = import_deals(deals, store_name, batch_id=batch_id)
    except Exception as exc:
        record_pipeline_run(
            batch_id=batch_id,
            store_name=store_name,
            step_name="import",
            status="failed",
            started_at=started_at,
            finished_at=datetime.now().isoformat(),
            message=str(exc),
        )
        mark_batch_failed(batch_id, message=str(exc))
        raise

    update_batch(
        batch_id,
        row_count=counts["inserted"],
        warning_count=counts["warnings"],
        error_count=counts["failed"],
    )
    record_pipeline_run(
        batch_id=batch_id,
        store_name=store_name,
        step_name="import",
        status="completed",
        started_at=started_at,
        finished_at=datetime.now().isoformat(),
        message=f"inserted={counts['inserted']} warnings={counts['warnings']} failed={counts['failed']}",
    )
    if auto_publish:
        mark_batch_published(
            batch_id,
            row_count=counts["inserted"],
            warning_count=counts["warnings"],
            error_count=counts["failed"],
        )

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    total = cur.execute(
        """
        SELECT COUNT(*)
        FROM deals d
        JOIN batches b ON b.id = d.batch_id
        WHERE d.store_name = ? AND b.status = 'published'
        """,
        (store_name,),
    ).fetchone()[0]
    by_category = cur.execute(
        """
        SELECT d.category, COUNT(*)
        FROM deals d
        JOIN batches b ON b.id = d.batch_id
        WHERE d.store_name = ? AND b.status = 'published'
        GROUP BY d.category
        ORDER BY COUNT(*) DESC
        """,
        (store_name,)
    ).fetchall()
    con.close()

    print(f"\n=== {store_name} ===")
    print(f"Batch ID:  {batch_id}")
    print(f"Inserted:  {counts['inserted']}")
    print(f"Skipped:   {counts['skipped']}  (duplicates)")
    print(f"Failed:    {counts['failed']}")
    print(f"Warnings:  {counts['warnings']}")
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
