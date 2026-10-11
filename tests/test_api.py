import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from conftest import manager_of, users_by_role


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def test_health(client):
    r = client.get("/health").json()
    assert r["status"] == "ok" and r["planner"] == "local" and r["sops"] == 12


def test_full_approval_flow_over_http(client, employee):
    r = client.post("/requests", json={"requester_id": employee["user_id"], "text": "Please grant me editor access to Jira"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "awaiting_approval"
    aid = body["approval_id"]
    assert client.get("/approvals", params={"status": "pending"}).json()[0]["approval_id"] == aid

    bad = client.post(f"/approvals/{aid}/decision", json={"approver_id": employee["user_id"], "decision": "approve"})
    assert bad.status_code == 403

    ok = client.post(f"/approvals/{aid}/decision", json={"approver_id": manager_of(employee)["user_id"], "decision": "approve"})
    assert ok.status_code == 200 and ok.json()["status"] == "completed"
    again = client.post(f"/approvals/{aid}/decision", json={"approver_id": manager_of(employee)["user_id"], "decision": "approve"})
    assert again.status_code == 409

    assert client.get(f"/requests/{body['request_id']}").json()["status"] == "completed"
    assert client.get("/mock/crm/users/" + employee["user_id"]).json()["access"]["jira"] == "editor"
    audit = client.get("/audit", params={"request_id": body["request_id"]}).json()
    assert any(a["event"] == "approval_decided" for a in audit)
    assert client.get("/audit/verify").json()["valid"] is True
    m = client.get("/metrics").json()
    assert m["requests"] == 1 and m["approvals"] == {"approved": 1}
    assert any(t["issue_type"] == "Access" for t in client.get("/mock/jira/tickets").json())


def test_validation_and_not_found(client):
    assert client.post("/requests", json={"requester_id": "U001", "text": "x"}).status_code == 422
    assert client.get("/requests/REQ-nope").status_code == 404
    assert client.post("/approvals/APR-nope/decision", json={"approver_id": "U001", "decision": "approve"}).status_code == 404
    assert client.get("/mock/crm/customers/CUST-0000").status_code == 404


def test_retrieve_debug_endpoint(client):
    r = client.get("/retrieve", params={"q": "VPN keeps dropping"}).json()
    assert next(iter(r["intent_scores"])) == "vpn_connectivity" and r["chunks"][0]["ref"].startswith("NET-001")
