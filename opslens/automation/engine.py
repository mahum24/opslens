"""Layer 4: run the workflow rules and turn what they find into tasks.

Safe to run as often as you like: a rule only creates a task when there is
no open task for the same thing, and a task closes itself when its rule
stops finding the problem. Notifications go to an outbox table; set
TEAMS_WEBHOOK_URL to also post Teams-channel ones for real.
"""
import os
import uuid
from datetime import datetime

import duckdb
import pandas as pd
import requests
import yaml

from opslens.settings import AS_OF_DATE, CONFIG_DIR

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.tasks (
    task_id VARCHAR, rule_id VARCHAR, dedupe_key VARCHAR, title VARCHAR, detail VARCHAR,
    owner VARCHAR, priority VARCHAR, status VARCHAR, created_at TIMESTAMP, closed_at TIMESTAMP, close_reason VARCHAR
);
CREATE TABLE IF NOT EXISTS ops.notifications (
    notification_id VARCHAR, task_id VARCHAR, channel VARCHAR, recipient VARCHAR, subject VARCHAR,
    body VARCHAR, created_at TIMESTAMP, delivered BOOLEAN
);
CREATE TABLE IF NOT EXISTS ops.automation_runs (
    run_id VARCHAR, rule_id VARCHAR, run_at TIMESTAMP, matched INTEGER, created INTEGER, auto_closed INTEGER, error VARCHAR
);
"""


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(template: str, row: dict) -> str:
    row = {k: (float(v) if hasattr(v, "is_finite") else v) for k, v in row.items()}   # Decimal -> float
    return template.format_map(SafeDict(row))


def deliver(channel: str, subject: str, body: str) -> bool:
    url = os.getenv("TEAMS_WEBHOOK_URL")
    if channel != "teams" or not url:
        return False
    try:
        return requests.post(url, json={"text": f"**{subject}**\n\n{body}"}, timeout=5).ok
    except requests.RequestException:
        return False


def run_rules(con: duckdb.DuckDBPyConnection, now: datetime | None = None) -> pd.DataFrame:
    con.execute(DDL)
    now = now or datetime.combine(AS_OF_DATE, datetime.now().time())
    run_id = uuid.uuid4().hex[:8]
    summary = []
    for rule in yaml.safe_load(open(CONFIG_DIR / "workflows.yaml"))["rules"]:
        created = closed = matched = 0
        error = None
        try:
            rows = con.execute(rule["sql"]).df().to_dict("records")
            matched = len(rows)
            open_keys = {k for (k,) in con.execute(
                "SELECT dedupe_key FROM ops.tasks WHERE rule_id = ? AND status = 'open'", [rule["id"]]).fetchall()}
            found = set()
            for row in rows:
                key = render(rule["dedupe_key"], row)
                found.add(key)
                if key in open_keys:
                    continue
                task_id = f"T-{uuid.uuid4().hex[:8]}"
                title, detail = render(rule["title"], row), render(rule["detail"], row)
                owner = render(rule["owner"], row)
                priority = row.get(rule["priority_from"]) if rule.get("priority_from") else rule.get("priority", "P3")
                con.execute("INSERT INTO ops.tasks VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, NULL, NULL)",
                            [task_id, rule["id"], key, title, detail, owner, priority, now])
                con.execute("INSERT INTO ops.notifications VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            [uuid.uuid4().hex[:10], task_id, rule["channel"], owner, title, detail, now,
                             deliver(rule["channel"], title, detail)])
                created += 1
            # The problem has gone away (ticket resolved, invoice paid): close the task.
            for key in open_keys - found:
                con.execute("""UPDATE ops.tasks SET status = 'closed', closed_at = ?, close_reason = 'condition cleared'
                               WHERE rule_id = ? AND dedupe_key = ? AND status = 'open'""", [now, rule["id"], key])
                closed += 1
        except Exception as e:      # one broken rule must not stop the others
            error = str(e)[:300]
        con.execute("INSERT INTO ops.automation_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [run_id, rule["id"], now, matched, created, closed, error])
        summary.append({"rule": rule["id"], "matched": matched, "created": created, "auto_closed": closed, "error": error})
    return pd.DataFrame(summary)
