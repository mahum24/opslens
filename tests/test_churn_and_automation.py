from opslens.automation.engine import run_rules


def test_explainable_model_beats_chance_on_unseen_months(con):
    auc = con.execute("SELECT roc_auc FROM ml.model_metrics WHERE model = 'logistic_regression'").fetchone()[0]
    assert auc > 0.65


def test_high_risk_customers_have_reasons(con):
    missing = con.execute("SELECT count(*) FROM ml.churn_scores WHERE risk_band = 'High' AND reason_1 IS NULL").fetchone()[0]
    assert missing == 0


def test_running_rules_again_does_not_duplicate_tasks(con):
    before = con.execute("SELECT count(*) FROM ops.tasks").fetchone()[0]
    summary = run_rules(con)
    assert summary.created.sum() == 0
    assert con.execute("SELECT count(*) FROM ops.tasks").fetchone()[0] == before


def test_tasks_close_when_the_problem_goes_away(con):
    con.execute("CREATE TABLE _keep AS SELECT * FROM integration.match_review_queue")
    con.execute("DELETE FROM integration.match_review_queue")     # a person confirmed every match
    try:
        summary = run_rules(con).set_index("rule")
        assert summary.loc["data_match_review", "auto_closed"] > 0
        assert con.execute("""SELECT count(*) FROM ops.tasks
                              WHERE rule_id = 'data_match_review' AND status = 'open'""").fetchone()[0] == 0
    finally:
        con.execute("INSERT INTO integration.match_review_queue SELECT * FROM _keep")
        con.execute("DROP TABLE _keep")
