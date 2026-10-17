"""Layer 1a: load every source system, as it is, into the raw schema.

Nothing is cleaned here. Raw tables keep the source's own column names and
formats, plus two lineage columns, so any number on a dashboard can be traced
back to the file it came from.
"""
import json
from datetime import datetime, timezone

import duckdb
import pandas as pd
import yaml

from opslens.settings import CONFIG_DIR, SOURCES_DIR


def load_config():
    return yaml.safe_load(open(CONFIG_DIR / "sources.yaml"))["sources"]


def read_table(spec) -> pd.DataFrame:
    path = SOURCES_DIR / spec["path"]
    if spec["format"] == "csv":
        # Everything as text: typing is a staging decision, not a loading one.
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    if spec["format"] == "jsonl":
        return pd.read_json(path, lines=True, dtype=False)
    if spec["format"] == "json":
        records = json.load(open(path))[spec.get("record_path")] if spec.get("record_path") else json.load(open(path))
        if spec.get("flatten"):
            key = spec["flatten"]
            records = [{"id": r["id"], **r[key]} for r in records]
        return pd.DataFrame(records)
    raise ValueError(f"Unknown format {spec['format']}")


def ingest(con: duckdb.DuckDBPyConnection) -> list[dict]:
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    loaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    report = []
    for source in load_config():
        for spec in source["tables"]:
            df = read_table(spec)
            df["_source_system"] = source["system"]
            df["_source_file"] = spec["path"]
            df["_loaded_at"] = loaded_at
            con.register("_incoming", df)
            con.execute(f"CREATE OR REPLACE TABLE raw.{spec['name']} AS SELECT * FROM _incoming")
            con.unregister("_incoming")
            report.append({"system": source["system"], "table": f"raw.{spec['name']}", "rows": len(df),
                           "pii_columns": ", ".join(spec.get("pii", []))})
    con.register("_report", pd.DataFrame(report))
    con.execute("CREATE SCHEMA IF NOT EXISTS ops")
    con.execute("CREATE OR REPLACE TABLE ops.ingest_log AS SELECT *, now() AS run_at FROM _report")
    con.unregister("_report")
    return report
