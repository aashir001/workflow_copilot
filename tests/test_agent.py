import pytest

from app.agent import ApprovalError
from app.mocks import CallCtx
from app.models import Plan, RequestIn, ToolCall
from app.planner import PlanningContext, build_vars

from conftest import CUSTOMERS, manager_of, users_by_role


def submit(cop, user, text):
    return cop.resolve(RequestIn(requester_id=user["user_id"], text=text))


# ------------------------------------------------------------------ happy paths
def test_password_reset_completes_end_to_end(cop, employee):
    out = submit(cop, employee, "I forgot my password, please reset it")
    assert out.status == "completed" and out.intent == "password_reset"
    assert [s.tool for s in out.steps] == ["jira_create_ticket", "crm_reset_user_password", "slack_dm", "jira_update_ticket"]
    tid = out.steps[0].result["ticket_id"]
    assert cop.b.tickets[tid]["status"] == "Resolved"                      # {ticket_id} flowed into the last step
    assert cop.b.users[employee["user_id"]]["must_change_password"] is True
    assert any(m["to"] == employee["user_id"] and tid in m["text"] for m in cop.b.messages)
    assert out.sources and out.latency_ms > 0 and out.sim_latency_ms > out.latency_ms


def test_rag_grounded_reply_for_vpn(cop, employee):
    out = submit(cop, employee, "VPN keeps disconnecting every few minutes")
    assert out.status == "completed" and out.intent == "vpn_connectivity"
    dm = next(m for m in cop.b.messages if m["to"] == employee["user_id"])
    assert "(1)" in dm["text"] and "VPN" in dm["text"]                    # SOP procedure text, not invented


def test_incident_priority_and_alert(cop, employee):
    out = submit(cop, employee, "Production is down - checkout API returning 500s for all users")
    assert out.status == "completed" and out.intent == "incident_report"
    t = next(iter(cop.b.tickets.values()))
    assert t["priority"] == "P1" and t["project"] == "INC"
    assert any(m["to"] == "#incidents" for m in cop.b.messages)


# ------------------------------------------------------------------ approvals
def test_sensitive_step_pauses_then_runs_after_approval(cop, employee):
    mgr = manager_of(employee)
    out = submit(cop, employee, "Please grant me admin access to the data warehouse")
    assert out.status == "awaiting_approval" and out.approval_id
    assert [s.status for s in out.steps] == ["ok", "pending_approval"]     # ticket created, grant NOT executed
    assert "data-warehouse" not in cop.b.users[employee["user_id"]]["access"]
    assert any(m["to"] == "#approvals" for m in cop.b.messages)

    done = cop.decide(out.approval_id, mgr["user_id"], "approve", "ok")
    assert done.status == "completed"
    assert cop.b.users[employee["user_id"]]["access"]["data-warehouse"] == "admin"
    assert [s.status for s in done.steps] == ["ok"] * 4


def test_rejection_closes_ticket_and_grants_nothing(cop, employee):
    mgr = manager_of(employee)
    out = submit(cop, employee, "Please grant me editor access to Jira")
    done = cop.decide(out.approval_id, mgr["user_id"], "reject", "not needed for your role")
    assert done.status == "rejected" and done.failure_type == "approval_rejected"
    assert "jira" not in cop.b.users[employee["user_id"]]["access"]
    tid = out.steps[0].result["ticket_id"]
    assert cop.b.tickets[tid]["status"] == "Rejected"
    assert [s.status for s in done.steps] == ["ok", "skipped", "skipped", "skipped"]


def test_least_privilege_default_role(cop, employee):
    out = submit(cop, employee, "I need access to the finance share drive")
    done = cop.decide(out.approval_id, manager_of(employee)["user_id"], "approve")
    assert done.status == "completed"
    assert cop.b.users[employee["user_id"]]["access"]["finance-drive"] == "viewer"


