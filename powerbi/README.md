# Building the OpsLens Power BI report (in the browser)

You do not need Power BI Desktop (which is Windows-only). Everything below
works at [app.powerbi.com](https://app.powerbi.com) with a work or university
account.

## 1. Get the data

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

## 2. Upload it

1. Go to **My workspace**, then **New item** (or **+ New**) → **Semantic model** → **Excel** (or
   **Upload → Browse** and pick the file).
2. Choose `OpsLens_PowerBI.xlsx` and import it. Power BI creates a semantic model with all ten tables.

## 3. Set up the relationships (the star schema)

Open the semantic model and click **Open data model**. Drag to create these
relationships (all *one-to-many*, single direction, from the dimension to the fact):

| From (one) | To (many) |
|---|---|
| DimCustomer[customer_ref] | FactMRR, FactInvoice, FactTicket, FactTelecomUsage, CustomerHealth [customer_ref] |
| DimMonth[month] | FactMRR, FactInvoice, FactTicket, FactTelecomUsage, MRRMovements [month] |
| DimProduct[product_code] | FactMRR, FactInvoice [product_code] |

Power BI may have guessed some of these already; delete any it guessed between two fact tables.

## 4. Add the measures

Still in the data model, select **FactMRR**, click **New measure**, and paste
each measure from [`measures.dax`](measures.dax) one at a time.

Format them: MRR, revenue and debt as **Currency (£)**, the `%` and rate measures as **Percentage**.

## 5. Build three report pages

Click **Create report** (or **Auto-create** then edit). Suggested layout:

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

Tip: keep one colour per service line across every page, and avoid charts with two Y axes.

## 6. Share it

Save the report. Take a screenshot of each page and add
them to `docs/screenshots/`, or use **File → Export → PDF**.
