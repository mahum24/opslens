"""Check every SQL query the AI writes before it touches the warehouse.

Defence in depth, because a language model can be wrong or be talked into
things:
  1. DuckDB's own parser must accept it as exactly one SELECT statement
     (anything that is not a SELECT cannot even be parsed into the tree).
  2. Every table it reads must be allowed for the user's role. Names that
     are not schema-qualified must be the query's own CTEs.
  3. No table functions (read_csv, glob, ...), so it cannot read files.
  4. It runs on a read-only connection with external access switched off.
  5. Only the first N rows are ever fetched.
"""
import fnmatch
import json

import duckdb
import pandas as pd
import yaml

from opslens.settings import CONFIG_DIR

MAX_ROWS = 200


class GuardError(Exception):
    """The query was refused. The message is safe to show to the user and to the model."""


def load_policy():
    return yaml.safe_load(open(CONFIG_DIR / "access_policy.yaml"))


def allowed_patterns(role: str) -> list[str]:
    roles = load_policy()["roles"]
    if role not in roles:
        raise GuardError(f"Unknown role '{role}'")
    return roles[role]["tables"]


def is_allowed(table: str, role: str) -> bool:
    return any(fnmatch.fnmatch(table, p) for p in allowed_patterns(role))


def _walk(node, tables: set, ctes: set, functions: set):
    if isinstance(node, dict):
        if node.get("type") == "BASE_TABLE":
            tables.add((node.get("schema_name") or "", node.get("table_name")))
        elif node.get("type") == "TABLE_FUNCTION":
            functions.add(node.get("function", {}).get("function_name", "?"))
        if "cte_map" in node:
            for entry in node["cte_map"].get("map", []):
                ctes.add(entry.get("key"))
        for v in node.values():
            _walk(v, tables, ctes, functions)
    elif isinstance(node, list):
        for v in node:
            _walk(v, tables, ctes, functions)


def check(sql: str, role: str, con: duckdb.DuckDBPyConnection) -> list[str]:
    """Return the tables the query reads, or raise GuardError explaining why it is refused."""
    sql = sql.strip().rstrip(";")
    if not sql:
        raise GuardError("Empty query")
    tree = json.loads(con.execute("SELECT json_serialize_sql(?::VARCHAR)", [sql]).fetchone()[0])
    if tree.get("error"):
        raise GuardError(f"Only a single read-only SELECT query is allowed ({tree.get('error_message')})")
    if len(tree["statements"]) != 1:
        raise GuardError("Only one statement is allowed")
    tables, ctes, functions = set(), set(), set()
    _walk(tree["statements"][0], tables, ctes, functions)
    if functions:
        raise GuardError(f"Table functions are not allowed: {', '.join(sorted(functions))}")
    used = []
    for schema, name in tables:
        if not schema:
            if name in ctes:
                continue
            if "/" in name or "." in name:
                raise GuardError("Reading files is not allowed")
            raise GuardError(f"Table '{name}' must be written with its schema, e.g. mart.{name}")
        full = f"{schema}.{name}"
        if not is_allowed(full, role):
            raise GuardError(f"The {role} role cannot read {full}")
        used.append(full)
    if not used:
        raise GuardError("The query must read at least one warehouse table")
    return sorted(set(used))


def run(sql: str, role: str, con: duckdb.DuckDBPyConnection, max_rows: int = MAX_ROWS):
    """Check, then run. Returns (dataframe, tables read, truncated?)."""
    tables = check(sql, role, con)
    cur = con.execute(sql.strip().rstrip(";"))
    cols = [d[0] for d in cur.description]
    rows = cur.fetchmany(max_rows + 1)
    return pd.DataFrame(rows[:max_rows], columns=cols), tables, len(rows) > max_rows


def connect_read_only(path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=True, config={"enable_external_access": False})
