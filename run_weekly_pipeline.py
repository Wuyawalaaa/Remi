import os
import sys
import shlex
import subprocess
from datetime import datetime

import stores
from database_utils import ensure_pipeline_schema, record_pipeline_run

BASE_DIR = os.path.dirname(__file__)
PYTHON = sys.executable   # use the same interpreter as the parent, so launchd's stripped PATH doesn't matter



def _run_step(store_name, command):
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

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:] or None))
