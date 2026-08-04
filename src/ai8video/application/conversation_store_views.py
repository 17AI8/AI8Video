"""Public projections for durable conversation rows."""

from __future__ import annotations

import sqlite3
from typing import AbstractSet, Any

from ai8video.application.conversation_store_schema import load_json


def conversation_dict(
    row: sqlite3.Row,
    *,
    busy_run_states: AbstractSet[str],
    protected_run_states: AbstractSet[str],
) -> dict[str, Any]:
    run_state = str(row["active_run_state"] or "") if "active_run_state" in row.keys() else ""
    lifecycle = lifecycle_state(int(row["message_count"]), run_state, busy_run_states)
    return {
        "id": row["conversation_id"],
        "title": row["title"],
        "executionMode": row["execution_mode"],
        "modeLocked": bool(row["mode_locked"]),
        "modeSwitchAllowed": not bool(row["mode_locked"]) and lifecycle != "busy",
        "revision": int(row["revision"]),
        "epoch": int(row["epoch"]),
        "messageCount": int(row["message_count"]),
        "activeRunId": row["active_run_id"],
        "agentRunState": run_state or None,
        "lifecycleState": lifecycle,
        "modelBinding": load_json(row["model_binding_json"]),
        "legacyAdopted": bool(row["legacy_adopted"]),
        "canReset": run_state not in protected_run_states,
        "canDelete": run_state not in protected_run_states,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def message_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["message_id"],
        "role": row["role"],
        "content": row["content"],
        "metadata": load_json(row["metadata_json"]),
        "createdAt": row["created_at"],
    }


def run_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["run_id"],
        "state": row["state"],
        "decisionCount": int(row["decision_count"]),
        "maxDecisions": int(row["max_decisions"]),
        "costUnits": float(row["cost_units"]),
        "costLimit": float(row["cost_limit"]),
        "pendingActionId": row["pending_action_id"],
    }


def lifecycle_state(
    message_count: int,
    run_state: str,
    busy_run_states: AbstractSet[str],
) -> str:
    if run_state in busy_run_states:
        return "busy"
    if run_state == "waiting_user":
        return "waiting_user"
    if run_state in {"failed", "cancelled"}:
        return run_state
    if run_state == "succeeded":
        return "completed"
    return "idle" if message_count else "empty"
