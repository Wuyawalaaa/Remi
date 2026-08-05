"""Make each batch own its rows, so a rejected import cannot blank a live store.

Before: UNIQUE(product_name, store_name, valid_from), and the import's upsert
reassigned batch_id on conflict. Re-importing a week therefore MOVED rows off the
currently-published batch onto the new one. Reproduced: 99 Ranch's live batch went
181 rows -> 0 while still reading status='published', row_count=181. Any check
that ran after the import was checking a store that had already gone dark.

After: a non-null product_key per row, UNIQUE(batch_id, product_key). Re-importing
creates a fresh, independent set of rows and never touches an existing batch.

product_key rather than the raw columns because brand and price are both nullable,
and SQLite treats NULLs as distinct in a unique index — a key containing them
silently stops constraining exactly the rows that repeat most (unpriced ad tiles
such as "Snacks", which appeared ten times in one test). It also has to include
brand: one real Safeway week lists Corona, Lagunitas and Coors all as "Beer" at
$13.99, and a key without brand collapses three deals into one.

Idempotent, atomic, and safe to re-run.
"""
import os
import sys
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

EXPECTED_INDEX = "CREATE UNIQUE INDEX idx_dedup ON deals (batch_id, product_key)"


def build_product_key(brand, product_name, per_unit_volume, price_flyer):
    """Stable, never NULL. Distinguishes same-name deals that differ in brand,
    size or price; collapses genuine re-inserts of the same deal."""
    parts = [
        (brand or "").strip().lower(),
        (product_name or "").strip().lower(),
        (per_unit_volume or "").strip().lower(),
        "" if price_flyer is None else f"{float(price_flyer):.2f}",
    ]
    return "|".join(" ".join(p.split()) for p in parts)


def migrate(db_path=DB_PATH, verbose=True):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    have = {r[1] for r in cur.execute("PRAGMA table_info(deals)")}
    if "product_key" not in have:
        cur.execute("ALTER TABLE deals ADD COLUMN product_key TEXT")
        if verbose:
            print("  已添加 product_key 列")

    filled = 0
    for r in cur.execute(
        "SELECT id, brand, product_name, per_unit_volume, price_flyer FROM deals WHERE product_key IS NULL"
    ).fetchall():
        cur.execute(
            "UPDATE deals SET product_key = ? WHERE id = ?",
            (build_product_key(r["brand"], r["product_name"], r["per_unit_volume"], r["price_flyer"]), r["id"]),
        )
        filled += 1
    if verbose and filled:
        print(f"  已为 {filled} 条历史数据补上 product_key")

    clashes = cur.execute(
        """SELECT batch_id, product_key, COUNT(*) n FROM deals
           WHERE batch_id IS NOT NULL AND product_key IS NOT NULL
           GROUP BY batch_id, product_key HAVING n > 1"""
    ).fetchall()
    if clashes:
        con.rollback()
        con.close()
        raise RuntimeError(
            f"{len(clashes)} 组历史数据在新规则下冲突，迁移中止，数据库未改动。"
            f" 例: batch={clashes[0]['batch_id'][:8]} key={clashes[0]['product_key'][:60]}"
        )

    # One transaction: a half-applied migration would leave the table with no
    # unique constraint at all, which reads as success while every later import
    # silently saves nothing.
    cur.execute("DROP INDEX IF EXISTS idx_dedup")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup ON deals (batch_id, product_key)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_deals_product_name ON deals(product_name)")
    # Structurally prevents two live batches for one store, which after this
    # change would double-list every product rather than being merely untidy.
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_published "
        "ON batches(store_name) WHERE status = 'published'"
    )
    con.commit()

    got = cur.execute("SELECT sql FROM sqlite_master WHERE name='idx_dedup'").fetchone()[0]
    con.close()
    normalize_run_store_names(db_path, verbose=verbose)
    if verbose:
        print(f"  索引现在是: {got}")
    return got


def normalize_run_store_names(db_path=DB_PATH, verbose=True):
    """Make pipeline_runs.store_name agree with batches.store_name.

    The orchestrator wrote slugs ("hmart") while the importer wrote display names
    ("H Mart"), so the admin dashboard's join between the two tables matched
    nothing — and matched nothing precisely for the stores whose download step
    failed before the importer ever ran. Four months of weekly failures were
    invisible for this reason alone.
    """
    import stores
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    changed = 0
    for key in stores.STORES:
        display = stores.display_name(key)
        if display == key:
            continue
        cur.execute("UPDATE pipeline_runs SET store_name = ? WHERE store_name = ?", (display, key))
        changed += cur.rowcount
    con.commit()
    con.close()
    if verbose and changed:
        print(f"  已将 {changed} 条运行记录的店名统一为显示名")
    return changed


def verify(db_path=DB_PATH):
    con = sqlite3.connect(db_path)
    idx = {r[0]: r[1] for r in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND name IN ('idx_dedup','idx_one_published')"
    )}
    nulls = con.execute("SELECT COUNT(*) FROM deals WHERE product_key IS NULL").fetchone()[0]
    con.close()
    problems = []
    if "batch_id, product_key" not in (idx.get("idx_dedup") or ""):
        problems.append(f"idx_dedup 不对: {idx.get('idx_dedup')}")
    if "idx_one_published" not in idx:
        problems.append("缺少 idx_one_published")
    if nulls:
        problems.append(f"{nulls} 条数据的 product_key 为空")
    return (not problems), problems


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    print(f"迁移 {path}")
    migrate(path)
    ok, problems = verify(path)
    print("  验证:", "通过" if ok else "失败 -> " + "; ".join(problems))
    sys.exit(0 if ok else 1)


