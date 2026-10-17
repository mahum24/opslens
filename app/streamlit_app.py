"""OpsLens dashboard and AI assistant.

    streamlit run app/streamlit_app.py

Reads the warehouse read-only. Run `python -m opslens.pipeline` first.
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from opslens.assistant import sql_guard
from opslens.assistant.assistant import Assistant
from opslens.assistant.llm import LLMError, get_llm
from opslens.assistant.retriever import Retriever
from opslens.governance import audit
from opslens.settings import AS_OF_DATE, WAREHOUSE_PATH

st.set_page_config(page_title="OpsLens", page_icon="📊", layout="wide")

# ---------- chart style: fixed categorical order, thin marks, quiet grid ----------
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
STATUS = {"High": "#d03b3b", "Medium": "#fab219", "Low": "#0ca30c"}
SERVICE_LINES = ["Telecoms", "Managed IT", "Cloud Hosting", "Data Centre", "Software Development", "Hardware"]
LINE_COLOURS = dict(zip(SERVICE_LINES, SERIES))
PRIORITY_COLOURS = dict(zip(["P1", "P2", "P3", "P4"], SERIES))
pio.templates["opslens"] = go.layout.Template(layout=dict(
    font=dict(family="Inter, system-ui, sans-serif", color="#0b0b0b", size=13),
    paper_bgcolor="#fcfcfb", plot_bgcolor="#fcfcfb", colorway=SERIES,
    xaxis=dict(showgrid=False, linecolor="#c3c2b7", tickfont=dict(color="#898781")),
    yaxis=dict(gridcolor="#e1e0d9", zeroline=False, tickfont=dict(color="#898781")),
    legend=dict(orientation="h", y=1.12, x=0, title=None),
    margin=dict(l=8, r=8, t=48, b=8), hovermode="x unified"))
pio.templates.default = "opslens"


def style(fig, height=320):
    fig.update_traces(selector=dict(type="scatter"), line_width=2)
    fig.update_layout(height=height, title_font_size=15)
    return fig


@st.cache_resource
def connection():
    return sql_guard.connect_read_only(WAREHOUSE_PATH)


@st.cache_data(ttl=300)
def q(sql: str) -> pd.DataFrame:
    return connection().execute(sql).df()


def gbp(v):
    return f"£{v:,.0f}"


if not WAREHOUSE_PATH.exists():
    st.error("No warehouse yet. Run `python -m opslens.pipeline` first.")
    st.stop()

# ---------- sidebar ----------
st.sidebar.title("OpsLens")
st.sidebar.caption(f"Northgate Digital Ltd · data to {AS_OF_DATE:%d %b %Y}")
page = st.sidebar.radio("Page", ["Overview", "Customer health", "Service & network", "Automation",
                                 "Data integration", "Ask OpsLens", "Audit log"])
role = st.sidebar.selectbox("Signed in as", list(sql_guard.load_policy()["roles"]),
                            help="Changes what the AI assistant is allowed to read.")
st.sidebar.caption(sql_guard.load_policy()["roles"][role]["description"])

# ---------- pages ----------
if page == "Overview":
    st.header("Business overview")
    mrr = q("SELECT * FROM mart.mrr_monthly ORDER BY month")
    last, prev = mrr.iloc[-1], mrr.iloc[-2]
    sla = q("""SELECT sum(met) / sum(met + breached) AS rate FROM mart.sla_monthly
               WHERE month = (SELECT max(month) FROM mart.sla_monthly)""").rate.iat[0]
    owed = q("SELECT coalesce(sum(amount_gbp), 0) AS v FROM mart.receivables WHERE days_overdue > 0").v.iat[0]
    tasks = q("SELECT count(*) AS n FROM ops.tasks WHERE status = 'open'").n.iat[0]
    c = st.columns(6)
    c[0].metric("MRR", gbp(last.mrr), f"{last.mrr - prev.mrr:+,.0f} vs last month")
    c[1].metric("Active customers", int(last.active_customers), f"{int(last.active_customers - prev.active_customers):+d}")
    c[2].metric("Churn (last 3 months avg)", f"{mrr.customer_churn_rate.tail(3).mean():.1%}", delta_color="inverse",
                delta=f"{mrr.customer_churn_rate.tail(3).mean() - mrr.customer_churn_rate.iloc[-6:-3].mean():+.1%}")
    c[3].metric("SLA met last month", f"{sla:.0%}")
    c[4].metric("Overdue debt", gbp(owed))
    c[5].metric("Open tasks", int(tasks))

    left, right = st.columns(2)
    left.plotly_chart(style(px.line(mrr, x="month", y="mrr", title="Monthly recurring revenue",
                                    labels={"mrr": "MRR (£)", "month": ""})), width="stretch")
    mv = mrr.melt(id_vars="month", value_vars=["new_mrr", "expansion_mrr", "contraction_mrr", "churned_mrr"])
    mv.loc[mv.variable.isin(["contraction_mrr", "churned_mrr"]), "value"] *= -1
    mv["variable"] = mv.variable.str.replace("_mrr", "").str.capitalize()
    fig = px.bar(mv, x="month", y="value", color="variable", title="Where MRR came from and went",
                 labels={"value": "£ per month", "month": "", "variable": ""},
                 color_discrete_sequence=SERIES)
    fig.update_traces(marker_line_color="#fcfcfb", marker_line_width=1)
    right.plotly_chart(style(fig.update_layout(barmode="relative")), width="stretch")

    rev = q("SELECT month, service_line, invoiced_gbp FROM mart.revenue_monthly ORDER BY month")
    st.plotly_chart(style(px.area(rev, x="month", y="invoiced_gbp", color="service_line",
                                  category_orders={"service_line": SERVICE_LINES}, color_discrete_map=LINE_COLOURS,
                                  title="Invoiced revenue by service line",
                                  labels={"invoiced_gbp": "£ invoiced", "month": "", "service_line": ""})),
                    width="stretch")

elif page == "Customer health":
    st.header("Customer health and churn risk")
    st.caption("Churn probability is for the next 90 days. Reasons show what pushed each customer's risk up most.")
    h = q("SELECT * FROM mart.customer_health ORDER BY churn_probability DESC")
    f1, f2 = st.columns(2)
    am = f1.multiselect("Account manager", sorted(h.account_manager.unique()))
    band = f2.multiselect("Risk band", ["High", "Medium", "Low"], default=["High"])
    v = h[(h.account_manager.isin(am) if am else True) & (h.risk_band.isin(band) if band else True)]
    c = st.columns(3)
    c[0].metric("Customers shown", len(v))
    c[1].metric("MRR at high risk", gbp(h.loc[h.risk_band == "High", "current_mrr"].sum()))
    c[2].metric("Share of MRR at high risk", f"{h.loc[h.risk_band == 'High', 'current_mrr'].sum() / h.current_mrr.sum():.1%}")
    st.dataframe(v[["customer_name", "account_manager", "current_mrr", "churn_probability", "risk_band",
                    "reason_1", "reason_2", "reason_3", "tickets_90d", "sla_breaches_90d", "avg_csat_90d", "overdue_gbp"]],
                 hide_index=True, width="stretch",
                 column_config={"customer_name": "Customer", "account_manager": "Account manager",
                                "current_mrr": st.column_config.NumberColumn("MRR", format="£%.0f"),
                                "churn_probability": st.column_config.ProgressColumn("Churn risk (90 days)", format="percent",
                                                                                     min_value=0, max_value=1),
                                "risk_band": "Band", "reason_1": "Main reason", "reason_2": "Second reason",
                                "reason_3": "Third reason", "tickets_90d": "Tickets (90d)",
                                "sla_breaches_90d": "SLA breaches (90d)", "avg_csat_90d": "CSAT (90d)",
                                "overdue_gbp": st.column_config.NumberColumn("Overdue", format="£%.0f")})
    pick = st.selectbox("Look at one customer", v.customer_name if len(v) else h.customer_name)
    ref = h.loc[h.customer_name == pick, "customer_ref"].iat[0]
    cm = q(f"SELECT * FROM mart.customer_month WHERE customer_ref = '{ref}' ORDER BY month")
    a, b = st.columns(2)
    a.plotly_chart(style(px.bar(cm, x="month", y=["tickets", "sla_breaches"], barmode="group",
                                title=f"{pick}: tickets and SLA breaches",
                                labels={"value": "Tickets", "month": "", "variable": ""}), 280), width="stretch")
    b.plotly_chart(style(px.line(cm, x="month", y="mrr", title=f"{pick}: MRR", labels={"mrr": "£", "month": ""}), 280),
                   width="stretch")
    with st.expander("How the churn model works and how good it is"):
        st.dataframe(q("SELECT * FROM ml.model_metrics"), hide_index=True)
        st.markdown("Trained on monthly snapshots up to Sep 2025 and tested on later months it never saw. "
                    "The logistic regression is used because every score can be explained; the gradient boosting "
                    "model is a benchmark. Reasons only appear when they point the way the business would expect.")
        st.dataframe(q("SELECT feature, coefficient FROM ml.model_coefficients ORDER BY abs(coefficient) DESC"),
                     hide_index=True)

elif page == "Service & network":
    st.header("Service desk and network")
    sla = q("SELECT * FROM mart.sla_monthly WHERE month >= DATE '2025-01-01' ORDER BY month")
    a, b = st.columns(2)
    a.plotly_chart(style(px.line(sla, x="month", y="sla_met_rate", color="priority", color_discrete_map=PRIORITY_COLOURS,
                                 title="SLA met rate by priority", labels={"sla_met_rate": "Met", "month": "", "priority": ""})
                         .update_yaxes(tickformat=".0%")), width="stretch")
    cat = q("""SELECT month, category, count(*) AS tickets FROM core.fct_ticket WHERE month >= DATE '2025-01-01'
               GROUP BY ALL ORDER BY month""")
    top = cat.groupby("category").tickets.sum().nlargest(5).index
    cat["category"] = cat.category.where(cat.category.isin(top), "Other")
    cat = cat.groupby(["month", "category"], as_index=False).tickets.sum()
    b.plotly_chart(style(px.bar(cat, x="month", y="tickets", color="category", title="Tickets by category",
                                category_orders={"category": list(top) + ["Other"]},
                                color_discrete_sequence=SERIES[:5] + ["#898781"],
                                labels={"month": "", "category": ""})
                         .update_traces(marker_line_color="#fcfcfb", marker_line_width=1)), width="stretch")
    st.subheader("Open tickets close to or past their SLA")
    st.dataframe(q("""SELECT t.ticket_id, d.customer_name, t.priority, t.category, round(t.age_hours, 1) AS age_hours,
                             t.target_hours, t.sla_status FROM core.fct_ticket t LEFT JOIN core.dim_customer d USING (customer_ref)
                      WHERE t.resolved_at IS NULL ORDER BY t.sla_used_ratio DESC"""), hide_index=True, width="stretch")
    a, b = st.columns(2)
    dr = q("""SELECT month, sum(dropped_calls) / sum(calls) AS rate FROM core.fct_telecom_usage_month
              GROUP BY 1 ORDER BY 1""")
    a.plotly_chart(style(px.line(dr, x="month", y="rate", title="Dropped call rate, all customers",
                                 labels={"rate": "Dropped", "month": ""}).update_yaxes(tickformat=".2%")), width="stretch")
    up = q("""SELECT month, avg(uptime_target_met::INT) AS rate FROM core.fct_hosting_month GROUP BY 1 ORDER BY 1""")
    b.plotly_chart(style(px.line(up, x="month", y="rate", title="Servers meeting the 99.9% uptime target",
                                 labels={"rate": "Servers", "month": ""}).update_yaxes(tickformat=".0%")), width="stretch")

elif page == "Automation":
    st.header("Workflow automation")
    st.caption("Rules live in config/workflows.yaml. Each run creates tasks for new problems and closes tasks "
               "whose problem has gone. Run `python -m opslens.pipeline` to run them again.")
    t = q("""SELECT r.rule_id, count(*) FILTER (WHERE status = 'open') AS open_tasks,
                    count(*) FILTER (WHERE status = 'closed') AS auto_closed
             FROM ops.tasks r GROUP BY 1 ORDER BY open_tasks DESC""")
    st.dataframe(t, hide_index=True)
    owner = st.selectbox("Owner", ["All"] + sorted(q("SELECT DISTINCT owner FROM ops.tasks").owner))
    tasks = q("SELECT priority, owner, title, detail, created_at, rule_id FROM ops.tasks WHERE status = 'open' ORDER BY priority, created_at")
    if owner != "All":
        tasks = tasks[tasks.owner == owner]
    st.dataframe(tasks, hide_index=True, width="stretch")
    with st.expander("Notification outbox"):
        st.dataframe(q("SELECT created_at, channel, recipient, subject, delivered FROM ops.notifications ORDER BY created_at DESC LIMIT 200"),
                     hide_index=True, width="stretch")

elif page == "Data integration":
    st.header("Data integration and quality")
    st.caption("Five source systems with no shared customer ID, linked into one customer record.")
    st.dataframe(q("SELECT system, \"table\", rows, pii_columns FROM ops.ingest_log"), hide_index=True, width="stretch")
    m = q("""SELECT source_system, method, count(*) AS records FROM integration.customer_xref
             WHERE source_system <> 'billing' GROUP BY ALL ORDER BY source_system, records DESC""")
    st.plotly_chart(style(px.bar(m, x="records", y="source_system", color="method", orientation="h",
                                 title="How each system's records were linked to a customer",
                                 labels={"source_system": "", "method": ""}, color_discrete_sequence=SERIES)
                          .update_traces(marker_line_color="#fcfcfb", marker_line_width=1)
                          .update_layout(hovermode="closest"), 300), width="stretch")
    a, b = st.columns(2)
    a.subheader("Data quality checks")
    a.dataframe(q("SELECT status, check_name, failing_rows FROM ops.data_quality_results"), hide_index=True,
                width="stretch")
    b.subheader("Waiting for a person to confirm")
    b.dataframe(q("SELECT * FROM integration.match_review_queue"), hide_index=True, width="stretch")

elif page == "Ask OpsLens":
    st.header("Ask OpsLens")
    st.caption("Ask about customers, revenue, support or policy. Every answer shows the query it ran and the "
               "documents it used, so you can check it.")
    provider = st.radio("AI model", ["gemini", "ollama"], horizontal=True,
                        format_func=lambda p: "Gemini (external API, personal data masked)" if p == "gemini"
                        else "Ollama (self-hosted, data stays on our servers)")
    examples = ["Which high-risk customers pay us the most, and what should their account managers do?",
                "How has MRR changed over the last 6 months?",
                "Which customers are owed a service credit for last month's hosting uptime?",
                "Who owes us money more than 60 days overdue, and what does the billing policy say to do?",
                "What was the SLA met rate for P1 tickets each month this year?"]
    cols = st.columns(len(examples))
    for i, ex in enumerate(examples):
        if cols[i].button(ex, key=f"ex{i}", width="stretch"):
            st.session_state.question = ex
    question = st.chat_input("Ask a question") or st.session_state.pop("question", None)
    if question:
        st.chat_message("user").write(question)
        try:
            assistant = Assistant(connection(), get_llm(provider), role=role, retriever=st.cache_resource(Retriever)())
        except LLMError as e:
            st.error(str(e))
            st.stop()
        with st.spinner("Finding the evidence..."):
            a = assistant.ask(question)
        with st.chat_message("assistant"):
            badge = {"high": "🟢 High", "medium": "🟡 Medium", "low": "🔴 Low"}[a.confidence]
            st.markdown(a.answer)
            st.caption(f"Confidence: {badge} · {a.provider} · {a.latency_ms / 1000:.1f}s"
                       + (" · personal data masked before sending" if a.masked else " · no data left our servers"))
            for c in a.caveats:
                st.caption(f"⚠️ {c}")
            if a.sql:
                with st.expander(f"Query ({', '.join(a.tables) or 'not run'})"):
                    st.code(a.sql, language="sql")
                    if a.data is not None:
                        st.dataframe(a.data, hide_index=True, width="stretch")
            if a.sources:
                with st.expander(f"Policy sources ({len(a.sources)})"):
                    for i, s in enumerate(a.sources):
                        st.markdown(f"**[D{i + 1}]** {s}")
            with st.expander("Steps"):
                for s in a.steps:
                    st.markdown(f"- {s}")

elif page == "Audit log":
    st.header("AI assistant audit log")
    st.caption("Every question, who asked it, which model answered, the SQL it ran and the sources it used.")
    log = pd.DataFrame(audit.read())
    if log.empty:
        st.info("No questions asked yet.")
    else:
        st.dataframe(log, hide_index=True, width="stretch")
