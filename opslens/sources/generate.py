"""Generate the five source systems of Northgate Digital Ltd, an IT & telecoms company.

It sells telecoms, cloud hosting, data
centre space, managed IT support, software projects and hardware to small
businesses around Teesside.

Each system is written the way real SME systems tend to look: its own IDs,
its own spelling of the customer's name, its own date format. Nothing joins
cleanly, which is the problem Layer 1 has to solve.

Behind the scenes every customer has a hidden "risk" that drifts month to
month. Risky customers raise more tickets, rate support lower, use less and
pay later, and some of them leave. The platform never sees the risk, only
its footprints, so the churn model has a real (but noisy) signal to find.
"""
import json
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from opslens.settings import AS_OF_DATE, HISTORY_START, SOURCES_DIR

SEED = 42
N_CUSTOMERS = 420

PLACES = ["Tees", "Cleveland", "Riverside", "Northern", "Redcar", "Stockton", "Hartlepool",
          "Darlington", "Ironworks", "Brunel", "Transporter", "Saltburn", "Yarm", "Guisborough",
          "Ormesby", "Marton", "Acklam", "Linthorpe", "Coulby", "Eston", "Teesside", "Wilton",
          "Billingham", "Thornaby", "Ingleby", "Nunthorpe", "Skelton", "Seaton", "Norton", "Kirkleatham"]
TRADES = {
    "Engineering": "Manufacturing", "Fabrications": "Manufacturing", "Chemicals": "Manufacturing",
    "Logistics": "Logistics", "Haulage": "Logistics", "Freight": "Logistics",
    "Dental": "Healthcare", "Pharmacy": "Healthcare", "Care Homes": "Healthcare", "Clinic": "Healthcare",
    "Academy": "Education", "Primary School": "Education", "College": "Education",
    "Solicitors": "Professional Services", "Accountancy": "Professional Services",
    "Recruitment": "Professional Services", "Estates": "Professional Services",
    "Motors": "Retail", "Foods": "Retail", "Print": "Retail", "Furniture": "Retail",
    "Builders": "Construction", "Construction": "Construction", "Roofing": "Construction",
    "Veterinary": "Healthcare", "Marine": "Manufacturing", "Trust": "Charity", "Foundation": "Charity",
}
SUFFIXES = ["Ltd", "Ltd", "Ltd", "Limited", "LLP", "& Co", "Group"]
TOWNS = ["Middlesbrough", "Middlesbrough", "Stockton-on-Tees", "Redcar", "Hartlepool",
         "Darlington", "Thornaby", "Billingham", "Guisborough", "Yarm"]
POSTCODES = {"Middlesbrough": "TS1", "Stockton-on-Tees": "TS18", "Redcar": "TS10",
             "Hartlepool": "TS24", "Darlington": "DL1", "Thornaby": "TS17",
             "Billingham": "TS23", "Guisborough": "TS14", "Yarm": "TS15"}
ACCOUNT_MANAGERS = ["Sarah Collins", "James Patel", "Emma Hughes", "Tom Reid"]
FIRST = ["Amelia", "Oliver", "Isla", "Harry", "Ava", "Jack", "Mia", "George", "Sophie", "Noah",
         "Grace", "Leo", "Lily", "Arthur", "Freya", "Mohammed", "Aisha", "Daniel", "Chloe", "Ryan"]
LAST = ["Smith", "Jones", "Taylor", "Brown", "Wilson", "Khan", "Robinson", "Wright", "Thompson",
        "Walker", "White", "Hall", "Green", "Ali", "Wood", "Clarke", "Lee", "Hussain", "Bell", "Scott"]

# code: (description, monthly price or None for one-off, per-unit?)
CATALOGUE = {
    "TEL-VOIP": ("Hosted VoIP seat", 12.0, True),
    "TEL-FTTP": ("Business FTTP broadband", 45.0, False),
    "TEL-LL": ("Leased line 100Mb", 350.0, False),
    "TEL-MOB": ("Business mobile SIM", 18.0, True),
    "CLD-VM": ("Cloud virtual server", 60.0, True),
    "CLD-BKP": ("Cloud backup 1TB", 25.0, True),
    "DC-COLO": ("Data centre colocation (1U)", 120.0, True),
    "MSP-SUP": ("Managed IT support per user", 30.0, True),
    "SW-DEV": ("Software development project", None, False),
    "HW-SALE": ("Hardware supply", None, False),
}
TELECOM_CODES = {"TEL-VOIP", "TEL-FTTP", "TEL-LL", "TEL-MOB"}
HOSTING_CODES = {"CLD-VM", "DC-COLO"}
TICKET_CATEGORIES = {
    "TEL": ["Telephony", "Broadband"], "CLD": ["Hosting", "Backup"], "DC": ["Hosting"],
    "MSP": ["Email/M365", "Hardware", "Security", "User access"], "SW": ["Software"], "HW": ["Hardware"],
}
SLA_HOURS = {"P1": 4, "P2": 8, "P3": 24, "P4": 72}


