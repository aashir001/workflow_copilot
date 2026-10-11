"""Manual-handling time MODEL used only by the evaluation.

These are ASSUMPTIONS, not measurements: rough seconds a person needs for each step when handling the
same request by hand (read it, find the SOP, check records, click through each system, write a handoff).
Edit them to match what you actually observed, then re-run `python scripts/run_eval.py`.
The automated side is measured (real compute time) plus a fixed virtual latency per mock API call.
Human approval wait time is excluded from BOTH sides, so it does not flatter the comparison.
"""

READ_REQUEST_S = 30          # read and understand the request
FIND_SOP_S = 60              # search the wiki / SOP library for the right procedure
LOOKUP_S = 40                # per record check (employee directory, CRM customer/order, existing tickets)
ESCALATION_WRITEUP_S = 120   # write up and hand off a case that cannot be resolved
REPLY_S = 30                 # write a denial / rejection reply

TOOL_S = {
    "jira_create_ticket": 90,
    "jira_update_ticket": 45,
    "slack_post_message": 30,
    "slack_dm": 30,
    "crm_reset_user_password": 75,
    "crm_grant_access": 90,
    "crm_revoke_access": 120,
    "crm_update_customer": 75,
    "crm_issue_refund": 120,
    "crm_export_customer_data": 150,
}


def manual_seconds(executed_tools: list[str], n_lookups: int, status: str) -> float:
    t = READ_REQUEST_S + FIND_SOP_S + LOOKUP_S * n_lookups
    t += sum(TOOL_S.get(tool, 60) for tool in executed_tools)
    if status == "escalated":
        t += ESCALATION_WRITEUP_S
    elif status in ("denied", "rejected"):
        t += REPLY_S
    return float(t)
