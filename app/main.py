"""FastAPI service. Run with:  uvicorn app.main:app --reload"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from .agent import ApprovalError, Copilot
from .audit import Store
from .config import Settings
from .mocks import Backends, CallCtx
from .models import DecisionIn, RequestIn, ResolveOut
from .rag import Retriever, load_sops


def build_copilot(settings: Settings | None = None) -> Copilot:
    s = settings or Settings()
    backends = Backends.from_data(s.data_dir, failure_rate=s.failure_rate, seed=s.seed)
    retriever = Retriever(load_sops(s.sop_dir))
    return Copilot(s, backends, retriever, Store(s.db_path))


def create_app(settings: Settings | None = None) -> FastAPI:
    holder: dict[str, Copilot] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        holder["c"] = build_copilot(settings)
        yield
        holder["c"].store.close()

    app = FastAPI(title="Enterprise Workflow Copilot (simulated)", version="1.0.0", lifespan=lifespan)

    def c() -> Copilot:
        return holder["c"]

    # ---- core workflow --------------------------------------------------------------------
    @app.get("/health")
    def health():
        return {"status": "ok", "planner": c().planner.name, "sops": len(c().sops)}

    @app.post("/requests", response_model=ResolveOut)
    def submit_request(req: RequestIn):
        """Classify the request with RAG over SOPs, then plan and execute (pausing for approval if needed)."""
        return c().resolve(req)

    @app.get("/requests/{request_id}", response_model=ResolveOut)
    def get_request(request_id: str):
        out = c().get(request_id)
        if not out:
            raise HTTPException(404, "request not found")
        return out

    @app.get("/approvals")
    def list_approvals(status: str | None = Query(None, pattern="^(pending|approved|rejected)$")):
        return c().store.list_approvals(status)

    @app.post("/approvals/{approval_id}/decision", response_model=ResolveOut)
    def decide(approval_id: str, body: DecisionIn):
        try:
            return c().decide(approval_id, body.approver_id, body.decision, body.reason)
        except ApprovalError as e:
            raise HTTPException(e.status_code, e.message)

    # ---- observability --------------------------------------------------------------------
    @app.get("/audit")
    def audit(request_id: str | None = None, limit: int = 200):
        return c().store.audit_rows(request_id, limit)

    @app.get("/audit/verify")
    def audit_verify():
        return c().store.verify_chain()

    @app.get("/metrics")
    def metrics():
        return c().metrics()

    # ---- RAG debugging --------------------------------------------------------------------
    @app.get("/retrieve")
    def retrieve(q: str, k: int = 4):
        r = c().retriever
        return {"intent_scores": {k_: round(v, 4) for k_, v in list(r.intent_scores(q).items())[:5]},
                "chunks": [{"ref": ch.ref, "score": round(sc, 4), "text": ch.text[:200]} for ch, sc in r.search(q, k)]}

    # ---- mock-service inspection ----------------------------------------------------------
    @app.get("/mock/jira/tickets")
    def mock_jira():
        return list(c().b.tickets.values())

    @app.get("/mock/slack/messages")
    def mock_slack():
        return c().b.messages

    @app.get("/mock/crm/users/{user_id}")
    def mock_user(user_id: str):
        u = c().b.get_user(user_id)
        if not u:
            raise HTTPException(404, "user not found")
        return u

    @app.get("/mock/crm/customers/{customer_id}")
    def mock_customer(customer_id: str):
        try:
            return c().b.crm_lookup_customer(customer_id, CallCtx())
        except Exception as e:
            raise HTTPException(404, str(e))

    return app


app = create_app()
