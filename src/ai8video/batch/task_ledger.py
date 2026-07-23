from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Any

from ai8video.batch.agent_task_ledger import AgentTaskLedger
from ai8video.batch.agent_task_models import (
    TASK_QUEUED,
    AgentTaskSpec,
    LegacyTaskUpdate,
    legacy_target_state,
)
from ai8video.batch.generation_execution_policy import (
    TERMINAL_EXECUTION_STATES as _TERMINAL_EXECUTION_STATES,
    execution_update_guard,
    resolve_sticky_cancellation,
)
from ai8video.core.legacy_payload import normalize_legacy_video_payload
from ai8video.core.paths import PROJECT_ROOT

DEFAULT_TASK_LEDGER_PATH = (
    PROJECT_ROOT / "temp" / "ai8video" / "task_ledger.sqlite3"
).resolve()
_TERMINAL_BATCH_STATUSES = frozenset(
    {"completed", "completed_with_error", "failed", "cancelled", "canceled"}
)


class TaskLedger:
    def __init__(
        self,
        path: str | Path = DEFAULT_TASK_LEDGER_PATH,
        timeout_seconds: float = 10,
    ) -> None:
        self.path = Path(path)
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.agent_tasks = AgentTaskLedger(self.path, timeout_seconds=self.timeout_seconds)
        self._initialized = False
        self._initialize_lock = Lock()

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS generation_batches (
                        generation_batch_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        phase TEXT,
                        progress_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT
                    )
                    """
                )
                self._ensure_execution_columns(connection)
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_generation_batches_session_updated
                    ON generation_batches(session_id, updated_at)
                    """
                )
            self.agent_tasks.initialize()
            self._initialized = True

    @staticmethod
    def _ensure_execution_columns(connection: sqlite3.Connection) -> None:
        existing = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(generation_batches)").fetchall()
        }
        columns = {
            "task_type": "TEXT",
            "execution_state": "TEXT",
            "worker_id": "TEXT",
            "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
            "request_json": "TEXT",
            "result_json": "TEXT",
            "error_type": "TEXT",
            "error_message": "TEXT",
            "lease_expires_at": "TEXT",
        }
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE generation_batches ADD COLUMN {name} {definition}"
                )

    def upsert_generation_batch(
        self,
        *,
        session_id: str,
        generation_batch_id: str,
        status: str,
        phase: str | None = None,
        progress: dict[str, Any] | None = None,
    ) -> None:
        normalized_session_id = _require_text(session_id, "session_id")
        normalized_generation_batch_id = _require_text(generation_batch_id, "generation_batch_id")
        normalized_status = _require_text(status, "status")
        now = _isoformat(time.time())
        completed_at = now if normalized_status in _TERMINAL_BATCH_STATUSES else None
        progress_json = json.dumps(progress or {}, ensure_ascii=False, sort_keys=True)
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO generation_batches (
                    generation_batch_id,
                    session_id,
                    status,
                    phase,
                    progress_json,
                    created_at,
                    updated_at,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(generation_batch_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    status = excluded.status,
                    phase = excluded.phase,
                    progress_json = excluded.progress_json,
                    updated_at = excluded.updated_at,
                    completed_at = COALESCE(excluded.completed_at, generation_batches.completed_at)
                WHERE generation_batches.completed_at IS NULL
                """,
                (
                    normalized_generation_batch_id,
                    normalized_session_id,
                    normalized_status,
                    str(phase or "").strip() or None,
                    progress_json,
                    now,
                    now,
                    completed_at,
                ),
            )

    def get_generation_batch(self, generation_batch_id: str) -> dict[str, Any] | None:
        normalized_generation_batch_id = _require_text(generation_batch_id, "generation_batch_id")
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                f"""
                {self._select_columns()}
                FROM generation_batches
                WHERE generation_batch_id = ?
                """,
                (normalized_generation_batch_id,),
            ).fetchone()
        return _row_to_generation_batch(row)

    def get_latest_generation_batch_for_session(self, session_id: str) -> dict[str, Any] | None:
        normalized_session_id = _require_text(session_id, "session_id")
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                f"""
                {self._select_columns()}
                FROM generation_batches
                WHERE session_id = ?
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                LIMIT 1
                """,
                (normalized_session_id,),
            ).fetchone()
        return _row_to_generation_batch(row)

    def list_active_generation_batches(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                {self._select_columns()}
                FROM generation_batches
                WHERE completed_at IS NULL
                ORDER BY updated_at ASC
                """
            ).fetchall()
        return [_row_to_generation_batch(row) for row in rows if row is not None]

    def ensure_generation_batch(
        self,
        *,
        session_id: str,
        generation_batch_id: str,
        task_type: str = "chat_generation",
        request_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """在生成尚未进入进度状态机前先登记任务事实。"""
        safe_request_snapshot = _safe_request_snapshot(request_snapshot)
        self._insert_generation_batch_if_missing(
            session_id=session_id,
            generation_batch_id=generation_batch_id,
            task_type=task_type,
            request_snapshot=safe_request_snapshot,
        )
        self.agent_tasks.create_task(
            AgentTaskSpec(
                task_id=generation_batch_id,
                generation_batch_id=generation_batch_id,
                session_id=session_id,
                task_type=task_type,
                agent_role="supervisor",
                input_snapshot=dict(safe_request_snapshot or {}),
                idempotency_key=f"generation-batch:{generation_batch_id}",
            )
        )

    def update_generation_execution(
        self,
        generation_batch_id: str,
        *,
        task_type: str | None = None,
        execution_state: str | None = None,
        worker_id: str | None = None,
        cancel_requested: bool | None = None,
        request_snapshot: dict[str, Any] | None = None,
        result_snapshot: dict[str, Any] | None = None,
        error: BaseException | str | None = None,
        lease_expires_at: str | None = None,
    ) -> None:
        normalized_generation_batch_id = _require_text(generation_batch_id, "generation_batch_id")
        legacy_update = _build_legacy_update(
            normalized_generation_batch_id,
            execution_state=execution_state,
            worker_id=worker_id,
            cancel_requested=cancel_requested,
            request_snapshot=request_snapshot,
            result_snapshot=result_snapshot,
            error=error,
            lease_expires_at=lease_expires_at,
        )
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _apply_generation_execution_update(
                connection,
                self.agent_tasks,
                task_type,
                legacy_update,
            )

    def _insert_generation_batch_if_missing(
        self,
        *,
        session_id: str,
        generation_batch_id: str,
        task_type: str,
        request_snapshot: dict[str, Any] | None,
    ) -> bool:
        normalized_session_id = _require_text(session_id, "session_id")
        normalized_batch_id = _require_text(generation_batch_id, "generation_batch_id")
        now = _isoformat(time.time())
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO generation_batches (
                    generation_batch_id, session_id, status, phase, progress_json,
                    created_at, updated_at, task_type, execution_state, request_json
                ) VALUES (?, ?, 'queued', 'queued', '{}', ?, ?, ?, 'queued', ?)
                """,
                (
                    normalized_batch_id,
                    normalized_session_id,
                    now,
                    now,
                    task_type,
                    _json_or_none(request_snapshot),
                ),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _select_columns() -> str:
        return (
            "SELECT generation_batch_id, session_id, status, phase, progress_json, "
            "created_at, updated_at, completed_at, task_type, execution_state, worker_id, "
            "cancel_requested, request_json, result_json, error_type, error_message, "
            "lease_expires_at"
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.timeout_seconds)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            f"PRAGMA busy_timeout = {max(0, int(self.timeout_seconds * 1000))}"
        )
        return connection


def _execution_assignments(
    task_type: str | None,
    update: LegacyTaskUpdate,
) -> tuple[list[str], list[Any]]:
    assignments: list[str] = []
    values: list[Any] = []
    fields = (
        ("task_type", task_type),
        ("execution_state", update.execution_state),
        ("worker_id", update.worker_id),
        ("request_json", _json_or_none(update.input_snapshot)),
        ("result_json", _json_or_none(update.result_snapshot)),
        ("lease_expires_at", update.lease_expires_at),
        ("error_type", update.error_type),
        ("error_message", update.error_message),
    )
    for field_name, value in fields:
        if value is not None:
            assignments.append(f"{field_name} = ?")
            values.append(value)
    if update.cancel_requested is not None:
        assignments.append("cancel_requested = ?")
        values.append(1 if update.cancel_requested else 0)
    return assignments, values


def _error_fields(error: BaseException | str | None) -> tuple[str | None, str | None]:
    if error is None:
        return None, None
    return type(error).__name__, str(error)


def _build_legacy_update(
    task_id: str,
    *,
    execution_state: str | None,
    worker_id: str | None,
    cancel_requested: bool | None,
    request_snapshot: dict[str, Any] | None,
    result_snapshot: dict[str, Any] | None,
    error: BaseException | str | None,
    lease_expires_at: str | None,
) -> LegacyTaskUpdate:
    error_type, error_message = _error_fields(error)
    return LegacyTaskUpdate(
        task_id=task_id,
        execution_state=str(execution_state or "").strip().lower() or None,
        worker_id=worker_id,
        cancel_requested=cancel_requested,
        input_snapshot=_safe_request_snapshot(request_snapshot),
        result_snapshot=result_snapshot,
        error_type=error_type,
        error_message=error_message,
        lease_expires_at=lease_expires_at,
    )


def _resolve_execution_update(
    connection: sqlite3.Connection,
    update: LegacyTaskUpdate,
) -> LegacyTaskUpdate:
    row = connection.execute(
        "SELECT execution_state, cancel_requested FROM generation_batches "
        "WHERE generation_batch_id = ?",
        (update.task_id,),
    ).fetchone()
    if row is None:
        return update
    return resolve_sticky_cancellation(
        update,
        current_execution_state=row["execution_state"],
        current_cancel_requested=bool(row["cancel_requested"]),
    )


def _apply_generation_execution_update(
    connection: sqlite3.Connection,
    agent_tasks: AgentTaskLedger,
    task_type: str | None,
    update: LegacyTaskUpdate,
) -> None:
    resolved = _resolve_execution_update(connection, update)
    assignments, values = _execution_assignments(task_type, resolved)
    if not assignments:
        return
    assignments.append("updated_at = ?")
    values.append(_isoformat(time.time()))
    where_clause, where_values = execution_update_guard(
        resolved.task_id,
        resolved.execution_state,
    )
    cursor = connection.execute(
        f"UPDATE generation_batches SET {', '.join(assignments)} WHERE {where_clause}",
        [*values, *where_values],
    )
    if cursor.rowcount != 1:
        return
    synced = agent_tasks.sync_legacy_execution_in_connection(connection, resolved)
    expected = (
        legacy_target_state(resolved, TASK_QUEUED)
        if resolved.execution_state is not None
        else None
    )
    if synced is None or (expected is not None and synced.state != expected):
        raise RuntimeError("generation and root agent execution state diverged")


def _safe_request_snapshot(
    request_snapshot: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if request_snapshot is None:
        return None
    fields: dict[str, dict[str, Any]] = {}
    for key, value in sorted(request_snapshot.items(), key=lambda item: str(item[0])):
        field_name = str(key)
        if isinstance(value, str):
            fields[field_name] = {
                "type": "string",
                "length": len(value),
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            }
        elif isinstance(value, (list, tuple, set, dict)):
            fields[field_name] = {"type": type(value).__name__, "size": len(value)}
        else:
            fields[field_name] = {"type": type(value).__name__}
    return {
        "snapshotMode": "redacted",
        "fieldNames": list(fields),
        "fields": fields,
    }


def _row_to_generation_batch(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "generationBatchId": row["generation_batch_id"],
        "sessionId": row["session_id"],
        "status": row["status"],
        "phase": row["phase"],
        "progress": normalize_legacy_video_payload(json.loads(row["progress_json"] or "{}")),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "completedAt": row["completed_at"],
        "taskType": row["task_type"],
        "executionState": row["execution_state"],
        "workerId": row["worker_id"],
        "cancelRequested": bool(row["cancel_requested"]),
        "requestSnapshot": _load_json(row["request_json"]),
        "resultSnapshot": _load_json(row["result_json"]),
        "errorType": row["error_type"],
        "errorMessage": row["error_message"],
        "leaseExpiresAt": row["lease_expires_at"],
    }


def _require_text(value: str | None, field_name: str) -> str:
    normalized_value = str(value or "").strip()
    if not normalized_value:
        raise ValueError(f"{field_name} is required")
    return normalized_value


def _isoformat(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_or_none(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_json(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
