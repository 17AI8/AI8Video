"""Compact, durable context projection for the main Agent."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from ai8video.application.conversation_store import ConversationStoreError
from ai8video.application.conversation_store_schema import (
    dump_json,
    load_json,
    now_iso,
    open_connection,
    transaction,
)


def get_run_context(path: str | Path, run_id: str) -> dict[str, Any]:
    with open_connection(Path(path)) as connection:
        _require_run(connection, run_id)
        row = connection.execute(
            "SELECT context_json FROM agent_run_contexts WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return load_json(row["context_json"]) if row else {}


def update_run_context(
    path: str | Path,
    run_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    target = Path(path)
    with transaction(target) as connection:
        _require_run(connection, run_id)
        row = connection.execute(
            "SELECT context_json FROM agent_run_contexts WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        context = load_json(row["context_json"]) if row else {}
        context.update(updates)
        connection.execute(
            """
            INSERT INTO agent_run_contexts (run_id, context_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                context_json = excluded.context_json,
                updated_at = excluded.updated_at
            """,
            (run_id, dump_json(context), now_iso()),
        )
    return context


def build_agent_state_snapshot(path: str | Path, run_id: str) -> dict[str, Any]:
    with open_connection(Path(path)) as connection:
        run = _require_run(connection, run_id)
        context_row = connection.execute(
            "SELECT context_json FROM agent_run_contexts WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        actions = connection.execute(
            """
            SELECT * FROM agent_actions
            WHERE run_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 8
            """,
            (run_id,),
        ).fetchall()
        observations = connection.execute(
            """
            SELECT * FROM agent_observations
            WHERE run_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 6
            """,
            (run_id,),
        ).fetchall()
    return {
        "run": {
            "id": run["run_id"],
            "state": run["state"],
            "decisionCount": int(run["decision_count"]),
            "maxDecisions": int(run["max_decisions"]),
            "costUnits": float(run["cost_units"]),
            "costLimit": float(run["cost_limit"]),
            "noProgressCount": int(run["no_progress_count"]),
        },
        "context": load_json(context_row["context_json"]) if context_row else {},
        "actions": [_action_snapshot(row) for row in reversed(actions)],
        "observations": [_observation_snapshot(row) for row in reversed(observations)],
    }


def latest_successful_action_output(
    path: str | Path,
    run_id: str,
    tool_names: tuple[str, ...],
) -> dict[str, Any] | None:
    placeholders = ",".join("?" for _ in tool_names)
    query = f"""
        SELECT output_json FROM agent_actions
        WHERE run_id = ? AND state = 'succeeded' AND tool_name IN ({placeholders})
        ORDER BY completed_at DESC, created_at DESC LIMIT 1
    """
    with open_connection(Path(path)) as connection:
        row = connection.execute(query, (run_id, *tool_names)).fetchone()
    return load_json(row["output_json"]) if row and row["output_json"] else None


def get_action_snapshot(path: str | Path, action_id: str) -> dict[str, Any]:
    with open_connection(Path(path)) as connection:
        row = connection.execute(
            "SELECT * FROM agent_actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
    if row is None:
        raise ConversationStoreError("agent_action_not_found", "Agent action 不存在。", status=404)
    return _action_snapshot(row)


def set_run_waiting_user(
    path: str | Path,
    run_id: str,
    *,
    code: str,
    message: str,
) -> None:
    with transaction(Path(path)) as connection:
        _require_run(connection, run_id)
        connection.execute(
            """
            UPDATE agent_runs
            SET state = 'waiting_user', error_code = ?, error_message = ?,
                completed_at = NULL, updated_at = ?
            WHERE run_id = ?
            """,
            (code, message, now_iso(), run_id),
        )


def set_run_queued(path: str | Path, run_id: str) -> None:
    with transaction(Path(path)) as connection:
        run = _require_run(connection, run_id)
        if run["state"] in {"succeeded", "failed", "cancelled"}:
            return
        connection.execute(
            """
            UPDATE agent_runs
            SET state = 'queued', error_code = NULL, error_message = NULL, updated_at = ?
            WHERE run_id = ?
            """,
            (now_iso(), run_id),
        )


def set_run_waiting_runtime(path: str | Path, run_id: str) -> None:
    with transaction(Path(path)) as connection:
        run = _require_run(connection, run_id)
        if run["state"] in {"succeeded", "failed", "cancelled"}:
            return
        connection.execute(
            """
            UPDATE agent_runs
            SET state = 'waiting_runtime', error_code = NULL, error_message = NULL,
                completed_at = NULL, updated_at = ?
            WHERE run_id = ?
            """,
            (now_iso(), run_id),
        )


def _require_run(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise ConversationStoreError("agent_run_not_found", "Agent run 不存在。", status=404)
    return row


def _action_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["action_id"],
        "runId": row["run_id"],
        "conversationId": row["conversation_id"],
        "tool": row["tool_name"],
        "toolName": row["tool_name"],
        "state": row["state"],
        "input": load_json(row["input_json"]),
        "output": load_json(row["output_json"]) if row["output_json"] else None,
        "sideEffects": bool(row["side_effects"]),
        "replaySafe": bool(row["replay_safe"]),
        "requiresApproval": bool(row["requires_approval"]),
        "costUnits": float(row["cost_units"]),
        "costCharged": bool(row["cost_charged"]),
        "attempt": int(row["attempt"]),
        "errorCode": row["error_code"],
    }


def _observation_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["observation_id"],
        "actionId": row["action_id"],
        "kind": row["kind"],
        "state": row["state"],
        "payload": load_json(row["payload_json"]),
        "terminal": bool(row["terminal"]),
    }
