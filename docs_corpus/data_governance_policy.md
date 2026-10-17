# Data Governance and AI Use Policy

Northgate Digital Ltd (fictional). Aligned with UK GDPR and the Data Protection Act 2018.

## Personal data

Contact names, email addresses and phone numbers are personal data. They stay in the source systems (CRM, billing, service desk) and the raw layer of the data platform. The reporting layer used by dashboards and the AI assistant holds company-level data only.

## Access

Staff see data by role (leadership, account manager, service desk, finance). Access is enforced by the platform, not by hiding menu items.

## Use of AI models

- The AI assistant may only read the reporting layer, using read-only queries checked by a SQL guard.
- When an external AI model (outside our own data centre) is used, emails, phone numbers and postcodes are masked before anything is sent.
- A self-hosted model running in our own data centre may be used for sensitive questions so that no data leaves our infrastructure.
- Every question, the SQL it ran and the sources it used are logged for audit for 12 months.
- AI answers support decisions; they do not make them. Credit holds, suspensions and retention offers are always decided by a person.

## Retention

Ticket and usage data is kept for 6 years after a customer leaves, for contract and tax reasons, then deleted.