@dataclass
class Sub:
    sub_id: str
    code: str
    qty: int
    price: float
    start: date
    end: date | None = None


@dataclass
class Customer:
    idx: int
    name: str
    sector: str
    town: str
    employees: int
    manager: str
    domain: str
    joined: date
    risk: float
    subs: list = field(default_factory=list)
    churned_on: date | None = None
    shock_months: int = 0
    contacts: list = field(default_factory=list)


def month_starts(start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def slug(name: str) -> str:
    base = re.sub(r"\b(ltd|limited|llp|group|& co)\b", "", name.lower())
    return re.sub(r"[^a-z0-9]+", "", base)


class Generator:
    def __init__(self, seed=SEED):
        self.rng = np.random.default_rng(seed)
        self.r = random.Random(seed)
        self.sub_seq = 0
        self.customers: list[Customer] = []
        self.invoice_lines, self.tickets, self.usage, self.hosting = [], [], [], []

    # ---------- customers ----------
    def make_customers(self):
        used = set()
        while len(self.customers) < N_CUSTOMERS:
            place, trade = self.r.choice(PLACES), self.r.choice(list(TRADES))
            name = f"{place} {trade} {self.r.choice(SUFFIXES)}"
            if (place, trade) in used:
                continue
            used.add((place, trade))
            idx = len(self.customers)
            # A third joined during the history window, the rest were already customers.
            if self.r.random() < 0.33:
                joined = HISTORY_START + timedelta(days=self.r.randint(0, (AS_OF_DATE - HISTORY_START).days - 60))
            else:
                joined = date(2017, 1, 1) + timedelta(days=self.r.randint(0, (HISTORY_START - date(2017, 1, 1)).days))
            employees = int(np.clip(self.rng.lognormal(2.8, 0.8), 3, 400))
            town = self.r.choice(TOWNS)
            c = Customer(idx, name, TRADES[trade], town, employees, self.r.choice(ACCOUNT_MANAGERS),
                         slug(name) + ".co.uk", joined, float(self.rng.beta(2, 5)))
            for _ in range(self.r.randint(1, 3)):
                fn, ln = self.r.choice(FIRST), self.r.choice(LAST)
                c.contacts.append({"firstname": fn, "lastname": ln,
                                   "email": f"{fn.lower()}.{ln.lower()}@{c.domain}",
                                   "phone": f"01642 {self.r.randint(200000, 999999)}",
                                   "jobtitle": self.r.choice(["Office Manager", "Director", "Finance Manager",
                                                              "IT Lead", "Practice Manager", "Owner"])})
            self.customers.append(c)
            self.initial_subs(c)

    def new_sub(self, c, code, start, qty=None):
        self.sub_seq += 1
        desc, price, per_unit = CATALOGUE[code]
        if qty is None:
            qty = max(1, int(c.employees * self.r.uniform(0.3, 0.9))) if per_unit else 1
            if code in {"CLD-VM", "DC-COLO", "CLD-BKP"}:
                qty = self.r.randint(1, 6)
        # Older contracts were signed at slightly different prices.
        c.subs.append(Sub(f"S{self.sub_seq:05d}", code, qty, round(price * self.r.uniform(0.9, 1.05), 2), start))

    def initial_subs(self, c):
        start = max(c.joined, HISTORY_START)
        core = self.r.choice([["TEL-VOIP", "TEL-FTTP"], ["MSP-SUP"], ["TEL-VOIP", "TEL-MOB"],
                              ["CLD-VM", "CLD-BKP"], ["MSP-SUP", "TEL-VOIP"], ["DC-COLO"], ["TEL-FTTP"]])
        if c.employees > 60 and self.r.random() < 0.4:
            core.append("TEL-LL")
        for code in core:
            self.new_sub(c, code, start)

    # ---------- month by month ----------
    def simulate(self):
        for m in month_starts(HISTORY_START, AS_OF_DATE):
            m_end = min(month_end(m), AS_OF_DATE)
            for c in self.customers:
                if c.churned_on or c.joined > m_end:
                    continue
                self.step_risk(c)
                active = [s for s in c.subs if s.start <= m_end and s.end is None]
                if not active:
                    continue
                self.month_tickets(c, m, m_end, active)
                self.month_usage(c, m, active)
                self.month_invoice(c, m, active)
                self.month_changes(c, m, m_end, active)

    def step_risk(self, c):
        c.risk = float(np.clip(c.risk + self.rng.normal(0, 0.04), 0, 1))
        if c.shock_months == 0 and self.r.random() < 0.012:
            c.shock_months = self.r.randint(2, 4)   # a bad outage or a falling-out
        if c.shock_months:
            c.risk = min(1.0, c.risk + 0.12)
            c.shock_months -= 1

    def month_tickets(self, c, m, m_end, active):
        seats = sum(s.qty for s in active if s.code in {"TEL-VOIP", "MSP-SUP"})
        n = self.rng.poisson(0.3 + seats / 40 + 3.0 * c.risk ** 1.5)
        families = sorted({s.code.split("-")[0] for s in active})
        for _ in range(n):
            opened = datetime.combine(m, datetime.min.time()) + timedelta(
                days=self.r.randint(0, (m_end - m).days), hours=self.r.randint(7, 18), minutes=self.r.randint(0, 59))
            if opened.date() > AS_OF_DATE:
                continue
            fam = self.r.choice(families)
            pr = self.r.choices(["P1", "P2", "P3", "P4"], weights=[0.04 + 0.1 * c.risk, 0.16, 0.45, 0.35])[0]
            sla = SLA_HOURS[pr]
            hours = float(self.rng.lognormal(np.log(sla * (0.32 + 0.85 * c.risk)), 0.5))
            resolved = opened + timedelta(hours=hours)
            as_of_end = datetime.combine(AS_OF_DATE, datetime.max.time().replace(microsecond=0))
            status = "Resolved"
            if resolved > as_of_end:
                resolved, status = None, "Open"
            csat = None
            if status == "Resolved" and self.r.random() < 0.45:
                csat = int(np.clip(round(self.rng.normal(4.7 - 2.6 * c.risk, 0.7)), 1, 5))
            contact = self.r.choice(c.contacts)
            self.tickets.append({
                "opened": opened, "resolved": resolved, "status": status, "priority": pr,
                "category": self.r.choice(TICKET_CATEGORIES[fam]), "csat": csat,
                "contact_email": contact["email"], "customer": c,
                "first_response_mins": int(self.rng.gamma(2, 12 if pr in ("P1", "P2") else 45)),
            })

    def month_usage(self, c, m, active):
        tel = [s for s in active if s.code in TELECOM_CODES]
        if tel:
            seats = sum(s.qty for s in tel if s.code in {"TEL-VOIP", "TEL-MOB"}) or 1
            calls = int(seats * self.rng.normal(95, 15) * (1 - 0.55 * c.risk))
            calls = max(calls, 0)
            self.usage.append({"customer": c, "month": m, "calls": calls,
                               "minutes": int(calls * self.rng.normal(3.4, 0.4)),
                               "dropped": int(self.rng.binomial(max(calls, 1), 0.004 + 0.02 * c.risk)),
                               "data_gb": round(float(self.rng.gamma(2, 20 * seats ** 0.5)), 1)})
        hosts = [s for s in active if s.code in HOSTING_CODES]
        for s in hosts:
            for i in range(min(s.qty, 3)):
                self.hosting.append({"customer": c, "month": m, "server": f"{s.code[:2]}-{s.sub_id}-{i + 1}",
                                     "uptime": round(100 - float(self.rng.gamma(1, 0.008 + 0.15 * c.risk ** 3)), 3),
                                     "incidents": int(self.rng.poisson(0.1 + 1.2 * c.risk)),
                                     "cpu": round(float(np.clip(self.rng.normal(38, 15), 2, 99)), 1)})

    def month_invoice(self, c, m, active):
        inv_date = month_end(m)
        if inv_date > AS_OF_DATE:
            inv_date = AS_OF_DATE
        lines = [(s.code, CATALOGUE[s.code][0], s.qty, s.price) for s in active]
        if self.r.random() < 0.006 + 0.00005 * c.employees:
            lines.append(("SW-DEV", "Software development project", 1, round(self.r.uniform(1500, 15000), -2)))
        if self.r.random() < 0.03:
            lines.append(("HW-SALE", self.r.choice(["Laptops", "Desk phones", "Firewall", "Switch", "Monitors"]),
                          self.r.randint(1, 8), round(self.r.uniform(150, 1200), 2)))
        due = inv_date + timedelta(days=30)
        days_to_pay = max(0, int(self.rng.normal(22 + 45 * c.risk ** 2, 8)))
        if c.risk > 0.5 and self.r.random() < 0.12:
            days_to_pay += self.r.randint(40, 120)    # disputed or simply not paid
        paid = inv_date + timedelta(days=days_to_pay)
        if paid > AS_OF_DATE:
            paid = None
        self.invoice_lines.append({"customer": c, "date": inv_date, "due": due, "paid": paid, "lines": lines})

    def month_changes(self, c, m, m_end, active):
        hazard = 0.0015 + 0.07 * c.risk ** 2.5
        if c.risk > 0.35 and self.r.random() < hazard:
            c.churned_on = m_end
            for s in active:
                s.end = m_end
            return
        if self.r.random() < 0.012 * (1 - c.risk):           # upsell
            codes = [k for k, v in CATALOGUE.items() if v[1] and k not in {s.code for s in active}]
            if codes:
                self.new_sub(c, self.r.choice(codes), m_end + timedelta(days=1))
        elif len(active) > 1 and self.r.random() < 0.008 * c.risk:   # downsell
            self.r.choice(active).end = m_end

    # ---------- write each system in its own style ----------
    def billing_name(self, c):
        n = c.name.upper() if self.r.random() < 0.5 else c.name
        return n.replace(" LTD", " LIMITED") if self.r.random() < 0.6 else n

    def crm_name(self, c):
        n = c.name
        roll = self.r.random()
        if roll < 0.12:
            n = n.replace(" Ltd", "").replace(" Limited", "")
        elif roll < 0.2:
            n = n.replace("Engineering", "Eng.").replace("Logistics", "Logistic").replace("Solicitors", "Solicitor")
        elif roll < 0.24:
            # a typo, as typed into a CRM in a hurry
            i = self.r.randint(1, len(n) - 3)
            n = n[:i] + n[i + 1] + n[i] + n[i + 2:]
        return n

    def desk_name(self, c):
        words = c.name.split()
        return self.r.choice([c.name, " ".join(words[:2]), words[0] + " " + words[1][:4], c.name.lower()])

    def write(self):
        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        cust_ref = {c.idx: f"CUST{c.idx + 1:04d}" for c in self.customers}
        tel_acct = {c.idx: f"TV-{500000 + c.idx * 7:06d}" for c in self.customers}
        has_tel = {c.idx for c in self.customers if any(s.code in TELECOM_CODES for s in c.subs)}

        # 1. CRM: a HubSpot-style JSON API export. Some customers were never added.
        companies, contacts = [], []
        for c in self.customers:
            if self.r.random() < 0.04:
                continue
            cid = f"HS-{10000 + c.idx * 13}"
            companies.append({"id": cid, "properties": {
                "name": self.crm_name(c), "domain": c.domain if self.r.random() < 0.9 else "",
                "industry": c.sector, "city": c.town, "numberofemployees": str(c.employees),
                "hubspot_owner": c.manager, "createdate": f"{c.joined.isoformat()}T09:00:00Z",
                "lifecyclestage": "former_customer" if c.churned_on else "customer"}})
            for i, ct in enumerate(c.contacts):
                contacts.append({"id": f"{cid}-C{i + 1}", "company_id": cid, **ct})
        (SOURCES_DIR / "crm").mkdir(exist_ok=True)
        json.dump({"results": companies}, open(SOURCES_DIR / "crm/companies.json", "w"), indent=1)
        json.dump({"results": contacts}, open(SOURCES_DIR / "crm/contacts.json", "w"), indent=1)

        # 2. Billing: finance CSV exports, UK date format, its own customer refs.
        (SOURCES_DIR / "billing").mkdir(exist_ok=True)
        bc = []
        for c in self.customers:
            generic = self.r.random() < 0.15
            bc.append({"customer_ref": cust_ref[c.idx], "customer_name": self.billing_name(c),
                       "billing_email": (f"{slug(c.name)[:10]}accounts@gmail.com" if generic else f"accounts@{c.domain}"),
                       "postcode": f"{POSTCODES[c.town]} {self.r.randint(1, 9)}{self.r.choice('ABDEFGHJLNPQRSTUWXYZ')}{self.r.choice('ABDEFGHJLNPQRSTUWXYZ')}",
                       "telecom_account_no": tel_acct[c.idx] if c.idx in has_tel else "",
                       "payment_terms_days": 30})
        pd.DataFrame(bc).to_csv(SOURCES_DIR / "billing/customers.csv", index=False)

        subs = [{"subscription_id": s.sub_id, "customer_ref": cust_ref[c.idx], "product_code": s.code,
                 "quantity": s.qty, "monthly_price": s.price, "start_date": s.start.strftime("%d/%m/%Y"),
                 "end_date": s.end.strftime("%d/%m/%Y") if s.end else ""}
                for c in self.customers for s in c.subs if s.start <= AS_OF_DATE]
        pd.DataFrame(subs).to_csv(SOURCES_DIR / "billing/subscriptions.csv", index=False)

        rows, inv_no = [], 100000
        for inv in sorted(self.invoice_lines, key=lambda x: (x["date"], x["customer"].idx)):
            inv_no += 1
            c = inv["customer"]
            status = "Paid" if inv["paid"] else ("Overdue" if inv["due"] < AS_OF_DATE else "Awaiting payment")
            for code, desc, qty, price in inv["lines"]:
                rows.append({"invoice_no": f"INV-{inv_no}", "invoice_date": inv["date"].strftime("%d/%m/%Y"),
                             "customer_ref": cust_ref[c.idx], "product_code": code, "description": desc,
                             "quantity": qty, "unit_price": price, "line_total_gbp": round(qty * price, 2),
                             "due_date": inv["due"].strftime("%d/%m/%Y"), "status": status,
                             "paid_date": inv["paid"].strftime("%d/%m/%Y") if inv["paid"] else ""})
        pd.DataFrame(rows).to_csv(SOURCES_DIR / "billing/invoice_lines.csv", index=False)

        # 3. Service desk: free-text organisation names, ISO timestamps.
        (SOURCES_DIR / "servicedesk").mkdir(exist_ok=True)
        trows = []
        for i, t in enumerate(sorted(self.tickets, key=lambda x: x["opened"])):
            c = t["customer"]
            trows.append({"ticket_id": f"SD-{20000 + i}", "organisation": self.desk_name(c),
                          "requester_email": t["contact_email"] if self.r.random() < 0.85 else f"{self.r.choice(FIRST).lower()}{self.r.randint(1, 99)}@outlook.com",
                          "priority": t["priority"], "category": t["category"], "status": t["status"],
                          "opened_at": t["opened"].isoformat(timespec="minutes"),
                          "first_response_at": (t["opened"] + timedelta(minutes=t["first_response_mins"])).isoformat(timespec="minutes"),
                          "resolved_at": t["resolved"].isoformat(timespec="minutes") if t["resolved"] else "",
                          "csat_score": t["csat"] if t["csat"] is not None else ""})
        pd.DataFrame(trows).to_csv(SOURCES_DIR / "servicedesk/tickets.csv", index=False)
        pd.DataFrame({"ticket_id": [r["ticket_id"] for r in trows],
                      "customer_ref": [cust_ref[t["customer"].idx] for t in sorted(self.tickets, key=lambda x: x["opened"])]}
                     ).to_csv(SOURCES_DIR / "_answer_key_tickets.csv", index=False)

        # 4. Telecoms platform: monthly call records keyed by telecoms account number.
        (SOURCES_DIR / "telecoms").mkdir(exist_ok=True)
        pd.DataFrame([{"account_no": tel_acct[u["customer"].idx], "period": u["month"].strftime("%Y-%m"),
                       "calls": u["calls"], "minutes": u["minutes"], "dropped_calls": u["dropped"],
                       "data_gb": u["data_gb"]} for u in self.usage]).to_csv(SOURCES_DIR / "telecoms/usage_monthly.csv", index=False)

        # 5. Hosting monitoring: JSON lines, servers tagged with the customer's domain.
        (SOURCES_DIR / "hosting").mkdir(exist_ok=True)
        with open(SOURCES_DIR / "hosting/server_metrics.jsonl", "w") as f:
            for h in self.hosting:
                f.write(json.dumps({"server_id": h["server"], "owner_domain": h["customer"].domain,
                                    "month": h["month"].strftime("%Y-%m"), "uptime_pct": h["uptime"],
                                    "incidents": h["incidents"], "cpu_avg_pct": h["cpu"]}) + "\n")

        # Answer key for tests only: which source record belongs to which real customer.
        pd.DataFrame([{"true_customer": c.idx, "customer_ref": cust_ref[c.idx], "domain": c.domain,
                       "churned_on": c.churned_on} for c in self.customers]).to_csv(SOURCES_DIR / "_answer_key.csv", index=False)


def generate():
    g = Generator()
    g.make_customers()
    g.simulate()
    g.write()
    churned = sum(1 for c in g.customers if c.churned_on)
    print(f"Generated {len(g.customers)} customers ({churned} churned), {len(g.tickets)} tickets, "
          f"{len(g.invoice_lines)} invoices into {SOURCES_DIR}")


if __name__ == "__main__":
    generate()
