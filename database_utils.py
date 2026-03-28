import os
import uuid
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")


def get_connection():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _table_exists(cur, table_name):
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(cur, table_name, column_name):
    rows = cur.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def ensure_pipeline_schema():
    con = get_connection()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id               TEXT PRIMARY KEY,
            store_name       TEXT NOT NULL,
            valid_from       DATE,
            valid_to         DATE,
            status           TEXT NOT NULL,
            created_at       DATETIME NOT NULL,
            published_at     DATETIME,
            row_count        INTEGER NOT NULL DEFAULT 0,
            warning_count    INTEGER NOT NULL DEFAULT 0,
            error_count      INTEGER NOT NULL DEFAULT 0,
            source_flyer_path TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id    TEXT,
            store_name  TEXT NOT NULL,
            step_name   TEXT NOT NULL,
            status      TEXT NOT NULL,
            started_at  DATETIME,
            finished_at DATETIME,
            message     TEXT,
            FOREIGN KEY(batch_id) REFERENCES batches(id)
        )
    """)

    if _table_exists(cur, "deals") and not _column_exists(cur, "deals", "batch_id"):
        cur.execute("ALTER TABLE deals ADD COLUMN batch_id TEXT")

    if _table_exists(cur, "deals"):
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deals_batch_id ON deals(batch_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batches_store_status ON batches(store_name, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_runs_batch_id ON pipeline_runs(batch_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_runs_store_step ON pipeline_runs(store_name, step_name)")

    _backfill_batches(cur)
    _normalize_published_batches(cur)

    con.commit()
    con.close()


def _backfill_batches(cur):
    if not _table_exists(cur, "deals") or not _column_exists(cur, "deals", "batch_id"):
        return

    rows = cur.execute("""
        SELECT store_name, valid_from, valid_to,
               MAX(scraped_at) AS created_at,
               COUNT(*) AS row_count
        FROM deals
        WHERE batch_id IS NULL
        GROUP BY store_name, valid_from, valid_to
        ORDER BY store_name, valid_from
    """).fetchall()

    for row in rows:
        batch_id = str(uuid.uuid4())
        created_at = row["created_at"] or datetime.now().isoformat()
        cur.execute("""
            INSERT INTO batches (
                id, store_name, valid_from, valid_to,
                status, created_at, published_at,
                row_count, warning_count, error_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """, (
            batch_id,
            row["store_name"],
            row["valid_from"],
            row["valid_to"],
            "superseded",
            created_at,
            created_at,
            row["row_count"],
        ))
        cur.execute("""
            UPDATE deals
            SET batch_id = ?
            WHERE batch_id IS NULL
              AND store_name = ?
              AND valid_from = ?
              AND valid_to = ?
        """, (
            batch_id,
            row["store_name"],
            row["valid_from"],
            row["valid_to"],
        ))


def _normalize_published_batches(cur):
    stores = cur.execute("SELECT DISTINCT store_name FROM batches").fetchall()
    for store_row in stores:
        store_name = store_row["store_name"]
        latest = cur.execute("""
            SELECT id
            FROM batches
            WHERE store_name = ?
            ORDER BY
                CASE status
                    WHEN 'published' THEN 0
                    WHEN 'running' THEN 1
                    WHEN 'failed' THEN 2
                    ELSE 3
                END,
                COALESCE(published_at, created_at) DESC,
                COALESCE(valid_to, '') DESC,
                created_at DESC
            LIMIT 1
        """, (store_name,)).fetchone()
        if not latest:
            continue
        cur.execute("""
            UPDATE batches
            SET status = CASE WHEN id = ? THEN 'published' ELSE 'superseded' END,
                published_at = CASE
                    WHEN id = ? AND published_at IS NULL THEN created_at
                    ELSE published_at
                END
            WHERE store_name = ?
              AND status IN ('published', 'superseded')
        """, (latest["id"], latest["id"], store_name))


def create_batch(store_name, valid_from=None, valid_to=None, source_flyer_path=None, status="running"):
    ensure_pipeline_schema()
    batch_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat()
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO batches (
            id, store_name, valid_from, valid_to, status,
            created_at, published_at, row_count, warning_count, error_count, source_flyer_path
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0, 0, 0, ?)
    """, (batch_id, store_name, valid_from, valid_to, status, created_at, source_flyer_path))
    con.commit()
    con.close()
    return batch_id


def update_batch(batch_id, **fields):
    if not fields:
        return
    ensure_pipeline_schema()
    assignments = []
    values = []
    for key, value in fields.items():
        assignments.append(f"{key} = ?")
        values.append(value)
    values.append(batch_id)

    con = get_connection()
    cur = con.cursor()
    cur.execute(f"UPDATE batches SET {', '.join(assignments)} WHERE id = ?", values)
    con.commit()
    con.close()


def mark_batch_published(batch_id, row_count=None, warning_count=None, error_count=None):
    ensure_pipeline_schema()
    con = get_connection()
    cur = con.cursor()
    row = cur.execute("SELECT store_name FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not row:
        con.close()
        raise ValueError(f"Unknown batch_id: {batch_id}")

    now = datetime.now().isoformat()
    cur.execute("""
        UPDATE batches
        SET status = 'superseded'
        WHERE store_name = ?
          AND id <> ?
          AND status = 'published'
    """, (row["store_name"], batch_id))

    cur.execute("""
        UPDATE batches
        SET status = 'published',
            published_at = ?,
            row_count = COALESCE(?, row_count),
            warning_count = COALESCE(?, warning_count),
            error_count = COALESCE(?, error_count)
        WHERE id = ?
    """, (now, row_count, warning_count, error_count, batch_id))
    con.commit()
    con.close()


def mark_batch_failed(batch_id, message=None, row_count=None, warning_count=None, error_count=None):
    updates = {
        "status": "failed",
    }
    if row_count is not None:
        updates["row_count"] = row_count
    if warning_count is not None:
        updates["warning_count"] = warning_count
    if error_count is not None:
        updates["error_count"] = error_count
    update_batch(batch_id, **updates)

    if message:
        batch = get_batch(batch_id)
        record_pipeline_run(
            batch_id=batch_id,
            store_name=batch["store_name"],
            step_name="batch",
            status="failed",
            message=message,
        )


def get_batch(batch_id):
    ensure_pipeline_schema()
    con = get_connection()
    cur = con.cursor()
    row = cur.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    con.close()
    return row


def record_pipeline_run(store_name, step_name, status, batch_id=None, message=None, started_at=None, finished_at=None):
    ensure_pipeline_schema()
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO pipeline_runs (
            batch_id, store_name, step_name, status,
            started_at, finished_at, message
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (batch_id, store_name, step_name, status, started_at, finished_at, message))
    con.commit()
    con.close()
