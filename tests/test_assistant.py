import pandas as pd

from conftest import FakeLLM
from opslens.assistant.assistant import Assistant
from opslens.governance.pii import mask_frame, mask_text

TOP_RISK = {"needs_data": True, "needs_docs": True, "doc_query": "review call checklist",
            "sql": "SELECT customer_name, current_mrr FROM mart.customer_health WHERE risk_band = 'High' LIMIT 5"}


def test_answer_carries_its_evidence(con):
    a = Assistant(con, FakeLLM([TOP_RISK]), role="leadership").ask("Who should we call first?")
    assert a.error is None and a.tables == ["mart.customer_health"]
    assert len(a.data) == 5 and a.sources and a.confidence == "high"


def test_role_without_access_is_told_so_and_no_data_is_sent(con):
    llm = FakeLLM([TOP_RISK])
    a = Assistant(con, llm, role="service_desk").ask("Who should we call first?")
    assert "does not have access" in a.answer and a.data is None
    assert len(llm.prompts) == 1          # only the planning call, never an answer call with data


def test_broken_sql_gets_one_repair_attempt(con):
    bad = {**TOP_RISK, "sql": "SELECT no_such_column FROM mart.customer_health"}
    a = Assistant(con, FakeLLM([bad, TOP_RISK]), role="leadership").ask("Who should we call first?")
    assert a.data is not None and any("First query" in s for s in a.steps)


def test_personal_data_is_masked_for_external_models(con):
    llm = FakeLLM([{"needs_data": False, "needs_docs": True, "doc_query": "credit", "sql": None}])
    Assistant(con, llm).ask("Customer jo.bloggs@teesprint.co.uk on 01642 123456 at TS1 4AB asked about credits")
    assert all("jo.bloggs" not in p and "123456" not in p and "TS1 4AB" not in p for p in llm.prompts)


def test_mask_helpers():
    assert mask_text("mail a@b.co.uk or call 07700 900123") == "mail [email] or call [phone]"
    df = mask_frame(pd.DataFrame({"billing_email": ["x@y.com"], "note": ["ring 01642 555123"]}))
    assert df.billing_email.iat[0] == "[masked]" and df.note.iat[0] == "ring [phone]"
