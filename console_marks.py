"""Manual fault-code edits, layered over what the detector produced.

The detector's output has no identity: rerun it and the whole set is replaced.
That is fine for the codes it derives, and useless for a judgement someone made
by reading the flyer. So judgements are stored, and the codes shown are

    detector output  −  what was removed  +  what was added by hand

Removing carries a reason, because "the code is gone" means two different things
and collapsing them makes the numbers unreadable:

  误报 (false_positive) — the rule fired on something that was actually fine.
                          Counts against the rule.
  已解决 (resolved)      — the fault was real and has been dealt with.
                          Counts for the rule.

Adding by hand is the only signal for what the detector never saw at all, which
is the one number a detector can never produce about itself.

Marks key on deals.id, so they survive re-running detection over the same batch
and retire with the batch when next week's flyer replaces it. They deliberately
do not follow a product across weeks: a mark says "this row is wrong", and next
week's row is a different row. A pattern worth carrying forward should become a
rule here, not a sticky note on one item.
"""
import sqlite3
from datetime import datetime

REMOVE_VERDICTS = {"false_positive": "误报", "resolved": "已解决"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS deal_fault_marks (
    deal_id    INTEGER NOT NULL,
    code       TEXT    NOT NULL,
    action     TEXT    NOT NULL CHECK (action IN ('add', 'remove')),
    verdict    TEXT,
    note       TEXT,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (deal_id, code)
);
CREATE INDEX IF NOT EXISTS idx_marks_deal ON deal_fault_marks(deal_id);
"""


def ensure_schema(db_path):
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()


def load(db_path):
    """All marks, as {deal_id: {code: {action, verdict, note}}}."""
    ensure_schema(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    out = {}
    for r in con.execute("SELECT * FROM deal_fault_marks"):
        out.setdefault(r["deal_id"], {})[r["code"]] = {
            "action": r["action"], "verdict": r["verdict"], "note": r["note"],
        }
    con.close()
    return out


def put(db_path, deal_id, code, action, verdict=None, note=None):
    """Record one edit. action='add'|'remove'; verdict required for remove."""
    if action not in ("add", "remove"):
        raise ValueError(f"unknown action {action!r}")
    if action == "remove" and verdict not in REMOVE_VERDICTS:
        raise ValueError("removing a code needs a reason: false_positive or resolved")

    ensure_schema(db_path)
    con = sqlite3.connect(db_path)
    con.execute(
        """INSERT INTO deal_fault_marks (deal_id, code, action, verdict, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(deal_id, code) DO UPDATE SET
               action = excluded.action, verdict = excluded.verdict,
               note = excluded.note, created_at = excluded.created_at""",
        (deal_id, code, action, verdict, note, datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()
    con.close()


def clear(db_path, deal_id, code):
    """Undo an edit, putting the row back to whatever the detector says."""
    ensure_schema(db_path)
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM deal_fault_marks WHERE deal_id = ? AND code = ?", (deal_id, code))
    con.commit()
    con.close()


def apply(faults, marks_for_deal, catalog):
    """Layer one row's marks over its detected faults.

    Returns the list the UI should show. Each entry gains `origin`:
      'auto'   — the detector found it and nobody disagreed
      'manual' — added by hand; the detector missed it
    Removed codes drop out entirely but are returned separately so the row can
    still show what was dismissed and why.
    """
    marks = marks_for_deal or {}
    shown, dismissed = [], []

    for f in faults:
        m = marks.get(f["code"])
        if m and m["action"] == "remove":
            dismissed.append({**f, "verdict": m["verdict"],
                              "verdict_label": REMOVE_VERDICTS.get(m["verdict"], m["verdict"])})
        else:
            shown.append({**f, "origin": "auto"})

    detected = {f["code"] for f in faults}
    for code, m in marks.items():
        if m["action"] == "add" and code not in detected:
            spec = catalog.get(code, {})
            shown.append({
                "code": code,
                "label": spec.get("label", code),
                "rule": "手动添加——检测器没看出来",
                "confidence": "手动",
                "detail": m.get("note") or "",
                "origin": "manual",
            })

    return shown, dismissed
