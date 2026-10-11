"""Tool registry: JSON-schema tool definitions bound to the (mock) backends.

The same definitions feed three consumers: the local planner, the Anthropic tool-use API and any
OpenAI-compatible endpoint (e.g. Ollama), so swapping the planner never changes what a tool means.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .mocks import Backends, CallCtx


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[[dict, CallCtx], dict]
    kind: str = "write"  # read | write


def _obj(props: dict[str, tuple[str, str]], required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": {k: {"type": t, "description": d} for k, (t, d) in props.items()},
        "required": required if required is not None else list(props),
    }


def build_registry(b: Backends) -> dict[str, ToolSpec]:
    S = "string"
    specs = [
        # --- read-only context tools ---
        ToolSpec("directory_lookup", "Look up an employee record by id.", _obj({"user_id": (S, "Employee id, e.g. U007")}),
                 lambda a, c: b.directory_lookup(a["user_id"], c), "read"),
        ToolSpec("crm_lookup_customer", "Look up a customer and their orders.", _obj({"customer_id": (S, "e.g. CUST-1004")}),
                 lambda a, c: b.crm_lookup_customer(a["customer_id"], c), "read"),
        ToolSpec("crm_lookup_order", "Look up one order.", _obj({"order_id": (S, "e.g. ORD-50012")}),
                 lambda a, c: b.crm_lookup_order(a["order_id"], c), "read"),
        ToolSpec("jira_search", "List a user's open Jira tickets.", _obj({"reporter_id": (S, "Employee id")}),
                 lambda a, c: b.jira_search(a["reporter_id"], c), "read"),
        # --- write tools ---
        ToolSpec("jira_create_ticket", "Create a Jira ticket. Returns ticket_id.",
                 _obj({"project": (S, "Jira project key"), "issue_type": (S, "Issue type"), "priority": (S, "Priority"),
                       "summary": (S, "One-line summary"), "description": (S, "Details")},
                      ["project", "issue_type", "priority", "summary"]),
                 lambda a, c: b.jira_create_ticket(a, c)),
        ToolSpec("jira_update_ticket", "Update a Jira ticket's status and/or add a comment.",
                 _obj({"ticket_id": (S, "Ticket key"), "status": (S, "New status"), "comment": (S, "Comment")}, ["ticket_id"]),
                 lambda a, c: b.jira_update_ticket(a, c)),
        ToolSpec("slack_post_message", "Post a message to a Slack channel.",
                 _obj({"channel": (S, "Channel such as #incidents"), "text": (S, "Message")}),
                 lambda a, c: b.slack_post_message(a, c)),
        ToolSpec("slack_dm", "Send a Slack direct message to an employee.",
                 _obj({"user_id": (S, "Employee id"), "text": (S, "Message")}),
                 lambda a, c: b.slack_dm(a, c)),
        ToolSpec("crm_reset_user_password", "Issue a temporary password and force a change at next sign-in.",
                 _obj({"user_id": (S, "Employee id")}), lambda a, c: b.crm_reset_user_password(a, c)),
        ToolSpec("crm_grant_access", "Grant a role on a system. SENSITIVE: requires human approval.",
                 _obj({"user_id": (S, "Employee id"), "system": (S, "crm|jira|data-warehouse|finance-drive|bi-dashboards|github"),
                       "role": (S, "viewer|editor|admin|standard")}),
                 lambda a, c: b.crm_grant_access(a, c)),
        ToolSpec("crm_revoke_access", "Revoke all access and deactivate an employee. SENSITIVE: requires human approval.",
                 _obj({"user_id": (S, "Employee id")}), lambda a, c: b.crm_revoke_access(a, c)),
        ToolSpec("crm_update_customer", "Change a customer's email, phone or billing_address. SENSITIVE: requires approval.",
                 _obj({"customer_id": (S, "Customer id"), "field": (S, "email|phone|billing_address"), "value": (S, "New value")}),
                 lambda a, c: b.crm_update_customer(a, c)),
        ToolSpec("crm_issue_refund", "Refund an order (never more than the order value). SENSITIVE: requires approval.",
                 _obj({"order_id": (S, "Order id"), "amount": ("number", "Refund amount"), "reason": (S, "Reason")}),
                 lambda a, c: b.crm_issue_refund(a, c)),
        ToolSpec("crm_export_customer_data", "Export all personal data held on a customer. SENSITIVE: requires approval.",
                 _obj({"customer_id": (S, "Customer id")}), lambda a, c: b.crm_export_customer_data(a, c)),
    ]
    return {s.name: s for s in specs}


def validate_args(spec: ToolSpec, args: dict) -> str | None:
    """Return an error string if required params are missing or placeholders were never resolved."""
    import re

    for req in spec.parameters.get("required", []):
        if req not in args or args[req] in (None, ""):
            return f"missing required argument '{req}'"
    for k, v in args.items():
        if isinstance(v, str) and re.search(r"\{[a-z_]+\}", v):
            return f"argument '{k}' contains an unresolved placeholder: {v[:60]}"
    return None
