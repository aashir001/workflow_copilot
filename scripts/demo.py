"""Narrated demo of three flows (self-service, approval, escalation) using the real FastAPI app in-process.

    python scripts/demo.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

USERS = {u["user_id"]: u for u in json.loads((Path(__file__).resolve().parent.parent / "data" / "users.json").read_text())}


def show(title: str, out: dict) -> None:
    print(f"\n=== {title}")
    print(f"status={out['status']}  intent={out['intent']}  confidence={out['confidence']}  retries={out['retries']}")
    print(f"summary: {out['summary']}")
    for s in out["steps"]:
        print(f"  step {s['index']} {s['tool']:<26} {s['status']}")


def main() -> None:
    s = Settings()
    s.db_path = os.path.join(tempfile.mkdtemp(), "demo.db")
    employee = next(u for u in USERS.values() if u["role"] == "employee" and u["status"] == "active")
    manager = USERS[employee["manager_id"]]
    with TestClient(create_app(s)) as c:
        r = c.post("/requests", json={"requester_id": employee["user_id"], "text": "VPN keeps disconnecting every few minutes"}).json()
        show(f"1. Self-service troubleshooting ({employee['name']}) - SOP-grounded reply, no approval needed", r)

        r = c.post("/requests", json={"requester_id": employee["user_id"], "text": "Please grant me admin access to the data warehouse"}).json()
        show("2a. Sensitive request pauses for human approval", r)
        bad = c.post(f"/approvals/{r['approval_id']}/decision", json={"approver_id": employee["user_id"], "decision": "approve"})
        print(f"  self-approval attempt -> HTTP {bad.status_code}: {bad.json()['detail']}")
        done = c.post(f"/approvals/{r['approval_id']}/decision", json={"approver_id": manager["user_id"], "decision": "approve", "reason": "needed for Q4 analysis"}).json()
        show(f"2b. Line manager {manager['name']} approves -> remaining steps run", done)

        r = c.post("/requests", json={"requester_id": employee["user_id"], "text": "Can you book me a flight to Goa next weekend?"}).json()
        show("3. Out of scope -> low-confidence handoff to a human", r)

        print("\n=== Audit trail for flow 2")
        for row in c.get("/audit", params={"request_id": done["request_id"]}).json():
            print(f"  {row['ts'][11:23]}  {row['event']:<20} {row['actor']:<10} {row['tool'] or '':<24} {row['status'] or ''}")
        print("\naudit chain:", c.get("/audit/verify").json())
        print("metrics:", json.dumps(c.get("/metrics").json(), indent=1))


if __name__ == "__main__":
    main()
