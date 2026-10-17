"""Layer 2: build the unified reporting dataset from the SQL models."""
import duckdb
import pandas as pd
import yaml

from opslens.settings import AS_OF_DATE, CONFIG_DIR
from pathlib import Path

MODELS_DIR = Path(__file__).parent / "models"


def load_reference_data(con: duckdb.DuckDBPyConnection):
    rules = yaml.safe_load(open(CONFIG_DIR / "business_rules.yaml"))
    con.execute("CREATE SCHEMA IF NOT EXISTS ref")
    for name in ("sla_targets", "products"):
        con.register("_ref", pd.DataFrame(rules[name]))
        con.execute(f"CREATE OR REPLACE TABLE ref.{name} AS SELECT * FROM _ref")
        con.unregister("_ref")
    settings = pd.DataFrame([{"key": k, "value": float(v)} for k, v in rules.items() if not isinstance(v, list)])
    con.register("_ref", settings)
    con.execute("CREATE OR REPLACE TABLE ref.settings AS SELECT * FROM _ref")
    con.unregister("_ref")


def build(con: duckdb.DuckDBPyConnection) -> list[str]:
    load_reference_data(con)
    built = []
    for path in sorted(MODELS_DIR.glob("*.sql")):
        sql = path.read_text().replace("$AS_OF", f"DATE '{AS_OF_DATE.isoformat()}'")
        con.execute(sql)
        built.append(path.name)
    return built
