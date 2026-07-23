"""多 Agent 任务状态与传输契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any


TASK_QUEUED = "queued"
TASK_RUNNING = "running"
TASK_RETRY_WAIT = "retry_wait"
TASK_CANCEL_REQUESTED = "cancel_requested"
TASK_RECOVERY_REQUIRED = "recovery_required"
TASK_SUCCEEDED = "succeeded"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"

TERMINAL_TASK_STATES = frozenset({TASK_SUCCEEDED, TASK_FAILED, TASK_CANCELLED})
CLAIMABLE_TASK_STATES = frozenset({TASK_QUEUED, TASK_RETRY_WAIT})
LEASED_TASK_STATES = frozenset({TASK_RUNNING, TASK_CANCEL_REQUESTED})
TASK_TRANSITIONS = {
    TASK_QUEUED: {
        TASK_RUNNING,
        TASK_CANCEL_REQUESTED,
        TASK_CANCELLED,
        TASK_SUCCEEDED,
        TASK_FAILED,
    },
    TASK_RUNNING: {
        TASK_RETRY_WAIT,
        TASK_CANCEL_REQUESTED,
        TASK_RECOVERY_REQUIRED,
        TASK_SUCCEEDED,
        TASK_FAILED,
        TASK_CANCELLED,
    },
    TASK_RETRY_WAIT: {
        TASK_RUNNING,
        TASK_CANCEL_REQUESTED,
        TASK_RECOVERY_REQUIRED,
        TASK_FAILED,
        TASK_CANCELLED,
    },
    TASK_CANCEL_REQUESTED: {TASK_CANCELLED},
    TASK_RECOVERY_REQUIRED: {TASK_QUEUED, TASK_FAILED, TASK_CANCELLED},
    TASK_SUCCEEDED: set(),
    TASK_FAILED: set(),
    TASK_CANCELLED: set(),
}


@dataclass(frozen=True)
class AgentTaskSpec:
    task_id: str
    generation_batch_id: str
    session_id: str
    task_type: str
    agent_role: str
    input_snapshot: dict[str, Any] = field(default_factory=dict)
    parent_task_id: str | None = None
    idempotency_key: str | None = None
    priority: int = 100
    max_attempts: int = 1


@dataclass(frozen=True)
class AgentResult:
    task_id: str
    output_snapshot: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class AgentTaskTransition:
    task_id: str
    expected_version: int
    target_state: str
    result: AgentResult | None = None
    event_payload: dict[str, Any] | None = None
    next_retry_at: str | None = None


@dataclass(frozen=True)
class LegacyTaskUpdate:
    task_id: str
    execution_state: str | None = None
    worker_id: str | None = None
    cancel_requested: bool | None = None
    input_snapshot: dict[str, Any] | None = None
    result_snapshot: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
    lease_expires_at: str | None = None


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    generation_batch_id: str
    session_id: str
    task_type: str
    agent_role: str
    state: str
    version: int
    input_snapshot: dict[str, Any]
    output_snapshot: dict[str, Any] | None
    parent_task_id: str | None
    idempotency_key: str | None
    priority: int
    attempt: int
    max_attempts: int
    worker_id: str | None
    lease_expires_at: str | None
    next_retry_at: str | None
    error_type: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None


@dataclass(frozen=True)
class AgentTaskEvent:
    event_id: int
    task_id: str
    event_type: str
    from_state: str | None
    to_state: str | None
    task_version: int
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class AgentTaskEventDraft:
    task_id: str
    event_type: str
    from_state: str | None
    to_state: str | None
    task_version: int
    payload: dict[str, Any] = field(default_factory=dict)


def normalize_spec(spec: AgentTaskSpec) -> AgentTaskSpec:
    return AgentTaskSpec(
        task_id=require_text(spec.task_id, "task_id"),
        generation_batch_id=require_text(spec.generation_batch_id, "generation_batch_id"),
        session_id=require_text(spec.session_id, "session_id"),
        task_type=require_text(spec.task_type, "task_type"),
        agent_role=require_text(spec.agent_role, "agent_role"),
        input_snapshot=dict(spec.input_snapshot or {}),
        parent_task_id=str(spec.parent_task_id or "").strip() or None,
        idempotency_key=str(spec.idempotency_key or "").strip() or None,
        priority=int(spec.priority),
        max_attempts=max(1, int(spec.max_attempts)),
    )


def legacy_target_state(update: LegacyTaskUpdate, current_state: str) -> str:
    state_map = {
        "created": TASK_QUEUED,
        "queued": TASK_QUEUED,
        "running": TASK_RUNNING,
        "cancel_requested": TASK_CANCEL_REQUESTED,
        "cancelled": TASK_CANCELLED,
        "canceled": TASK_CANCELLED,
        "completed": TASK_SUCCEEDED,
        "failed": TASK_FAILED,
    }
    mapped_state = state_map.get(str(update.execution_state or "").strip(), current_state)
    if current_state in TERMINAL_TASK_STATES:
        return current_state
    if current_state == TASK_CANCEL_REQUESTED:
        return TASK_CANCELLED if mapped_state in TERMINAL_TASK_STATES else TASK_CANCEL_REQUESTED
    if update.cancel_requested:
        if mapped_state in TERMINAL_TASK_STATES:
            return TASK_CANCELLED
        return TASK_CANCEL_REQUESTED
    return mapped_state


def require_state(value: str) -> str:
    state = require_text(value, "task state")
    if state not in TASK_TRANSITIONS:
        raise ValueError(f"unsupported task state: {state}")
    return state


def require_text(value: str | None, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def isoformat(timestamp: float | None = None) -> str:
    value = time.time() if timestamp is None else timestamp
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
