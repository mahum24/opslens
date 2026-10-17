"""Layer 5: the AI assistant - questions in plain English, answers with evidence.

Each question goes through the same steps, and every step is shown to the user:

  1. Plan: the model decides whether it needs numbers (and writes SQL),
     policy documents (and writes a search), or both.
  2. Data: the SQL is checked by the guard for the user's role, then run
     read-only. If it fails, the model gets one chance to fix it.
  3. Documents: the relevant policy passages are found.
  4. Answer: the model answers only from that evidence, cites it, and says
     how confident it is. Personal data is masked if the model is external.
  5. Audit: the question, SQL, sources and outcome are logged.

The model never sees the database itself, only the rows the guard allowed.
"""
import re
import time
from dataclasses import dataclass, field

import duckdb
import pandas as pd
import yaml

from opslens.assistant import sql_guard
from opslens.assistant.retriever import Retriever
from opslens.governance import audit
from opslens.governance.pii import mask_frame, mask_text
from opslens.settings import AS_OF_DATE, CONFIG_DIR

EVIDENCE_ROWS = 60
CONFIDENCE_ORDER = ["low", "medium", "high"]

PLAN_SYSTEM = """You are the planning step of OpsLens, the decision-support assistant of Northgate Digital Ltd,
an IT and telecoms company (telecoms, cloud hosting, data centre, managed IT, software, hardware).
Decide what evidence is needed to answer the user's question, and return JSON only:
{"needs_data": true|false, "sql": "<one DuckDB SELECT query or null>",
 "needs_docs": true|false, "doc_query": "<search words for the policy documents or null>",
 "clarification": "<a short question back to the user, only if the question cannot be answered as asked, else null>"}

Rules for SQL:
- DuckDB dialect. Use only the tables listed below, always schema-qualified (e.g. mart.customer_health).
- Prefer the mart tables; they are already aggregated.
- Dates: DATE '2026-03-01'; months are stored as the first day of the month. "Now" means the reporting date.
- Return at most 50 rows, with clear column names. Round money to 2 decimals and rates to 3.
- Never select contact details. Never write anything other than a SELECT.
Use documents for policies, targets, procedures, prices and "what should we do" questions.
Questions that need both (e.g. "which customers are owed a service credit?") need both."""

ANSWER_SYSTEM = """You are OpsLens, the decision-support assistant of Northgate Digital Ltd.
Answer the question using ONLY the evidence given: the query results and the document passages.
- Quote numbers exactly as they appear in the results. Do not estimate or invent numbers.
- Cite document passages as [D1], [D2] next to the sentence that uses them.
- If the evidence does not answer the question, say so plainly and say what data would be needed.
- Lead with the direct answer, then explain what drove it when the results show that (e.g. new vs churned MRR).
- Where it helps a decision, suggest a next step, citing the policy or playbook it comes from.
- Plain British English, short paragraphs or bullet points, no jargon. Under 200 words.
- Format money as £1,234 (no pence unless under £10), rates as percentages (0.607 -> 60.7%) and months as "April 2026".
- Caveats are only for real limits of the data or the answer; do not mention evidence that was not needed.
Return JSON only: {"answer": "<markdown>", "confidence": "high"|"medium"|"low",
                   "caveats": ["<anything the reader should know, e.g. data limits>"]}"""


@dataclass
class Answer:
    question: str
    role: str
    answer: str = ""
    confidence: str = "low"
    caveats: list = field(default_factory=list)
    sql: str | None = None
    tables: list = field(default_factory=list)
    data: pd.DataFrame | None = None
    truncated: bool = False
    sources: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    provider: str = ""
    masked: bool = False
    error: str | None = None
    latency_ms: int = 0


