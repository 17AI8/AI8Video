"""Agent 任务的认领栅栏、严格租约完成与恢复收敛。"""

from __future__ import annotations

import sqlite3
import time
from typing import Iterable

from ai8video.batch.agent_task_models import (
    LEASED_TASK_STATES,
    TASK_CANCEL_REQUESTED,
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_QUEUED,
    TASK_RECOVERY_REQUIRED,
    TASK_RETRY_WAIT,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    TERMINAL_TASK_STATES,
    AgentResult,
    AgentTask,
    AgentTaskEventDraft,
    isoformat,
)
from ai8video.batch.agent_task_storage import (
    dump_json_or_none,
    get_required_task,
    insert_event,
    row_to_task,
    select_ready_task,
    task_is_ready,
)


def claim_task_record(
    connection: sqlite3.Connection,
    task: AgentTask,
    worker_id: str,
    *,
    lease_seconds: float,
    now: float | None = None,
) -> AgentTask | None:
    if not task_is_ready(connection, task):
        return None
    now_value = time.time() if now is None else float(now)
    new_version = task.version + 1
    cursor = connection.execute(
        """
        UPDATE agent_tasks
        SET state = ?, worker_id = ?, lease_expires_at = ?, next_retry_at = NULL,
            attempt = attempt + 1, version = ?, updated_at = ?,
            started_at = COALESCE(started_at, ?)
        WHERE task_id = ? AND version = ? AND state = ?
        """,
        (
            TASK_RUNNING,
            worker_id,
            isoformat(now_value + max(0.05, float(lease_seconds))),
            new_version,
            isoformat(now_value),
            isoformat(now_value),
            task.task_id,
            task.version,
            task.state,
        ),
    )
    if cursor.rowcount != 1:
        return None
    _insert_state_event(
        connection,
        task,
        TASK_RUNNING,
        new_version,
        "task_claimed",
        {"workerId": worker_id},
    )
    return get_required_task(connection, task.task_id)


def claim_next_ready_record(
    connection: sqlite3.Connection,
    task_types: Iterable[str],
    worker_id: str,
    *,
    exclude_task_ids: Iterable[str] = (),
    lease_seconds: float,
    now: float | None = None,
) -> AgentTask | None:
    normalized_types = tuple(sorted({str(item).strip() for item in task_types if str(item).strip()}))
    if not normalized_types:
        return None
    excluded_ids = tuple(sorted({str(item).strip() for item in exclude_task_ids if str(item).strip()}))
    now_value = time.time() if now is None else float(now)
    task = select_ready_task(
        connection,
        normalized_types,
        excluded_ids,
        isoformat(now_value),
    )
    if task is None:
        return None
    return claim_task_record(
        connection,
        task,
        worker_id,
        lease_seconds=lease_seconds,
        now=now_value,
    )


def renew_owned_lease_record(
    connection: sqlite3.Connection,
    task_id: str,
    worker_id: str,
    expected_version: int,
    *,
    lease_seconds: float,
    now: float | None = None,
) -> AgentTask | None:
    task = get_required_task(connection, task_id)
    now_value = time.time() if now is None else float(now)
    now_text = isoformat(now_value)
    if not _owns_live_lease(task, worker_id, expected_version, now_text):
        return None
    new_version = task.version + 1
    cursor = connection.execute(
        "UPDATE agent_tasks SET lease_expires_at = ?, version = ?, updated_at = ? "
        "WHERE task_id = ? AND version = ? AND worker_id = ? AND state = ?",
        (
            isoformat(now_value + max(0.05, float(lease_seconds))),
            new_version,
            now_text,
            task.task_id,
            task.version,
            worker_id,
            task.state,
        ),
    )
    if cursor.rowcount != 1:
        return None
    _insert_state_event(
        connection,
        task,
        task.state,
        new_version,
        "lease_renewed",
        {"workerId": worker_id},
    )
    return get_required_task(connection, task.task_id)


def finish_owned_task_record(
    connection: sqlite3.Connection,
    task_id: str,
    worker_id: str,
    expected_version: int,
    result: AgentResult,
    *,
    retry_delay_seconds: float = 0,
    now: float | None = None,
) -> AgentTask | None:
    task = get_required_task(connection, task_id)
    now_value = time.time() if now is None else float(now)
    now_text = isoformat(now_value)
    if not _owns_live_lease(task, worker_id, expected_version, now_text):
        return None
    if result.task_id != task.task_id:
        raise ValueError("result task_id does not match claimed task")
    target_state, effective_result, next_retry_at = _completion_plan(
        task,
        result,
        retry_delay_seconds=retry_delay_seconds,
        now=now_value,
    )
    return _write_owned_completion(
        connection,
        task,
        worker_id,
        target_state,
        effective_result,
        next_retry_at,
        now_text,
    )


