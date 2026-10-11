"""Authorization rules. These are enforced by the orchestrator, never delegated to the planner.

An LLM planner can propose tool calls, but it cannot (a) call tools the matched SOP does not list,
(b) skip human approval for steps the SOP marks sensitive, or (c) approve its own request.
"""
from __future__ import annotations

from .rag import SOP

# Tools that always need a human approval regardless of what an SOP says.
ALWAYS_SENSITIVE = {"crm_revoke_access", "crm_issue_refund", "crm_export_customer_data"}


def is_sensitive(sop: SOP, tool: str) -> bool:
    return tool in ALWAYS_SENSITIVE or tool in sop.sensitive_tools


def can_initiate(requester: dict, sop: SOP) -> tuple[bool, str]:
    if requester.get("status") not in ("active",):
        return False, f"requester {requester['user_id']} is not an active employee"
    if requester["role"] not in sop.allowed_roles:
        return False, f"role '{requester['role']}' may not request '{sop.title}' (allowed: {', '.join(sop.allowed_roles)})"
    return True, ""


def check_target_scope(requester: dict, target: dict | None, sop: SOP) -> tuple[bool, str]:
    """Self-service is always fine; acting on someone else needs a management relationship."""
    if not sop.target_scoped or target is None or target["user_id"] == requester["user_id"]:
        return True, ""
    if requester["role"] in ("hr", "it_admin"):
        return True, ""
    if requester["role"] == "manager" and target.get("manager_id") == requester["user_id"]:
        return True, ""
    return False, f"{requester['name']} is not allowed to act on behalf of {target['name']}"


def can_approve(approver: dict, sop: SOP, requester: dict, target: dict | None) -> tuple[bool, str]:
    if approver.get("status") != "active":
        return False, "approver is not an active employee"
    if approver["user_id"] == requester["user_id"]:
        return False, "separation of duties: requesters cannot approve their own request"
    if approver["role"] not in sop.approver_roles:
        return False, f"role '{approver['role']}' cannot approve (needs one of: {', '.join(sop.approver_roles)})"
    # Line managers may only approve actions on their own people.
    if approver["role"] == "manager" and sop.target_scoped and target is not None:
        if target.get("manager_id") != approver["user_id"]:
            return False, f"{approver['name']} is not the line manager of {target['name']}"
    return True, ""
