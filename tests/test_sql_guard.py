import pytest

from opslens.assistant import sql_guard


@pytest.mark.parametrize("sql, why", [
    ("DROP TABLE mart.mrr_monthly", "single read-only SELECT"),
    ("SELECT 1 AS a; DELETE FROM mart.mrr_monthly", "single read-only SELECT"),
    ("SELECT * FROM raw.crm_contacts", "cannot read raw.crm_contacts"),
    ("SELECT * FROM integration.customer_xref", "cannot read"),
    ("SELECT * FROM read_csv('/etc/passwd')", "Table functions"),
    ("SELECT * FROM 'data/sources/crm/contacts.json'", "Reading files"),
    ("SELECT * FROM customer_health", "schema"),
    ("SELECT 42", "at least one"),
])
def test_dangerous_or_out_of_scope_queries_are_refused(con, sql, why):
    with pytest.raises(sql_guard.GuardError, match=why):
        sql_guard.check(sql, "leadership", con)


def test_roles_only_reach_their_own_tables(con):
    sql = "SELECT sum(amount_gbp) FROM mart.receivables"
    assert sql_guard.check(sql, "finance", con) == ["mart.receivables"]
    with pytest.raises(sql_guard.GuardError, match="service_desk role cannot read"):
        sql_guard.check(sql, "service_desk", con)


def test_ctes_and_subqueries_are_checked_too(con):
    with pytest.raises(sql_guard.GuardError):
        sql_guard.check("WITH x AS (SELECT * FROM raw.crm_contacts) SELECT * FROM x", "leadership", con)
    with pytest.raises(sql_guard.GuardError):
        sql_guard.check("SELECT * FROM mart.sla_monthly WHERE month IN (SELECT month FROM mart.mrr_monthly)",
                        "service_desk", con)


def test_results_are_capped(con):
    df, _, truncated = sql_guard.run("SELECT * FROM core.fct_ticket", "leadership", con, max_rows=10)
    assert len(df) == 10 and truncated
