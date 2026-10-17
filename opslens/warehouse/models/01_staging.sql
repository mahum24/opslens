-- Staging: one clean, typed table per source table, linked to customer_ref.
-- Rows that could not be linked keep a NULL customer_ref (they are counted
-- by the data quality checks, never silently dropped).
CREATE SCHEMA IF NOT EXISTS staging;

CREATE OR REPLACE TABLE staging.billing_customers AS
SELECT customer_ref, customer_name, billing_email, postcode, NULLIF(telecom_account_no, '') AS telecom_account_no,
       payment_terms_days::INT AS payment_terms_days
FROM raw.billing_customers;

CREATE OR REPLACE TABLE staging.subscriptions AS
SELECT subscription_id, customer_ref, product_code, quantity::INT AS quantity,
       monthly_price::DECIMAL(10, 2) AS monthly_price,
       strptime(start_date, '%d/%m/%Y')::DATE AS start_date,
       try_strptime(NULLIF(end_date, ''), '%d/%m/%Y')::DATE AS end_date
FROM raw.billing_subscriptions;

CREATE OR REPLACE TABLE staging.invoice_lines AS
SELECT invoice_no, strptime(invoice_date, '%d/%m/%Y')::DATE AS invoice_date, customer_ref, product_code,
       description, quantity::INT AS quantity, unit_price::DECIMAL(12, 2) AS unit_price,
       line_total_gbp::DECIMAL(12, 2) AS line_total_gbp,
       strptime(due_date, '%d/%m/%Y')::DATE AS due_date, status,
       try_strptime(NULLIF(paid_date, ''), '%d/%m/%Y')::DATE AS paid_date
FROM raw.billing_invoice_lines;

CREATE OR REPLACE TABLE staging.tickets AS
SELECT t.ticket_id, x.customer_ref, coalesce(x.method, 'unmatched') AS match_method, x.confidence AS match_confidence,
       t.priority, t.category, t.status,
       t.opened_at::TIMESTAMP AS opened_at,
       t.first_response_at::TIMESTAMP AS first_response_at,
       NULLIF(t.resolved_at, '')::TIMESTAMP AS resolved_at,
       NULLIF(t.csat_score, '')::INT AS csat_score
FROM raw.sd_tickets t
LEFT JOIN integration.customer_xref x
       ON x.source_system = 'servicedesk' AND NOT x.needs_review
      AND x.source_key = t.organisation || '|' || lower(split_part(t.requester_email, '@', 2));

CREATE OR REPLACE TABLE staging.telecom_usage AS
SELECT b.customer_ref, u.account_no, strptime(u.period || '-01', '%Y-%m-%d')::DATE AS month,
       u.calls::INT AS calls, u.minutes::INT AS minutes, u.dropped_calls::INT AS dropped_calls,
       u.data_gb::DOUBLE AS data_gb
FROM raw.tel_usage u
LEFT JOIN staging.billing_customers b ON b.telecom_account_no = u.account_no;

CREATE OR REPLACE TABLE staging.hosting_metrics AS
SELECT x.customer_ref, h.server_id, h.owner_domain, strptime(h.month || '-01', '%Y-%m-%d')::DATE AS month,
       h.uptime_pct::DOUBLE AS uptime_pct, h.incidents::INT AS incidents, h.cpu_avg_pct::DOUBLE AS cpu_avg_pct
FROM raw.host_metrics h
LEFT JOIN integration.customer_xref x
       ON x.source_system = 'hosting' AND x.source_key = h.owner_domain AND NOT x.needs_review;
