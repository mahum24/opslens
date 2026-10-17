# OpsLens Power BI model

The reporting layer is exported as a Power BI semantic model: ten tables in a
star schema, a DAX measure library and three report pages.

## 1. Data

```bash
python -m opslens.pipeline
```

This writes `data/powerbi/OpsLens_PowerBI.xlsx`: one sheet per table, each
saved as a named Excel table, so Power BI imports them as separate tables.

| Table | What it is | Rows (approx.) |
|---|---|---|
| DimCustomer | one row per customer | 420 |
| DimProduct | product catalogue and service line | 10 |
| DimMonth | one row per month | 33 |
| FactMRR | each subscription's MRR, per month | 17,500 |
| FactInvoice | every invoice line | 18,000 |
| FactTicket | every service desk ticket | 12,500 |
| FactTelecomUsage | calls and dropped calls per customer per month | 6,100 |
| CustomerHealth | churn risk and reasons for each active customer | 330 |
| MRRMovements | new / expansion / contraction / churned MRR per month | 33 |
| Tasks | tasks the automation rules created | ~120 |

## 2. Import

In Power BI, import `OpsLens_PowerBI.xlsx` as a semantic model (**New item → Semantic model → Excel**). All ten tables load.

## 3. Relationships (the star schema)

All *one-to-many*, single direction, from the dimension to the fact:

| From (one) | To (many) |
|---|---|
| DimCustomer[customer_ref] | FactMRR, FactInvoice, FactTicket, FactTelecomUsage, CustomerHealth [customer_ref] |
| DimMonth[month] | FactMRR, FactInvoice, FactTicket, FactTelecomUsage, MRRMovements [month] |
| DimProduct[product_code] | FactMRR, FactInvoice [product_code] |

There are no relationships between fact tables.

## 4. Measures

All measures are in [`measures.dax`](measures.dax) and live on **FactMRR**.

Formats: MRR, revenue and debt as **Currency (£)**, the `%` and rate measures as **Percentage**.

## 5. Report pages

**Page 1: Executive overview**
- Cards: MRR, MRR Change %, Active Customers, Customer Churn Rate, SLA Met %, Overdue Debt
- Line chart: MRR by DimMonth[month]
- Stacked column chart: MRRMovements new_mrr, expansion_mrr, contraction_mrr, churned_mrr by month
- Stacked area: Invoiced Revenue by month, legend DimProduct[service_line]
- Slicer: DimCustomer[sector]

**Page 2: Customer risk**
- Cards: MRR at High Risk, Share of MRR at High Risk
- Table: DimCustomer[customer_name], account_manager, CustomerHealth current_mrr,
  churn_probability, risk_band, reason_1, reason_2 (sort by churn_probability, filter risk_band = High)
- Bar chart: MRR at High Risk by DimCustomer[account_manager]
- Slicers: account_manager, risk_band

**Page 3: Service desk & network**
- Line chart: SLA Met % by month, legend FactTicket[priority]
- Stacked column: Tickets by month, legend FactTicket[category]
- Card: Average CSAT, Median Resolution Hours, Open Tickets Breached
- Line chart: Dropped Call Rate by month

Each service line keeps one colour across every page, and no chart uses two Y axes.