def _write_owned_completion(
    connection: sqlite3.Connection,
    task: AgentTask,
    worker_id: str,
    target_state: str,
    result: AgentResult,
    next_retry_at: str | None,
    now_text: str,
) -> AgentTask | None:
    new_version = task.version + 1
    worker_value = None if target_state == TASK_RETRY_WAIT else worker_id
    cursor = connection.execute(
        """
        UPDATE agent_tasks
        SET state = ?, output_json = ?, error_type = ?, error_message = ?,
            next_retry_at = ?, worker_id = ?, lease_expires_at = NULL,
            version = ?, updated_at = ?, completed_at = ?
        WHERE task_id = ? AND version = ? AND worker_id = ? AND state = ?
        """,
        (
            target_state,
            dump_json_or_none(result.output_snapshot),
            result.error_type,
            result.error_message,
            next_retry_at,
            worker_value,
            new_version,
            now_text,
            now_text if target_state in TERMINAL_TASK_STATES else None,
            task.task_id,
            task.version,
            worker_id,
            task.state,
        ),
    )
    if cursor.rowcount != 1:
        return None
    _insert_state_event(
        connection,
        task,
        target_state,
        new_version,
        _completion_event_type(target_state),
        {"workerId": worker_id},
    )
    return get_required_task(connection, task.task_id)


def request_cancel_record(
    connection: sqlite3.Connection,
    task_id: str,
    *,
    now: float | None = None,
) -> AgentTask:
    task = get_required_task(connection, task_id)
    if task.state in TERMINAL_TASK_STATES or task.state == TASK_CANCEL_REQUESTED:
        return task
    now_text = isoformat(time.time() if now is None else float(now))
    target_state = TASK_CANCEL_REQUESTED if task.state == TASK_RUNNING else TASK_CANCELLED
    new_version = task.version + 1
    cursor = connection.execute(
        """
        UPDATE agent_tasks
        SET state = ?, error_type = ?, error_message = ?,
            lease_expires_at = CASE WHEN ? THEN lease_expires_at ELSE NULL END,
            worker_id = CASE WHEN ? THEN worker_id ELSE NULL END,
            next_retry_at = NULL, version = ?, updated_at = ?, completed_at = ?
        WHERE task_id = ? AND version = ?
        """,
        (
            target_state,
            "CancelRequested",
            "task cancellation was requested",
            int(target_state == TASK_CANCEL_REQUESTED),
            int(target_state == TASK_CANCEL_REQUESTED),
            new_version,
            now_text,
            now_text if target_state == TASK_CANCELLED else None,
            task.task_id,
            task.version,
        ),
    )
    if cursor.rowcount != 1:
        return get_required_task(connection, task.task_id)
    _insert_state_event(connection, task, target_state, new_version, "cancel_requested", {})
    return get_required_task(connection, task.task_id)


def settle_blocked_task_records(
    connection: sqlite3.Connection,
    *,
    now: float | None = None,
) -> list[AgentTask]:
    now_text = isoformat(time.time() if now is None else float(now))
    settled: list[AgentTask] = []
    while True:
        rows = connection.execute(
            """
            SELECT DISTINCT task.* FROM agent_tasks AS task
            JOIN agent_task_edges AS edge ON edge.task_id = task.task_id
            JOIN agent_tasks AS dependency ON dependency.task_id = edge.depends_on_task_id
            WHERE task.state IN (?, ?) AND dependency.state IN (?, ?)
            ORDER BY task.priority ASC, task.created_at ASC LIMIT 100
            """,
            (TASK_QUEUED, TASK_RETRY_WAIT, TASK_FAILED, TASK_CANCELLED),
        ).fetchall()
        if not rows:
            return settled
        changed = _cancel_blocked_rows(connection, rows, now_text)
        settled.extend(changed)
        if not changed:
            return settled


