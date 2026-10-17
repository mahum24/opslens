-- Marts: ready-made answers for dashboards, the automation rules and the AI
-- assistant. Each one is described in config/semantic_layer.yaml.
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS ml;

-- MRR movements per month: where did revenue come from and where did it go.
CREATE OR REPLACE TABLE mart.mrr_monthly AS
WITH c AS (
    SELECT customer_ref, month, sum(mrr) AS mrr FROM core.fct_mrr_month GROUP BY ALL
), grid AS (
    SELECT m.month, k.customer_ref, coalesce(c.mrr, 0) AS mrr
    FROM core.dim_month m CROSS JOIN (SELECT DISTINCT customer_ref FROM c) k
    LEFT JOIN c USING (customer_ref, month)
), g AS (
    SELECT *, lag(mrr) OVER (PARTITION BY customer_ref ORDER BY month) AS prev FROM grid
), agg AS (
    SELECT month,
           sum(mrr) AS mrr,
           count(*) FILTER (WHERE mrr > 0) AS active_customers,
           count(*) FILTER (WHERE prev = 0 AND mrr > 0) AS new_customers,
           count(*) FILTER (WHERE prev > 0 AND mrr = 0) AS churned_customers,
           sum(CASE WHEN prev = 0 AND mrr > 0 THEN mrr ELSE 0 END) AS new_mrr,
           sum(CASE WHEN prev > 0 AND mrr > prev THEN mrr - prev ELSE 0 END) AS expansion_mrr,
           sum(CASE WHEN prev > 0 AND mrr > 0 AND mrr < prev THEN prev - mrr ELSE 0 END) AS contraction_mrr,
           sum(CASE WHEN prev > 0 AND mrr = 0 THEN prev ELSE 0 END) AS churned_mrr
    FROM g GROUP BY month
)
SELECT *,
       round(churned_customers / nullif(lag(active_customers) OVER (ORDER BY month), 0), 4) AS customer_churn_rate,
       round(churned_mrr / nullif(lag(mrr) OVER (ORDER BY month), 0), 4) AS revenue_churn_rate
FROM agg ORDER BY month;

CREATE OR REPLACE TABLE mart.revenue_monthly AS
SELECT month, service_line, sum(line_total_gbp) AS invoiced_gbp,
       sum(line_total_gbp) FILTER (WHERE recurring) AS recurring_gbp,
       sum(line_total_gbp) FILTER (WHERE NOT recurring) AS one_off_gbp,
       count(DISTINCT customer_ref) AS customers_invoiced
FROM core.fct_invoice_line GROUP BY ALL ORDER BY month, service_line;

CREATE OR REPLACE TABLE mart.sla_monthly AS
SELECT month, priority, count(*) AS tickets,
       count(*) FILTER (WHERE sla_status = 'Met') AS met,
       count(*) FILTER (WHERE sla_status IN ('Breached', 'Open - breached')) AS breached,
       round(count(*) FILTER (WHERE sla_status = 'Met') / nullif(count(*) FILTER (WHERE resolved_at IS NOT NULL), 0), 4) AS sla_met_rate,
       round(median(resolution_hours), 2) AS median_resolution_hours,
       round(avg(csat_score), 2) AS avg_csat,
       count(csat_score) AS csat_responses
FROM core.fct_ticket GROUP BY ALL ORDER BY month, priority;

CREATE OR REPLACE TABLE mart.receivables AS
WITH inv AS (
    SELECT invoice_no, customer_ref, invoice_date, due_date, sum(line_total_gbp) AS amount_gbp
    FROM core.fct_invoice_line WHERE paid_date IS NULL GROUP BY ALL
)
SELECT inv.*, date_diff('day', due_date, $AS_OF) AS days_overdue,
       CASE WHEN due_date >= $AS_OF THEN 'Not yet due'
            WHEN date_diff('day', due_date, $AS_OF) <= 30 THEN '1-30 days'
            WHEN date_diff('day', due_date, $AS_OF) <= 60 THEN '31-60 days'
            ELSE '60+ days' END AS ageing_bucket
