-- Core: the unified reporting dataset. A star schema, one golden record per
-- customer, and facts that all speak the same customer_ref.
CREATE SCHEMA IF NOT EXISTS core;

CREATE OR REPLACE TABLE core.dim_month AS
SELECT month::DATE AS month,
       least(last_day(month), $AS_OF)::DATE AS month_end,
       year(month) AS year, quarter(month) AS quarter, strftime(month, '%b %Y') AS month_label
FROM generate_series(DATE '2024-01-01', $AS_OF, INTERVAL 1 MONTH) AS g(month);

CREATE OR REPLACE TABLE core.dim_product AS
SELECT * FROM ref.products;

CREATE OR REPLACE TABLE core.dim_customer AS
WITH crm AS (
    SELECT x.customer_ref, c.id AS crm_id, c.name AS crm_name, c.industry, c.city,
           TRY_CAST(c.numberofemployees AS INT) AS employees, c.hubspot_owner,
           c.createdate::TIMESTAMP::DATE AS crm_created
    FROM raw.crm_companies c
    JOIN integration.customer_xref x ON x.source_system = 'crm' AND x.source_key = c.id AND NOT x.needs_review
    QUALIFY row_number() OVER (PARTITION BY x.customer_ref ORDER BY x.confidence DESC) = 1
), subs AS (
    SELECT customer_ref, min(start_date) AS first_start,
           max(end_date) AS last_end,
           count(*) FILTER (WHERE end_date IS NULL OR end_date > $AS_OF) AS active_subscriptions
    FROM staging.subscriptions GROUP BY customer_ref
)
SELECT b.customer_ref,
       -- Billing is the legal name but often in capitals. Use the CRM's nicer casing only when it is
       -- the same name; CRM names are typed by hand and sometimes misspelt.
       CASE WHEN b.customer_name <> upper(b.customer_name) THEN b.customer_name
            WHEN lower(replace(b.customer_name, ' LIMITED', ' LTD')) = lower(replace(crm.crm_name, ' Limited', ' Ltd'))
                 THEN crm.crm_name
            ELSE replace(replace(list_aggregate(list_transform(string_split(lower(b.customer_name), ' '),
                                 w -> upper(w[1]) || w[2:]), 'string_agg', ' '), ' Llp', ' LLP'), ' Plc', ' PLC')
       END AS customer_name,
       coalesce(crm.industry, 'Unknown') AS sector,
       coalesce(crm.city, 'Unknown') AS town,
       split_part(b.postcode, ' ', 1) AS postcode_district,
       crm.employees,
       coalesce(crm.hubspot_owner, 'Unassigned') AS account_manager,
       coalesce(crm.crm_created, subs.first_start) AS customer_since,
       CASE WHEN subs.active_subscriptions > 0 THEN 'Active' ELSE 'Churned' END AS status,
       CASE WHEN subs.active_subscriptions = 0 THEN subs.last_end END AS churn_date,
       subs.active_subscriptions,
       crm.crm_id IS NOT NULL AS in_crm,
       b.telecom_account_no IS NOT NULL AS has_telecoms
FROM staging.billing_customers b
LEFT JOIN crm USING (customer_ref)
LEFT JOIN subs USING (customer_ref);

-- Monthly recurring revenue: what each subscription is worth at each month end.
CREATE OR REPLACE TABLE core.fct_mrr_month AS
SELECT m.month, s.customer_ref, s.subscription_id, s.product_code, p.service_line,
       s.quantity, (s.quantity * s.monthly_price)::DECIMAL(12, 2) AS mrr
FROM core.dim_month m
JOIN staging.subscriptions s
  ON s.start_date <= m.month_end AND (s.end_date IS NULL OR s.end_date >= m.month_end)
JOIN core.dim_product p USING (product_code);

CREATE OR REPLACE TABLE core.fct_invoice_line AS
SELECT i.*, p.service_line, p.recurring,
       date_trunc('month', i.invoice_date)::DATE AS month,
       date_diff('day', i.invoice_date, i.paid_date) AS days_to_pay,
       i.paid_date IS NULL AND i.due_date < $AS_OF AS is_overdue
FROM staging.invoice_lines i
JOIN core.dim_product p USING (product_code);

CREATE OR REPLACE TABLE core.fct_ticket AS
WITH t AS (
    SELECT t.*, s.target_hours,
           date_diff('minute', t.opened_at, coalesce(t.resolved_at, $AS_OF::TIMESTAMP + INTERVAL 1 DAY)) / 60.0 AS age_hours,
           date_diff('minute', t.opened_at, t.first_response_at) AS first_response_minutes
    FROM staging.tickets t JOIN ref.sla_targets s USING (priority)
)
SELECT *, date_trunc('month', opened_at)::DATE AS month,
       CASE WHEN resolved_at IS NOT NULL THEN round(age_hours, 2) END AS resolution_hours,
       CASE WHEN resolved_at IS NOT NULL AND age_hours <= target_hours THEN 'Met'
            WHEN resolved_at IS NOT NULL THEN 'Breached'
            WHEN age_hours <= target_hours THEN 'Open - within SLA'
            ELSE 'Open - breached' END AS sla_status,
       round(age_hours / target_hours, 2) AS sla_used_ratio
FROM t;

CREATE OR REPLACE TABLE core.fct_telecom_usage_month AS
SELECT customer_ref, month, calls, minutes, dropped_calls, data_gb,
       round(dropped_calls / nullif(calls, 0), 4) AS dropped_call_rate
FROM staging.telecom_usage;

CREATE OR REPLACE TABLE core.fct_hosting_month AS
SELECT customer_ref, server_id, month, uptime_pct, incidents, cpu_avg_pct,
       uptime_pct >= (SELECT value FROM ref.settings WHERE key = 'hosting_uptime_target_pct') AS uptime_target_met
FROM staging.hosting_metrics;
