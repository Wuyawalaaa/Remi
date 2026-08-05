import os
import sys
import shlex
import subprocess
from datetime import datetime

import stores
import publish_gate
from database_utils import ensure_pipeline_schema, get_connection, record_pipeline_run

BASE_DIR = os.path.dirname(__file__)
PYTHON = sys.executable   # use the same interpreter as the parent, so launchd's stripped PATH doesn't matter



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