FROM inv;

-- One row per customer per month: the footprint the churn model learns from.
CREATE OR REPLACE TABLE mart.customer_month AS
WITH m AS (
    SELECT customer_ref, month, sum(mrr) AS mrr, count(DISTINCT product_code) AS products
    FROM core.fct_mrr_month GROUP BY ALL
), t AS (
    SELECT customer_ref, month, count(*) AS tickets,
           count(*) FILTER (WHERE priority = 'P1') AS p1_tickets,
           count(*) FILTER (WHERE sla_status IN ('Breached', 'Open - breached')) AS sla_breaches,
           avg(csat_score) AS avg_csat, count(csat_score) AS csat_responses
    FROM core.fct_ticket WHERE customer_ref IS NOT NULL GROUP BY ALL
), h AS (
    SELECT customer_ref, month, min(uptime_pct) AS min_uptime_pct, sum(incidents) AS hosting_incidents
    FROM core.fct_hosting_month WHERE customer_ref IS NOT NULL GROUP BY ALL
)
SELECT m.*, coalesce(t.tickets, 0) AS tickets, coalesce(t.p1_tickets, 0) AS p1_tickets,
       coalesce(t.sla_breaches, 0) AS sla_breaches, t.avg_csat, coalesce(t.csat_responses, 0) AS csat_responses,
       u.calls, u.dropped_call_rate, h.min_uptime_pct, coalesce(h.hosting_incidents, 0) AS hosting_incidents
FROM m
LEFT JOIN t USING (customer_ref, month)
LEFT JOIN core.fct_telecom_usage_month u USING (customer_ref, month)
LEFT JOIN h USING (customer_ref, month);

-- Filled by the churn model (opslens.analytics.churn_model).
CREATE TABLE IF NOT EXISTS ml.churn_scores (
    customer_ref VARCHAR, scored_at DATE, churn_probability DOUBLE, risk_band VARCHAR,
    reason_1 VARCHAR, reason_2 VARCHAR, reason_3 VARCHAR, model_version VARCHAR
);

-- The one-page view of every active customer.
CREATE OR REPLACE VIEW mart.customer_health AS
WITH last3 AS (
    SELECT customer_ref, sum(tickets) AS tickets_90d, sum(sla_breaches) AS sla_breaches_90d,
           sum(p1_tickets) AS p1_tickets_90d,
           sum(avg_csat * csat_responses) / nullif(sum(csat_responses), 0) AS avg_csat_90d
    FROM mart.customer_month
    WHERE month > date_trunc('month', $AS_OF) - INTERVAL 3 MONTH GROUP BY customer_ref
), owed AS (
    SELECT customer_ref, sum(amount_gbp) FILTER (WHERE days_overdue > 0) AS overdue_gbp
    FROM mart.receivables GROUP BY customer_ref
)
SELECT d.customer_ref, d.customer_name, d.sector, d.town, d.account_manager, d.customer_since,
       cm.mrr AS current_mrr, cm.products,
       coalesce(l.tickets_90d, 0) AS tickets_90d, coalesce(l.p1_tickets_90d, 0) AS p1_tickets_90d,
       coalesce(l.sla_breaches_90d, 0) AS sla_breaches_90d, round(l.avg_csat_90d, 2) AS avg_csat_90d,
       coalesce(o.overdue_gbp, 0) AS overdue_gbp,
       s.churn_probability, s.risk_band, s.reason_1, s.reason_2, s.reason_3
FROM core.dim_customer d
JOIN mart.customer_month cm ON cm.customer_ref = d.customer_ref AND cm.month = date_trunc('month', $AS_OF)
LEFT JOIN last3 l ON l.customer_ref = d.customer_ref
LEFT JOIN owed o ON o.customer_ref = d.customer_ref
LEFT JOIN ml.churn_scores s ON s.customer_ref = d.customer_ref
WHERE d.status = 'Active';
