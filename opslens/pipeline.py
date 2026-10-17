"""Run the whole platform end to end.

    python -m opslens.pipeline              # build from the existing source files
    python -m opslens.pipeline --regenerate # make fresh synthetic source data first
"""
import argparse
import time

import duckdb

from opslens.analytics.churn_model import train_and_score
from opslens.analytics.powerbi_export import export
from opslens.automation.engine import run_rules
from opslens.ingest.connectors import ingest
from opslens.ingest.entity_resolution import resolve
from opslens.settings import SOURCES_DIR, WAREHOUSE_PATH
from opslens.sources.generate import generate
from opslens.warehouse.build import build
from opslens.warehouse.quality import run_checks


def step(name):
    print(f"\n== {name}")
    return time.perf_counter()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regenerate", action="store_true", help="generate fresh synthetic source data")
    ap.add_argument("--skip-export", action="store_true", help="skip the Power BI export")
    args = ap.parse_args(argv)

    if args.regenerate or not (SOURCES_DIR / "billing/customers.csv").exists():
        step("Generating synthetic source systems")
        generate()

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    try:
        step("Layer 1 - ingest source systems")
        for r in ingest(con):
            print(f"  {r['table']:<28} {r['rows']:>7,} rows")

        step("Layer 1 - link records to customers")
        for system, s in resolve(con).items():
            print(f"  {system:<12} {s['records']:>5} records, {s['auto_link_rate']:.1%} linked automatically")

        step("Layer 2 - build unified reporting dataset")
        print("  models:", ", ".join(build(con)))
        dq = run_checks(con)
        for r in dq.itertuples():
            print(f"  [{r.status:^4}] {r.check_name} ({r.failing_rows})")
        if (dq.status == "fail").any():
            raise SystemExit("Data quality errors - stopping before anyone sees wrong numbers.")

        step("Layer 3 - churn risk model")
        res = train_and_score(con)
        for m in res["metrics"]:
            print(f"  {m['model']:<20} ROC AUC {m['roc_auc']:.3f}  PR AUC {m['pr_auc']:.3f}  "
                  f"top-10% precision {m['precision_top10pct']:.1%} (base rate {m['base_rate']:.1%})")
        print(f"  scored {res['scored']} active customers, {res['high_risk']} high risk")

        step("Layer 4 - workflow automation")
        for r in run_rules(con).itertuples():
            print(f"  {r.rule:<24} matched {r.matched:>3}, new tasks {r.created:>3}, closed {r.auto_closed:>3}"
                  + (f"  ERROR {r.error}" if r.error else ""))

        if not args.skip_export:
            step("Layer 3 - Power BI export")
            for name, n in export(con).items():
                print(f"  {name:<18} {n:>7,} rows")
    finally:
        con.close()
    print(f"\nDone. Warehouse: {WAREHOUSE_PATH}")


if __name__ == "__main__":
    main()
