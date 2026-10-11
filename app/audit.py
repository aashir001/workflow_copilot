"""SQLite persistence: requests, approvals and a hash-chained, append-only audit log."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests(
  request_id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, requester_id TEXT, text TEXT,
  intent TEXT, confidence REAL, status TEXT, failure_type TEXT, escalated INTEGER DEFAULT 0,
  retries INTEGER DEFAULT 0, latency_ms REAL DEFAULT 0, sim_latency_ms REAL DEFAULT 0,
  summary TEXT, state_json TEXT);
CREATE TABLE IF NOT EXISTS approvals(
  approval_id TEXT PRIMARY KEY, request_id TEXT, step_index INTEGER, tool TEXT, args_json TEXT,
  approver_roles TEXT, status TEXT, created_at TEXT, decided_at TEXT, approver_id TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, request_id TEXT, event TEXT, actor TEXT, tool TEXT,
  args_json TEXT, result_json TEXT, status TEXT, detail TEXT, prev_hash TEXT, hash TEXT);
CREATE INDEX IF NOT EXISTS idx_audit_req ON audit_log(request_id);
"""

GENESIS = "0" * 64


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()
            row = self._db.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            self._last_hash = row["hash"] if row else GENESIS

    # ---- audit log -------------------------------------------------------------------------
    @staticmethod
    def _digest(prev: str, fields: dict[str, Any]) -> str:
        return hashlib.sha256((prev + json.dumps(fields, sort_keys=True, default=str)).encode()).hexdigest()

    def log(self, request_id: str | None, event: str, actor: str = "copilot", tool: str | None = None,
            args: Any = None, result: Any = None, status: str | None = None, detail: str | None = None) -> None:
        fields = {
            "ts": now(), "request_id": request_id, "event": event, "actor": actor, "tool": tool,
            "args_json": json.dumps(args, default=str) if args is not None else None,
            "result_json": json.dumps(result, default=str) if result is not None else None,
            "status": status, "detail": detail,
        }
        with self._lock:
            digest = self._digest(self._last_hash, fields)
            self._db.execute(
                "INSERT INTO audit_log(ts,request_id,event,actor,tool,args_json,result_json,status,detail,prev_hash,hash)"
                " VALUES(:ts,:request_id,:event,:actor,:tool,:args_json,:result_json,:status,:detail,:prev,:hash)",
                {**fields, "prev": self._last_hash, "hash": digest},
            )
            self._db.commit()
            self._last_hash = digest

    def audit_rows(self, request_id: str | None = None, limit: int = 500) -> list[dict]:
        with self._lock:
            if request_id:
                cur = self._db.execute("SELECT * FROM audit_log WHERE request_id=? ORDER BY id LIMIT ?", (request_id, limit))
            else:
                cur = self._db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    def verify_chain(self) -> dict:
        """Recompute every hash; any edited, deleted or reordered row breaks the chain."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        prev = GENESIS
        for r in rows:
            fields = {k: r[k] for k in ("ts", "request_id", "event", "actor", "tool", "args_json", "result_json", "status", "detail")}
            if r["prev_hash"] != prev or self._digest(prev, fields) != r["hash"]:
                return {"valid": False, "rows": len(rows), "first_bad_id": r["id"]}
            prev = r["hash"]
        return {"valid": True, "rows": len(rows), "first_bad_id": None}

    # ---- requests --------------------------------------------------------------------------
    def save_request(self, d: dict) -> None:
        d = {**d, "updated_at": now()}
        with self._lock:
            self._db.execute(
                "INSERT INTO requests(request_id,created_at,updated_at,requester_id,text,intent,confidence,status,failure_type,"
                "escalated,retries,latency_ms,sim_latency_ms,summary,state_json) VALUES(:request_id,:created_at,:updated_at,"
                ":requester_id,:text,:intent,:confidence,:status,:failure_type,:escalated,:retries,:latency_ms,:sim_latency_ms,"
                ":summary,:state_json) ON CONFLICT(request_id) DO UPDATE SET updated_at=:updated_at,intent=:intent,"
                "confidence=:confidence,status=:status,failure_type=:failure_type,escalated=:escalated,retries=:retries,"
                "latency_ms=:latency_ms,sim_latency_ms=:sim_latency_ms,summary=:summary,state_json=:state_json",
                d,
            )
            self._db.commit()

    def get_request(self, request_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        return dict(r) if r else None

    def all_requests(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM requests").fetchall()]

    # ---- approvals -------------------------------------------------------------------------
    def create_approval(self, approval_id: str, request_id: str, step_index: int, tool: str, args: dict,
                        approver_roles: list[str]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO approvals(approval_id,request_id,step_index,tool,args_json,approver_roles,status,created_at)"
                " VALUES(?,?,?,?,?,?,'pending',?)",
                (approval_id, request_id, step_index, tool, json.dumps(args, default=str), ",".join(approver_roles), now()),
            )
            self._db.commit()

    def get_approval(self, approval_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        return dict(r) if r else None

    def list_approvals(self, status: str | None = None) -> list[dict]:
        with self._lock:
            if status:
                cur = self._db.execute("SELECT * FROM approvals WHERE status=? ORDER BY created_at", (status,))
            else:
                cur = self._db.execute("SELECT * FROM approvals ORDER BY created_at")
            return [dict(r) for r in cur.fetchall()]

    def decide_approval(self, approval_id: str, status: str, approver_id: str, reason: str | None) -> bool:
        """Atomically move pending -> approved/rejected. Returns False if it was already decided."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE approvals SET status=?, decided_at=?, approver_id=?, reason=? WHERE approval_id=? AND status='pending'",
                (status, now(), approver_id, reason, approval_id),
            )
            self._db.commit()
            return cur.rowcount == 1

    def close(self) -> None:
        with self._lock:
            self._db.close()
