"""Planners turn (request + retrieved SOP context) into a routed intent and an ordered list of tool calls.

LocalPlanner       offline & deterministic: RAG picks the SOP, the SOP's action template becomes the plan.
AnthropicPlanner   real LLM tool-calling through the Anthropic Messages API (needs ANTHROPIC_API_KEY).
OpenAICompatPlanner  real LLM tool-calling through any OpenAI-compatible server, e.g. Ollama (free, local).
FallbackPlanner    tries an LLM planner first and falls back to LocalPlanner on any error.

Planners only *propose* a plan. Authorization, approvals, retries and auditing are enforced by the
orchestrator (agent.py), so a wrong or adversarial LLM output cannot bypass them.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .config import Settings
from .entities import EXPLICIT_TARGET_INTENTS
from .models import Plan, ToolCall
from .rag import SOP, Chunk, Retriever
from .tools import ToolSpec

OUT_OF_SCOPE = "out_of_scope"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
WRITE_TOOLS_HIDDEN = {"directory_lookup", "crm_lookup_customer", "crm_lookup_order", "jira_search"}


def fill(value: Any, vars: dict[str, Any]) -> Any:
    """Substitute {placeholders}. Unknown placeholders are left untouched so they can be resolved later
    (e.g. {ticket_id} only exists after the Jira step ran)."""
    if isinstance(value, str):
        full = PLACEHOLDER.fullmatch(value)
        if full and full.group(1) in vars and vars[full.group(1)] is not None:
            return vars[full.group(1)]  # keep native type (numbers)

        def sub(m: re.Match) -> str:
            v = vars.get(m.group(1))
            return str(v) if v is not None else m.group(0)

        return PLACEHOLDER.sub(sub, value)
    if isinstance(value, dict):
        return {k: fill(v, vars) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, vars) for v in value]
    return value


@dataclass
class PlanningContext:
    text: str
    requester: dict
    entities: dict[str, Any]
    intent_scores: dict[str, float]
    chunks: list[tuple[Chunk, float]]
    sops: dict[str, SOP]
    retriever: Retriever
    target: dict | None = None          # explicit target user record (if any)
    customer: dict | None = None
    order: dict | None = None
    open_tickets: list[dict] = field(default_factory=list)


def build_vars(intent: str, ctx: PlanningContext) -> dict[str, Any]:
    e, req = ctx.entities, ctx.requester
    target = ctx.target if ctx.target else (None if intent in EXPLICIT_TARGET_INTENTS else req)
    amount = e.get("amount")
    if amount is None and ctx.order is not None and intent == "refund_request":
        amount = ctx.order["amount"]
    customer_id = e.get("customer_id") or (ctx.order or {}).get("customer_id")
    return {
        "requester_id": req["user_id"], "requester_name": req["name"],
        "target_user_id": target["user_id"] if target else None,
        "target_user_name": target["name"] if target else None,
        "target_user": target["user_id"] if target else None,
        "request_text": ctx.text.strip()[:300],
        "amount": amount, "order_id": e.get("order_id") if (ctx.order or intent != "refund_request") else None,
        "customer_id": customer_id, "role": e.get("role"), "system": e.get("system"),
        "field": e.get("field"), "value": e.get("value"), "software": e.get("software"),
        "device": e.get("device"), "priority": e.get("priority"),
        "expense_priority": "High" if (amount or 0) > 1000 else "Medium",
        "sop_steps": ctx.retriever.procedure_steps(intent),
        "intent": intent,
    }


class Planner(Protocol):
    name: str

    def plan(self, ctx: PlanningContext) -> Plan: ...


def _escalation(intent: str, conf: float, margin: float, reason: str, rationale: str, sources: list[str]) -> Plan:
    return Plan(intent=intent, confidence=conf, margin=margin, escalate=True, escalate_reason=reason,
                rationale=rationale, sources=sources)


class LocalPlanner:
    name = "local"

    def __init__(self, settings: Settings):
        self.s = settings

    def plan(self, ctx: PlanningContext) -> Plan:
        ranked = list(ctx.intent_scores.items())
        (top_intent, top), second = ranked[0], (ranked[1][1] if len(ranked) > 1 else 0.0)
        margin = top - second
        sources = [c.ref for c, _ in ctx.chunks]
        if top < self.s.min_score or margin < self.s.min_margin:
            return _escalation(OUT_OF_SCOPE, top, margin, "low_confidence",
                               f"best SOP match '{top_intent}' scored {top:.2f} (margin {margin:.2f}) - below routing threshold", sources)
        sop = ctx.sops[top_intent]
        vars = build_vars(top_intent, ctx)
        missing = [r for r in sop.requires if not vars.get(r)]
        if missing:
            return _escalation(top_intent, top, margin, "missing_information",
                               f"{sop.title}: missing required information: {', '.join(missing)}", sources)
        steps = [ToolCall(tool=a["tool"], args=fill(a.get("args", {}), vars)) for a in sop.actions]
        return Plan(intent=top_intent, confidence=top, margin=margin, steps=steps, vars=vars, sources=sources,
                    rationale=f"RAG matched {sop.sop_id} ({sop.title}) with score {top:.2f}, margin {margin:.2f}")


# ------------------------------------------------------------------------------------------
# LLM planners
# ------------------------------------------------------------------------------------------
ROUTE_TOOL = {
    "name": "route_request",
    "description": "Declare which SOP intent this request belongs to. Call exactly once, first. "
                   f"Use '{OUT_OF_SCOPE}' if no SOP applies.",
    "parameters": {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "description": "One of the listed SOP intents or out_of_scope"},
            "confidence": {"type": "number", "description": "0..1"},
            "rationale": {"type": "string", "description": "One sentence"},
        },
        "required": ["intent", "confidence"],
    },
}

SYSTEM_PROMPT = """You are an enterprise workflow copilot. You turn an employee request into a plan of tool calls.
Rules:
1. Call `route_request` once, first, with the best-matching SOP intent (or out_of_scope).
2. Then call the tools, in order, that the matched SOP requires. Only use tools listed for that SOP.
3. Use real values from the request and context. To refer to the Jira ticket you created earlier, write the
   literal placeholder {ticket_id}; to refer to an export or refund id use {export_id} / {refund_id}.
