"""Agent 任务图的 SQLite 结构与底层映射。"""

from __future__ import annotations

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator

from ai8video.batch.agent_task_models import (
    CLAIMABLE_TASK_STATES,
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_QUEUED,
    TASK_RETRY_WAIT,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    TERMINAL_TASK_STATES,
    AgentTask,
    AgentTaskEvent,
    AgentTaskEventDraft,
    AgentTaskSpec,
    AgentTaskTransition,
    LegacyTaskUpdate,
    isoformat,
    require_text,
)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    generation_batch_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    parent_task_id TEXT,
    task_type TEXT NOT NULL,
    agent_role TEXT NOT NULL,
    state TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    idempotency_key TEXT,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT,
    error_type TEXT,
    error_message TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    worker_id TEXT,
    lease_expires_at TEXT,
    next_retry_at TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY(parent_task_id) REFERENCES agent_tasks(task_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tasks_idempotency
    ON agent_tasks(generation_batch_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_tasks_batch_state
    ON agent_tasks(generation_batch_id, state, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_lease
    ON agent_tasks(state, lease_expires_at);

CREATE TABLE IF NOT EXISTS agent_task_edges (
    task_id TEXT NOT NULL,
    depends_on_task_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(task_id, depends_on_task_id),
    CHECK(task_id <> depends_on_task_id),
    FOREIGN KEY(task_id) REFERENCES agent_tasks(task_id),
    FOREIGN KEY(depends_on_task_id) REFERENCES agent_tasks(task_id)
);

CREATE TABLE IF NOT EXISTS agent_task_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    task_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES agent_tasks(task_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_task_events_task
    ON agent_task_events(task_id, event_id);
"""


def initialize_schema(path: Path, timeout_seconds: float = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_connection(path, timeout_seconds) as connection:
        connection.executescript(_SCHEMA_SQL)


@contextmanager
def open_connection(path: Path, timeout_seconds: float = 10) -> Iterator[sqlite3.Connection]:
    with closing(_connect(path, timeout_seconds)) as connection, connection:
        yield connection


@contextmanager
def transaction(path: Path, timeout_seconds: float = 10) -> Iterator[sqlite3.Connection]:
    with closing(_connect(path, timeout_seconds)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()


def find_existing_task(
    connection: sqlite3.Connection,
    spec: AgentTaskSpec,
) -> AgentTask | None:
    row = connection.execute("SELECT * FROM agent_tasks WHERE task_id = ?", (spec.task_id,)).fetchone()
    if row is None and spec.idempotency_key:
        row = connection.execute(
            "SELECT * FROM agent_tasks WHERE generation_batch_id = ? AND idempotency_key = ?",
            (spec.generation_batch_id, spec.idempotency_key),
        ).fetchone()
    if row is None:
        return None
    task = row_to_task(row)
    identity = (task.generation_batch_id, task.session_id, task.task_type, task.agent_role)
    expected = (spec.generation_batch_id, spec.session_id, spec.task_type, spec.agent_role)
    if identity != expected:
        raise ValueError("task identity conflicts with an existing task")
    return task


def create_task_record(
    connection: sqlite3.Connection,
    spec: AgentTaskSpec,
) -> AgentTask:
    existing = find_existing_task(connection, spec)
    if existing is not None:
        return existing
    if spec.parent_task_id:
        parent = get_required_task(connection, spec.parent_task_id)
        if (parent.generation_batch_id, parent.session_id) != (
            spec.generation_batch_id,
            spec.session_id,
        ):
            raise ValueError("parent task must belong to the same batch and session")
    now = isoformat()
    connection.execute(
        """
        INSERT INTO agent_tasks (
            task_id, generation_batch_id, session_id, parent_task_id,
            task_type, agent_role, state, priority, idempotency_key,
            input_json, max_attempts, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            spec.task_id,
            spec.generation_batch_id,
            spec.session_id,
            spec.parent_task_id,
            spec.task_type,
            spec.agent_role,
            TASK_QUEUED,
            spec.priority,
            spec.idempotency_key,
            dump_json(spec.input_snapshot),
            spec.max_attempts,
            now,
            now,
        ),
    )
    insert_event(
        connection,
        AgentTaskEventDraft(
            task_id=spec.task_id,
            event_type="task_created",
            from_state=None,
            to_state=TASK_QUEUED,
            task_version=0,
        ),
    )
    return get_required_task(connection, spec.task_id)


def add_dependency_record(
    connection: sqlite3.Connection,
    task: AgentTask,
    dependency: AgentTask,
) -> bool:
    if task.task_id == dependency.task_id:
        raise ValueError("task cannot depend on itself")
    if (task.generation_batch_id, task.session_id) != (
        dependency.generation_batch_id,
        dependency.session_id,
    ):
        raise ValueError("dependency must belong to the same batch and session")
    if task.state != TASK_QUEUED:
        raise ValueError("dependencies can only be added to queued tasks")
    if dependency_creates_cycle(connection, task.task_id, dependency.task_id):
        raise ValueError("dependency would create a cycle")
    cursor = connection.execute(
        "INSERT OR IGNORE INTO agent_task_edges VALUES (?, ?, ?)",
        (task.task_id, dependency.task_id, isoformat()),
    )
    if not cursor.rowcount:
        return False
    insert_event(
        connection,
        AgentTaskEventDraft(
            task_id=task.task_id,
            event_type="dependency_added",
            from_state=task.state,
            to_state=task.state,
            task_version=task.version,
            payload={"dependsOnTaskId": dependency.task_id},
        ),
    )
    return True


def get_required_task(connection: sqlite3.Connection, task_id: str) -> AgentTask:
    row = connection.execute("SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError(f"agent task not found: {task_id}")
    return row_to_task(row)


def task_is_ready(connection: sqlite3.Connection, task: AgentTask) -> bool:
    if task.state not in CLAIMABLE_TASK_STATES or task.attempt >= task.max_attempts:
        return False
    if task.next_retry_at and task.next_retry_at > isoformat():
        return False
    row = connection.execute(
        """
        SELECT 1 FROM agent_task_edges AS edge
        JOIN agent_tasks AS dependency ON dependency.task_id = edge.depends_on_task_id
        WHERE edge.task_id = ? AND dependency.state <> ? LIMIT 1
        """,
        (task.task_id, TASK_SUCCEEDED),
    ).fetchone()
    return row is None


def select_ready_task(
    connection: sqlite3.Connection,
    task_types: Iterable[str],
    excluded_task_ids: Iterable[str],
    now_text: str,
) -> AgentTask | None:
    normalized_types = tuple(task_types)
    if not normalized_types:
        return None
    excluded_ids = tuple(excluded_task_ids)
    type_placeholders = ", ".join("?" for _ in normalized_types)
    exclusion_sql = ""
    if excluded_ids:
        exclusion_sql = f"AND task.task_id NOT IN ({', '.join('?' for _ in excluded_ids)})"
    row = connection.execute(
        f"""
        SELECT task.* FROM agent_tasks AS task
        WHERE task.state IN (?, ?)
          AND task.task_type IN ({type_placeholders})
          {exclusion_sql}
          AND task.attempt < task.max_attempts
          AND (task.next_retry_at IS NULL OR task.next_retry_at <= ?)
          AND NOT EXISTS (
              SELECT 1 FROM agent_task_edges AS edge
              JOIN agent_tasks AS dependency ON dependency.task_id = edge.depends_on_task_id
              WHERE edge.task_id = task.task_id AND dependency.state <> ?
          )
        ORDER BY task.priority ASC, task.created_at ASC, task.task_id ASC LIMIT 1
        """,
        (
            *CLAIMABLE_TASK_STATES,
            *normalized_types,
            *excluded_ids,
            now_text,
            TASK_SUCCEEDED,
        ),
    ).fetchone()
    return row_to_task(row) if row is not None else None


def dependency_creates_cycle(
    connection: sqlite3.Connection,
    task_id: str,
    dependency_id: str,
) -> bool:
    row = connection.execute(
        """
        WITH RECURSIVE dependencies(task_id) AS (
            SELECT depends_on_task_id FROM agent_task_edges WHERE task_id = ?
            UNION
            SELECT edge.depends_on_task_id
            FROM agent_task_edges AS edge
            JOIN dependencies ON edge.task_id = dependencies.task_id
        )
        SELECT 1 FROM dependencies WHERE task_id = ? LIMIT 1
        """,
        (dependency_id, task_id),
    ).fetchone()
    return row is not None


def insert_event(connection: sqlite3.Connection, event: AgentTaskEventDraft) -> None:
    connection.execute(
        """
        INSERT INTO agent_task_events (
            task_id, event_type, from_state, to_state,
            task_version, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.task_id,
            event.event_type,
            event.from_state,
            event.to_state,
            event.task_version,
            dump_json(event.payload),
            isoformat(),
        ),
    )


def row_to_task(row: sqlite3.Row) -> AgentTask:
    return AgentTask(
        task_id=row["task_id"],
        generation_batch_id=row["generation_batch_id"],
        session_id=row["session_id"],
        task_type=row["task_type"],
        agent_role=row["agent_role"],
        state=row["state"],
        version=int(row["version"]),
        input_snapshot=load_json(row["input_json"]) or {},
        output_snapshot=load_json(row["output_json"]),
        parent_task_id=row["parent_task_id"],
        idempotency_key=row["idempotency_key"],
        priority=int(row["priority"]),
        attempt=int(row["attempt"]),
        max_attempts=int(row["max_attempts"]),
        worker_id=row["worker_id"],
        lease_expires_at=row["lease_expires_at"],
        next_retry_at=row["next_retry_at"],
        error_type=row["error_type"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def row_to_event(row: sqlite3.Row) -> AgentTaskEvent:
    return AgentTaskEvent(
        event_id=int(row["event_id"]),
        task_id=row["task_id"],
        event_type=row["event_type"],
        from_state=row["from_state"],
        to_state=row["to_state"],
        task_version=int(row["task_version"]),
        payload=load_json(row["payload_json"]) or {},
        created_at=row["created_at"],
    )


def dump_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def dump_json_or_none(value: dict[str, Any] | None) -> str | None:
    return None if value is None else dump_json(value)


def load_json(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def transition_values(
    task: AgentTask,
    transition: AgentTaskTransition,
    target_state: str,
    now: str,
) -> tuple[object, ...]:
    result = transition.result
    if result is not None and require_text(result.task_id, "result.task_id") != task.task_id:
        raise ValueError("result task_id does not match transition task_id")
    replace_result = int(result is not None or target_state == TASK_SUCCEEDED)
    return (
        target_state,
        replace_result,
        dump_json_or_none(result.output_snapshot if result else None),
        replace_result,
        result.error_type if result else None,
        replace_result,
        result.error_message if result else None,
        transition.next_retry_at if target_state == TASK_RETRY_WAIT else None,
        task.version + 1,
        now,
        now if target_state in TERMINAL_TASK_STATES else None,
        task.task_id,
        task.version,
    )


def legacy_sync_values(
    task: AgentTask,
    update: LegacyTaskUpdate,
    target_state: str,
    now: str,
) -> tuple[object, ...]:
    output_snapshot = None if target_state == TASK_CANCELLED else update.result_snapshot
    error_type = None if target_state == TASK_CANCELLED else update.error_type
    error_message = None if target_state == TASK_CANCELLED else update.error_message
    replace_error = int(
        target_state in TERMINAL_TASK_STATES
        or error_type is not None
        or error_message is not None
    )
    return (
        target_state,
        update.worker_id,
        dump_json_or_none(update.input_snapshot),
        dump_json_or_none(output_snapshot),
        replace_error,
        error_type,
        replace_error,
        error_message,
        target_state,
        TASK_RUNNING,
        TASK_RUNNING,
        target_state,
        TASK_SUCCEEDED,
        TASK_FAILED,
        TASK_CANCELLED,
        update.lease_expires_at,
        task.version + 1,
        now,
        target_state,
        TASK_RUNNING,
        now,
        now if target_state in TERMINAL_TASK_STATES else task.completed_at,
        task.task_id,
        task.version,
    )


def _connect(path: Path, timeout_seconds: float) -> sqlite3.Connection:
    timeout = max(0.0, float(timeout_seconds))
    connection = sqlite3.connect(path, timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {max(0, int(timeout * 1000))}")
    return connection
