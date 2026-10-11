import pytest

from app.tools import build_registry
from app.mocks import Backends
from app.config import Settings


def test_all_sops_are_consistent(retriever):
    tools = build_registry(Backends.from_data(Settings().data_dir))
    assert len(retriever.sops) == 12
    for sop in retriever.sops.values():
        assert sop.actions, sop.intent
        for a in sop.actions:
            assert a["tool"] in tools, f"{sop.sop_id}: unknown tool {a['tool']}"
            assert tools[a["tool"]].kind == "write"
        assert set(sop.sensitive_tools) <= sop.allowed_tools, sop.sop_id
        if sop.sensitive_tools:
            assert sop.approver_roles, f"{sop.sop_id} has sensitive tools but no approvers"
        assert "When to use" in sop.sections and "Procedure" in sop.sections


@pytest.mark.parametrize("query,intent", [
    ("I forgot my password and I'm locked out", "password_reset"),
    ("Please grant me read-only access to the data warehouse", "access_request"),
    ("New hire Priya Sharma starts Monday, set up her accounts", "employee_onboarding"),
    ("Customer CUST-1004 wants a refund on order ORD-50012", "refund_request"),
    ("production is down, outage on the checkout API", "incident_report"),
    ("VPN keeps disconnecting", "vpn_connectivity"),
    ("DSAR from a customer, export all their personal data", "data_export_request"),
    ("I need a Tableau license", "software_license"),
])
def test_retrieval_routes_to_expected_sop(retriever, query, intent):
    scores = retriever.intent_scores(query)
    assert next(iter(scores)) == intent


def test_typos_still_route(retriever):
    assert next(iter(retriever.intent_scores("pasword resett pleaes, i am lokced out"))) == "password_reset"


def test_search_returns_ranked_chunks_with_refs(retriever):
    hits = retriever.search("revoke access for a leaver", k=3)
    assert len(hits) == 3
    assert hits[0][1] >= hits[1][1] >= hits[2][1]
    assert hits[0][0].ref.startswith("HR-002")


def test_procedure_steps_are_grounded_in_sop(retriever):
    steps = retriever.procedure_steps("vpn_connectivity")
    assert "(1)" in steps and "VPN" in steps
