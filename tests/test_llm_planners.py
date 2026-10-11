"""LLM planners are tested against stubbed HTTP transports (no network, no API keys)."""
import json

import httpx

from app.agent import Copilot
from app.models import RequestIn
from app.planner import AnthropicPlanner, FallbackPlanner, LocalPlanner, OpenAICompatPlanner

from conftest import manager_of


def anthropic_response(blocks):
    return httpx.Response(200, json={"content": blocks, "stop_reason": "tool_use"})


def tool_use(name, **inp):
    return {"type": "tool_use", "id": "toolu_1", "name": name, "input": inp}


def make_cop(settings, backends, retriever, store, planner_factory):
    cop = Copilot(settings, backends, retriever, store)
    cop.planner = planner_factory(cop.tools)
    return cop


def test_anthropic_planner_end_to_end(settings, backends, retriever, store, employee):
    seen = {}

    def handler(request: httpx.Request):
        seen["headers"], seen["body"] = request.headers, json.loads(request.content)
        uid = employee["user_id"]
        return anthropic_response([
            tool_use("route_request", intent="vpn_connectivity", confidence=0.93, rationale="VPN drops"),
            tool_use("jira_create_ticket", project="NET", issue_type="Support", priority="Medium", summary="VPN drops", description="x"),
            tool_use("slack_dm", user_id=uid, text="Ticket {ticket_id} opened for your VPN issue."),
        ])

    cop = make_cop(settings, backends, retriever, store, lambda tools: AnthropicPlanner(
        settings, tools, client=httpx.Client(transport=httpx.MockTransport(handler)), api_key="sk-test"))
    out = cop.resolve(RequestIn(requester_id=employee["user_id"], text="VPN keeps disconnecting every few minutes"))

    assert out.status == "completed" and out.intent == "vpn_connectivity" and out.confidence == 0.93
    assert seen["headers"]["x-api-key"] == "sk-test" and seen["headers"]["anthropic-version"] == "2023-06-01"
    names = [t["name"] for t in seen["body"]["tools"]]
    assert names[0] == "route_request" and "slack_dm" in names and "directory_lookup" not in names   # read tools hidden
    assert seen["body"]["model"] == settings.anthropic_model and seen["body"]["tool_choice"] == {"type": "any"}
    dm = next(m for m in backends.messages if m["to"] == employee["user_id"])
    assert dm["text"] == f"Ticket {out.steps[0].result['ticket_id']} opened for your VPN issue."   # placeholder resolved


def test_llm_sensitive_plan_still_pauses_for_approval(settings, backends, retriever, store, employee):
    uid = employee["user_id"]

    def handler(request):
        return anthropic_response([
            tool_use("route_request", intent="access_request", confidence=0.9),
            tool_use("crm_grant_access", user_id=uid, system="crm", role="admin"),
        ])

    cop = make_cop(settings, backends, retriever, store, lambda tools: AnthropicPlanner(
        settings, tools, client=httpx.Client(transport=httpx.MockTransport(handler)), api_key="k"))
    out = cop.resolve(RequestIn(requester_id=uid, text="give me admin on the CRM"))
    assert out.status == "awaiting_approval"
    done = cop.decide(out.approval_id, manager_of(employee)["user_id"], "approve")
    assert done.status == "completed" and backends.users[uid]["access"]["crm"] == "admin"


def test_llm_out_of_scope_escalates(settings, backends, retriever, store, employee):
    handler = lambda r: anthropic_response([tool_use("route_request", intent="out_of_scope", confidence=0.4, rationale="not covered")])
    cop = make_cop(settings, backends, retriever, store, lambda tools: AnthropicPlanner(
        settings, tools, client=httpx.Client(transport=httpx.MockTransport(handler)), api_key="k"))
    out = cop.resolve(RequestIn(requester_id=employee["user_id"], text="please translate this paragraph"))
    assert out.status == "escalated" and out.failure_type == "low_confidence"


def test_llm_hallucinated_intent_is_escalated(settings, backends, retriever, store, employee):
    handler = lambda r: anthropic_response([tool_use("route_request", intent="launch_rockets", confidence=0.99)])
    cop = make_cop(settings, backends, retriever, store, lambda tools: AnthropicPlanner(
        settings, tools, client=httpx.Client(transport=httpx.MockTransport(handler)), api_key="k"))
    out = cop.resolve(RequestIn(requester_id=employee["user_id"], text="VPN keeps disconnecting"))
    assert out.status == "escalated"


def test_openai_compat_planner_parses_string_arguments(settings, backends, retriever, store, employee):
    uid = employee["user_id"]
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        calls = [
            {"id": "1", "type": "function", "function": {"name": "route_request", "arguments": json.dumps({"intent": "hardware_request", "confidence": 0.8})}},
            {"id": "2", "type": "function", "function": {"name": "jira_create_ticket", "arguments": json.dumps(
                {"project": "ITSD", "issue_type": "Hardware", "priority": "Low", "summary": "monitor"})}},
            {"id": "3", "type": "function", "function": {"name": "slack_dm", "arguments": json.dumps({"user_id": uid, "text": "Logged {ticket_id}"})}},
        ]
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": calls}}]})

    cop = make_cop(settings, backends, retriever, store, lambda tools: OpenAICompatPlanner(
        settings, tools, client=httpx.Client(transport=httpx.MockTransport(handler))))
    out = cop.resolve(RequestIn(requester_id=uid, text="I need a new monitor"))
    assert out.status == "completed" and out.intent == "hardware_request"
    assert seen["url"].endswith("/v1/chat/completions") and seen["body"]["tools"][0]["function"]["name"] == "route_request"


def test_fallback_to_local_planner_on_llm_failure(settings, backends, retriever, store, employee):
    errors = []
    handler = lambda r: httpx.Response(500, json={"error": "boom"})

    def factory(tools):
        llm = AnthropicPlanner(settings, tools, client=httpx.Client(transport=httpx.MockTransport(handler)), api_key="k")
        return FallbackPlanner(llm, LocalPlanner(settings), on_fallback=errors.append)

    cop = make_cop(settings, backends, retriever, store, factory)
    out = cop.resolve(RequestIn(requester_id=employee["user_id"], text="VPN keeps disconnecting every few minutes"))
    assert out.status == "completed" and out.intent == "vpn_connectivity" and errors


def test_missing_api_key_falls_back(settings, backends, retriever, store, employee, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    errors = []

    def factory(tools):
        return FallbackPlanner(AnthropicPlanner(settings, tools, api_key=""), LocalPlanner(settings), on_fallback=errors.append)

    cop = make_cop(settings, backends, retriever, store, factory)
    out = cop.resolve(RequestIn(requester_id=employee["user_id"], text="I forgot my password"))
    assert out.status == "completed" and "ANTHROPIC_API_KEY" in errors[0]
