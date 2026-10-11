"""In-memory simulations of Jira, CRM (customers, orders, user access) and Slack.

* ToolError      -> permanent failure (bad input, business rule). Never retried.
* TransientError -> 5xx / timeout style failure. The orchestrator retries with backoff.
* Writes honour an idempotency key, so a retried call can never create a duplicate ticket/refund.
* Each call adds a virtual per-service latency to ``ctx.sim_ms`` instead of sleeping, so large
  evaluations stay fast while still reporting a realistic end-to-end time.
"""
from __future__ import annotations

import json
import random
import re
import threading
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

LATENCY_MS = {"jira": 350.0, "crm": 250.0, "slack": 150.0}
ACCESS_ROLES = {"viewer", "editor", "admin", "standard"}
ACCESS_SYSTEMS = {"crm", "jira", "data-warehouse", "finance-drive", "bi-dashboards", "github"}
CHANNELS = {"#it-access", "#it-onboarding", "#it-offboarding", "#finance-expenses", "#finance-refunds",
            "#incidents", "#privacy", "#approvals", "#ops-triage"}
CUSTOMER_FIELDS = {"email", "phone", "billing_address"}


class ToolError(Exception):
    """Permanent error: the request itself is invalid or violates a business rule."""


class TransientError(Exception):
    """Temporary error: safe to retry."""


@dataclass
class CallCtx:
    request_id: str = "-"
    idem_key: str | None = None
    sim_ms: float = 0.0


