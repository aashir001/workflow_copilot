from app.models import RequestIn

from conftest import manager_of, users_by_role


def test_every_step_and_decision_is_logged(cop, employee):
    out = cop.resolve(RequestIn(requester_id=employee["user_id"], text="Please grant me admin access to the data warehouse"))
    cop.decide(out.approval_id, manager_of(employee)["user_id"], "approve", "fine")
    events = [r["event"] for r in cop.store.audit_rows(out.request_id)]
    for needed in ("request_received", "plan_created", "tool_attempt", "approval_requested", "approval_decided", "completed"):
        assert needed in events
    attempts = [r for r in cop.store.audit_rows(out.request_id) if r["event"] == "tool_attempt" and r["tool"] == "crm_grant_access"]
    assert len(attempts) == 1 and attempts[0]["status"] == "ok"
    decided = next(r for r in cop.store.audit_rows(out.request_id) if r["event"] == "approval_decided")
    assert decided["actor"] == manager_of(employee)["user_id"]


def test_chain_verifies_and_detects_tampering(cop, employee):
    cop.resolve(RequestIn(requester_id=employee["user_id"], text="VPN keeps disconnecting"))
    assert cop.store.verify_chain()["valid"]
    cop.store._db.execute("UPDATE audit_log SET detail='tampered' WHERE id=3")
    cop.store._db.commit()
    res = cop.store.verify_chain()
    assert not res["valid"] and res["first_bad_id"] == 3


def test_deleting_a_row_breaks_the_chain(cop, employee):
    cop.resolve(RequestIn(requester_id=employee["user_id"], text="VPN keeps disconnecting"))
    cop.store._db.execute("DELETE FROM audit_log WHERE id=4")
    cop.store._db.commit()
    assert not cop.store.verify_chain()["valid"]


def test_metrics_reflect_runs(cop, employee):
    cop.resolve(RequestIn(requester_id=employee["user_id"], text="VPN keeps disconnecting"))
    cop.resolve(RequestIn(requester_id=employee["user_id"], text="Tell me a joke"))
    m = cop.metrics()
    assert m["requests"] == 2 and m["by_status"] == {"completed": 1, "escalated": 1}
    assert m["failure_types"] == {"low_confidence": 1} and m["escalation_rate"] == 0.5
