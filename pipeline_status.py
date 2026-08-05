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
MAX_DAYS_WITHOUT_FETCH = 8  # a multi-week flyer must not silence a store for a month


def _useful_line(message, limit=180):
    if not message:
        return None
    lines = [ln.strip() for ln in message.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    tail = lines[-1]
    if tail.startswith("File ") and len(lines) > 1:
        tail = lines[-2]
    return tail[:limit]


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
             AND COALESCE(started_at, finished_at) IS NOT NULL
             AND step_name NOT IN ('skip', 'retire_expired')""",
        (store_display,),
    ).fetchall() if r[0]} | {r[0] for r in cur.execute(
        """SELECT DISTINCT substr(created_at, 1, 10) d FROM batches
           WHERE store_name = ? AND created_at IS NOT NULL""",
        (store_display,),
    ).fetchall() if r[0]}, reverse=True)

    # Success means the batch actually reached 'published'. Falling back to
    # created_at scored a REJECTED batch as a success — the gate throwing out bad
    # data raised the store's reliability, which is the opposite of the point.
    published_days = {r[0] for r in cur.execute(
        """SELECT DISTINCT substr(published_at, 1, 10) FROM batches
           WHERE store_name = ? AND published_at IS NOT NULL
             AND status IN ('published', 'superseded', 'expired')
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
    last_fetch = attempts[0][0] if attempts else None
    days_since_fetch = (
        (date.fromisoformat(today) - date.fromisoformat(last_fetch)).days if last_fetch else None
    )
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

    # Both ends matter. Checking only valid_to let a flyer that starts next week
    # count as today's data, so the site served prices not yet in effect while
    # the real current week was never fetched.
    data_valid = bool(
        live and live["n"] and live["valid_to"] and live["valid_from"]
        and live["valid_from"] <= today <= live["valid_to"]
    )
    days_stale = None
    not_yet = False
    if live and live["valid_to"] and live["valid_to"] < today:
        days_stale = (date.fromisoformat(today) - date.fromisoformat(live["valid_to"])).days
    if live and live["valid_from"] and live["valid_from"] > today:
        not_yet = True
    missing_dates = bool(live and live["n"] and not (live["valid_from"] and live["valid_to"]))

    return {
        "key": key,
        "display": display,
        "has_data": bool(live and live["n"]),
        "data_valid": data_valid,
        "days_stale": days_stale,
        "not_yet_valid": not_yet,
        "missing_dates": missing_dates,
        "valid_from": live["valid_from"] if live else None,
        "valid_to": live["valid_to"] if live else None,
        "rows": live["n"] if live else 0,
        "last_success": last_ok,
        "days_since_success": (
            (date.fromisoformat(today) - date.fromisoformat(last_ok)).days if last_ok else None
        ),
        "days_since_fetch": days_since_fetch,
        "consecutive_ok": consecutive,
        "recent_ok": sum(1 for _, ok in recent if ok),
        "recent_total": len(recent),
        "lifetime_ok": sum(1 for _, ok in attempts if ok),
        "lifetime_total": len(attempts),
        "last_run_step": last_run["step_name"] if last_run else None,
        "last_run_status": last_run["status"] if last_run else None,
        # The last line of a traceback names the error; the first 60 characters
        # are "Traceback (most recent call last):" for every Python failure alike.
        "last_run_message": _useful_line(last_run["message"]) if last_run else None,
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
    if not per_store:
        problems.append("没有启用任何店铺")
    for s in per_store:
        if not s["has_data"]:
            when = f"最后成功 {s['last_success']}" if s["last_success"] else "从未成功过"
            problems.append(f"{s['display']}：没有数据（{when}）")
        elif s["missing_dates"]:
            problems.append(f"{s['display']}：数据没有有效期，无法判断是否当前")
        elif s["not_yet_valid"]:
            problems.append(f"{s['display']}：上线的是 {s['valid_from']} 才生效的传单，不是本周")
        elif not s["data_valid"]:
            problems.append(f"{s['display']}：数据已过期 {s['days_stale']} 天")

        # Health was judged purely on whether data existed and was in date. Four
        # stores could fail to fetch every morning for a week and the banner
        # stayed green the whole time, because last week's flyer was still valid
        # -- then blamed "expired data" on day eight rather than "fetching broke".
        if s["last_run_status"] in ("failed", "rejected"):
            why = f"：{s['last_run_message']}" if s["last_run_message"] else ""
            problems.append(f"{s['display']}：上次抓取{'被拒绝' if s['last_run_status']=='rejected' else '失败'}{why}")
        elif s["days_since_fetch"] is not None and s["days_since_fetch"] > MAX_DAYS_WITHOUT_FETCH:
            problems.append(
                f"{s['display']}：已 {s['days_since_fetch']} 天没有真正抓取（数据仍在有效期内）"
            )
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
