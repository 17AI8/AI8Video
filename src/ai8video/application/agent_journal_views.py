"""Public projections for Agent journal rows."""

from __future__ import annotations

import sqlite3
from typing import Any

from ai8video.application.conversation_store_schema import load_json


def run_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["run_id"],
        "conversationId": row["conversation_id"],
        "state": row["state"],
        "decisionCount": int(row["decision_count"]),
        "maxDecisions": int(row["max_decisions"]),
        "costUnits": float(row["cost_units"]),
        "costLimit": float(row["cost_limit"]),
        "pendingActionId": row["pending_action_id"],
        "noProgressCount": int(row["no_progress_count"]),
        "error": error_dict(row),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def action_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["action_id"],
        "runId": row["run_id"],
        "conversationId": row["conversation_id"],
        "toolName": row["tool_name"],
        "idempotencyKey": row["idempotency_key"],
        "state": row["state"],
        "input": load_json(row["input_json"]),
        "output": load_json(row["output_json"], None) if row["output_json"] else None,
        "sideEffects": bool(row["side_effects"]),
        "replaySafe": bool(row["replay_safe"]),
        "requiresApproval": bool(row["requires_approval"]),
        "costUnits": float(row["cost_units"]),
        "costCharged": bool(row["cost_charged"]),
        "attempt": int(row["attempt"]),
        "maxAttempts": int(row["max_attempts"]),
        "error": error_dict(row),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def observation_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["observation_id"],
        "runId": row["run_id"],
        "actionId": row["action_id"],
        "kind": row["kind"],
        "state": row["state"],
        "payload": load_json(row["payload_json"]),
        "progressHash": row["progress_hash"],
        "terminal": bool(row["terminal"]),
        "createdAt": row["created_at"],
    }


def error_dict(row: sqlite3.Row) -> dict[str, str] | None:
    if not row["error_code"] and not row["error_message"]:
        return None
    return {
        "code": str(row["error_code"] or "unknown"),
        "message": str(row["error_message"] or ""),
    }