class Assistant:
    def __init__(self, con: duckdb.DuckDBPyConnection, llm, role: str = "leadership", retriever=None):
        self.con, self.llm, self.role = con, llm, role
        self.retriever = retriever or Retriever()
        self.semantic = yaml.safe_load(open(CONFIG_DIR / "semantic_layer.yaml"))

    # ---------- context for the model ----------
    def schema_context(self) -> str:
        cols = self.con.execute("""SELECT table_schema || '.' || table_name AS t, column_name, data_type
                                   FROM information_schema.columns
                                   WHERE table_schema IN ('mart', 'core', 'ops')
                                   ORDER BY t, ordinal_position""").df()
        lines = []
        for table, desc in self.semantic["tables"].items():
            if not sql_guard.is_allowed(table, self.role):
                continue
            c = cols[cols.t == table]
            col_list = ", ".join(f"{r.column_name} {r.data_type}" for r in c.itertuples())
            lines.append(f"- {table}: {desc.strip()}\n  columns: {col_list}")
        glossary = "\n".join(f"- {k}: {' '.join(v.split())}" for k, v in self.semantic["glossary"].items())
        examples = "\n".join(f"Q: {e['question']}\nSQL: {' '.join(e['sql'].split())}"
                             for e in self.semantic["examples"]
                             if all(sql_guard.is_allowed(t, self.role) for t in _tables_in(e["sql"])))
        docs = sorted({p.doc for p in self.retriever.passages})
        return (f"Reporting date: {AS_OF_DATE.isoformat()} (latest month {AS_OF_DATE.strftime('%B %Y')}).\n"
                f"User role: {self.role}.\n\nTables you may use:\n" + "\n".join(lines) +
                f"\n\nBusiness glossary:\n{glossary}\n\nExamples:\n{examples}\n\n"
                f"Policy documents available: {', '.join(docs)}")

    # ---------- the steps ----------
    def ask(self, question: str) -> Answer:
        t0 = time.perf_counter()
        a = Answer(question=question, role=self.role, provider=self.llm.name, masked=self.llm.external)
        try:
            self._answer(question, a)
        except Exception as e:
            a.error = str(e)
            a.answer = f"Sorry, I could not answer that: {e}"
            a.confidence = "low"
        a.latency_ms = int((time.perf_counter() - t0) * 1000)
        audit.log({"role": a.role, "provider": a.provider, "question": a.question, "sql": a.sql,
                   "tables": a.tables, "rows": None if a.data is None else len(a.data),
                   "sources": a.sources, "confidence": a.confidence, "masked": a.masked,
                   "error": a.error, "latency_ms": a.latency_ms})
        return a

    def _answer(self, question: str, a: Answer):
        q_out = mask_text(question) if self.llm.external else question
        plan = self.llm.json(PLAN_SYSTEM, f"{self.schema_context()}\n\nQuestion: {q_out}")
        a.steps.append(f"Plan: data={'yes' if plan.get('needs_data') else 'no'}, "
                       f"documents={'yes' if plan.get('needs_docs') else 'no'}")
        if plan.get("clarification") and not plan.get("needs_data") and not plan.get("needs_docs"):
            a.answer, a.confidence = plan["clarification"], "low"
            return

        evidence_conf = "high"
        if plan.get("needs_data") and plan.get("sql"):
            a.sql = plan["sql"]
            try:
                a.data, a.tables, a.truncated = sql_guard.run(a.sql, self.role, self.con)
                a.steps.append(f"Query ran on {', '.join(a.tables)}: {len(a.data)} rows")
            except (sql_guard.GuardError, duckdb.Error) as e:
                a.steps.append(f"First query refused or failed: {e}")
                if isinstance(e, sql_guard.GuardError) and "role cannot read" in str(e):
                    a.answer = (f"Your role ({self.role}) does not have access to the data needed for this "
                                f"question ({e}). Please ask someone with access.")
                    return
                fix = self.llm.json(PLAN_SYSTEM, f"{self.schema_context()}\n\nQuestion: {q_out}\n\n"
                                                 f"Your previous SQL:\n{a.sql}\nfailed with: {e}\n"
                                                 "Return the same JSON with corrected SQL.")
                a.sql = fix.get("sql") or a.sql
                try:
                    a.data, a.tables, a.truncated = sql_guard.run(a.sql, self.role, self.con)
                    a.steps.append(f"Corrected query ran on {', '.join(a.tables)}: {len(a.data)} rows")
                except (sql_guard.GuardError, duckdb.Error) as e2:
                    a.steps.append(f"Corrected query failed too: {e2}")
                    evidence_conf = "low"
            if a.data is not None and a.data.empty:
                evidence_conf = "medium"

        passages = []
        if plan.get("needs_docs"):
            passages = self.retriever.search(plan.get("doc_query") or question)
            a.sources = [p.citation for p in passages]
            a.steps.append(f"Found {len(passages)} policy passage(s)")
            if not passages:
                evidence_conf = min(evidence_conf, "medium", key=CONFIDENCE_ORDER.index)

        rows = "(no data query was run)"
        if a.data is not None:
            shown = mask_frame(a.data) if self.llm.external else a.data
            rows = shown.head(EVIDENCE_ROWS).to_csv(index=False) or "(query returned no rows)"
            if a.truncated or len(a.data) > EVIDENCE_ROWS:
                rows += f"\n(showing first {min(len(a.data), EVIDENCE_ROWS)} rows only)"
        docs = "\n\n".join(f"[D{i + 1}] {p.citation}\n{p.text}" for i, p in enumerate(passages)) or "(none)"
        out = self.llm.json(ANSWER_SYSTEM, f"Question: {q_out}\n\nSQL that was run:\n{a.sql or '(none)'}\n\n"
                                           f"Query results (CSV):\n{rows}\n\nDocument passages:\n{docs}")
        a.answer = out.get("answer", "").strip() or "No answer was returned."
        a.caveats = out.get("caveats") or []
        # The model's own confidence can only lower ours, never raise it above what the evidence supports.
        model_conf = out.get("confidence", "medium") if out.get("confidence") in CONFIDENCE_ORDER else "medium"
        a.confidence = min(model_conf, evidence_conf, key=CONFIDENCE_ORDER.index)
        if a.truncated:
            a.caveats.append(f"The query returned more than {sql_guard.MAX_ROWS} rows; only the first ones were used.")


def _tables_in(sql: str) -> list[str]:
    return re.findall(r"\b((?:mart|core|ops)\.\w+)", sql)