def test_invalid_approvers_are_refused(cop, employee):
    out = submit(cop, employee, "Please grant me read-only access to GitHub")
    other_mgr = next(m for m in users_by_role("manager") if m["user_id"] != employee["manager_id"])
    for bad, code in [(employee, 403), (other_mgr, 403), (users_by_role("finance")[0], 403)]:
        with pytest.raises(ApprovalError) as e:
            cop.decide(out.approval_id, bad["user_id"], "approve")
        assert e.value.status_code == code
    assert cop.b.users[employee["user_id"]]["access"].get("github") is None
    refused = [r for r in cop.store.audit_rows(out.request_id) if r["event"] == "approval_refused"]
    assert len(refused) == 3


def test_approval_cannot_be_decided_twice(cop, employee):
    out = submit(cop, employee, "Please grant me read-only access to GitHub")
    mgr = manager_of(employee)
    cop.decide(out.approval_id, mgr["user_id"], "approve")
    with pytest.raises(ApprovalError) as e:
        cop.decide(out.approval_id, mgr["user_id"], "approve")
    assert e.value.status_code == 409
    assert len(cop.b.tickets) == 1 and len([m for m in cop.b.messages if m["to"] == "#it-access"]) == 1


def test_unknown_approval_404(cop):
    with pytest.raises(ApprovalError) as e:
        cop.decide("APR-nope", "U001", "approve")
    assert e.value.status_code == 404


def test_refund_over_order_value_escalates_after_approval(cop):
    agent = users_by_role("support_agent")[0]
    fin = users_by_role("finance")[0]
    order = CUSTOMERS[0]["orders"][0]
    out = submit(cop, agent, f"Please issue a refund of ${int(order['amount']) + 500} for order {order['order_id']}")
    assert out.status == "awaiting_approval"
    done = cop.decide(out.approval_id, fin["user_id"], "approve")
    assert done.status == "escalated" and done.failure_type == "tool_error"
    assert cop.b.refunds == []                                            # nothing was refunded
    assert any(t["project"] == "TRIAGE" for t in cop.b.tickets.values())


def test_refund_defaults_to_full_order_value(cop):
    agent, fin = users_by_role("support_agent")[0], users_by_role("finance")[0]
    order = CUSTOMERS[1]["orders"][0]
    out = submit(cop, agent, f"issue full refund for order {order['order_id']}, customer cancelled")
    done = cop.decide(out.approval_id, fin["user_id"], "approve")
    assert done.status == "completed"
    assert cop.b.refunds[0]["amount"] == order["amount"]


def test_offboarding_revokes_access_only_after_approval(cop):
    hr = users_by_role("hr")[0]
    approver = users_by_role("it_admin")[0]
    target = users_by_role("employee")[0]
    out = submit(cop, hr, f"{target['name']} is leaving the company on Friday - please revoke all access")
    assert out.status == "awaiting_approval"
    assert cop.b.users[target["user_id"]]["status"] == "active"
    done = cop.decide(out.approval_id, approver["user_id"], "approve")
    assert done.status == "completed" and cop.b.users[target["user_id"]]["status"] == "offboarded"


# ------------------------------------------------------------------ denials & escalations
def test_role_not_allowed_is_denied_with_no_side_effects(cop, employee):
    target = users_by_role("employee")[1]
    out = submit(cop, employee, f"{target['name']} is leaving the company on Friday - please revoke all access")
    assert out.status == "denied" and out.failure_type == "permission_denied"
    assert cop.b.tickets == {} and cop.b.messages == []


def test_acting_for_a_peer_is_denied(cop):
    a, b = users_by_role("employee")[0], next(u for u in users_by_role("employee") if u["manager_id"] != users_by_role("employee")[0]["manager_id"])
    out = submit(cop, a, f"Please reset the password for {b['name']}, they are locked out of their account")
    assert out.status == "denied"


def test_unknown_requester_denied(cop):
    out = cop.resolve(RequestIn(requester_id="U999", text="reset my password"))
    assert out.status == "denied" and out.failure_type == "unknown_user"


def test_out_of_scope_escalates_to_triage(cop, employee):
    out = submit(cop, employee, "Tell me a joke")
    assert out.status == "escalated" and out.intent == "out_of_scope" and out.failure_type == "low_confidence"
    assert next(iter(cop.b.tickets)).startswith("TRIAGE-")
    assert any(m["to"] == "#ops-triage" for m in cop.b.messages)