def requeue_replay_safe_records(
    connection: sqlite3.Connection,
    task_types: Iterable[str],
    *,
    now: float | None = None,
) -> list[AgentTask]:
    normalized_types = tuple(sorted({str(item).strip() for item in task_types if str(item).strip()}))
    if not normalized_types:
        return []
    placeholders = ", ".join("?" for _ in normalized_types)
    rows = connection.execute(
        f"SELECT * FROM agent_tasks WHERE state = ? AND task_type IN ({placeholders}) "
        "ORDER BY priority ASC, created_at ASC",
        (TASK_RECOVERY_REQUIRED, *normalized_types),
    ).fetchall()
    now_text = isoformat(time.time() if now is None else float(now))
    recovered: list[AgentTask] = []
    for row in rows:
        recovered_task = _requeue_recovery_row(connection, row_to_task(row), now_text)
        if recovered_task is not None:
            recovered.append(recovered_task)
    return recovered


def _owns_live_lease(
    task: AgentTask,
    worker_id: str,
    expected_version: int,
    now_text: str,
) -> bool:
    return bool(
        task.version == int(expected_version)
        and task.state in LEASED_TASK_STATES
        and task.worker_id == worker_id
        and task.lease_expires_at
        and task.lease_expires_at > now_text
    )


def _completion_plan(
    task: AgentTask,
    result: AgentResult,
    *,
    retry_delay_seconds: float,
    now: float,
) -> tuple[str, AgentResult, str | None]:
    if task.state == TASK_CANCEL_REQUESTED:
        return (
            TASK_CANCELLED,
            AgentResult(task.task_id, error_type="CancelledByRequest", error_message="task cancelled"),
            None,
        )
    if not result.error_type and not result.error_message:
        return TASK_SUCCEEDED, result, None
    if task.attempt < task.max_attempts:
        return TASK_RETRY_WAIT, result, isoformat(now + max(0.0, float(retry_delay_seconds)))
    return TASK_FAILED, result, None


def _cancel_blocked_rows(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
    now_text: str,
) -> list[AgentTask]:
    changed: list[AgentTask] = []
    for row in rows:
        task = row_to_task(row)
        cursor = connection.execute(
            "UPDATE agent_tasks SET state = ?, error_type = ?, error_message = ?, "
            "worker_id = NULL, lease_expires_at = NULL, next_retry_at = NULL, "
            "version = ?, updated_at = ?, completed_at = ? WHERE task_id = ? AND version = ?",
            (
                TASK_CANCELLED,
                "DependencyNotSucceeded",
                "a required dependency failed or was cancelled",
                task.version + 1,
                now_text,
                now_text,
                task.task_id,
                task.version,
            ),
        )
        if cursor.rowcount != 1:
            continue
        _insert_state_event(
            connection,
            task,
            TASK_CANCELLED,
            task.version + 1,
            "dependency_blocked",
            {},
        )
        changed.append(get_required_task(connection, task.task_id))
    return changed


def _requeue_recovery_row(
    connection: sqlite3.Connection,
    task: AgentTask,
    now_text: str,
) -> AgentTask | None:
    exhausted = task.attempt >= task.max_attempts
    target_state = TASK_FAILED if exhausted else TASK_QUEUED
    cursor = connection.execute(
        """
        UPDATE agent_tasks
        SET state = ?, worker_id = NULL, lease_expires_at = NULL, next_retry_at = NULL,
            error_type = ?, error_message = ?, version = ?, updated_at = ?, completed_at = ?
        WHERE task_id = ? AND version = ? AND state = ?
        """,
        (
            target_state,
            "RecoveryAttemptsExhausted" if exhausted else task.error_type,
            "safe task exhausted its recovery attempts" if exhausted else task.error_message,
            task.version + 1,
            now_text,
            now_text if exhausted else None,
            task.task_id,
            task.version,
            TASK_RECOVERY_REQUIRED,
        ),
    )
    if cursor.rowcount != 1:
        return None
    _insert_state_event(
        connection,
        task,
        target_state,
        task.version + 1,
        "recovery_exhausted" if exhausted else "recovery_requeued",
        {},
    )
    return get_required_task(connection, task.task_id)


def _completion_event_type(target_state: str) -> str:
    return {
        TASK_SUCCEEDED: "task_succeeded",
        TASK_FAILED: "task_failed",
        TASK_CANCELLED: "task_cancelled",
        TASK_RETRY_WAIT: "task_retry_scheduled",
    }[target_state]


def _insert_state_event(
    connection: sqlite3.Connection,
    task: AgentTask,
    target_state: str,
    version: int,
    event_type: str,
    payload: dict,
) -> None:
    insert_event(
        connection,
        AgentTaskEventDraft(
            task_id=task.task_id,
            event_type=event_type,
            from_state=task.state,
            to_state=target_state,
            task_version=version,
            payload=payload,
        ),
    )
