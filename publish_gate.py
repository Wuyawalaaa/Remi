"""The only place allowed to decide a batch may go live.

Design notes, each earned from a failure that was reproduced:

- Every number is counted from the `deals` rows themselves. `batches.row_count`
  is written by a caller-supplied counter that increments on updates too, and one
  live batch claims 264 rows while owning none. Never trust it; overwrite it.
- Rejection must be free of side effects. That only holds because batches now own
  their own rows (see migrate_batch_independence). Before that change, the import
  had already moved rows off the live batch by the time this ran, so "rejecting"
  still blanked the store.
- Expiry is a warning, not a rejection. Six of eight 99 Ranch imports landed on
  the flyer's final valid day; if the job ever runs a day late, a hard expiry rule
  would reject that store every week, forever. A one-day-stale flyer beats none.
- The trailing-median rule filters empty batches BEFORE taking the last N.
  Filtering after meant a store with five consecutive failures had no baseline
  left, i.e. the collapse rule switched itself off for exactly the broken store.
"""
import statistics
from datetime import date, timedelta

MIN_ROWS = 10                 # no real weekly grocery flyer is smaller
MIN_PRICED_FRACTION = 0.70    # data that exists but cannot be shopped
MIN_MEDIAN_FRACTION = 0.50    # soft collapse, e.g. 200 -> 12
MIN_HISTORY = 2               # median rule stays off until a baseline exists
EXPIRY_GRACE_DAYS = 3         # a late run must not permanently reject a store


class BatchRejected(Exception):
    def __init__(self, batch_id, store_name, reasons):
        self.batch_id = batch_id
        self.store_name = store_name
        self.reasons = reasons
        super().__init__(f"{store_name} batch rejected: " + "; ".join(reasons))


def evaluate(cur, batch_id, today=None):
    """Return (ok, reasons, warnings, facts). Reads only."""
    today = today or date.today().isoformat()
    reasons, warnings = [], []

    batch = cur.execute(
        "SELECT id, store_name, valid_from, valid_to, row_count FROM batches WHERE id = ?",
        (batch_id,),
    ).fetchone()
    if batch is None:
        return False, [f"批次 {batch_id} 不存在"], [], {}

    store = batch["store_name"]
    rows = cur.execute(
        "SELECT price_flyer, store_name FROM deals WHERE batch_id = ?", (batch_id,)
    ).fetchall()

    actual = len(rows)
    priced = sum(1 for r in rows if r["price_flyer"] is not None)
    frac = (priced / actual) if actual else 0.0
    facts = {
        "store": store, "actual_rows": actual, "claimed_rows": batch["row_count"],
        "priced": priced, "priced_fraction": frac, "valid_to": batch["valid_to"],
    }

    if actual == 0:
        reasons.append(f"没有任何数据（声称 {batch['row_count']} 条，实际 0 条）")
    elif actual < MIN_ROWS:
        reasons.append(f"只有 {actual} 条，低于最低 {MIN_ROWS} 条")
    elif frac < MIN_PRICED_FRACTION:
        reasons.append(f"只有 {priced}/{actual} 条有价格（{frac:.0%}），低于 {MIN_PRICED_FRACTION:.0%}")

    # Rows must belong to the store the batch claims. Nothing enforces this at
    # insert time, and a mismatch surfaces one store's deals under another's name.
    stray = {r["store_name"] for r in rows} - {store}
    if stray:
        reasons.append(f"批次里混入了别家店的数据: {', '.join(sorted(stray))}")

    if batch["valid_to"]:
        try:
            vt = date.fromisoformat(batch["valid_to"])
        except ValueError:
            reasons.append(f"有效期格式无法识别：{batch['valid_to']}")
        else:
            cutoff = date.fromisoformat(today) - timedelta(days=EXPIRY_GRACE_DAYS)
            if vt < cutoff:
                reasons.append(f"有效期过期太久（{batch['valid_to']}，今天 {today}）")
            elif vt < date.fromisoformat(today):
                warnings.append(f"数据已过期 {(date.fromisoformat(today) - vt).days} 天，仍然发布")
    else:
        warnings.append("这批数据没有有效期")

    # Filter empties first, then take the last N — otherwise a run of failures
    # leaves no history and silently disables this rule.
    history = [
        n for (n,) in cur.execute(
            """
            SELECT (SELECT COUNT(*) FROM deals d WHERE d.batch_id = b.id) AS n
            FROM batches b
            WHERE b.store_name = ? AND b.id != ?
              AND b.status IN ('published', 'superseded') AND b.published_at IS NOT NULL
            ORDER BY b.published_at DESC
            """,
            (store, batch_id),
        ).fetchall() if n > 0
    ][:6]
    facts["history"] = history
    if actual > 0 and len(history) >= MIN_HISTORY:
        median = statistics.median(history)
        facts["median"] = median
        if actual < median * MIN_MEDIAN_FRACTION:
            reasons.append(f"条数骤降：{actual} 条，该店近期中位数 {median:.0f} 条")

    return (not reasons), reasons, warnings, facts


def assert_publishable(cur, batch_id, today=None):
    ok, reasons, warnings, facts = evaluate(cur, batch_id, today=today)
    if not ok:
        raise BatchRejected(batch_id, facts.get("store", "?"), reasons)
    return warnings, facts
