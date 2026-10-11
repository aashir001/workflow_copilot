"""Orchestrator: context gathering -> RAG routing -> plan -> policy -> execute with approvals, retries, audit.

Lifecycle of a request
    received -> context (directory, CRM, Jira) -> retrieve SOPs -> planner proposes steps
             -> policy checks (role, target scope, tool whitelist)
             -> execute steps in order
                  sensitive step -> pause as `awaiting_approval` (state persisted in SQLite)
                  transient tool failure -> retry with exponential backoff (idempotent writes)
                  permanent failure / low confidence / missing info -> hand off to a human (triage ticket)
             -> completed | denied | escalated | rejected
Every decision and every tool attempt is written to the hash-chained audit log.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from .audit import Store, now
from .config import Settings
from .entities import extract_entities
from .mocks import Backends, CallCtx, ToolError, TransientError
from .models import RequestIn, ResolveOut, StepResult
from .planner import OUT_OF_SCOPE, Planner, PlanningContext, fill, make_planner
from .policy import can_approve, can_initiate, check_target_scope, is_sensitive
from .rag import Retriever
from .tools import ToolSpec, build_registry, validate_args


class ApprovalError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code, self.message = status_code, message


@dataclass
class RunState:
    request_id: str
    requester_id: str
    text: str
    created_at: str = field(default_factory=now)
    intent: str = OUT_OF_SCOPE
    confidence: float = 0.0
    steps: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    vars: dict[str, Any] = field(default_factory=dict)
    next_index: int = 0
    approved_index: int | None = None
    approval_id: str | None = None
    target_id: str | None = None
    status: str = "completed"
    failure_type: str | None = None
    summary: str = ""
    sources: list[str] = field(default_factory=list)
    retries: int = 0
    active_ms: float = 0.0
    sim_ms: float = 0.0


class Copilot:
    def __init__(self, settings: Settings, backends: Backends, retriever: Retriever, store: Store,
                 planner: Planner | None = None):
        self.s, self.b, self.store, self.retriever = settings, backends, store, retriever
        self.sops = retriever.sops
        self.tools: dict[str, ToolSpec] = build_registry(backends)
        self.planner = planner or make_planner(
            settings, self.tools, on_fallback=lambda msg: self.store.log(None, "planner_fallback", detail=msg))

    # ======================================================================================
    # public API
    # ======================================================================================
    def resolve(self, req: RequestIn) -> ResolveOut:
        t0 = time.perf_counter()
        st = RunState(request_id="REQ-" + uuid.uuid4().hex[:10], requester_id=req.requester_id, text=req.text)
        ctx = CallCtx(request_id=st.request_id)
        self.store.log(st.request_id, "request_received", actor=req.requester_id, args={"text": req.text, "channel": req.channel})
        try:
            self._handle_new(st, ctx, req)
        except Exception as exc:  # never leave a request half-handled because of a bug
            self.store.log(st.request_id, "internal_error", status="error", detail=f"{type(exc).__name__}: {exc}")
            self._escalate(st, ctx, "internal_error", f"{type(exc).__name__}: {exc}")
        return self._finish(st, ctx, t0)

    def decide(self, approval_id: str, approver_id: str, decision: str, reason: str | None = None) -> ResolveOut:
        ap = self.store.get_approval(approval_id)
        if not ap:
            raise ApprovalError(404, f"approval {approval_id} not found")
        if ap["status"] != "pending":
            raise ApprovalError(409, f"approval {approval_id} was already {ap['status']}")
        row = self.store.get_request(ap["request_id"])
        st = RunState(**json.loads(row["state_json"]))
        approver = self.b.get_user(approver_id)
        if not approver:
            raise ApprovalError(404, f"approver {approver_id} not found")
        requester = self.b.get_user(st.requester_id)
        target = self.b.get_user(st.target_id) if st.target_id else None
        sop = self.sops[st.intent]
        ok, why = can_approve(approver, sop, requester, target)
        if not ok:
            self.store.log(st.request_id, "approval_refused", actor=approver_id, status="denied", detail=why)
            raise ApprovalError(403, why)
        new_status = "approved" if decision == "approve" else "rejected"
        if not self.store.decide_approval(approval_id, new_status, approver_id, reason):
            raise ApprovalError(409, f"approval {approval_id} was already decided")
        self.store.log(st.request_id, "approval_decided", actor=approver_id, tool=ap["tool"], status=new_status, detail=reason)

        t0 = time.perf_counter()
        ctx = CallCtx(request_id=st.request_id)
        try:
            if decision == "reject":
                self._reject(st, ctx, approver, reason)
            else:
                st.approved_index = st.next_index
                st.approval_id = None
                self._execute(st, ctx, sop)
        except Exception as exc:
            self.store.log(st.request_id, "internal_error", status="error", detail=f"{type(exc).__name__}: {exc}")
            self._escalate(st, ctx, "internal_error", f"{type(exc).__name__}: {exc}")
        return self._finish(st, ctx, t0)

    def get(self, request_id: str) -> ResolveOut | None:
        row = self.store.get_request(request_id)
        return self._out(RunState(**json.loads(row["state_json"]))) if row else None

    def metrics(self) -> dict:
        rows = self.store.all_requests()
        n = len(rows)
        if not n:
            return {"requests": 0}
        by_status = Counter(r["status"] for r in rows)
        lat = np.array([r["latency_ms"] for r in rows])
        sim = np.array([r["sim_latency_ms"] for r in rows])
        approvals = Counter(a["status"] for a in self.store.list_approvals())
        return {
            "requests": n,
            "by_status": dict(by_status),
            "by_intent": dict(Counter(r["intent"] for r in rows)),
            "failure_types": dict(Counter(r["failure_type"] for r in rows if r["failure_type"])),
            "task_completion_rate": round(by_status.get("completed", 0) / n, 4),
            "escalation_rate": round(by_status.get("escalated", 0) / n, 4),
            "total_retries": int(sum(r["retries"] for r in rows)),
            "latency_ms": {"p50": round(float(np.percentile(lat, 50)), 2), "p95": round(float(np.percentile(lat, 95)), 2)},
            "sim_latency_ms": {"p50": round(float(np.percentile(sim, 50)), 1), "p95": round(float(np.percentile(sim, 95)), 1)},
            "approvals": dict(approvals),
        }

    # ======================================================================================
    # request handling
    # ======================================================================================
    def _handle_new(self, st: RunState, ctx: CallCtx, req: RequestIn) -> None:
        # 1) who is asking?
        outcome, requester, msg, _ = self._invoke(st, ctx, self.tools["directory_lookup"], {"user_id": req.requester_id}, "ctx-requester")
        if outcome == "service_unavailable":
            return self._escalate(st, ctx, "service_unavailable", f"directory lookup failed: {msg}")
        if requester is None:
            st.status, st.failure_type = "denied", "unknown_user"
            st.summary = f"Requester {req.requester_id} was not found in the directory."
            self.store.log(st.request_id, "denied", actor=req.requester_id, status="denied", detail=st.summary)
            return

        # 2) entities + context from CRM / Jira
        entities = extract_entities(req.text, self.b.list_users(), requester)
        target = entities["explicit_target"]
        if target:
            self._read(st, ctx, "directory_lookup", {"user_id": target["user_id"]})
        order = self._read(st, ctx, "crm_lookup_order", {"order_id": entities["order_id"]}) if entities["order_id"] else None
        cust_id = entities["customer_id"] or (order or {}).get("customer_id")
        customer = self._read(st, ctx, "crm_lookup_customer", {"customer_id": cust_id}) if cust_id else None
        open_tickets: list[dict] = []
        if getattr(self.planner, "uses_open_tickets", False):
            res = self._read(st, ctx, "jira_search", {"reporter_id": requester["user_id"]})
            open_tickets = (res or {}).get("tickets", [])

        # 3) retrieve SOPs and plan
        chunks = self.retriever.search(req.text, k=4)
        scores = self.retriever.intent_scores(req.text)
        pctx = PlanningContext(text=req.text, requester=requester, entities=entities, intent_scores=scores, chunks=chunks,
                               sops=self.sops, retriever=self.retriever, target=target, customer=customer, order=order,
                               open_tickets=open_tickets)
        plan = self.planner.plan(pctx)
        st.intent, st.confidence, st.sources = plan.intent, round(plan.confidence, 4), plan.sources
        self.store.log(st.request_id, "plan_created", tool=None, status="escalate" if plan.escalate else "ok",
                       args={"intent": plan.intent, "confidence": round(plan.confidence, 3), "margin": round(plan.margin, 3),
                             "steps": [s.tool for s in plan.steps], "sources": plan.sources},
                       detail=plan.rationale)
        if plan.escalate:
            self._escalate(st, ctx, plan.escalate_reason or "low_confidence", plan.rationale)
            return

        # 4) policy gates (enforced here, not by the planner)
        sop = self.sops[plan.intent]
        ok, why = can_initiate(requester, sop)
        if not ok:
            return self._deny(st, "permission_denied", why)
        tgt_id = plan.vars.get("target_user_id")
        tgt = self.b.get_user(tgt_id) if tgt_id else None
        ok, why = check_target_scope(requester, tgt, sop)
        if not ok:
            return self._deny(st, "permission_denied", why)
        bad = [s.tool for s in plan.steps if s.tool not in sop.allowed_tools or s.tool not in self.tools or self.tools[s.tool].kind == "read"]
        if bad or not plan.steps:
            self.store.log(st.request_id, "policy_violation", status="blocked", detail=f"tools not allowed by {sop.sop_id}: {bad}" if bad else "empty plan")
            self._escalate(st, ctx, "policy_violation" if bad else "planner_error",
                           f"planner proposed tools outside {sop.sop_id}: {bad}" if bad else "planner returned no steps")
            return

        st.steps = [s.model_dump() for s in plan.steps]
        st.vars = {k: v for k, v in plan.vars.items() if isinstance(v, (str, int, float, bool)) or v is None}
        st.target_id = tgt_id
        self._execute(st, ctx, sop)

    # --------------------------------------------------------------------------------------
    def _execute(self, st: RunState, ctx: CallCtx, sop) -> None:
        while st.next_index < len(st.steps):
            i = st.next_index
            step = st.steps[i]
            spec = self.tools[step["tool"]]
            st.results = [r for r in st.results if not (r["index"] == i and r["status"] == "pending_approval")]
            args = fill(step["args"], st.vars)
            if spec.name == "jira_create_ticket":
                args.setdefault("reporter", st.requester_id)

            if is_sensitive(sop, spec.name) and st.approved_index != i:
                self._request_approval(st, ctx, sop, i, spec.name, args)
                return

            err = validate_args(spec, args)
            if err:
                st.results.append(StepResult(index=i, tool=spec.name, args=args, status="failed", error=err).model_dump())
                self.store.log(st.request_id, "step_invalid", tool=spec.name, args=args, status="error", detail=err)
                self._fail(st, ctx, i + 1, "tool_error", f"{spec.name}: {err}")
                return

            outcome, res, msg, attempts = self._invoke(st, ctx, spec, args, str(i))
            if outcome != "ok":
                st.results.append(StepResult(index=i, tool=spec.name, args=args, status="failed", attempts=attempts, error=msg).model_dump())
                self._fail(st, ctx, i + 1, outcome, f"{spec.name} failed: {msg}")
                return
            st.results.append(StepResult(index=i, tool=spec.name, args=args, status="ok", attempts=attempts, result=res).model_dump())
            st.vars.update({k: v for k, v in (res or {}).items() if k != "status" and isinstance(v, (str, int, float, bool))})
            if st.approved_index == i:
                st.approved_index = None
            st.next_index = i + 1

        st.status, st.failure_type = "completed", None
        tid = st.vars.get("ticket_id")
        n_ok = sum(1 for r in st.results if r["status"] == "ok")
        st.summary = f"Resolved as '{st.intent}' ({n_ok} actions executed" + (f", ticket {tid}" if tid else "") + ")."
        self.store.log(st.request_id, "completed", status="completed", detail=st.summary)

    def _request_approval(self, st: RunState, ctx: CallCtx, sop, i: int, tool: str, args: dict) -> None:
        aid = "APR-" + uuid.uuid4().hex[:8]
        self.store.create_approval(aid, st.request_id, i, tool, args, sop.approver_roles)
        st.approval_id, st.status = aid, "awaiting_approval"
        st.results.append(StepResult(index=i, tool=tool, args=args, status="pending_approval").model_dump())
        roles = "/".join(sop.approver_roles)
        st.summary = f"Waiting for {roles} approval ({aid}) before running {tool}."
        self.store.log(st.request_id, "approval_requested", tool=tool, args=args, status="pending", detail=f"needs {roles}; approval {aid}")
        self._best_effort(st, ctx, "slack_post_message", {
            "channel": "#approvals", "text": f"Approval {aid} needed from {roles}: {sop.title} for {st.vars.get('target_user_name') or st.vars.get('customer_id') or 'n/a'} ({tool})."}, "approval-notify")

    def _reject(self, st: RunState, ctx: CallCtx, approver: dict, reason: str | None) -> None:
        st.status, st.failure_type, st.approval_id = "rejected", "approval_rejected", None
        note = f"Rejected by {approver['name']}" + (f": {reason}" if reason else ".")
        if st.vars.get("ticket_id"):
            self._best_effort(st, ctx, "jira_update_ticket", {"ticket_id": st.vars["ticket_id"], "status": "Rejected", "comment": note}, "reject-ticket")
        self._best_effort(st, ctx, "slack_dm", {"user_id": st.requester_id, "text": f"Your request ({st.intent}) was not approved. {note}"}, "reject-dm")
        for j in range(st.next_index, len(st.steps)):
            st.results = [r for r in st.results if r["index"] != j]
            st.results.append(StepResult(index=j, tool=st.steps[j]["tool"], args=st.steps[j]["args"], status="skipped").model_dump())
        st.summary = note
        self.store.log(st.request_id, "rejected", actor=approver["user_id"], status="rejected", detail=note)

    def _deny(self, st: RunState, failure: str, why: str) -> None:
        st.status, st.failure_type, st.summary = "denied", failure, f"Not permitted: {why}."
        self.store.log(st.request_id, "denied", actor=st.requester_id, status="denied", detail=why)

    def _fail(self, st: RunState, ctx: CallCtx, from_index: int, failure: str, detail: str) -> None:
        for j in range(from_index, len(st.steps)):
            st.results.append(StepResult(index=j, tool=st.steps[j]["tool"], args=st.steps[j]["args"], status="skipped").model_dump())
        self._escalate(st, ctx, failure, detail)

    def _escalate(self, st: RunState, ctx: CallCtx, reason: str, detail: str) -> None:
        """Hand the request to a human: triage ticket + #ops-triage alert (best effort)."""
        st.status, st.failure_type = "escalated", reason
        st.approval_id = None
        res = self._best_effort(st, ctx, "jira_create_ticket", {
            "project": "TRIAGE", "issue_type": "Handoff", "priority": "High" if reason in ("service_unavailable", "internal_error", "policy_violation") else "Medium",
            "summary": f"Copilot handoff ({reason})", "description": f"{st.text}\n\nReason: {detail}", "reporter": st.requester_id}, "escalate-ticket")
        tid = (res or {}).get("ticket_id")
        self._best_effort(st, ctx, "slack_post_message", {"channel": "#ops-triage", "text": f"Handoff {tid or ''} ({reason}) from {st.requester_id}: {st.text[:140]}"}, "escalate-slack")
        st.summary = f"Handed to a human team ({reason}): {detail.rstrip('.')}." + (f" Triage ticket {tid}." if tid else "")
        self.store.log(st.request_id, "escalated", status="escalated", detail=f"{reason}: {detail}")

    # ======================================================================================
    # tool invocation
    # ======================================================================================
    def _invoke(self, st: RunState, ctx: CallCtx, spec: ToolSpec, args: dict, idx: str):
        """Run one tool with retries. Returns (outcome, result, error, attempts); outcome is
        'ok' | 'tool_error' (permanent) | 'service_unavailable' (retries exhausted)."""
        ctx.idem_key = f"{st.request_id}:{idx}" if spec.kind == "write" else None
        for attempt in range(1, self.s.max_attempts + 1):
            try:
                res = spec.fn(args, ctx)
                self.store.log(st.request_id, "tool_attempt", tool=spec.name, args=args, result=res, status="ok", detail=f"attempt {attempt}")
                return "ok", res, None, attempt
            except TransientError as e:
                last = attempt == self.s.max_attempts
                self.store.log(st.request_id, "tool_attempt", tool=spec.name, args=args, status="transient_error", detail=f"attempt {attempt}: {e}")
                if last:
                    return "service_unavailable", None, str(e), attempt
                st.retries += 1
                time.sleep(self.s.retry_base_delay_s * (2 ** (attempt - 1)))
            except ToolError as e:
                self.store.log(st.request_id, "tool_attempt", tool=spec.name, args=args, status="error", detail=f"attempt {attempt}: {e}")
                return "tool_error", None, str(e), attempt
        return "service_unavailable", None, "exhausted", self.s.max_attempts  # pragma: no cover

    def _read(self, st: RunState, ctx: CallCtx, tool: str, args: dict) -> dict | None:
        outcome, res, _, _ = self._invoke(st, ctx, self.tools[tool], args, f"ctx-{tool}")
        return res if outcome == "ok" else None

    def _best_effort(self, st: RunState, ctx: CallCtx, tool: str, args: dict, idx: str) -> dict | None:
        outcome, res, _, _ = self._invoke(st, ctx, self.tools[tool], args, idx)
        return res if outcome == "ok" else None

    # ======================================================================================
    # persistence / output
    # ======================================================================================
    def _finish(self, st: RunState, ctx: CallCtx, t0: float) -> ResolveOut:
        st.active_ms += (time.perf_counter() - t0) * 1000
        st.sim_ms += ctx.sim_ms
        self.store.save_request({
            "request_id": st.request_id, "created_at": st.created_at, "requester_id": st.requester_id, "text": st.text,
            "intent": st.intent, "confidence": st.confidence, "status": st.status, "failure_type": st.failure_type,
            "escalated": int(st.status == "escalated"), "retries": st.retries, "latency_ms": round(st.active_ms, 3),
            "sim_latency_ms": round(st.active_ms + st.sim_ms, 3), "summary": st.summary,
            "state_json": json.dumps(asdict(st), default=str)})
        return self._out(st)

    def _out(self, st: RunState) -> ResolveOut:
        return ResolveOut(
            request_id=st.request_id, status=st.status, intent=st.intent, confidence=st.confidence, summary=st.summary,
            failure_type=st.failure_type, approval_id=st.approval_id,
            steps=[StepResult(**r) for r in sorted(st.results, key=lambda r: r["index"])], sources=st.sources,
            latency_ms=round(st.active_ms, 3), sim_latency_ms=round(st.active_ms + st.sim_ms, 3), retries=st.retries)
