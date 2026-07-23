"""多 Agent 任务图的持久化状态机。

当前仍由单进程执行任务；这里先固定任务、依赖、事件、CAS 和租约协议，
后续替换执行 transport 时不再改动持久化语义。
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
import time

from ai8video.batch.agent_task_models import (
    LEASED_TASK_STATES,
    TASK_CANCEL_REQUESTED,
    TASK_CANCELLED,
    TASK_QUEUED,
    TASK_RECOVERY_REQUIRED,
    TASK_RETRY_WAIT,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    TASK_TRANSITIONS,
    TERMINAL_TASK_STATES,
    AgentTask,
    AgentTaskEvent,
    AgentTaskEventDraft,
    AgentResult,
    AgentTaskSpec,
    AgentTaskTransition,
    LegacyTaskUpdate,
    isoformat,
    legacy_target_state,
    normalize_spec,
    require_state,
    require_text,
)
from ai8video.batch.agent_task_storage import (
    add_dependency_record,
    create_task_record,
    get_required_task,
    initialize_schema,
    insert_event,
    legacy_sync_values,
    open_connection,
    row_to_event,
    row_to_task,
    task_is_ready,
    transaction,
    transition_values,
)
from ai8video.batch.agent_task_inline import record_inline_task_result
from ai8video.batch.agent_task_claims import (
    claim_next_ready_record,
    claim_task_record,
    finish_owned_task_record,
    renew_owned_lease_record,
    request_cancel_record,
    requeue_replay_safe_records,
    settle_blocked_task_records,
)


_TRANSITION_SQL = """
UPDATE agent_tasks
SET state = ?, output_json = CASE WHEN ? THEN ? ELSE output_json END,
    error_type = CASE WHEN ? THEN ? ELSE error_type END,
    error_message = CASE WHEN ? THEN ? ELSE error_message END,
    next_retry_at = ?, lease_expires_at = NULL,
    version = ?, updated_at = ?, completed_at = ?
WHERE task_id = ? AND version = ?
"""

_LEGACY_SYNC_SQL = """
UPDATE agent_tasks
SET state = ?, worker_id = COALESCE(?, worker_id),
    input_json = COALESCE(?, input_json), output_json = COALESCE(?, output_json),
    error_type = CASE WHEN ? THEN ? ELSE error_type END,
    error_message = CASE WHEN ? THEN ? ELSE error_message END,
    attempt = CASE WHEN ? = ? AND state <> ? THEN attempt + 1 ELSE attempt END,
    lease_expires_at = CASE
        WHEN ? IN (?, ?, ?) THEN NULL
        ELSE COALESCE(?, lease_expires_at)
    END,
    version = ?, updated_at = ?,
    started_at = CASE WHEN ? = ? THEN COALESCE(started_at, ?) ELSE started_at END,
    completed_at = ?
