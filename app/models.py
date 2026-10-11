from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["completed", "awaiting_approval", "escalated", "denied", "rejected"]


class RequestIn(BaseModel):
    requester_id: str
    text: str = Field(min_length=3, max_length=4000)
    channel: str = "api"


class ToolCall(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    intent: str
    confidence: float = 0.0
    margin: float = 0.0
    steps: list[ToolCall] = Field(default_factory=list)
    rationale: str = ""
    sources: list[str] = Field(default_factory=list)
    escalate: bool = False
    escalate_reason: str | None = None  # a failure_type string
    vars: dict[str, Any] = Field(default_factory=dict)


class StepResult(BaseModel):
    index: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: Literal["ok", "failed", "pending_approval", "skipped"]
    attempts: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None


class ResolveOut(BaseModel):
    request_id: str
    status: Status
    intent: str
    confidence: float
    summary: str
    failure_type: str | None = None
    approval_id: str | None = None
    steps: list[StepResult] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0          # measured compute time (excludes human approval wait)
    sim_latency_ms: float = 0.0      # measured + virtual per-service latency of the mock APIs
    retries: int = 0


class DecisionIn(BaseModel):
    approver_id: str
    decision: Literal["approve", "reject"]
    reason: str | None = None
