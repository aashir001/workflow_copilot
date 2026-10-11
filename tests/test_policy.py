from app.policy import can_approve, can_initiate, check_target_scope, is_sensitive

from conftest import USERS, manager_of, users_by_role


def test_role_gate(retriever):
    emp = users_by_role("employee")[0]
    hr = users_by_role("hr")[0]
    assert not can_initiate(emp, retriever.sops["employee_offboarding"])[0]
    assert can_initiate(hr, retriever.sops["employee_offboarding"])[0]
    pending = next(u for u in USERS if u["status"] == "pending_start")
    assert not can_initiate(pending, retriever.sops["password_reset"])[0]


def test_target_scope():
    emp_a, emp_b = [u for u in users_by_role("employee") if u["manager_id"] != users_by_role("employee")[0]["manager_id"]][:1] + [users_by_role("employee")[0]]
    sop = type("S", (), {"target_scoped": True})()
    assert check_target_scope(emp_a, emp_a, sop)[0]                       # self
    assert not check_target_scope(emp_a, emp_b, sop)[0]                   # peer
    assert check_target_scope(manager_of(emp_b), emp_b, sop)[0]           # own manager
    assert not check_target_scope(manager_of(emp_a), emp_b, sop)[0]       # someone else's manager
    assert check_target_scope(users_by_role("hr")[0], emp_b, sop)[0]      # HR


def test_separation_of_duties_and_line_manager_rule(retriever):
    sop = retriever.sops["access_request"]
    emp = users_by_role("employee")[0]
    mgr = manager_of(emp)
    other_mgr = next(m for m in users_by_role("manager") if m["user_id"] != mgr["user_id"])
    it = users_by_role("it_admin")[0]
    assert can_approve(mgr, sop, emp, emp)[0]
    assert not can_approve(emp, sop, emp, emp)[0]                  # self-approval
    assert not can_approve(other_mgr, sop, emp, emp)[0]            # not the line manager
    assert can_approve(it, sop, emp, emp)[0]
    assert not can_approve(users_by_role("finance")[0], sop, emp, emp)[0]   # wrong role


def test_sensitivity_cannot_be_dropped_by_an_sop(retriever):
    # even for an SOP that does not list it, these tools always need approval
    sop = retriever.sops["password_reset"]
    assert is_sensitive(sop, "crm_revoke_access")
    assert is_sensitive(sop, "crm_issue_refund")
    assert not is_sensitive(sop, "slack_dm")
