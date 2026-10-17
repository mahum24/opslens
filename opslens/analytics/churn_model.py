"""Which customers are likely to leave in the next 90 days, and why.

The model is trained on monthly snapshots of the past. For each snapshot it
only uses what was known on that day (tickets opened, invoices overdue by
then), so it is never trained on the future. It is tested on later snapshots
than it was trained on, which is how it will be used.

Classes are not re-weighted, so a score of 0.20 really means "about a 1 in 5
chance", which is what people reading it will assume.

A logistic regression is used for scoring because each prediction can be
broken down into the factors that pushed it up, in plain words. A gradient
boosting model is trained alongside it as a benchmark, so we can see how
much accuracy (if any) the simpler, explainable model gives up.
"""
from datetime import date

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from opslens.settings import AS_OF_DATE

MODEL_VERSION = "churn-lr-v1"
HORIZON_MONTHS = 3
TRAIN_UNTIL = date(2025, 9, 1)       # snapshots up to here train, later ones test

FEATURES = {
    # feature: (plain-English reason, direction that means more risk: +1 higher, -1 lower)
    "p1_tickets_3m": ("Major outages reported ({v:.0f} P1 tickets in 3 months)", +1),
    "sla_breaches_3m": ("Support missed its SLA {v:.0f} times in 3 months", +1),
    "csat_3m": ("Low satisfaction scores (average {v:.1f} out of 5)", -1),
    "calls_trend": ("Call volume is falling ({v:+.0%} vs the 3 months before)", -1),
    "dropped_rate_3m": ("Poor call quality ({v:.1%} of calls dropped)", +1),
    "overdue_invoices": ("{v:.0f} invoices overdue", +1),
    "max_days_late": ("Paying late (up to {v:.0f} days after due date)", +1),
    "hosting_incidents_3m": ("Hosting incidents ({v:.0f} in 3 months)", +1),
    "products": ("Uses only {v:.0f} of our services", -1),
    "tenure_months": ("Fairly new customer ({v:.0f} months)", -1),
    "tickets_3m": (None, 0),     # context only: big customers simply raise more tickets
    "mrr": (None, 0),            # context only
}


