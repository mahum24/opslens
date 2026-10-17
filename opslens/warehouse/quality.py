"""Data quality checks, run after every build.

Each check is a query that returns the rows that break a rule. "error"
checks stop the numbers being trusted; "warn" checks are worth a look.
Results are kept in ops.data_quality_results so the dashboard can show
how healthy the data is, not just what it says.
"""
import duckdb
import pandas as pd

CHECKS = [
    ("Every customer appears once", "error",
     "SELECT customer_ref FROM core.dim_customer GROUP BY 1 HAVING count(*) > 1"),
    ("No invoice line without a known product", "error",
     "SELECT * FROM staging.invoice_lines WHERE product_code NOT IN (SELECT product_code FROM core.dim_product)"),
    ("No negative invoice amounts", "error",
     "SELECT * FROM core.fct_invoice_line WHERE line_total_gbp < 0"),
    ("No events after the reporting date", "error",
     "SELECT ticket_id FROM core.fct_ticket WHERE opened_at::DATE > (SELECT max(month_end) FROM core.dim_month)"),
    ("Every raw invoice line reaches the warehouse", "error",
     """SELECT 'difference' FROM (SELECT (SELECT count(*) FROM raw.billing_invoice_lines) AS a,
                                         (SELECT count(*) FROM core.fct_invoice_line) AS b) WHERE a <> b"""),
    ("Recurring invoices match MRR for each month", "error",
     """SELECT i.month FROM (SELECT month, sum(line_total_gbp) AS inv FROM core.fct_invoice_line WHERE recurring GROUP BY 1) i
        JOIN (SELECT month, sum(mrr) AS mrr FROM core.fct_mrr_month GROUP BY 1) m USING (month)
        WHERE abs(inv - mrr) > 1"""),
    ("Tickets linked to a customer (at least 99%)", "warn",
     "SELECT 1 FROM core.fct_ticket HAVING avg((customer_ref IS NULL)::INT) > 0.01"),
    ("Hosting servers linked to a customer", "warn",
     "SELECT DISTINCT server_id FROM core.fct_hosting_month WHERE customer_ref IS NULL"),
    ("Customers missing from the CRM", "warn",
     "SELECT customer_ref FROM core.dim_customer WHERE NOT in_crm AND status = 'Active'"),
    ("Records waiting for a person to confirm the match", "warn",
     "SELECT * FROM integration.match_review_queue"),
]


def run_checks(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    results = []
    for name, severity, sql in CHECKS:
        failing = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
        results.append({"check_name": name, "severity": severity, "failing_rows": failing,
                        "status": "pass" if failing == 0 else ("fail" if severity == "error" else "warn")})
    df = pd.DataFrame(results)
    con.register("_dq", df)
    con.execute("CREATE OR REPLACE TABLE ops.data_quality_results AS SELECT *, now() AS checked_at FROM _dq")
    con.unregister("_dq")
    return df