def test_missing_required_information_escalates(cop):
    hr = users_by_role("hr")[0]
    out = submit(cop, hr, "A new hire is starting Monday, please set up their accounts")
    assert out.status == "escalated" and out.failure_type == "missing_information"


def test_expense_without_amount_escalates(cop, employee):
    out = submit(cop, employee, "Please reimburse my client dinner expense")
    assert out.status == "escalated" and out.failure_type == "missing_information"


# ------------------------------------------------------------------ reliability
def test_transient_failures_are_retried(cop, employee):
    cop.b.force_failures("jira", 2)
    out = submit(cop, employee, "VPN keeps disconnecting every few minutes")
    assert out.status == "completed" and out.retries == 2
    assert len(cop.b.tickets) == 1                                         # no duplicate despite retries
    assert out.steps[1].attempts == 3


def test_retries_exhausted_escalates_without_duplicates(cop, employee):
    cop.b.force_failures("jira", 3)  # 3 failures == exhausts the 3 attempts of the first Jira call
    out = submit(cop, employee, "I need a Tableau license for my new project")
    assert out.status == "escalated" and out.failure_type == "service_unavailable"
    assert out.steps[0].status == "failed" and out.steps[0].attempts == 3


def test_idempotency_key_prevents_duplicate_writes(backends):
    args = {"project": "ITSD", "issue_type": "Task", "priority": "Low", "summary": "x"}
    a = backends.jira_create_ticket(args, CallCtx(request_id="r", idem_key="r:0"))
    b = backends.jira_create_ticket(args, CallCtx(request_id="r", idem_key="r:0"))
    assert a["ticket_id"] == b["ticket_id"] and len(backends.tickets) == 1 and b["deduplicated"]


# ------------------------------------------------------------------ the planner cannot bypass policy
class StubPlanner:
    name = "stub"

    def __init__(self, intent, steps):
        self.intent, self.steps = intent, steps

    def plan(self, ctx: PlanningContext) -> Plan:
        return Plan(intent=self.intent, confidence=0.99, steps=self.steps, vars=build_vars(self.intent, ctx))


def test_planner_cannot_call_tools_outside_the_sop(settings, backends, retriever, store, employee):
    from app.agent import Copilot
    steps = [ToolCall(tool="crm_revoke_access", args={"user_id": employee["user_id"]})]
    cop = Copilot(settings, backends, retriever, store, planner=StubPlanner("password_reset", steps))
    out = submit(cop, employee, "forgot my password")
    assert out.status == "escalated" and out.failure_type == "policy_violation"
    assert backends.users[employee["user_id"]]["status"] == "active"


def test_planner_cannot_skip_approval(settings, backends, retriever, store, employee):
    from app.agent import Copilot
    steps = [ToolCall(tool="crm_grant_access", args={"user_id": employee["user_id"], "system": "crm", "role": "admin"})]
    cop = Copilot(settings, backends, retriever, store, planner=StubPlanner("access_request", steps))
    out = submit(cop, employee, "make me admin")
    assert out.status == "awaiting_approval"
    assert backends.users[employee["user_id"]]["access"].get("crm") != "admin"


def test_planner_cannot_invent_tools(settings, backends, retriever, store, employee):
    from app.agent import Copilot
    cop = Copilot(settings, backends, retriever, store, planner=StubPlanner("password_reset", [ToolCall(tool="drop_database")]))
    out = submit(cop, employee, "forgot my password")
    assert out.status == "escalated" and out.failure_type == "policy_violation"


def test_state_persists_across_a_new_copilot_instance(settings, backends, retriever, store, employee):
    """Approvals survive a service restart because run state lives in SQLite, not in memory."""
    from app.agent import Copilot
    c1 = Copilot(settings, backends, retriever, store)
    out = submit(c1, employee, "Please grant me editor access to GitHub")
    c2 = Copilot(settings, backends, retriever, store)       # simulated restart (same DB, same backends)
    done = c2.decide(out.approval_id, manager_of(employee)["user_id"], "approve")
    assert done.status == "completed"
    assert c2.get(out.request_id).status == "completed"