4. Sensitive tools (grants, revocations, refunds, exports, customer changes) are gated by human approval that the
   system adds automatically - still include them in the plan, never try to avoid approval.
5. If required information is missing or the request matches no SOP, call route_request with out_of_scope.
Return only tool calls."""


def _sop_brief(sop: SOP) -> str:
    acts = ", ".join(a["tool"] for a in sop.actions)
    return f"- intent={sop.intent} ({sop.sop_id} {sop.title}); tools: {acts}; requires: {sop.requires or 'nothing'}"


def llm_user_prompt(ctx: PlanningContext) -> str:
    sops = "\n".join(_sop_brief(s) for s in ctx.sops.values())
    chunks = "\n\n".join(f"[{c.ref}] {c.text[:600]}" for c, _ in ctx.chunks)
    ent = {k: v for k, v in ctx.entities.items() if v and k != "explicit_target"}
    target = ctx.target["user_id"] if ctx.target else None
    return (
        f"REQUEST: {ctx.text}\n\n"
        f"REQUESTER: {json.dumps({k: ctx.requester.get(k) for k in ('user_id', 'name', 'role', 'manager_id')})}\n"
        f"NAMED TARGET USER (if any): {target}\n"
        f"EXTRACTED ENTITIES: {json.dumps(ent)}\n"
        f"OPEN TICKETS FOR REQUESTER: {len(ctx.open_tickets)}\n\n"
        f"AVAILABLE SOPS:\n{sops}\n\nMOST RELEVANT SOP EXCERPTS:\n{chunks}"
    )


def _plan_from_calls(calls: list[tuple[str, dict]], ctx: PlanningContext, sources: list[str]) -> Plan:
    route = next((a for n, a in calls if n == "route_request"), None)
    if route is None:
        raise ValueError("LLM did not call route_request")
    intent = str(route.get("intent", OUT_OF_SCOPE))
    conf = float(route.get("confidence", 0.0) or 0.0)
    rationale = str(route.get("rationale", ""))
    if intent == OUT_OF_SCOPE or intent not in ctx.sops:
        return _escalation(OUT_OF_SCOPE, conf, 0.0, "low_confidence", rationale or "LLM judged request out of scope", sources)
    steps = [ToolCall(tool=n, args=a) for n, a in calls if n != "route_request"]
    vars = build_vars(intent, ctx)
    return Plan(intent=intent, confidence=conf, margin=0.0, steps=steps, vars=vars, sources=sources, rationale=rationale)


class AnthropicPlanner:
    name = "anthropic"
    uses_open_tickets = True
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, settings: Settings, tools: dict[str, ToolSpec], client: httpx.Client | None = None,
                 api_key: str | None = None):
        self.s, self.tools = settings, tools
        self.client = client or httpx.Client(timeout=60)
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")

    def _tool_defs(self) -> list[dict]:
        defs = [{"name": ROUTE_TOOL["name"], "description": ROUTE_TOOL["description"], "input_schema": ROUTE_TOOL["parameters"]}]
        for t in self.tools.values():
            if t.name not in WRITE_TOOLS_HIDDEN:
                defs.append({"name": t.name, "description": t.description, "input_schema": t.parameters})
        return defs

    def plan(self, ctx: PlanningContext) -> Plan:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        body = {"model": self.s.anthropic_model, "max_tokens": 1500, "system": SYSTEM_PROMPT,
                "tools": self._tool_defs(), "tool_choice": {"type": "any"},
                "messages": [{"role": "user", "content": llm_user_prompt(ctx)}]}
        r = self.client.post(self.URL, json=body, headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                                           "content-type": "application/json"})
        r.raise_for_status()
        calls = [(b["name"], b.get("input", {})) for b in r.json().get("content", []) if b.get("type") == "tool_use"]
        return _plan_from_calls(calls, ctx, [c.ref for c, _ in ctx.chunks])


class OpenAICompatPlanner:
    name = "openai_compat"
    uses_open_tickets = True

    def __init__(self, settings: Settings, tools: dict[str, ToolSpec], client: httpx.Client | None = None,
                 api_key: str | None = None):
        self.s, self.tools = settings, tools
        self.client = client or httpx.Client(timeout=180)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "ollama")

    def _tool_defs(self) -> list[dict]:
        defs = [{"type": "function", "function": ROUTE_TOOL}]
        for t in self.tools.values():
            if t.name not in WRITE_TOOLS_HIDDEN:
                defs.append({"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}})
        return defs

    def plan(self, ctx: PlanningContext) -> Plan:
        body = {"model": self.s.openai_model, "tools": self._tool_defs(), "temperature": 0,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": llm_user_prompt(ctx)}]}
        r = self.client.post(self.s.openai_base_url.rstrip("/") + "/chat/completions", json=body,
                             headers={"Authorization": f"Bearer {self.api_key}"})
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        calls: list[tuple[str, dict]] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc["function"]
            args = fn.get("arguments", {})
            calls.append((fn["name"], json.loads(args) if isinstance(args, str) else args))
        return _plan_from_calls(calls, ctx, [c.ref for c, _ in ctx.chunks])


class FallbackPlanner:
    """Use an LLM planner when possible; on any error fall back to the deterministic planner."""

    def __init__(self, primary: Planner, fallback: Planner, on_fallback=None):
        self.primary, self.fallback, self.on_fallback = primary, fallback, on_fallback
        self.name = f"{primary.name}+fallback"
        self.uses_open_tickets = getattr(primary, "uses_open_tickets", False)

    def plan(self, ctx: PlanningContext) -> Plan:
        try:
            return self.primary.plan(ctx)
        except Exception as exc:  # network, auth, malformed output...
            if self.on_fallback:
                self.on_fallback(f"{type(exc).__name__}: {exc}")
            p = self.fallback.plan(ctx)
            p.rationale = f"[fallback after LLM planner error] {p.rationale}"
            return p


def make_planner(settings: Settings, tools: dict[str, ToolSpec], on_fallback=None) -> Planner:
    local = LocalPlanner(settings)
    if settings.planner == "anthropic":
        return FallbackPlanner(AnthropicPlanner(settings, tools), local, on_fallback)
    if settings.planner == "openai_compat":
        return FallbackPlanner(OpenAICompatPlanner(settings, tools), local, on_fallback)
    return local
