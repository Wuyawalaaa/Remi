"""Answers the questions you need in order to tell one failure apart from another.

The four-month outage was not caused by a lack of failures being recorded. It was
caused by six different failures all looking identical from outside: "this store
has no deals" reads the same whether nothing was configured, the download broke,
the gate rejected everything, the data expired, or the scheduler never fired.

So this module is not a list of alarms. It answers the smallest set of questions
that tells those cases apart:

  when did the whole job last run          — separates "scheduler never fired"
  when did this store last succeed         — separates "never worked" from "just broke"
  is the current data still valid          — separates stale from absent
  what happened on the last attempt        — separates broken source from rejected data
  how reliable lately, and overall         — separates a blip from a chronic problem
  is it configured but empty               — separates "not set up" from "set up and failing"

Reliability is four numbers because no smaller set works. Consecutive-failure
count was dropped: exhaustively comparing every possible 8-run history showed it
adds zero distinguishing power over the last-success date — they are the same
fact stated twice. Consecutive *successes* does add power, and resets to zero the
moment the newest attempt fails, so it answers "can I trust this right now".
Recent and lifetime rates are both kept because comparing them is itself the
signal: low lifetime with high recent means fixed, the reverse means newly broken.
"""
import sqlite3
from datetime import date, datetime

import stores

RECENT_WINDOW = 10          # attempts, not days — cadence-independent
STALE_JOB_DAYS = 2          # no run in this long is itself the problem


def _attempts(cur, store_display):
    """One entry per day this store was attempted, newest first: (date, ok).

    An attempt is a day the pipeline touched this store at all. Success means a
    batch for it reached 'published' that day. Counting batches alone would miss
    the case where the download failed and no batch was ever created — which is
    exactly how Safeway and Cardenas failed every week for months.
    """
    # An attempt is a day this store was worked on, from either source. Run
    # records only start 2026-06-06, when the orchestrator was installed, so
    # counting them alone would score the successful March weeks as if they never
    # happened and report Safeway at 1/10 for a period it largely worked.
    days = sorted({r[0] for r in cur.execute(
        """SELECT DISTINCT substr(COALESCE(started_at, finished_at), 1, 10) d
           FROM pipeline_runs WHERE store_name = ?
             AND COALESCE(started_at, finished_at) IS NOT NULL""",
        (store_display,),
    ).fetchall() if r[0]} | {r[0] for r in cur.execute(
        """SELECT DISTINCT substr(created_at, 1, 10) d FROM batches
           WHERE store_name = ? AND created_at IS NOT NULL""",
        (store_display,),
    ).fetchall() if r[0]}, reverse=True)

    published_days = {r[0] for r in cur.execute(
        """SELECT DISTINCT substr(COALESCE(published_at, created_at), 1, 10) FROM batches
           WHERE store_name = ?
             AND (SELECT COUNT(*) FROM deals d WHERE d.batch_id = batches.id) > 0""",
        (store_display,),
    ).fetchall() if r[0]}

    return [(d, d in published_days) for d in days]


