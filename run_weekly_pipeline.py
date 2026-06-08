import os
import sys
import shlex
import subprocess
from datetime import datetime

from database_utils import ensure_pipeline_schema, record_pipeline_run

BASE_DIR = os.path.dirname(__file__)
PYTHON = sys.executable   # use the same interpreter as the parent, so launchd's stripped PATH doesn't matter

STORE_PIPELINES = {
    "safeway": [
        f"{PYTHON} download_safeway_flyer.py",
        f"{PYTHON} ocr_safeway_flyer.py",
        f"{PYTHON} parse_safeway_deals.py",
        f"{PYTHON} import_to_database.py safeway",
    ],
    "hmart": [
        f"{PYTHON} download_hmart_flyer.py",
        f"{PYTHON} ocr_hmart_flyer.py",
        f"{PYTHON} parse_hmart_deals.py",
        f"{PYTHON} import_to_database.py hmart",
    ],
    "99ranch": [
        f"{PYTHON} download_99ranch_flyer.py",
        # OCR step removed: 99 Ranch data is structured JSON from the API; parse_99ranch_deals.py
        # reads the raw.json directly and does not consume OCR output.
        f"{PYTHON} parse_99ranch_deals.py",
        f"{PYTHON} import_to_database.py 99ranch",
    ],
    "cardenas": [
        f"{PYTHON} download_cardenas_flyer.py",
        f"{PYTHON} parse_cardenas_deals.py",
        f"{PYTHON} import_to_database.py cardenas",
    ],
}


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
    targets = store_names or list(STORE_PIPELINES.keys())
    overall_ok = True

    for store_name in targets:
        commands = STORE_PIPELINES.get(store_name)
        if not commands:
            print(f"Unknown store '{store_name}'. Known: {', '.join(STORE_PIPELINES)}")
            overall_ok = False
            continue

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
