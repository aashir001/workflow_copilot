"""Lightweight, deterministic entity extraction used to fill SOP action templates."""
from __future__ import annotations

import re
from typing import Any

AMOUNT_RE = re.compile(r"(?:\$|usd\s?|rs\.?\s?|inr\s?|₹)\s?(\d[\d,]*(?:\.\d+)?)", re.I)
ORDER_RE = re.compile(r"\bORD-\d{4,6}\b", re.I)
CUST_RE = re.compile(r"\bCUST-\d{3,5}\b", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\+?\d[\d\s\-]{8,}\d")
ADDRESS_RE = re.compile(r"address\b.*?\bto\s+(.+?)[.!]?\s*$", re.I)
FILLER_RE = re.compile(r"[\s,.!-]*(?:thanks!?|thank you\.?|please advise\.?|asap\.?)\s*$", re.I)

SYSTEMS = [
    (r"data[- ]warehouse|snowflake|bigquery", "data-warehouse"),
    (r"finance (?:share|shared|drive)|shared drive|share drive", "finance-drive"),
    (r"dashboard|power bi|tableau|bi tool", "bi-dashboards"),
    (r"github", "github"),
    (r"jira", "jira"),
    (r"\bcrm\b", "crm"),
]
SOFTWARE = ["Tableau", "Alteryx", "Figma", "Adobe Acrobat Pro", "PyCharm Professional", "Microsoft Visio",
            "Power BI Pro", "Zoom", "Postman"]
DEVICES = [
    (r"docking station|dock\b", "docking station"),
    (r"charger", "laptop charger"),
    (r"webcam", "webcam"),
    (r"headset|headphones", "headset"),
    (r"monitor|screen", "monitor"),
    (r"keyboard", "keyboard"),
    (r"\bmouse\b", "mouse"),
    (r"laptop", "laptop"),
]
P1_RE = re.compile(r"outage|\bdown\b|unreachable|ransomware|breach|security incident|suspicious|malware|data leak", re.I)
P2_RE = re.compile(r"degrad|failing|failed|timing out|time out|intermittent|stale|slow", re.I)

EXPLICIT_TARGET_INTENTS = {"employee_onboarding", "employee_offboarding"}


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def extract_entities(text: str, users: list[dict], requester: dict) -> dict[str, Any]:
    ent: dict[str, Any] = {}
    core = FILLER_RE.sub("", text)  # drop trailing pleasantries before extracting values
    low = text.lower()

    m = AMOUNT_RE.search(text)
    ent["amount"] = _num(m.group(1)) if m else None
    m = ORDER_RE.search(text)
    ent["order_id"] = m.group(0).upper() if m else None
    m = CUST_RE.search(text)
    ent["customer_id"] = m.group(0).upper() if m else None

    # named people (first mention that is not the requester wins)
    hits = []
    for u in users:
        pos = low.find(u["name"].lower())
        if pos >= 0:
            hits.append((pos, u))
    hits.sort(key=lambda t: t[0])
    others = [u for _, u in hits if u["user_id"] != requester["user_id"]]
    ent["explicit_target"] = others[0] if others else None

    # access request details
    if re.search(r"admin", low):
        ent["role"] = "admin"
    elif re.search(r"read[- ]only|view[- ]only|viewer|view access", low):
        ent["role"] = "viewer"
    elif re.search(r"editor|edit access|edit permission|write access|contributor", low):
        ent["role"] = "editor"
    else:
        ent["role"] = "viewer"  # least privilege by default
    ent["system"] = "crm"
    for pat, name in SYSTEMS:
        if re.search(pat, low):
            ent["system"] = name
            break

    # customer account update
    field = value = None
    em = EMAIL_RE.search(text)
    if em and "email" in low:
        field, value = "email", em.group(0).rstrip(".")
    else:
        pm = PHONE_RE.search(text)
        if pm and re.search(r"phone|mobile|number", low):
            field, value = "phone", pm.group(0).strip()
        elif "address" in low:
            am = ADDRESS_RE.search(core)
            if am:
                field, value = "billing_address", am.group(1).strip()
    ent["field"], ent["value"] = field, value

    # software / hardware
    ent["software"] = next((s for s in SOFTWARE if s.lower() in low), "requested software")
    ent["device"] = next((name for pat, name in DEVICES if re.search(pat, low)), "equipment")

    # incident priority
    ent["priority"] = "P1" if P1_RE.search(text) else ("P2" if P2_RE.search(text) else "P3")
    return ent
