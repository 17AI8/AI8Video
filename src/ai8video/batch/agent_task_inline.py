"""无外部副作用的同步 Agent 观察任务单事务写入。"""

from __future__ import annotations

import sqlite3
from typing import Any

from ai8video.batch.agent_task_models import (
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_SUCCEEDED,
    TERMINAL_TASK_STATES,
    AgentResult,
    AgentTask,
    AgentTaskEventDraft,
    AgentTaskSpec,
    isoformat,
)
from ai8video.batch.agent_task_storage import (
    add_dependency_record,
    create_task_record,
    dump_json_or_none,
    get_required_task,
    insert_event,
)


def record_inline_task_result(
    connection: sqlite3.Connection,
    spec: AgentTaskSpec,
    result: AgentResult,
    *,
    dependency_task_id: str | None,
    worker_id: str,
    event_payload: dict[str, Any] | None,
) -> AgentTask | None:
    task = create_task_record(connection, spec)
    if task.state in TERMINAL_TASK_STATES:
        return task
    dependency = _inline_dependency(connection, task, dependency_task_id)
    if dependency is not None and dependency.state not in TERMINAL_TASK_STATES:
        raise RuntimeError("inline task dependency is not terminal")
    target_state = _inline_target_state(result, dependency)
    return _write_inline_completion(
        connection,
        task,
        _inline_result(task, result, dependency),
        target_state=target_state,
        worker_id=worker_id,
        event_payload=event_payload,
    )


def _inline_target_state(result: AgentResult, dependency: AgentTask | None) -> str:
    if dependency is not None and dependency.state != TASK_SUCCEEDED:
        return TASK_CANCELLED
    if result.error_type or result.error_message:
        return TASK_FAILED
    return TASK_SUCCEEDED


def _inline_dependency(
    connection: sqlite3.Connection,
    task: AgentTask,
    dependency_task_id: str | None,
) -> AgentTask | None:
    dependency_id = str(dependency_task_id or "").strip()
    if not dependency_id:
        return None
    dependency = get_required_task(connection, dependency_id)
    existing = connection.execute(
        "SELECT 1 FROM agent_task_edges WHERE task_id = ? AND depends_on_task_id = ?",
        (task.task_id, dependency.task_id),
    ).fetchone()
    if existing is None:
        add_dependency_record(connection, task, dependency)
    return dependency


def _inline_result(
    task: AgentTask,
    result: AgentResult,
    dependency: AgentTask | None,
) -> AgentResult:
    if dependency is None or dependency.state == TASK_SUCCEEDED:
        return result
    return AgentResult(
        task.task_id,
        error_type="DependencyNotSucceeded",
        error_message=f"dependency state: {dependency.state}",
    )


def _write_inline_completion(
    connection: sqlite3.Connection,
    task: AgentTask,
    result: AgentResult,
    *,
    target_state: str,
    worker_id: str,
    event_payload: dict[str, Any] | None,
) -> AgentTask | None:
    now = isoformat()
    cursor = connection.execute(
        """
        UPDATE agent_tasks
        SET state = ?, output_json = ?, error_type = ?, error_message = ?,
            worker_id = ?, attempt = MAX(attempt, 1), lease_expires_at = NULL,
            next_retry_at = NULL, version = ?, updated_at = ?,
            started_at = COALESCE(started_at, ?), completed_at = ?
        WHERE task_id = ? AND version = ?
        """,
        (
            target_state,
            dump_json_or_none(result.output_snapshot),
            result.error_type,
            result.error_message,
            worker_id,
            task.version + 1,
            now,
            now,
            now,
            task.task_id,
            task.version,
        ),
    )
    if cursor.rowcount != 1:
        return None
    insert_event(
        connection,
        AgentTaskEventDraft(
            task_id=task.task_id,
            event_type="task_transitioned",
            from_state=task.state,
            to_state=target_state,
            task_version=task.version + 1,
            payload=dict(event_payload or {}),
        ),
    )
    return get_required_task(connection, task.task_id)