WHERE task_id = ? AND version = ?
"""


class AgentTaskLedger:
    """SQLite 任务图；所有状态写入都带版本并产生审计事件。"""

    def __init__(self, path: str | Path, timeout_seconds: float = 10) -> None:
        self.path = Path(path)
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self._initialized = False
        self._initialize_lock = Lock()

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            initialize_schema(self.path, self.timeout_seconds)
            self._initialized = True

    def create_task(self, spec: AgentTaskSpec) -> AgentTask:
        normalized = normalize_spec(spec)
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            return create_task_record(connection, normalized)

    def add_dependency(self, task_id: str, depends_on_task_id: str) -> bool:
        task_id = require_text(task_id, "task_id")
        dependency_id = require_text(depends_on_task_id, "depends_on_task_id")
        if task_id == dependency_id:
            raise ValueError("task cannot depend on itself")
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            task = get_required_task(connection, task_id)
            dependency = get_required_task(connection, dependency_id)
            return add_dependency_record(connection, task, dependency)

    def create_task_with_dependency(
        self,
        spec: AgentTaskSpec,
        dependency_task_id: str | None = None,
    ) -> AgentTask:
        normalized = normalize_spec(spec)
        dependency_id = str(dependency_task_id or "").strip() or None
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            task = create_task_record(connection, normalized)
            if dependency_id and task.state == TASK_QUEUED:
                dependency = get_required_task(connection, dependency_id)
                add_dependency_record(connection, task, dependency)
            return get_required_task(connection, task.task_id)

    def get_task(self, task_id: str) -> AgentTask | None:
        task_id = require_text(task_id, "task_id")
        self.initialize()
        with open_connection(self.path, self.timeout_seconds) as connection:
            row = connection.execute("SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row_to_task(row) if row is not None else None

    def list_tasks(self, generation_batch_id: str) -> list[AgentTask]:
        batch_id = require_text(generation_batch_id, "generation_batch_id")
        self.initialize()
        with open_connection(self.path, self.timeout_seconds) as connection:
            rows = connection.execute(
                "SELECT * FROM agent_tasks WHERE generation_batch_id = ? "
                "ORDER BY priority ASC, created_at ASC, task_id ASC",
                (batch_id,),
            ).fetchall()
        return [row_to_task(row) for row in rows]

    def list_dependencies(self, task_id: str) -> list[str]:
        task_id = require_text(task_id, "task_id")
        self.initialize()
        with open_connection(self.path, self.timeout_seconds) as connection:
            rows = connection.execute(
                "SELECT depends_on_task_id FROM agent_task_edges "
                "WHERE task_id = ? ORDER BY depends_on_task_id ASC",
                (task_id,),
            ).fetchall()
        return [str(row["depends_on_task_id"]) for row in rows]

    def record_inline_result(
        self,
        spec: AgentTaskSpec,
        result: AgentResult,
        *,
        dependency_task_id: str | None = None,
        worker_id: str = "inline-worker",
        event_payload: dict | None = None,
    ) -> AgentTask | None:
        normalized = normalize_spec(spec)
        worker_id = require_text(worker_id, "worker_id")
        if require_text(result.task_id, "result.task_id") != normalized.task_id:
            raise ValueError("result task_id does not match spec task_id")
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            return record_inline_task_result(
                connection,
                normalized,
                result,
                dependency_task_id=dependency_task_id,
                worker_id=worker_id,
                event_payload=event_payload,
            )

    def list_ready_tasks(self, generation_batch_id: str, limit: int = 100) -> list[AgentTask]:
        batch_id = require_text(generation_batch_id, "generation_batch_id")
        self.initialize()
        with open_connection(self.path, self.timeout_seconds) as connection:
            rows = connection.execute(
                """
                SELECT task.* FROM agent_tasks AS task
                WHERE task.generation_batch_id = ?
                  AND task.state IN (?, ?)
                  AND task.attempt < task.max_attempts
                  AND (task.next_retry_at IS NULL OR task.next_retry_at <= ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM agent_task_edges AS edge
                      JOIN agent_tasks AS dependency
                        ON dependency.task_id = edge.depends_on_task_id
                      WHERE edge.task_id = task.task_id
                        AND dependency.state <> ?
                  )
                ORDER BY task.priority ASC, task.created_at ASC, task.task_id ASC
                LIMIT ?
                """,
                (
                    batch_id,
                    TASK_QUEUED,
                    TASK_RETRY_WAIT,
                    isoformat(),
                    TASK_SUCCEEDED,
                    max(1, int(limit)),
                ),
            ).fetchall()
        return [row_to_task(row) for row in rows]

    def claim_task(
        self,
        task_id: str,
        worker_id: str,
        expected_version: int,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> AgentTask | None:
        task_id = require_text(task_id, "task_id")
        worker_id = require_text(worker_id, "worker_id")
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            task = get_required_task(connection, task_id)
            if task.version != int(expected_version):
                return None
            return claim_task_record(
                connection,
                task,
                worker_id,
                lease_seconds=lease_seconds,
                now=now,
            )

    def claim_next_ready(
        self,
        task_types: list[str] | tuple[str, ...] | set[str],
        worker_id: str,
        *,
        exclude_task_ids: list[str] | tuple[str, ...] | set[str] = (),
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> AgentTask | None:
        worker_id = require_text(worker_id, "worker_id")
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            return claim_next_ready_record(
                connection,
                task_types,
                worker_id,
                exclude_task_ids=exclude_task_ids,
                lease_seconds=lease_seconds,
                now=now,
            )

    def transition_task(self, transition: AgentTaskTransition) -> AgentTask | None:
        task_id = require_text(transition.task_id, "task_id")
        target_state = require_state(transition.target_state)
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            task = get_required_task(connection, task_id)
            if task.version != int(transition.expected_version):
                return None
            if task.state in LEASED_TASK_STATES:
                raise ValueError("leased tasks require an ownership-aware transition")
            if target_state in LEASED_TASK_STATES:
                raise ValueError("leased states require an ownership-aware transition")
            if target_state not in TASK_TRANSITIONS[task.state]:
                raise ValueError(f"invalid task transition: {task.state} -> {target_state}")
            if target_state == TASK_RETRY_WAIT and not transition.next_retry_at:
                raise ValueError("retry_wait transition requires next_retry_at")
            if target_state == TASK_RETRY_WAIT and task.attempt >= task.max_attempts:
                raise ValueError("task has no retry attempts remaining")
            now = isoformat()
            new_version = task.version + 1
            cursor = connection.execute(
                _TRANSITION_SQL,
                transition_values(task, transition, target_state, now),
            )
            if cursor.rowcount != 1:
                return None
            insert_event(
                connection,
                AgentTaskEventDraft(
                    task_id=task_id,
                    event_type="task_transitioned",
                    from_state=task.state,
                    to_state=target_state,
                    task_version=new_version,
                    payload=dict(transition.event_payload or {}),
                ),
            )
            return get_required_task(connection, task_id)

    def renew_lease(
        self,
        task_id: str,
        worker_id: str,
        expected_version: int,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> AgentTask | None:
        task_id = require_text(task_id, "task_id")
        worker_id = require_text(worker_id, "worker_id")
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            return renew_owned_lease_record(
                connection,
                task_id,
                worker_id,
                expected_version,
                lease_seconds=lease_seconds,
                now=now,
            )

    def finish_claimed_task(
        self,
        task_id: str,
        worker_id: str,
        expected_version: int,
        result: AgentResult,
        *,
        retry_delay_seconds: float = 0,
        now: float | None = None,
    ) -> AgentTask | None:
        task_id = require_text(task_id, "task_id")
        worker_id = require_text(worker_id, "worker_id")
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            return finish_owned_task_record(
                connection,
                task_id,
                worker_id,
                expected_version,
                result,
                retry_delay_seconds=retry_delay_seconds,
                now=now,
            )

    def request_cancel(self, task_id: str, *, now: float | None = None) -> AgentTask:
        task_id = require_text(task_id, "task_id")
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            return request_cancel_record(connection, task_id, now=now)

    def mark_expired_leases_for_recovery(self, now: float | None = None) -> list[AgentTask]:
        now_text = isoformat(time.time() if now is None else now)
        recovered: list[AgentTask] = []
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            rows = connection.execute(
                "SELECT * FROM agent_tasks WHERE state IN (?, ?) "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
                (TASK_RUNNING, TASK_CANCEL_REQUESTED, now_text),
            ).fetchall()
            for row in rows:
                task = row_to_task(row)
                target_state = TASK_CANCELLED if task.state == TASK_CANCEL_REQUESTED else TASK_RECOVERY_REQUIRED
                new_version = task.version + 1
                cursor = connection.execute(
                    "UPDATE agent_tasks SET state = ?, lease_expires_at = NULL, "
                    "error_type = CASE WHEN ? = ? THEN 'CancelledAfterLeaseExpiry' ELSE error_type END, "
                    "error_message = CASE WHEN ? = ? THEN 'cancelled task lease expired' ELSE error_message END, "
                    "completed_at = CASE WHEN ? = ? THEN ? ELSE completed_at END, "
                    "version = ?, updated_at = ? "
                    "WHERE task_id = ? AND version = ?",
                    (
                        target_state,
                        target_state, TASK_CANCELLED,
                        target_state, TASK_CANCELLED,
                        target_state, TASK_CANCELLED, now_text,
                        new_version, now_text, task.task_id, task.version,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                event_type = "cancelled_lease_expired" if target_state == TASK_CANCELLED else "lease_expired"
                insert_event(
                    connection,
                    AgentTaskEventDraft(
                        task_id=task.task_id,
                        event_type=event_type,
                        from_state=task.state,
                        to_state=target_state,
                        task_version=new_version,
                    ),
                )
                recovered.append(get_required_task(connection, task.task_id))
        return recovered

    def settle_blocked_tasks(self, *, now: float | None = None) -> list[AgentTask]:
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            return settle_blocked_task_records(connection, now=now)

    def requeue_replay_safe_tasks(
        self,
        task_types: list[str] | tuple[str, ...] | set[str],
        *,
        now: float | None = None,
    ) -> list[AgentTask]:
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            return requeue_replay_safe_records(connection, task_types, now=now)

    def list_events(self, task_id: str) -> list[AgentTaskEvent]:
        task_id = require_text(task_id, "task_id")
        self.initialize()
        with open_connection(self.path, self.timeout_seconds) as connection:
            rows = connection.execute(
                "SELECT * FROM agent_task_events WHERE task_id = ? ORDER BY event_id ASC",
                (task_id,),
            ).fetchall()
        return [row_to_event(row) for row in rows]

    def sync_legacy_execution(self, update: LegacyTaskUpdate) -> AgentTask | None:
        task_id = require_text(update.task_id, "task_id")
        self.initialize()
        with transaction(self.path, self.timeout_seconds) as connection:
            return self.sync_legacy_execution_in_connection(connection, update)

    def sync_legacy_execution_in_connection(self, connection, update: LegacyTaskUpdate) -> AgentTask | None:
        task_id = require_text(update.task_id, "task_id")
        row = connection.execute("SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        task = row_to_task(row)
        if task.state in TERMINAL_TASK_STATES:
            return task
        target_state = legacy_target_state(update, task.state)
        if target_state != task.state and target_state not in TASK_TRANSITIONS[task.state]:
            return task
        new_version = task.version + 1
        now = isoformat()
        cursor = connection.execute(
            _LEGACY_SYNC_SQL,
            legacy_sync_values(task, update, target_state, now),
        )
        if cursor.rowcount != 1:
            return None
        insert_event(
            connection,
            AgentTaskEventDraft(
                task_id=task_id,
                event_type="legacy_execution_synced",
                from_state=task.state,
                to_state=target_state,
                task_version=new_version,
            ),
        )
        return get_required_task(connection, task_id)