def store_status(cur, key, today=None):
    today = today or date.today().isoformat()
    display = stores.display_name(key)

    live = cur.execute(
        """SELECT b.valid_from, b.valid_to,
                  (SELECT COUNT(*) FROM deals d WHERE d.batch_id = b.id) n
           FROM batches b WHERE b.store_name = ? AND b.status = 'published'""",
        (display,),
    ).fetchone()

    last_ok = cur.execute(
        """SELECT MAX(substr(published_at, 1, 10)) FROM batches
           WHERE store_name = ? AND published_at IS NOT NULL
             AND (SELECT COUNT(*) FROM deals d WHERE d.batch_id = batches.id) > 0""",
        (display,),
    ).fetchone()[0]

    attempts = _attempts(cur, display)
    recent = attempts[:RECENT_WINDOW]
    consecutive = 0
    for _, ok in attempts:
        if not ok:
            break
        consecutive += 1

    last_run = cur.execute(
        """SELECT step_name, status, message, substr(COALESCE(started_at, finished_at), 1, 16) t
           FROM pipeline_runs WHERE store_name = ?
           ORDER BY id DESC LIMIT 1""",
        (display,),
    ).fetchone()

    data_valid = bool(live and live["valid_to"] and live["valid_to"] >= today and live["n"])
    days_stale = None
    if live and live["valid_to"] and live["valid_to"] < today:
        days_stale = (date.fromisoformat(today) - date.fromisoformat(live["valid_to"])).days

    return {
        "key": key,
        "display": display,
        "has_data": bool(live and live["n"]),
        "data_valid": data_valid,
        "days_stale": days_stale,
        "valid_from": live["valid_from"] if live else None,
        "valid_to": live["valid_to"] if live else None,
        "rows": live["n"] if live else 0,
        "last_success": last_ok,
        "days_since_success": (
            (date.fromisoformat(today) - date.fromisoformat(last_ok)).days if last_ok else None
        ),
        "consecutive_ok": consecutive,
        "recent_ok": sum(1 for _, ok in recent if ok),
        "recent_total": len(recent),
        "lifetime_ok": sum(1 for _, ok in attempts if ok),
        "lifetime_total": len(attempts),
        "last_run_step": last_run["step_name"] if last_run else None,
        "last_run_status": last_run["status"] if last_run else None,
        "last_run_message": (last_run["message"] or "")[:200] if last_run else None,
        "last_run_at": last_run["t"] if last_run else None,
        # Newest attempt last. The full record is in pipeline_runs; this is the
        # shape at a glance -- FFFFFFFFFS reads as "was broken, just recovered".
        "sequence": "".join("S" if ok else "F" for _, ok in reversed(recent)),
    }


def overall(db_path, today=None):
    today = today or date.today().isoformat()
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    per_store = [store_status(cur, k, today) for k in stores.enabled_stores()]

    last_run_at = cur.execute(
        "SELECT MAX(COALESCE(started_at, finished_at)) FROM pipeline_runs"
    ).fetchone()[0]
    con.close()

    days_since_run = None
    if last_run_at:
        # Compare dates, not a midnight-vs-timestamp mix, which reported -1 for a
        # run that had happened earlier the same day.
        days_since_run = (date.fromisoformat(today) - date.fromisoformat(last_run_at[:10])).days

    problems = []
    for s in per_store:
        if not s["has_data"]:
            when = f"最后成功 {s['last_success']}" if s["last_success"] else "从未成功过"
            problems.append(f"{s['display']}：没有数据（{when}）")
        elif not s["data_valid"]:
            problems.append(f"{s['display']}：数据已过期 {s['days_stale']} 天")
    if days_since_run is not None and days_since_run > STALE_JOB_DAYS:
        problems.append(f"整个任务已 {days_since_run} 天没有运行")
    elif last_run_at is None:
        problems.append("从来没有运行记录")

    return {
        "today": today,
        "last_run_at": last_run_at,
        "days_since_run": days_since_run,
        "configured": len(per_store),
        "with_data": sum(1 for s in per_store if s["has_data"]),
        "stores": per_store,
        "problems": problems,
        "healthy": not problems,
    }


if __name__ == "__main__":
    import os
    st = overall(os.path.join(os.path.dirname(__file__), "database.db"))
    print(f"任务上次运行: {st['last_run_at']}  ({st['days_since_run']} 天前)")
    print(f"配置 {st['configured']} 家，其中 {st['with_data']} 家有数据")
    print()
    hdr = f"{'店':<10} {'当前数据':<26} {'最后成功':<12} {'连续':<5} {'近10次':<8} {'累计':<8} {'近况'}"
    print(hdr)
    print("-" * len(hdr))
    for s in st["stores"]:
        data = (f"{s['valid_from']}→{s['valid_to']} ({s['rows']}条)"
                if s["has_data"] else "无数据")
        if s["has_data"] and not s["data_valid"]:
            data += f" 过期{s['days_stale']}天"
        print(f"{s['display']:<10} {data:<26} {str(s['last_success']):<12} "
              f"{s['consecutive_ok']:<5} {s['recent_ok']}/{s['recent_total']:<6} "
              f"{s['lifetime_ok']}/{s['lifetime_total']:<6} {s['sequence']}")
    print()
    if st["problems"]:
        print("需要处理:")
        for p in st["problems"]:
            print("  ! " + p)
    else:
        print("一切正常")
