"""The only place that decides whether a batch may go live.

Design notes, each earned from a failure that was reproduced:

- Every number is counted from the `deals` rows. `batches.row_count` comes from a
  caller-supplied counter that increments on updates too, and one live batch
  claims 264 rows while owning none. Never trust it.
- Rejection is free of side effects only because batches own their own rows.
  Before that change the import had already moved rows off the live batch by the
  time this ran, so "rejecting" still blanked the store.
- Only two things reject; everything else publishes loudly. The owner is away for
  weeks at a time, so holding a suspicious batch for review would leave a store
  empty for as long as she is gone — which is the outage this exists to prevent.
  A questionable week that shouts beats no week at all.
- Thresholds sit well below observed variation. Real week-to-week counts move a
  lot on their own (Cardenas ran 199 then 302), so a tight rule would fire on
  healthy data and teach her to ignore it.
"""
import statistics
from datetime import date, timedelta

# Hard rejections. Both mean "there is nothing here worth serving".
MIN_PRICED_FRACTION_HARD = 0.20   # this unpriced is a parse failure, not a flyer
MIN_UNIT_FRACTION_HARD = 0.01     # 0% units means the source changed shape

# Warnings: published, but surfaced. Observed healthy ranges are priced 87-100%,
# units 86-100%, counts roughly 0.7-1.5x of a store's own median.
WARN_PRICED_FRACTION = 0.70
WARN_UNIT_FRACTION = 0.60
WARN_MEDIAN_FRACTION = 0.50
MIN_HISTORY = 2

# Published data older than this stops being served. A few days stale beats
# nothing; four months does not.
UNPUBLISH_AFTER_DAYS = 14


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
        "SELECT price_flyer, unit_flyer, store_name FROM deals WHERE batch_id = ?", (batch_id,)
    ).fetchall()

    actual = len(rows)
    priced = sum(1 for r in rows if r["price_flyer"] is not None)
    united = sum(1 for r in rows if r["unit_flyer"])
    pf = (priced / actual) if actual else 0.0
    uf = (united / actual) if actual else 0.0

    facts = {
        "store": store, "actual_rows": actual, "claimed_rows": batch["row_count"],
        "priced": priced, "priced_fraction": pf,
        "united": united, "unit_fraction": uf,
        "valid_from": batch["valid_from"], "valid_to": batch["valid_to"],
    }

    # --- hard rejections -----------------------------------------------------
    if actual == 0:
        reasons.append(f"没有任何数据（声称 {batch['row_count']} 条，实际 0 条）")

    stray = {r["store_name"] for r in rows} - {store}
    if stray:
        reasons.append(f"混入了别家店的数据: {', '.join(sorted(stray))}")

    if actual and pf < MIN_PRICED_FRACTION_HARD:
        reasons.append(f"几乎没有价格：{priced}/{actual} 条（{pf:.0%}）")

    if actual and uf < MIN_UNIT_FRACTION_HARD:
        # A whole flyer with no unit at all is not a bad week, it is the source
        # changing shape underneath us. Everything would normalise to "each" and
        # price comparison would quietly stop meaning anything, while every other
        # check still passed.
        reasons.append(f"一个单位都没解析出来（共 {actual} 条）——数据源格式可能变了")

    if reasons:
        return False, reasons, warnings, facts

    # --- warnings: published, but loud ---------------------------------------
    if pf < WARN_PRICED_FRACTION:
        warnings.append(f"只有 {priced}/{actual} 条有价格（{pf:.0%}）")
    if uf < WARN_UNIT_FRACTION:
        warnings.append(f"只有 {united}/{actual} 条有单位（{uf:.0%}）")

    if batch["valid_to"]:
        try:
            vt = date.fromisoformat(batch["valid_to"])
        except ValueError:
            warnings.append(f"有效期格式无法识别：{batch['valid_to']}")
        else:
            days = (date.fromisoformat(today) - vt).days
            if days > 0:
                warnings.append(f"数据已过期 {days} 天")
    else:
        warnings.append("这批数据没有有效期")

    if batch["valid_from"] and batch["valid_from"] > today:
        warnings.append(f"这批数据 {batch['valid_from']} 才生效，尚未开始")

    # Empty batches are filtered before the limit, not after. Filtering after
    # meant a store with a run of failures had no history left, so the collapse
    # rule switched itself off for exactly the store that needed it.
    history = [
        n for (n,) in cur.execute(
            """
            SELECT (SELECT COUNT(*) FROM deals d WHERE d.batch_id = b.id) AS n
            FROM batches b
            WHERE b.store_name = ? AND b.id != ?
              AND b.status IN ('published', 'superseded', 'expired')
              AND b.published_at IS NOT NULL
            ORDER BY b.published_at DESC
            """,
            (store, batch_id),
        ).fetchall() if n > 0
    ][:6]
    facts["history"] = history
    if len(history) >= MIN_HISTORY:
        median = statistics.median(history)
        facts["median"] = median
        if actual < median * WARN_MEDIAN_FRACTION:
            warnings.append(f"条数骤降：{actual} 条，该店近期中位数 {median:.0f} 条")

    return True, [], warnings, facts


def assert_publishable(cur, batch_id, today=None):
    ok, reasons, warnings, facts = evaluate(cur, batch_id, today=today)
    if not ok:
        raise BatchRejected(batch_id, facts.get("store", "?"), reasons)
    return warnings, facts


def retire_expired(con, today=None, max_age_days=UNPUBLISH_AFTER_DAYS):
    """Stop serving batches whose validity ended long ago.

    The gate only ever judged batches on their way in; nothing looked at what was
    already live. So a store whose updates failed kept serving the same prices
    indefinitely — Safeway and Cardenas sat 133 days past their validity and were
    indistinguishable from a working store. "Published" is what the agent will
    consume, so leaving expired prices there is a data problem, not a display one.

    Returns a list of (store_name, valid_to, days_expired) for what it retired.
    """
    today = today or date.today().isoformat()
    cutoff = (date.fromisoformat(today) - timedelta(days=max_age_days)).isoformat()
    cur = con.cursor()
    stale = cur.execute(
        """SELECT id, store_name, valid_to FROM batches
           WHERE status = 'published' AND valid_to IS NOT NULL AND valid_to < ?""",
        (cutoff,),
    ).fetchall()
    retired = []
    for row in stale:
        cur.execute("UPDATE batches SET status = 'expired' WHERE id = ?", (row["id"],))
        days = (date.fromisoformat(today) - date.fromisoformat(row["valid_to"])).days
        retired.append((row["store_name"], row["valid_to"], days))
    if retired:
        con.commit()
    return retired
