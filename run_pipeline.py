import os
import sys
import shlex
import subprocess
from datetime import date, datetime

import stores
import publish_gate
from database_utils import ensure_pipeline_schema, get_connection, record_pipeline_run

BASE_DIR = os.path.dirname(__file__)
PYTHON = sys.executable   # use the same interpreter as the parent, so launchd's stripped PATH doesn't matter



def already_current(store_key, today=None, max_days_without_fetch=8):
    """True if this store can safely be skipped today.

    Running daily means asking this every day, so it must be cheap and it must be
    right. It reads the database, not the filesystem: a file on disk only proves
    something was written, while a published batch proves it passed the gate.
    That difference is what stopped a bad week becoming permanent — the old
    skip-if-file-exists check meant one failed fetch wrote an empty artifact that
    every later run, manual ones included, then skipped over.

    Three things must all hold, and the last two were missing at first:

    - published data covers today
    - no newer attempt was rejected. Otherwise a mid-week rejection is locked out
      until the old flyer expires, which is a week — and rejections happen
      precisely when the source changed shape, so that is the worst time to stop
      looking.
    - a real fetch happened recently. A store publishing a four-week flyer would
      otherwise go a month untouched, and Lucky and FoodMaxx do publish those.

    One real fetch per store per day is also the ceiling, checked first. RunAtLoad
    fires on every login and wake, and an already-expired flyer publishes with a
    warning yet never satisfies the coverage test — together that is an unbounded
    loop re-downloading ~200 pages and writing a fresh batch every single login.
    """
    today = today or date.today().isoformat()
    display = stores.display_name(store_key)
    con = get_connection()
    try:
        live = con.execute(
            """SELECT id, published_at FROM batches b
               WHERE b.store_name = ? AND b.status = 'published'
                 AND b.valid_from <= ? AND b.valid_to >= ?
                 AND (SELECT COUNT(*) FROM deals d WHERE d.batch_id = b.id) > 0
               LIMIT 1""",
            (display, today, today),
        ).fetchone()
        if not live:
            return False

        newer_failure = con.execute(
            """SELECT 1 FROM batches
               WHERE store_name = ? AND status = 'failed'
                 AND created_at > COALESCE(?, '') LIMIT 1""",
            (display, live["published_at"]),
        ).fetchone()
        if newer_failure:
            return False

        tried_today = con.execute(
            """SELECT 1 FROM pipeline_runs WHERE store_name = ?
                 AND step_name NOT IN ('skip', 'retire_expired')
                 AND substr(COALESCE(started_at, finished_at), 1, 10) = ? LIMIT 1""",
            (display, today),
        ).fetchone()
        if tried_today:
            return True

        last_fetch = con.execute(
            """SELECT MAX(substr(COALESCE(started_at, finished_at), 1, 10))
               FROM pipeline_runs WHERE store_name = ?
                 AND step_name NOT IN ('skip', 'retire_expired')""",
            (display,),
        ).fetchone()[0]
        if last_fetch:
            gap = (date.fromisoformat(today) - date.fromisoformat(last_fetch)).days
            if gap > max_days_without_fetch:
                return False
        return True
    finally:
        con.close()


def _run_step(store_name, command):
    # Record the display name, matching what batches holds. These two tables used
    # to disagree -- slugs here, display names there -- and the admin dashboard
    # joined one to the other, silently matched nothing, and showed no run history
    # for the stores that were failing every week.
    store_name = stores.display_name(store_name)
    step_name = command.split()[1] if len(command.split()) > 1 else command
    started_at = datetime.now().isoformat()
    try:
        result = subprocess.run(
            shlex.split(command),
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            check=True,
        )
        record_pipeline_run(
            store_name=store_name,
            step_name=step_name,
            status="completed",
            started_at=started_at,
            finished_at=datetime.now().isoformat(),
            message=(result.stdout or result.stderr or "").strip()[:2000],
        )
        return True
    except subprocess.CalledProcessError as exc:
        output = "\n".join(part for part in [exc.stdout, exc.stderr] if part).strip()
        record_pipeline_run(
            store_name=store_name,
            step_name=step_name,
            status="failed",
            started_at=started_at,
            finished_at=datetime.now().isoformat(),
            message=output[:2000] or str(exc),
        )
        return False


def run(store_names=None):
    ensure_pipeline_schema()
    targets = store_names or list(stores.enabled_stores())
    overall_ok = True

    for store_name in targets:
        if store_name not in stores.STORES:
            print(f"Unknown store '{store_name}'. Known: {', '.join(stores.STORES)}")
            overall_ok = False
            continue
        try:
            skip = already_current(store_name)
        except Exception as exc:  # noqa: BLE001 - fail open: fetching beats doing nothing silently
            print(f"\n=== {store_name} === 跳过判断出错，改为照常抓取: {exc}")
            record_pipeline_run(
                store_name=stores.display_name(store_name), step_name="skip_check",
                status="failed", message=str(exc),
                started_at=datetime.now().isoformat(), finished_at=datetime.now().isoformat(),
            )
            skip = False

        if skip:
            print(f"\n=== {store_name} === 本周数据已在线，跳过")
            record_pipeline_run(
                store_name=stores.display_name(store_name), step_name="skip",
                status="skipped", message="already has published data covering today",
                started_at=datetime.now().isoformat(), finished_at=datetime.now().isoformat(),
            )
            continue

        commands = [f"{PYTHON} {step}" for step in stores.steps_for(store_name)]

        print(f"\n=== {store_name} ===")
        for command in commands:
            print(f"$ {command}")
            if not _run_step(store_name, command):
                overall_ok = False
                print(f"Failed: {command}")
                break

    # Nothing used to look at what was already live, so a store whose updates
    # failed kept serving the same prices for months. Retire anything long past
    # its validity, whether or not this run produced a replacement.
    con = get_connection()
    retired = publish_gate.retire_expired(con)
    con.close()
    for store_name, valid_to, days in retired:
        print(f"下架过期数据: {store_name}（有效期至 {valid_to}，已过期 {days} 天）")
        record_pipeline_run(
            store_name=store_name, step_name="retire_expired", status="retired",
            message=f"valid_to={valid_to} expired {days}d",
        )

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:] or None))
