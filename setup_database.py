import sqlite3
import json
import logging
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)


def create_database():
    db_exists = os.path.exists(DB_PATH)

    if db_exists:
        log.info(f"Database already exists at {DB_PATH}. Skipping creation.")
        return

    log.info(f"Creating new database at {DB_PATH}...")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.executescript("""
        CREATE TABLE deals (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            store_name          TEXT    NOT NULL,
            product_name        TEXT    NOT NULL,
            quality_tags        TEXT,               -- JSON array string, e.g. '["Organic", "Hass"]'
            original_price      REAL,               -- null if not shown on flyer
            sale_price          REAL    NOT NULL,
            unit                TEXT,               -- lb, oz, ea, gal, etc.
            discount_amount     REAL,               -- null if not calculable
            discount_percentage REAL,               -- null if not calculable
            valid_from          DATE    NOT NULL,
            valid_to            DATE    NOT NULL,
            category            TEXT,               -- meat, produce, dairy, etc.
            scraped_at          DATETIME NOT NULL,
            flyer_image_url     TEXT,               -- optional source URL
            normalized_price    REAL,               -- sale_price converted to base unit
            normalized_unit     TEXT,               -- per_lb, per_ea, per_gal, per_fl_oz
            price_source        TEXT,               -- 'flyer' | 'google_shopping'
            deal_type           TEXT,               -- exact deal format e.g. '2 for $5', 'Buy 1 Get 1 Free'
            package_size        TEXT,               -- package size/variety e.g. '1-lb', '12 ct', '16 oz'
            deal_conditions     TEXT,               -- purchase conditions e.g. 'must buy 2', 'Mix & Match eligible'
            promotion_group_id  TEXT                -- links items sharing one promotion block
        );

        -- Index for store-based filtering
        CREATE INDEX idx_store ON deals (store_name);

        -- Index for category-based filtering
        CREATE INDEX idx_category ON deals (category);

        -- Index for date range queries
        CREATE INDEX idx_valid_dates ON deals (valid_from, valid_to);

        -- Unique composite index for deduplication
        CREATE UNIQUE INDEX idx_dedup ON deals (product_name, store_name, valid_from);
    """)

    con.commit()
    con.close()
    log.info("Database and indexes created successfully.")


def insert_deal(
    store_name,
    product_name,
    sale_price,
    valid_from,
    valid_to,
    quality_tags=None,
    original_price=None,
    unit=None,
    discount_amount=None,
    discount_percentage=None,
    category=None,
    flyer_image_url=None,
    normalized_price=None,
    normalized_unit=None,
    price_source=None,
):
    """
    Insert a deal into the database.
    Skips silently if the same product/store/valid_from combination already exists.
    Returns True if inserted, False if skipped as duplicate.
    """
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    quality_tags_json = json.dumps(quality_tags) if quality_tags else None

    try:
        cur.execute("""
            INSERT INTO deals (
                store_name, product_name, quality_tags,
                original_price, sale_price, unit,
                discount_amount, discount_percentage,
                valid_from, valid_to, category,
                scraped_at, flyer_image_url,
                normalized_price, normalized_unit,
                price_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            store_name, product_name, quality_tags_json,
            original_price, sale_price, unit,
            discount_amount, discount_percentage,
            valid_from, valid_to, category,
            datetime.now().isoformat(), flyer_image_url,
            normalized_price, normalized_unit,
            price_source
        ))
        con.commit()
        log.info(f"Inserted: {store_name} - {product_name}")
        return True

    except sqlite3.IntegrityError:
        log.info(f"Skipped duplicate: {store_name} - {product_name} (valid_from={valid_from})")
        return False

    finally:
        con.close()


def insert_sample_data():
    log.info("Inserting sample data...")

    samples = [
        {
            "store_name": "Safeway",
            "product_name": "80/20 Ground Beef",
            "quality_tags": ["USDA Choice"],
            "original_price": 6.99,
            "sale_price": 4.99,
            "unit": "lb",
            "discount_amount": 2.00,
            "discount_percentage": 28.6,
            "valid_from": "2026-02-19",
            "valid_to": "2026-02-25",
            "category": "meat",
            "normalized_price": 4.99,
            "normalized_unit": "per_lb",
            "price_source": "flyer",
        },
        {
            "store_name": "Whole Foods",
            "product_name": "80/20 Ground Beef",
            "quality_tags": ["Organic", "Grass-fed"],
            "original_price": 10.99,
            "sale_price": 8.99,
            "unit": "lb",
            "discount_amount": 2.00,
            "discount_percentage": 18.2,
            "valid_from": "2026-02-19",
            "valid_to": "2026-02-25",
            "category": "meat",
            "normalized_price": 8.99,
            "normalized_unit": "per_lb",
            "price_source": "flyer",
        },
        {
            "store_name": "Cardenas Markets",
            "product_name": "Large Eggs",
            "quality_tags": [],
            "original_price": 4.49,
            "sale_price": 2.99,
            "unit": "doz",
            "discount_amount": 1.50,
            "discount_percentage": 33.4,
            "valid_from": "2026-02-19",
            "valid_to": "2026-02-25",
            "category": "dairy",
            # 12 eggs per dozen -> per_ea
            "normalized_price": round(2.99 / 12, 4),
            "normalized_unit": "per_ea",
            "price_source": "flyer",
        },
        {
            "store_name": "H Mart",
            "product_name": "Hass Avocado",
            "quality_tags": ["Hass"],
            "original_price": None,
            "sale_price": 0.99,
            "unit": "ea",
            "discount_amount": None,
            "discount_percentage": None,
            "valid_from": "2026-02-21",
            "valid_to": "2026-02-27",
            "category": "produce",
            "normalized_price": 0.99,
            "normalized_unit": "per_ea",
            "price_source": "flyer",
        },
        {
            # Duplicate of first entry — should be skipped
            "store_name": "Safeway",
            "product_name": "80/20 Ground Beef",
            "quality_tags": ["USDA Choice"],
            "original_price": 6.99,
            "sale_price": 4.99,
            "unit": "lb",
            "discount_amount": 2.00,
            "discount_percentage": 28.6,
            "valid_from": "2026-02-19",
            "valid_to": "2026-02-25",
            "category": "meat",
            "normalized_price": 4.99,
            "normalized_unit": "per_lb",
            "price_source": "flyer",
        },
    ]

    inserted = 0
    skipped = 0
    for s in samples:
        result = insert_deal(**s)
        if result:
            inserted += 1
        else:
            skipped += 1

    log.info(f"Sample data done: {inserted} inserted, {skipped} skipped.")


if __name__ == "__main__":
    create_database()
    insert_sample_data()
