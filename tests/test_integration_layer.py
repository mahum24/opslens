import pandas as pd

from opslens.ingest.entity_resolution import email_domain, normalise_name
from opslens.settings import SOURCES_DIR


def test_name_normalisation_ignores_case_legal_words_and_abbreviations():
    assert normalise_name("TEES ENGINEERING LIMITED") == normalise_name("Tees Eng. Ltd") == "tees engineering"
    assert normalise_name("Yarm Print & Co") == "yarm print"


def test_generic_email_domains_are_not_used_for_matching():
    assert email_domain("accounts@gmail.com") == ""
    assert email_domain("jo@teesprint.co.uk") == "teesprint.co.uk"


def test_tickets_are_linked_to_the_right_customer(con):
    truth = pd.read_csv(SOURCES_DIR / "_answer_key_tickets.csv")
    t = con.execute("SELECT ticket_id, customer_ref FROM core.fct_ticket").df().merge(truth, on="ticket_id",
                                                                                         suffixes=("", "_true"))
    linked = t[t.customer_ref.notna()]
    assert len(linked) / len(t) >= 0.99, "coverage"
    assert (linked.customer_ref == linked.customer_ref_true).mean() >= 0.995, "precision"


def test_uncertain_matches_go_to_review_not_into_the_numbers(con):
    review = con.execute("SELECT count(*) FROM integration.match_review_queue").fetchone()[0]
    leaked = con.execute("""SELECT count(*) FROM raw.sd_tickets t
                            JOIN integration.customer_xref x
                              ON x.source_system = 'servicedesk' AND x.needs_review
                             AND x.source_key = t.organisation || '|' || lower(split_part(t.requester_email, '@', 2))
                            JOIN core.fct_ticket f USING (ticket_id)
                            WHERE f.customer_ref IS NOT NULL""").fetchone()[0]
    assert review > 0 and leaked == 0
