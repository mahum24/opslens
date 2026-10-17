def test_no_data_quality_errors(con):
    failed = con.execute("SELECT check_name FROM ops.data_quality_results WHERE status = 'fail'").fetchall()
    assert failed == []


def test_mrr_movements_add_up(con):
    rows = con.execute("SELECT * FROM mart.mrr_monthly ORDER BY month").df()
    for prev, cur in zip(rows.itertuples(), rows.iloc[1:].itertuples()):
        change = cur.new_mrr + cur.expansion_mrr - cur.contraction_mrr - cur.churned_mrr
        assert abs(float(cur.mrr) - float(prev.mrr) - float(change)) < 0.01, cur.month


def test_customer_health_only_lists_active_customers(con):
    churned = con.execute("""SELECT count(*) FROM mart.customer_health h JOIN core.dim_customer d USING (customer_ref)
                             WHERE d.status <> 'Active'""").fetchone()[0]
    assert churned == 0
