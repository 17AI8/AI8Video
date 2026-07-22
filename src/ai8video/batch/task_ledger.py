from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from ai8video.core.paths import PROJECT_ROOT

DEFAULT_TASK_LEDGER_PATH = (
    PROJECT_ROOT / "temp" / "ai8video" / "task_ledger.sqlite3"
).resolve()


class TaskLedger:
    def __init__(self, path: str | Path = DEFAULT_TASK_LEDGER_PATH) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
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
        terminal_statuses = {"completed", "completed_with_error", "failed", "cancelled", "canceled"}
        completed_at = now if normalized_status in terminal_statuses else None
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
                   OR excluded.completed_at IS NOT NULL
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
        if self.get_generation_batch(generation_batch_id) is None:
            self.upsert_generation_batch(
                session_id=session_id,
                generation_batch_id=generation_batch_id,
                status="queued",
                phase="queued",
                progress={},
            )
        self.update_generation_execution(
            generation_batch_id,
            task_type=task_type,
            execution_state="queued",
            request_snapshot=request_snapshot,
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
        assignments: list[str] = []
        values: list[Any] = []
        fields = (
            ("task_type", task_type),
            ("execution_state", execution_state),
            ("worker_id", worker_id),
            ("request_json", _json_or_none(request_snapshot)),
            ("result_json", _json_or_none(result_snapshot)),
            ("lease_expires_at", lease_expires_at),
        )
        for field_name, value in fields:
            if value is not None:
                assignments.append(f"{field_name} = ?")
                values.append(value)
        if cancel_requested is not None:
            assignments.append("cancel_requested = ?")
            values.append(1 if cancel_requested else 0)
        if error is not None:
            assignments.extend(["error_type = ?", "error_message = ?"])
            values.extend([type(error).__name__, str(error)])
        if not assignments:
            return
        assignments.append("updated_at = ?")
        values.extend([_isoformat(time.time()), normalized_generation_batch_id])
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                f"UPDATE generation_batches SET {', '.join(assignments)} "
                "WHERE generation_batch_id = ? AND completed_at IS NULL",
                values,
            )

    @staticmethod
    def _select_columns() -> str:
        return (
            "SELECT generation_batch_id, session_id, status, phase, progress_json, "
            "created_at, updated_at, completed_at, task_type, execution_state, worker_id, "
            "cancel_requested, request_json, result_json, error_type, error_message, "
            "lease_expires_at"
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def _row_to_generation_batch(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "generationBatchId": row["generation_batch_id"],
        "sessionId": row["session_id"],
        "status": row["status"],
        "phase": row["phase"],
        "progress": json.loads(row["progress_json"] or "{}"),
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
