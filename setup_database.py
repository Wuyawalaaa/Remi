import sqlite3
import json
import logging
import os
from datetime import datetime
from database_utils import ensure_pipeline_schema

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
            brand               TEXT,
            quality_tags        TEXT,
            original_price      REAL,
            price_flyer         REAL,
            unit_flyer          TEXT,
            package_amount      INTEGER,
            per_unit_volume     TEXT,
            discount_amount     REAL,
            discount_percentage REAL,
            valid_from          DATE    NOT NULL,
            valid_to            DATE    NOT NULL,
            category            TEXT,
            scraped_at          DATETIME NOT NULL,
            flyer_image_url     TEXT,
            normalized_price    REAL,
            unit_norm           TEXT,
            price_source        TEXT,
            deal_type           TEXT,
            deal_conditions     TEXT,
            promotion_group_id  TEXT,
            product_key         TEXT,
            batch_id            TEXT
        );

        -- Index for store-based filtering
        CREATE INDEX idx_store ON deals (store_name);

        -- Index for category-based filtering
        CREATE INDEX idx_category ON deals (category);

        -- Index for date range queries
        CREATE INDEX idx_valid_dates ON deals (valid_from, valid_to);

        -- Unique composite index for deduplication
        CREATE UNIQUE INDEX idx_dedup ON deals (batch_id, product_key);
        CREATE INDEX idx_deals_product_name ON deals(product_name);
    """)

    con.commit()
    con.close()
    ensure_pipeline_schema()
    # Run the migration too, so a database created here is structurally identical
    # to one that was migrated. Otherwise a fresh install silently lacks
    # idx_one_published and can end up serving two batches for one store.
    from migrate_batch_independence import migrate
    migrate(DB_PATH, verbose=False)
    log.info("Database and indexes created successfully.")


if __name__ == "__main__":
    create_database()
