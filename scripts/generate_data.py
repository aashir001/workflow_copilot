"""Generate the synthetic company (users, CRM) and the 200 labeled evaluation requests.

Usage:  python scripts/generate_data.py            (deterministic, seed 42)

Eval design notes
* 12 intents x 15 cases = 180, plus 20 out-of-scope requests = 200.
* Each case carries the expected intent AND the expected final outcome (completed / rejected /
  denied / escalated), so the eval measures routing accuracy and end-to-end task correctness.
* Surface noise: filler prefixes/suffixes, lowercase, and typos (never inside ids/emails/amounts).
* A fixed 40% 'dev' / 60% 'test' split is stored in the file. Routing thresholds and SOP wording
  may only be tuned by looking at dev failures; 'test' is the number to quote.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
rng = random.Random(42)

NAMES = [
    "Aarav Mehta", "Priya Sharma", "Rohan Kapoor", "Neha Gupta", "Vikram Singh", "Ananya Iyer", "Karan Malhotra",
    "Sneha Reddy", "Arjun Nair", "Isha Verma", "Rahul Joshi", "Meera Pillai", "Siddharth Rao", "Kavya Menon",
    "Aditya Bansal", "Pooja Desai", "Nikhil Agarwal", "Divya Chopra", "Manish Tiwari", "Shruti Bhatt",
    "Harsh Vardhan", "Tanvi Kulkarni", "Gaurav Saxena", "Ritu Arora", "Varun Khanna", "Simran Kaur",
    "Deepak Yadav", "Nisha Patel", "Abhishek Jain", "Lakshmi Narayan", "Yash Thakur", "Bhavna Shah",
    "Mohit Sinha", "Aditi Rane", "Kabir Dutta", "Sana Qureshi", "Tarun Mishra", "Ira Banerjee", "Dev Anand", "Zoya Ali",
]
COMPANIES = ["Northwind Traders", "Bluepeak Logistics", "Cedar & Pine Retail", "Helios Solar", "Orchid Pharma",
             "Zenith Auto Parts", "Lotus Hospitality", "Quartz Analytics", "Ridge Outdoor Gear", "Sapphire Textiles"]


def build_users() -> list[dict]:
    it = iter(NAMES)
    users: list[dict] = []

    def add(role: str, dept: str, manager_id=None, status="active") -> dict:
        n = len(users) + 1
        name = next(it)
        u = {"user_id": f"U{n:03d}", "name": name, "email": name.lower().replace(" ", ".") + "@acme.example",
             "role": role, "department": dept, "manager_id": manager_id, "status": status, "access": {}}
        users.append(u)
        return u

    managers = [add("manager", d) for d in ["Sales", "Support", "Finance", "Engineering", "Operations", "Marketing", "Data", "Product"]]
    for _ in range(3):
        add("hr", "HR")
    for _ in range(3):
        add("finance", "Finance")
    for _ in range(3):
        add("it_admin", "IT")
    for _ in range(5):
        add("support_agent", "Support")
    for i in range(14):
        m = managers[i % len(managers)]
        u = add("employee", m["department"], m["user_id"])
        u["access"] = {"crm": rng.choice(["viewer", "editor"])}
    for i in range(4):  # new hires, not yet active
        m = managers[i % len(managers)]
        add("employee", m["department"], m["user_id"], status="pending_start")
    return users


def build_customers() -> list[dict]:
    customers = []
    oid = 50001
    for i in range(30):
        orders = []
        for _ in range(rng.randint(2, 3)):
            orders.append({"order_id": f"ORD-{oid}", "amount": float(rng.randrange(40, 2500, 5)), "status": "delivered"})
            oid += 1
        customers.append({
            "customer_id": f"CUST-{1001 + i}", "name": f"{COMPANIES[i % len(COMPANIES)]} #{i // len(COMPANIES) + 1}",
            "tier": rng.choice(["standard", "gold", "platinum"]), "email": f"accounts{i}@client{i}.example",
            "phone": f"+91 {rng.randrange(70000, 99999)} {rng.randrange(10000, 99999)}",
            "billing_address": f"{rng.randrange(1, 200)} {rng.choice(['MG Road', 'Park Street', 'Linking Road', 'Anna Salai'])}, "
                               f"{rng.choice(['Bengaluru', 'Kolkata', 'Mumbai', 'Chennai'])}",
            "orders": orders})
    return customers


# ------------------------------------------------------------------------------------------
# templates
# ------------------------------------------------------------------------------------------
T = {
    "password_reset": [
        "I'm locked out of my account and need my password reset",
        "forgot my password, can you reset it please",
        "my login isn't working - need a new password for my CRM account",
        "Can someone help me sign back in? I can't remember my credentials",
        "password expired and the self-service page keeps failing, please reset",
        "account locked after too many failed attempts, need it unlocked and reset",
        "Unable to log in since this morning. I think I need a credentials reset.",
        "please reset the password for my work account",
    ],
    "password_reset_other": [
        "Please reset the password for {name}, they are locked out of their account",
        "{name} forgot their password and cannot log in, can you reset it",
        "can you unlock the account of {name}? too many failed login attempts",
    ],
    "access_request": [
        "I need {role} to {system} to do my job",
        "Please grant me {role} on {system}",
        "Requesting {role} for {system} - needed for the quarterly review project",
        "Can I get permissions to {system}? {role} is enough",
        "need entry to {system}, {role} should do",
        "requesting an access upgrade on {system}, {role}",
    ],
    "access_request_other": [
        "Could you provision {role} to {system} for {name}?",
        "{name} needs access to {system} ({role}) starting this week",
    ],
    "employee_onboarding": [
        "New hire {name} starts Monday - please set up their accounts and laptop",
        "Onboarding {name}: need accounts, email and tools provisioned before day one",
        "Please start the onboarding checklist for our new joiner {name}",
        "{name} is joining the team next week and needs their standard access set up",
        "setup request for new employee {name} - day one provisioning",
        "We have a new team member, {name}, joining on the 1st. Kindly kick off onboarding.",
        "Can you provision a workstation and accounts for new starter {name}?",
        "onboard {name} please - first day is next Monday",
    ],
    "employee_onboarding_missing": [
        "A new hire is starting Monday, please set up their accounts",
        "our new joiner needs day one provisioning, kick off onboarding please",
    ],
    "employee_offboarding": [
        "{name} is leaving the company on Friday - please revoke all access",
        "Offboarding request for {name}: last working day is the 30th, disable accounts",
        "Please deactivate accounts for {name} who has resigned",
        "Employee exit: {name}. Remove access to all systems",
        "{name}'s last day is tomorrow, start the leaver process",
        "terminate system access for {name} effective end of day",
        "We need to offboard {name} - they're departing this week",
        "Revoke access and close accounts for departing employee {name}.",
    ],
    "expense_reimbursement": [
        "Please reimburse my client dinner expense of ${amount}",
        "I'd like to claim ${amount} for travel expenses from the Mumbai trip",
        "submitting an expense report: taxi and hotel totalling ${amount}",
        "Need reimbursement of ${amount} for the conference registration I paid out of pocket",
        "expense claim - ${amount} for team lunch, receipts attached",
        "can you process my reimbursement for ${amount}? I paid for the office supplies myself",
        "Pending reimbursement ${amount} for airfare, please process",
        "I spent ${amount} on a client visit and need to be paid back",
    ],
    "refund_request": [
        "Customer {cust} is asking for a refund on order {order}",
        "Please issue a refund of ${amount} for order {order}",
        "Refund request: order {order} was damaged on arrival, customer wants ${amount} back",
        "{cust} wants their money back for {order}, product never arrived",
        "process a refund for {order} - duplicate charge of ${amount}",
        "Client was charged twice on {order}. Please refund ${amount}.",
        "issue full refund for order {order}, customer cancelled",
        "refund ${amount} to {cust} against {order}, service was not delivered",
    ],
    "customer_account_update": [
        "Please update the email for customer {cust} to {email}",
        "{cust} has a new phone number, change it to {phone}",
        "change contact email on {cust}'s account to {email}",
        "Customer {cust} called - update their phone to {phone}",
        "update billing address for {cust} to {address}",
        "Please correct the email address on file for {cust}: {email}",
        "Account {cust}: new mobile number is {phone}, update the record",
        "modify the billing address for customer {cust} to {address}",
    ],
    "incident_report": [
        "Production is down - checkout API returning 500s for all users",
        "We have a security incident: suspicious logins detected on the admin portal",
        "Major outage: the customer portal is unreachable",
        "Reporting an incident - payment service is failing intermittently",
        "Service degradation on the reporting dashboard, queries are timing out",
        "ransomware warning popped up on a finance laptop, please treat as a security incident",
        "Site is down since 10am, customers are complaining",
        "incident: data pipeline failed overnight and dashboards show stale numbers",
    ],
    "software_license": [
        "I need a {software} license for my new project",
        "Please assign a {software} seat to me",
        "Request for {software} subscription - our team is starting analytics work",
        "Can I get {software} installed and licensed on my machine?",
        "need access to a {software} license, trial expired",
        "Software request: {software} licence for the next 12 months",
        "please buy me a {software} license",
        "Requesting {software} licence renewal, it expires this month",
    ],
    "data_export_request": [
        "Customer {cust} submitted a data subject access request - export all their data",
        "Please export all personal data we hold on {cust}, GDPR request",
        "{cust} wants a copy of their data. Can you run a data export?",
        "privacy request: provide a full data export for customer {cust}",
        "DSAR received from {cust}, need records exported",
        "Customer {cust} asked for everything we store about them, please compile an export",
        "right-of-access request for {cust} - export their account data",
        "Need a downloadable export of {cust}'s personal information for a compliance request",
    ],
    "vpn_connectivity": [
        "VPN keeps disconnecting every few minutes",
        "I can't connect to the VPN from home, it times out",
        "VPN client shows authentication failed",
        "Unable to reach internal sites - think the VPN is broken",
        "my vpn connection is very slow and drops constantly",
        "GlobalProtect won't connect after the latest update",
        "cannot establish tunnel to the corporate network from my laptop",
        "remote connection problems: VPN error 809",
    ],
    "hardware_request": [
        "I need a new {device}, mine stopped working",
        "Please order a {device} for me",
        "Request for a {device} - current one is broken",
        "Can I get a replacement {device}? It's faulty.",
        "Looking for an extra {device} for my desk",
        "hardware request: {device} needed for remote setup",
        "My {device} is damaged, need a replacement",
        "please arrange a {device} for the new project room",
    ],
}
OUT_OF_SCOPE = [
    "What's the weather like in Jaipur today", "Book me a flight to Goa next weekend", "Can you order lunch for the team",
    "Write a poem about Mondays", "Please schedule a meeting with the CFO", "I want to discuss my appraisal and promotion",
    "Where is the nearest coffee machine?", "Translate this paragraph into French", "How do I apply for parental leave?",
    "need a parking pass for the office garage", "Tell me a joke", "reserve conference room 4B for 3pm",
    "what is the capital of Australia", "update my emergency contact details", "can I get a salary certificate for my bank loan",
    "my chair is uncomfortable, ergonomic assessment please", "summarize yesterday's board meeting",
    "Who won the cricket match last night?", "plan the team offsite agenda", "send a thank-you note to our vendor",
]
ROLE_PHRASES = [("admin access", "admin"), ("read-only access", "viewer"), ("editor access", "editor")]
SYSTEM_PHRASES = ["the CRM", "Jira", "the data warehouse", "the finance share drive", "the sales dashboards", "GitHub"]
SOFTWARE = ["Tableau", "Alteryx", "Figma", "Adobe Acrobat Pro", "PyCharm Professional", "Microsoft Visio", "Power BI Pro", "Zoom", "Postman"]
DEVICES = ["laptop", "monitor", "keyboard", "mouse", "headset", "docking station", "webcam", "laptop charger"]
PREFIX = ["", "", "", "Hi team, ", "Hello, ", "Urgent: ", "Quick one - ", "Hey, "]
SUFFIX = ["", "", "", " Thanks!", " Thank you.", " Please advise.", " ASAP."]


def noisy(text: str) -> str:
    t = rng.choice(PREFIX) + text + rng.choice(SUFFIX)
    if rng.random() < 0.15:
        t = t.lower()
    if rng.random() < 0.20:  # typos: swap two adjacent letters in 1-2 plain words
        words = t.split(" ")
        idx = [i for i, w in enumerate(words) if len(w) > 5 and re.fullmatch(r"[A-Za-z]+", w)]
        for i in rng.sample(idx, min(len(idx), rng.choice([1, 2]))):
            w = words[i]
            j = rng.randrange(1, len(w) - 2)
            words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:]
        t = " ".join(words)
    return t


def main() -> None:
    users, customers = build_users(), build_customers()
    by_role: dict[str, list[dict]] = {}
    for u in users:
        by_role.setdefault(u["role"], []).append(u)
    employees = [u for u in by_role["employee"] if u["status"] == "active"]
    newhires = [u for u in by_role["employee"] if u["status"] == "pending_start"]
    managers, hr, fin, itadm, sup = by_role["manager"], by_role["hr"], by_role["finance"], by_role["it_admin"], by_role["support_agent"]
    uid = {u["user_id"]: u for u in users}
    mgr_of = lambda u: uid[u["manager_id"]]
    orders = [(c, o) for c in customers for o in c["orders"]]
    cases: list[dict] = []

    def add(intent, text, requester, status, approver=None, decision=None, note=""):
        cases.append({"text": noisy(text), "requester_id": requester["user_id"], "expected_intent": intent,
                      "expected_status": status, "approver_id": approver["user_id"] if approver else None,
                      "approval_decision": decision, "note": note})

    def decision():
        return ("approve", "completed") if rng.random() < 0.8 else ("reject", "rejected")

    # ---- password_reset (15): 10 self, 3 manager-for-report, 2 denied (employee for someone else) -------------
    for i in range(10):
        add("password_reset", T["password_reset"][i % 8], rng.choice(employees + managers + fin + sup), "completed")
    for i in range(3):
        rep = rng.choice(employees)
        add("password_reset", T["password_reset_other"][i % 3].format(name=rep["name"]), mgr_of(rep), "completed", note="manager for report")
    for i in range(2):
        a, b = rng.sample(employees, 2)
        add("password_reset", T["password_reset_other"][i].format(name=b["name"]), a, "denied", note="employee acting for peer")

    # ---- access_request (15) -------------------------------------------------------------------------------
    for i in range(10):
        emp = rng.choice(employees)
        rp, _ = rng.choice(ROLE_PHRASES)
        d, st = decision()
        add("access_request", T["access_request"][i % 6].format(role=rp, system=rng.choice(SYSTEM_PHRASES)), emp, st, mgr_of(emp), d, "self, line manager approves")
    for i in range(3):
        emp = rng.choice(employees)
        rp, _ = rng.choice(ROLE_PHRASES)
        d, st = decision()
        add("access_request", T["access_request_other"][i % 2].format(role=rp, system=rng.choice(SYSTEM_PHRASES), name=emp["name"]),
            mgr_of(emp), st, rng.choice(itadm), d, "manager for report, IT admin approves")
    for i in range(2):
        a, b = rng.sample(employees, 2)
        rp, _ = rng.choice(ROLE_PHRASES)
        add("access_request", T["access_request_other"][i % 2].format(role=rp, system=rng.choice(SYSTEM_PHRASES), name=b["name"]), a, "denied", note="peer")

    # ---- onboarding (15): 11 ok, 2 missing name, 2 denied (employee requester) ------------------------------
    for i in range(11):
        nh = newhires[i % 4]
        req = rng.choice([mgr_of(nh), mgr_of(nh), rng.choice(hr), rng.choice(itadm)])
        add("employee_onboarding", T["employee_onboarding"][i % 8].format(name=nh["name"]), req, "completed")
    for i in range(2):
        add("employee_onboarding", T["employee_onboarding_missing"][i], rng.choice(managers + hr), "escalated", note="missing name")
    for i in range(2):
        nh = newhires[i]
        add("employee_onboarding", T["employee_onboarding"][i].format(name=nh["name"]), rng.choice(employees), "denied", note="employee cannot onboard")

    # ---- offboarding (15): 12 sensitive ok/reject, 3 denied -------------------------------------------------
    for i in range(12):
        tgt = rng.choice(employees)
        req = rng.choice([mgr_of(tgt), mgr_of(tgt), rng.choice(hr)])
        approver = rng.choice([u for u in hr + itadm if u["user_id"] != req["user_id"]])
        d, st = decision()
        add("employee_offboarding", T["employee_offboarding"][i % 8].format(name=tgt["name"]), req, st, approver, d)
    for i in range(3):
        tgt = rng.choice(employees)
        add("employee_offboarding", T["employee_offboarding"][i].format(name=tgt["name"]), rng.choice(employees), "denied", note="employee cannot offboard")

    # ---- expense (15) ---------------------------------------------------------------------------------------
    for i in range(15):
        add("expense_reimbursement", T["expense_reimbursement"][i % 8].format(amount=f"{rng.randrange(20, 3000, 5):,}"),
            rng.choice(employees + managers + sup), "completed")

    # ---- refund (15): 10 ok/reject, 2 over-refund -> tool_error escalate, 3 denied ----------------------------
    for i in range(10):
        c, o = rng.choice(orders)
        req = rng.choice(sup)
        approver = rng.choice(fin + managers)
        amt = int(o["amount"] * rng.choice([0.5, 1.0]))
        d, st = decision()
        add("refund_request", T["refund_request"][i % 8].format(cust=c["customer_id"], order=o["order_id"], amount=f"{max(amt, 5):,}"),
            req, st, approver, d)
    for i in range(2):
        c, o = rng.choice(orders)
        add("refund_request", T["refund_request"][1].format(cust=c["customer_id"], order=o["order_id"], amount=f"{int(o['amount']) + 500:,}"),
            rng.choice(sup), "escalated", rng.choice(fin), "approve", "refund exceeds order value -> tool_error")
    for i in range(3):
        c, o = rng.choice(orders)
        add("refund_request", T["refund_request"][i].format(cust=c["customer_id"], order=o["order_id"], amount=f"{int(o['amount']):,}"),
            rng.choice(employees), "denied", note="employee cannot request refunds")

    # ---- customer_account_update (15): 12 ok/reject, 3 denied -------------------------------------------------
    for i in range(12):
        c = rng.choice(customers)
        req = rng.choice(sup + fin)
        approver = rng.choice([m for m in managers if m["user_id"] != req["user_id"]])
        d, st = decision()
        add("customer_account_update", T["customer_account_update"][i % 8].format(
            cust=c["customer_id"], email=f"billing{rng.randrange(100)}@newmail{rng.randrange(100)}.example",
            phone=f"+91 {rng.randrange(70000, 99999)} {rng.randrange(10000, 99999)}",
            address=f"{rng.randrange(1, 90)} Lake View Road, Pune"), req, st, approver, d)
    for i in range(3):
        c = rng.choice(customers)
        add("customer_account_update", T["customer_account_update"][i].format(
            cust=c["customer_id"], email="new@mail.example", phone="+91 98765 43210", address="5 Hill Street, Pune"),
            rng.choice(employees), "denied", note="employee cannot edit customers")

    # ---- incident (15) -----------------------------------------------------------------------------------------
    for i in range(15):
        add("incident_report", T["incident_report"][i % 8], rng.choice(employees + managers + sup + fin), "completed")

    # ---- software license (15) -------------------------------------------------------------------------------------
    for i in range(15):
        add("software_license", T["software_license"][i % 8].format(software=rng.choice(SOFTWARE)), rng.choice(employees + managers + fin), "completed")

    # ---- data export (15): 12 ok/reject, 3 denied ---------------------------------------------------------------------
    for i in range(12):
        c = rng.choice(customers)
        req = rng.choice(sup + managers)
        approver = rng.choice([u for u in itadm + managers if u["user_id"] != req["user_id"]])
        d, st = decision()
        add("data_export_request", T["data_export_request"][i % 8].format(cust=c["customer_id"]), req, st, approver, d)
    for i in range(3):
        c = rng.choice(customers)
        add("data_export_request", T["data_export_request"][i].format(cust=c["customer_id"]), rng.choice(employees), "denied", note="employee cannot request exports")

    # ---- vpn (15), hardware (15) ------------------------------------------------------------------------------------------
    for i in range(15):
        add("vpn_connectivity", T["vpn_connectivity"][i % 8], rng.choice(employees + managers + sup), "completed")
    for i in range(15):
        add("hardware_request", T["hardware_request"][i % 8].format(device=rng.choice(DEVICES)), rng.choice(employees + managers + sup), "completed")

    # ---- out of scope (20) --------------------------------------------------------------------------------------------------
    for t in OUT_OF_SCOPE:
        add("out_of_scope", t, rng.choice(employees + managers), "escalated", note="no SOP applies")

    rng.shuffle(cases)
    for i, c in enumerate(cases):
        c["case_id"] = f"C{i + 1:03d}"
        c["split"] = "dev" if i < 80 else "test"
    DATA.mkdir(exist_ok=True)
    (DATA / "users.json").write_text(json.dumps(users, indent=1))
    (DATA / "crm.json").write_text(json.dumps(customers, indent=1))
    with open(DATA / "eval_cases.jsonl", "w") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")
    print(f"wrote {len(users)} users, {len(customers)} customers, {len(cases)} eval cases "
          f"({sum(c['split'] == 'dev' for c in cases)} dev / {sum(c['split'] == 'test' for c in cases)} test)")


if __name__ == "__main__":
    main()
