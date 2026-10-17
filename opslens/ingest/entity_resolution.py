"""Layer 1b: semantic integration - work out which records are the same customer.

The five systems never share an ID. Billing is the master list (if we invoice
you, you are a customer), and every other record is linked to a billing
customer_ref using, in order of trust:

  1. an exact key the systems do share (telecoms account number)
  2. the email / web domain
  3. the cleaned-up company name
  4. a fuzzy name match, only when it is clearly better than the runner-up

Every link records how it was made and how confident it is. Anything below
the auto-accept line goes to a review queue for a person to confirm, rather
than being guessed.
"""
import re

import duckdb
import pandas as pd
from rapidfuzz import fuzz, process

AUTO_ACCEPT = 92      # fuzzy score needed to link without a human
REVIEW_FLOOR = 75     # below this we do not even suggest a match
MIN_MARGIN = 5        # best match must beat the second best by this much

GENERIC_DOMAINS = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.co.uk", "icloud.com", "btinternet.com"}
LEGAL_WORDS = r"\b(ltd|limited|llp|plc|group|co|and)\b"
ABBREVIATIONS = {r"\beng\b": "engineering", r"\blogistic\b": "logistics", r"\bsolicitor\b": "solicitors"}


def normalise_name(name: str) -> str:
    n = (name or "").lower().replace("&", " ")
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    for pattern, full in ABBREVIATIONS.items():
        n = re.sub(pattern, full, n)
    n = re.sub(LEGAL_WORDS, " ", n)
    return re.sub(r"\s+", " ", n).strip()


def email_domain(email: str) -> str:
    if not email or "@" not in email:
        return ""
    d = email.split("@", 1)[1].lower().strip()
    return "" if d in GENERIC_DOMAINS else d


class Matcher:
    def __init__(self, billing: pd.DataFrame, crm: pd.DataFrame, contacts: pd.DataFrame):
        self.by_name, self.by_domain = {}, {}
        for r in billing.itertuples():
            self.by_name[normalise_name(r.customer_name)] = r.customer_ref
            if d := email_domain(r.billing_email):
                self.by_domain[d] = r.customer_ref
        self.names = list(self.by_name)

    def fuzzy(self, name: str, scorer=fuzz.token_sort_ratio):
        hits = process.extract(normalise_name(name), self.names, scorer=scorer, limit=2)
        if not hits:
            return None, 0.0, 0.0
        best = hits[0]
        margin = best[1] - (hits[1][1] if len(hits) > 1 else 0)
        return self.by_name[best[0]], best[1], margin

    def match(self, name="", domain=""):
        """Return (customer_ref, method, confidence 0-1, needs_review)."""
        if domain and domain in self.by_domain:
            return self.by_domain[domain], "domain", 1.0, False
        n = normalise_name(name)
        if n in self.by_name:
            return self.by_name[n], "name_exact", 0.98, False
        ref, score, margin = self.fuzzy(name)
        # Service desk names are often cut short ("Billingham Buil"): try prefix-tolerant matching too.
        ref2, score2, margin2 = self.fuzzy(name, scorer=fuzz.partial_ratio)
        if score2 > score and len(n) >= 8:
            ref, score, margin = ref2, score2, margin2
        if score >= AUTO_ACCEPT and margin >= MIN_MARGIN:
            return ref, "name_fuzzy", round(score / 100, 2), False
        if score >= REVIEW_FLOOR:
            return ref, "name_fuzzy", round(score / 100, 2), True
        return None, "unmatched", 0.0, True


def resolve(con: duckdb.DuckDBPyConnection) -> dict:
    billing = con.execute("SELECT * FROM raw.billing_customers").df()
    crm = con.execute("SELECT * FROM raw.crm_companies").df()
    contacts = con.execute("SELECT * FROM raw.crm_contacts").df()
    m = Matcher(billing, crm, contacts)
    rows = [{"source_system": "billing", "source_key": r.customer_ref, "source_label": r.customer_name,
             "customer_ref": r.customer_ref, "method": "master", "confidence": 1.0, "needs_review": False}
            for r in billing.itertuples()]

    # CRM companies. A company's contacts tell us its domain even when the domain field is blank.
    contact_domains = (contacts.assign(d=contacts.email.map(email_domain))
                       .groupby("company_id").d.agg(lambda s: s.mode().iat[0] if len(s.mode()) else ""))
    crm_ref = {}
    for r in crm.itertuples():
        dom = (r.domain or "").lower() or contact_domains.get(r.id, "")
        ref, method, conf, review = m.match(r.name, dom)
        crm_ref[r.id] = ref
        rows.append({"source_system": "crm", "source_key": r.id, "source_label": r.name, "customer_ref": ref,
                     "method": method, "confidence": conf, "needs_review": review})
        if dom and ref and not review:
            m.by_domain.setdefault(dom, ref)    # CRM teaches us domains billing did not have

    # Service desk: match each distinct (organisation, email domain) pair once.
    pairs = con.execute("""SELECT DISTINCT organisation, lower(split_part(requester_email, '@', 2)) AS dom
                           FROM raw.sd_tickets""").df()
    for r in pairs.itertuples():
        dom = "" if r.dom in GENERIC_DOMAINS else r.dom
        ref, method, conf, review = m.match(r.organisation, dom)
        rows.append({"source_system": "servicedesk", "source_key": f"{r.organisation}|{r.dom}",
                     "source_label": r.organisation, "customer_ref": ref, "method": method,
                     "confidence": conf, "needs_review": review})

    # Telecoms: billing stores the telecoms account number, so this is an exact join.
    tel = dict(billing.loc[billing.telecom_account_no != "", ["telecom_account_no", "customer_ref"]].values)
    for acct in con.execute("SELECT DISTINCT account_no FROM raw.tel_usage").df().account_no:
        ref = tel.get(acct)
        rows.append({"source_system": "telecoms", "source_key": acct, "source_label": acct, "customer_ref": ref,
                     "method": "account_no" if ref else "unmatched", "confidence": 1.0 if ref else 0.0,
                     "needs_review": ref is None})

    # Hosting: servers are tagged with the customer's domain.
    for dom in con.execute("SELECT DISTINCT owner_domain FROM raw.host_metrics").df().owner_domain:
        ref = m.by_domain.get(dom)
        rows.append({"source_system": "hosting", "source_key": dom, "source_label": dom, "customer_ref": ref,
                     "method": "domain" if ref else "unmatched", "confidence": 1.0 if ref else 0.0,
                     "needs_review": ref is None})

    xref = pd.DataFrame(rows)
    con.execute("CREATE SCHEMA IF NOT EXISTS integration")
    con.register("_xref", xref)
    con.execute("CREATE OR REPLACE TABLE integration.customer_xref AS SELECT * FROM _xref")
    con.unregister("_xref")
    con.execute("""CREATE OR REPLACE TABLE integration.match_review_queue AS
                   SELECT source_system, source_key, source_label, customer_ref AS suggested_customer_ref,
                          confidence, method FROM integration.customer_xref
                   WHERE needs_review ORDER BY source_system, confidence DESC""")
    return (xref[xref.source_system != "billing"]
            .groupby("source_system")
            .agg(records=("source_key", "size"), auto_linked=("needs_review", lambda s: int((~s).sum())))
            .assign(auto_link_rate=lambda d: (d.auto_linked / d.records).round(3))
            .to_dict("index"))