def month_add(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def snapshot_features(con: duckdb.DuckDBPyConnection, snap: date) -> pd.DataFrame:
    """Features for every customer active in month `snap`, using only data known by its end."""
    q = f"""
    WITH active AS (
        SELECT customer_ref, mrr, products FROM mart.customer_month WHERE month = DATE '{snap}'
    ), win AS (
        SELECT customer_ref,
               sum(tickets) AS tickets_3m, sum(p1_tickets) AS p1_tickets_3m, sum(sla_breaches) AS sla_breaches_3m,
               sum(avg_csat * csat_responses) / nullif(sum(csat_responses), 0) AS csat_3m,
               sum(calls) AS calls_3m,
               avg(dropped_call_rate) AS dropped_rate_3m, sum(hosting_incidents) AS hosting_incidents_3m
        FROM mart.customer_month
        WHERE month > DATE '{snap}' - INTERVAL 3 MONTH AND month <= DATE '{snap}' GROUP BY 1
    ), prev AS (
        SELECT customer_ref, sum(calls) AS calls_prev
        FROM mart.customer_month
        WHERE month > DATE '{snap}' - INTERVAL 6 MONTH AND month <= DATE '{snap}' - INTERVAL 3 MONTH GROUP BY 1
    ), pay AS (
        -- what accounts receivable looked like at the end of the snapshot month
        SELECT customer_ref,
               count(DISTINCT invoice_no) FILTER (WHERE due_date < last_day(DATE '{snap}')
                     AND (paid_date IS NULL OR paid_date > last_day(DATE '{snap}'))) AS overdue_invoices,
               max(CASE WHEN due_date < last_day(DATE '{snap}') THEN
                   greatest(date_diff('day', due_date, least(coalesce(paid_date, DATE '9999-01-01'), last_day(DATE '{snap}'))), 0)
               END) AS max_days_late
        FROM core.fct_invoice_line
        WHERE invoice_date > DATE '{snap}' - INTERVAL 6 MONTH AND invoice_date <= last_day(DATE '{snap}')
        GROUP BY 1
    )
    SELECT a.customer_ref, a.mrr::DOUBLE AS mrr, a.products,
           date_diff('month', d.customer_since, DATE '{snap}') AS tenure_months,
           w.* EXCLUDE (customer_ref, calls_3m),
           CASE WHEN p.calls_prev > 0 THEN w.calls_3m / p.calls_prev - 1 END AS calls_trend,
           coalesce(pay.overdue_invoices, 0) AS overdue_invoices, coalesce(pay.max_days_late, 0) AS max_days_late,
           d.churn_date
    FROM active a
    JOIN core.dim_customer d USING (customer_ref)
    LEFT JOIN win w USING (customer_ref)
    LEFT JOIN prev p USING (customer_ref)
    LEFT JOIN pay USING (customer_ref)
    """
    df = con.execute(q).df()
    df["snapshot"] = snap
    snap_end = pd.Timestamp(month_add(snap, 1)) - pd.Timedelta(days=1)
    horizon_end = pd.Timestamp(month_add(snap, 1 + HORIZON_MONTHS)) - pd.Timedelta(days=1)
    cd = pd.to_datetime(df.churn_date)
    df["churned_next_90d"] = ((cd > snap_end) & (cd <= horizon_end)).astype(int)
    return df[~(cd <= snap_end)].reset_index(drop=True)     # already gone by the end of the month


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    X = df[list(FEATURES)].astype(float).copy()
    # A missing value means "nothing to measure" (no tickets, no phones), filled with a neutral value.
    X["csat_3m"] = X["csat_3m"].fillna(4.5)
    X["calls_trend"] = X["calls_trend"].fillna(0).clip(-1, 1)
    X["dropped_rate_3m"] = X["dropped_rate_3m"].fillna(0)
    X["mrr"] = np.log1p(X["mrr"])
    X["tenure_months"] = np.log1p(X["tenure_months"].clip(lower=0))
    return X.fillna(0)


def reasons(model, x_row: pd.Series, raw_row: pd.Series, k=3) -> list[str]:
    """The k factors that pushed this customer's risk up the most, in plain words."""
    scaler, lr = model.named_steps["standardscaler"], model.named_steps["logisticregression"]
    z = (x_row.values - scaler.mean_) / scaler.scale_
    contrib = pd.Series(z * lr.coef_[0], index=x_row.index).sort_values(ascending=False)
    out = []
    for feat, c in contrib.items():
        if c <= 0.05 or len(out) == k:
            break
        text, direction = FEATURES[feat]
        # Only give a reason when it points the way the business would expect. A model can
        # learn odd signs from correlated inputs; a reason like that would mislead people.
        if direction == 0 or np.sign(z[x_row.index.get_loc(feat)]) != direction:
            continue
        out.append(text.format(v=raw_row[feat] if pd.notna(raw_row[feat]) else 0))
    return out + [None] * (k - len(out))


def train_and_score(con: duckdb.DuckDBPyConnection) -> dict:
    as_of_month = date(AS_OF_DATE.year, AS_OF_DATE.month, 1)
    snaps, s = [], date(2024, 6, 1)
    while month_add(s, HORIZON_MONTHS) <= as_of_month:     # label needs 3 full months after the snapshot
        snaps.append(s)
        s = month_add(s, 1)
    data = pd.concat([snapshot_features(con, s) for s in snaps], ignore_index=True)
    train, test = data[data.snapshot <= TRAIN_UNTIL], data[data.snapshot > TRAIN_UNTIL]

    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5))
    gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300, random_state=0)
    metrics = []
    for name, model in (("logistic_regression", lr), ("gradient_boosting", gb)):
        model.fit(prepare(train), train.churned_next_90d)
        p = model.predict_proba(prepare(test))[:, 1]
        top = test.assign(p=p).sort_values("p", ascending=False)
        top_n = max(1, int(len(top) * 0.1))
        metrics.append({"model": name, "roc_auc": round(roc_auc_score(test.churned_next_90d, p), 3),
                        "pr_auc": round(average_precision_score(test.churned_next_90d, p), 3),
                        "base_rate": round(test.churned_next_90d.mean(), 3),
                        "precision_top10pct": round(top.head(top_n).churned_next_90d.mean(), 3),
                        "recall_top10pct": round(top.head(top_n).churned_next_90d.sum() / max(test.churned_next_90d.sum(), 1), 3),
                        "train_rows": len(train), "test_rows": len(test)})

    # Refit the explainable model on everything we have, then score today's customers.
    lr.fit(prepare(data), data.churned_next_90d)
    now = snapshot_features(con, as_of_month)
    now = now[now.churn_date.isna()]                     # already-leaving customers are not "at risk"
    Xn = prepare(now)
    now["churn_probability"] = lr.predict_proba(Xn)[:, 1]
    # Bands by rank, so the account team gets a list it can actually work through.
    q_hi, q_med = now.churn_probability.quantile([0.9, 0.7])
    now["risk_band"] = np.where(now.churn_probability >= q_hi, "High",
                                np.where(now.churn_probability >= q_med, "Medium", "Low"))
    rs = [reasons(lr, Xn.loc[i], now.loc[i]) for i in now.index]
    scores = pd.DataFrame({"customer_ref": now.customer_ref, "scored_at": AS_OF_DATE,
                           "churn_probability": now.churn_probability.round(4), "risk_band": now.risk_band,
                           "reason_1": [r[0] for r in rs], "reason_2": [r[1] for r in rs],
                           "reason_3": [r[2] for r in rs], "model_version": MODEL_VERSION})
    con.execute("DELETE FROM ml.churn_scores")
    con.register("_s", scores)
    con.execute("INSERT INTO ml.churn_scores SELECT * FROM _s")
    con.unregister("_s")

    coefs = pd.DataFrame({"feature": list(FEATURES), "coefficient": lr.named_steps["logisticregression"].coef_[0].round(3),
                          "reason_text": [v[0] for v in FEATURES.values()]})
    for name, df in (("model_metrics", pd.DataFrame(metrics)), ("model_coefficients", coefs)):
        con.register("_m", df)
        con.execute(f"CREATE OR REPLACE TABLE ml.{name} AS SELECT * FROM _m")
        con.unregister("_m")
    return {"metrics": metrics, "scored": len(scores), "high_risk": int((scores.risk_band == "High").sum())}
