#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_PATH="$REPO_ROOT/launchd/com.remi.pipeline.plist.template"
RUN_SCRIPT="$REPO_ROOT/scripts/run_pipeline.sh"
LOG_DIR="$REPO_ROOT/logs"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LABEL="com.remi.pipeline"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"

HOUR=6
MINUTE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hour)
      HOUR="$2"
      shift 2
      ;;
    --minute)
      MINUTE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--hour 0-23] [--minute 0-59]" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

python3 - "$TEMPLATE_PATH" "$PLIST_PATH" "$LABEL" "$REPO_ROOT" "$RUN_SCRIPT" "$LOG_DIR/pipeline.out.log" "$LOG_DIR/pipeline.err.log" "$HOUR" "$MINUTE" <<'PY'
import plistlib
import sys
from pathlib import Path

template_path, plist_path, label, workdir, run_script, stdout_path, stderr_path, hour, minute = sys.argv[1:]
text = Path(template_path).read_text(encoding="utf-8")
replacements = {
    "__LABEL__": label,
    "__WORKDIR__": workdir,
    "__RUN_SCRIPT__": run_script,
    "__STDOUT__": stdout_path,
    "__STDERR__": stderr_path,
    "__HOUR__": str(int(hour)),
    "__MINUTE__": str(int(minute)),
}
for src, dest in replacements.items():
    text = text.replace(src, dest)

data = plistlib.loads(text.encode("utf-8"))
with open(plist_path, "wb") as f:
    plistlib.dump(data, f, sort_keys=False)
PY

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/$LABEL"

echo "Installed $LABEL"
echo "Plist:  $PLIST_PATH"
echo "Logs:   $LOG_DIR/pipeline.out.log"
echo "Errors: $LOG_DIR/pipeline.err.log"
echo "Schedule: daily at hour=$HOUR minute=$MINUTE"