class Backends:
    def __init__(self, users: list[dict], customers: list[dict], failure_rate: float = 0.0, seed: int = 0):
        self.users: dict[str, dict] = {u["user_id"]: deepcopy(u) for u in users}
        self.customers: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        for c in deepcopy(customers):
            for o in c.get("orders", []):
                self.orders[o["order_id"]] = {**o, "customer_id": c["customer_id"], "refunded": 0.0}
            self.customers[c["customer_id"]] = c
        self.tickets: dict[str, dict] = {}
        self.messages: list[dict] = []
        self.refunds: list[dict] = []
        self.exports: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self._counters: dict[str, int] = defaultdict(int)
        self._idem: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._rng = random.Random(seed)
        self.failure_rate = failure_rate
        self._forced: dict[str, int] = {}

    @classmethod
    def from_data(cls, data_dir: Path, **kw) -> "Backends":
        data_dir = Path(data_dir)
        users = json.loads((data_dir / "users.json").read_text())
        customers = json.loads((data_dir / "crm.json").read_text())
        return cls(users, customers, **kw)

    # ---- fault injection -------------------------------------------------------------------
    def force_failures(self, service: str, n: int) -> None:
        """Make the next ``n`` calls to ``service`` fail transiently (used by tests)."""
        self._forced[service] = n

    def _enter(self, service: str, ctx: CallCtx) -> None:
        ctx.sim_ms += LATENCY_MS[service]
        self.calls.append((service, ctx.request_id))
        if self._forced.get(service, 0) > 0:
            self._forced[service] -= 1
            raise TransientError(f"{service} returned 503 (injected)")
        if self.failure_rate and self._rng.random() < self.failure_rate:
            raise TransientError(f"{service} returned 503")

    def _read(self, service: str, ctx: CallCtx, fn: Callable[[], dict]) -> dict:
        with self._lock:
            self._enter(service, ctx)
            return deepcopy(fn())

    def _write(self, service: str, ctx: CallCtx, fn: Callable[[], dict]) -> dict:
        with self._lock:
            self._enter(service, ctx)
            if ctx.idem_key and ctx.idem_key in self._idem:
                return deepcopy({**self._idem[ctx.idem_key], "deduplicated": True})
            res = fn()
            if ctx.idem_key:
                self._idem[ctx.idem_key] = deepcopy(res)
            return deepcopy(res)

    # ---- directory / CRM reads ---------------------------------------------------------------
    def list_users(self) -> list[dict]:
        return [deepcopy(u) for u in self.users.values()]

    def get_user(self, user_id: str) -> dict | None:
        u = self.users.get(user_id)
        return deepcopy(u) if u else None

    def directory_lookup(self, user_id: str, ctx: CallCtx) -> dict:
        def f():
            u = self.users.get(user_id)
            if not u:
                raise ToolError(f"user {user_id} not found")
            return u
        return self._read("crm", ctx, f)

    def crm_lookup_customer(self, customer_id: str, ctx: CallCtx) -> dict:
        def f():
            c = self.customers.get(customer_id)
            if not c:
                raise ToolError(f"customer {customer_id} not found")
            return c
        return self._read("crm", ctx, f)

    def crm_lookup_order(self, order_id: str, ctx: CallCtx) -> dict:
        def f():
            o = self.orders.get(order_id)
            if not o:
                raise ToolError(f"order {order_id} not found")
            return o
        return self._read("crm", ctx, f)

    def jira_search(self, reporter_id: str, ctx: CallCtx) -> dict:
        def f():
            open_ = [t for t in self.tickets.values() if t["reporter"] == reporter_id and t["status"] not in ("Resolved", "Rejected", "Closed")]
            return {"tickets": open_}
        return self._read("jira", ctx, f)

    # ---- Jira writes -------------------------------------------------------------------------
    def jira_create_ticket(self, args: dict, ctx: CallCtx) -> dict:
        def f():
            project = re.sub(r"[^A-Z]", "", str(args["project"]).upper()) or "GEN"
            self._counters[project] += 1
            tid = f"{project}-{self._counters[project]}"
            self.tickets[tid] = {
                "ticket_id": tid, "project": project, "issue_type": args.get("issue_type", "Task"),
                "priority": args.get("priority", "Medium"), "summary": args["summary"],
                "description": args.get("description", ""), "status": "Open", "reporter": args.get("reporter", ctx.request_id),
                "comments": [],
            }
            return {"ticket_id": tid, "status": "Open"}
        return self._write("jira", ctx, f)

    def jira_update_ticket(self, args: dict, ctx: CallCtx) -> dict:
        def f():
            t = self.tickets.get(args["ticket_id"])
            if not t:
                raise ToolError(f"ticket {args['ticket_id']} not found")
            if args.get("status"):
                t["status"] = args["status"]
            if args.get("comment"):
                t["comments"].append(args["comment"])
            return {"ticket_id": t["ticket_id"], "status": t["status"]}
        return self._write("jira", ctx, f)

    # ---- Slack writes ------------------------------------------------------------------------
    def slack_post_message(self, args: dict, ctx: CallCtx) -> dict:
        def f():
            if args["channel"] not in CHANNELS:
                raise ToolError(f"channel {args['channel']} does not exist")
            self.messages.append({"to": args["channel"], "text": args["text"]})
            return {"posted": True, "channel": args["channel"]}
        return self._write("slack", ctx, f)

    def slack_dm(self, args: dict, ctx: CallCtx) -> dict:
        def f():
            if args["user_id"] not in self.users:
                raise ToolError(f"user {args['user_id']} not found")
            self.messages.append({"to": args["user_id"], "text": args["text"]})
            return {"posted": True, "user_id": args["user_id"]}
        return self._write("slack", ctx, f)

    # ---- CRM writes --------------------------------------------------------------------------
    def crm_reset_user_password(self, args: dict, ctx: CallCtx) -> dict:
        def f():
            u = self.users.get(args["user_id"])
            if not u:
                raise ToolError(f"user {args['user_id']} not found")
            if u["status"] == "offboarded":
                raise ToolError(f"user {u['user_id']} is offboarded; reset refused")
            u["must_change_password"] = True
            return {"user_id": u["user_id"], "must_change_password": True}
        return self._write("crm", ctx, f)

    def crm_grant_access(self, args: dict, ctx: CallCtx) -> dict:
        def f():
            u = self.users.get(args["user_id"])
            if not u:
                raise ToolError(f"user {args['user_id']} not found")
            if args["role"] not in ACCESS_ROLES:
                raise ToolError(f"unknown role '{args['role']}'")
            if args["system"] not in ACCESS_SYSTEMS:
                raise ToolError(f"unknown system '{args['system']}'")
            u.setdefault("access", {})[args["system"]] = args["role"]
            return {"user_id": u["user_id"], "system": args["system"], "role": args["role"]}
        return self._write("crm", ctx, f)

    def crm_revoke_access(self, args: dict, ctx: CallCtx) -> dict:
        def f():
            u = self.users.get(args["user_id"])
            if not u:
                raise ToolError(f"user {args['user_id']} not found")
            if u["status"] == "offboarded":
                raise ToolError(f"user {u['user_id']} is already offboarded")
            n = len(u.get("access", {}))
            u["access"] = {}
            u["status"] = "offboarded"
            return {"user_id": u["user_id"], "revoked_systems": n, "status": "offboarded"}
        return self._write("crm", ctx, f)

    def crm_update_customer(self, args: dict, ctx: CallCtx) -> dict:
        def f():
            c = self.customers.get(args["customer_id"])
            if not c:
                raise ToolError(f"customer {args['customer_id']} not found")
            if args["field"] not in CUSTOMER_FIELDS:
                raise ToolError(f"field '{args['field']}' cannot be changed")
            if args["field"] == "email" and not re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.-]+", str(args["value"])):
                raise ToolError("invalid email address")
            c[args["field"]] = args["value"]
            return {"customer_id": c["customer_id"], "field": args["field"]}
        return self._write("crm", ctx, f)

    def crm_issue_refund(self, args: dict, ctx: CallCtx) -> dict:
        def f():
            o = self.orders.get(args["order_id"])
            if not o:
                raise ToolError(f"order {args['order_id']} not found")
            try:
                amount = float(args["amount"])
            except (TypeError, ValueError):
                raise ToolError("refund amount must be a number")
            if amount <= 0:
                raise ToolError("refund amount must be positive")
            if o["refunded"] + amount > o["amount"] + 1e-9:
                raise ToolError(f"refund {amount:.2f} exceeds refundable balance {o['amount'] - o['refunded']:.2f} on {o['order_id']}")
            o["refunded"] += amount
            rid = f"RF-{len(self.refunds) + 1:04d}"
            self.refunds.append({"refund_id": rid, "order_id": o["order_id"], "amount": amount})
            return {"refund_id": rid, "order_id": o["order_id"], "amount": amount}
        return self._write("crm", ctx, f)

    def crm_export_customer_data(self, args: dict, ctx: CallCtx) -> dict:
        def f():
            c = self.customers.get(args["customer_id"])
            if not c:
                raise ToolError(f"customer {args['customer_id']} not found")
            eid = f"EXP-{len(self.exports) + 1:04d}"
            self.exports.append({"export_id": eid, "customer_id": c["customer_id"]})
            return {"export_id": eid, "customer_id": c["customer_id"], "records": 1 + len(c.get("orders", []))}
        return self._write("crm", ctx, f)
