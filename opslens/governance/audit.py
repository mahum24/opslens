"""Append-only audit log of every question asked of the AI assistant.

Kept as JSON lines outside the warehouse, so the assistant can log while
the warehouse is open read-only, and so the log survives a rebuild.
"""
import json
from datetime import datetime, timezone

from opslens.settings import DATA_DIR

AUDIT_PATH = DATA_DIR / "audit" / "assistant_audit.jsonl"


def log(entry: dict, path=AUDIT_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps({"logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry},
                           default=str) + "\n")


def read(path=AUDIT_PATH, limit=200) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()[-limit:]][::-1]
