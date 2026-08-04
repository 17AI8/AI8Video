"""Server-authoritative conversation lifecycle store."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Callable, Iterable

from ai8video.application.conversation_store_schema import (
    dump_json,
    initialize_schema,
    load_json,
    now_iso,
    open_connection,
    transaction,
)
from ai8video.application.legacy_conversation_import import adopt_legacy_messages
from ai8video.application.conversation_store_views import (
    conversation_dict,
    message_dict,
    run_summary,
)
from ai8video.core.paths import PROJECT_ROOT


DEFAULT_CONVERSATION_STORE_PATH = (
    PROJECT_ROOT / "temp" / "ai8video" / "conversations.sqlite3"
).resolve()
EXECUTION_MODES = frozenset({"workflow", "agent"})
ACTIVE_RUN_STATES = frozenset({"deciding", "queued", "running", "waiting_runtime"})
BUSY_RUN_STATES = ACTIVE_RUN_STATES | {"cancelling"}
PROTECTED_RUN_STATES = BUSY_RUN_STATES | {"waiting_user"}


class ConversationStoreError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 409,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = int(status)
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": self.details}


class ConversationStore:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        max_conversations: int = 3,
        timeout_seconds: float = 10,
    ) -> None:
        configured = path or os.getenv("AI8VIDEO_CONVERSATION_STORE_PATH")
        self.path = Path(configured or DEFAULT_CONVERSATION_STORE_PATH).expanduser().resolve()
        self.max_conversations = max(1, int(max_conversations))
        self.timeout_seconds = float(timeout_seconds)
        initialize_schema(self.path, self.timeout_seconds)

    def list_conversations(self) -> list[dict[str, Any]]:
        with open_connection(self.path, self.timeout_seconds) as connection:
            rows = connection.execute(
                """
                SELECT conversations.*, agent_runs.state AS active_run_state
                FROM conversations
                LEFT JOIN agent_runs ON agent_runs.run_id = conversations.active_run_id
                WHERE conversations.deleted_at IS NULL
                ORDER BY conversations.updated_at DESC, conversations.created_at DESC
                """
            ).fetchall()
        items = [self._conversation_dict(row) for row in rows]
        if len(items) == 1:
            items[0]["canDelete"] = False
        return items

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with open_connection(self.path, self.timeout_seconds) as connection:
            row = self._required_row(connection, conversation_id)
            visible_count = self._visible_count(connection)
        item = self._conversation_dict(row)
        item["canDelete"] = visible_count > 1 and item["canDelete"]
        return item

    def create_conversation(
        self,
        *,
        title: str = "新对话",
        execution_mode: str = "workflow",
        conversation_id: str | None = None,
        legacy_adopted: bool = False,
        allow_over_limit: bool = False,
    ) -> dict[str, Any]:
        mode = self._require_mode(execution_mode)
        identifier = self._normalize_id(conversation_id) if conversation_id else self._new_id("conv")
        with transaction(self.path, self.timeout_seconds) as connection:
            visible_count = self._visible_count(connection)
            if not allow_over_limit and visible_count >= self.max_conversations:
                raise ConversationStoreError(
                    "conversation_limit_reached",
                    f"最多保留 {self.max_conversations} 个对话，请先删除一个再新建。",
                    details={"limit": self.max_conversations, "count": visible_count},
                )
            existing = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (identifier,),
            ).fetchone()
            if existing is not None:
                raise ConversationStoreError("conversation_exists", "对话已存在。")
            now = now_iso()
            connection.execute(
                """
                INSERT INTO conversations (
                    conversation_id, title, execution_mode, legacy_adopted,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (identifier, self._title(title), mode, int(legacy_adopted), now, now),
            )
            row = self._required_row(connection, identifier)
        return self._conversation_dict(row)

    def reconcile_legacy(self, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = [item for item in items if isinstance(item, dict)]
        with transaction(self.path, self.timeout_seconds) as connection:
            for item in normalized:
                identifier = self._normalize_id(item.get("id") or item.get("conversationId"))
                existing = connection.execute(
                    "SELECT conversation_id FROM conversations WHERE conversation_id = ?",
                    (identifier,),
                ).fetchone()
                if existing is not None:
                    continue
                messages = item.get("messages") if isinstance(item.get("messages"), list) else []
                mode = self._require_mode(item.get("executionMode") or item.get("mode") or "workflow")
                now = now_iso()
                connection.execute(
                    """
                    INSERT INTO conversations (
                        conversation_id, title, execution_mode, mode_locked, locked_at,
                        message_count, legacy_adopted, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        identifier,
                        self._title(item.get("title")),
                        mode,
                        int(bool(messages)),
                        now if messages else None,
                        len(messages),
                        now,
                        now,
                    ),
                )
                adopt_legacy_messages(connection, identifier, messages, created_at=now)
        return self.list_conversations()

    def set_execution_mode(
        self,
        conversation_id: str,
        execution_mode: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        mode = self._require_mode(execution_mode)
        with transaction(self.path, self.timeout_seconds) as connection:
            row = self._required_row(connection, conversation_id)
            self._check_revision(row, expected_revision)
            self._ensure_not_busy(row)
            if row["mode_locked"] or int(row["message_count"]) > 0:
                raise ConversationStoreError("conversation_mode_locked", "对话已经开始，不能切换执行模式。")
            if row["execution_mode"] != mode:
                connection.execute(
                    """
                    UPDATE conversations
                    SET execution_mode = ?, revision = revision + 1, updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (mode, now_iso(), row["conversation_id"]),
                )
            updated = self._required_row(connection, row["conversation_id"])
        return self._conversation_dict(updated)

    def lock_for_message(
        self,
        conversation_id: str,
        message: str,
        *,
        execution_mode: str,
        expected_revision: int | None,
        client_message_id: str | None,
        model_binding_factory: Callable[[], dict[str, Any]] | None = None,
        create_agent_run: bool = True,
    ) -> dict[str, Any]:
        content = str(message or "").strip()
        if not content:
            raise ConversationStoreError("empty_message", "消息不能为空。", status=400)
        mode = self._require_mode(execution_mode)
        client_id = str(client_message_id or "").strip()[:128] or None
        with transaction(self.path, self.timeout_seconds) as connection:
            row = self._required_row(connection, conversation_id)
            self._check_revision(row, expected_revision)
            if row["mode_locked"] and row["execution_mode"] != mode:
                raise ConversationStoreError("conversation_mode_locked", "对话执行模式已锁定。")
            duplicate = self._message_by_client_id(connection, row["conversation_id"], client_id)
            if duplicate is not None:
                return self._locked_message_result(connection, row, duplicate["message_id"])
            binding = load_json(row["model_binding_json"])
            first_message = int(row["message_count"]) == 0
            if first_message:
                binding = model_binding_factory() if model_binding_factory else {}
            next_title = row["title"]
            if first_message and str(next_title or "").strip() in {"", "新对话", "新会话"}:
                next_title = self._title_from_message(content)
            message_id = self._new_id("msg")
            now = now_iso()
            connection.execute(
                """
                INSERT INTO conversation_messages (
                    message_id, conversation_id, epoch, role, content,
                    client_message_id, created_at
                ) VALUES (?, ?, ?, 'user', ?, ?, ?)
                """,
                (message_id, row["conversation_id"], row["epoch"], content, client_id, now),
            )
            active_run_id = row["active_run_id"]
            active_run_state = str(row["active_run_state"] or "")
            if mode == "agent" and create_agent_run and (
                not active_run_id or active_run_state in {"succeeded", "failed", "cancelled"}
            ):
                active_run_id = self._insert_agent_run(connection, row, now)
            connection.execute(
                """
                UPDATE conversations
                SET title = ?, execution_mode = ?, mode_locked = 1,
                    locked_at = COALESCE(locked_at, ?),
                    message_count = message_count + 1,
                    active_run_id = ?, model_binding_json = ?,
                    revision = revision + 1, updated_at = ?
                WHERE conversation_id = ?
                """,
                (next_title, mode, now, active_run_id, dump_json(binding), now, row["conversation_id"]),
            )
            updated = self._required_row(connection, row["conversation_id"])
            result = self._locked_message_result(connection, updated, message_id)
        return result

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        role_name = str(role or "").strip().lower()
        if role_name not in {"assistant", "system", "tool"}:
            raise ConversationStoreError("invalid_message_role", "不支持这个消息角色。", status=400)
        with transaction(self.path, self.timeout_seconds) as connection:
            row = self._required_row(connection, conversation_id)
            message_id = self._new_id("msg")
            now = now_iso()
            connection.execute(
                """
                INSERT INTO conversation_messages (
                    message_id, conversation_id, epoch, role, content, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    row["conversation_id"],
                    row["epoch"],
                    role_name,
                    str(content or ""),
                    dump_json(metadata),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE conversations
                SET message_count = message_count + 1, revision = revision + 1, updated_at = ?
                WHERE conversation_id = ?
                """,
                (now, row["conversation_id"]),
            )
        return {"messageId": message_id, "role": role_name, "content": str(content or "")}

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with open_connection(self.path, self.timeout_seconds) as connection:
            row = self._required_row(connection, conversation_id)
            messages = connection.execute(
                """
                SELECT * FROM conversation_messages
                WHERE conversation_id = ? AND epoch = ?
                ORDER BY created_at, rowid
                """,
                (row["conversation_id"], row["epoch"]),
            ).fetchall()
        return [self._message_dict(message) for message in messages]

    def reset_conversation(self, conversation_id: str) -> dict[str, Any]:
        with transaction(self.path, self.timeout_seconds) as connection:
            row = self._required_row(connection, conversation_id)
            self._ensure_not_busy(row)
            now = now_iso()
            connection.execute(
                """
                UPDATE conversations
                SET epoch = epoch + 1, message_count = 0, mode_locked = 0,
                    locked_at = NULL, active_run_id = NULL, model_binding_json = '{}',
                    revision = revision + 1, updated_at = ?
                WHERE conversation_id = ?
                """,
                (now, row["conversation_id"]),
            )
            updated = self._required_row(connection, row["conversation_id"])
        return self._conversation_dict(updated)

    def delete_conversation(self, conversation_id: str) -> dict[str, Any]:
        with transaction(self.path, self.timeout_seconds) as connection:
            row = self._required_row(connection, conversation_id)
            self._ensure_not_busy(row)
            visible_count = self._visible_count(connection)
            if visible_count <= 1:
                raise ConversationStoreError(
                    "last_conversation_required",
                    "至少保留一个可用对话；最后一个对话不能删除。",
                )
            now = now_iso()
            connection.execute(
                """
                UPDATE conversations
                SET deleted_at = ?, active_run_id = NULL,
                    revision = revision + 1, updated_at = ?
                WHERE conversation_id = ?
                """,
                (now, now, row["conversation_id"]),
            )
        return {"conversationId": row["conversation_id"], "deleted": True}

    def _insert_agent_run(self, connection: sqlite3.Connection, row: sqlite3.Row, now: str) -> str:
        run_id = self._new_id("run")
        connection.execute(
            """
            INSERT INTO agent_runs (
                run_id, conversation_id, epoch, state, created_at, updated_at
            ) VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            (run_id, row["conversation_id"], row["epoch"], now, now),
        )
        return run_id

    def _locked_message_result(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        message_id: str,
    ) -> dict[str, Any]:
        current = self._required_row(connection, row["conversation_id"])
        run = None
        if current["active_run_id"]:
            run_row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (current["active_run_id"],),
            ).fetchone()
            run = self._run_summary(run_row) if run_row else None
        return {
            "conversation": self._conversation_dict(current),
            "messageId": message_id,
            "agentRun": run,
        }

    def _required_row(self, connection: sqlite3.Connection, conversation_id: str) -> sqlite3.Row:
        identifier = self._normalize_id(conversation_id)
        row = connection.execute(
            """
            SELECT conversations.*, agent_runs.state AS active_run_state
            FROM conversations
            LEFT JOIN agent_runs ON agent_runs.run_id = conversations.active_run_id
            WHERE conversations.conversation_id = ? AND conversations.deleted_at IS NULL
            """,
            (identifier,),
        ).fetchone()
        if row is None:
            raise ConversationStoreError("conversation_not_found", "对话不存在。", status=404)
        return row

    def _ensure_not_busy(self, row: sqlite3.Row) -> None:
        state = str(row["active_run_state"] or "")
        if state in PROTECTED_RUN_STATES:
            raise ConversationStoreError(
                "conversation_busy",
                "对话正在执行，暂时不能重置、删除或切换模式。",
                details={"runState": state, "activeRunId": row["active_run_id"]},
            )

    @staticmethod
    def _check_revision(row: sqlite3.Row, expected_revision: int | None) -> None:
        if expected_revision is None:
            return
        if int(row["revision"]) != int(expected_revision):
            raise ConversationStoreError(
                "conversation_revision_conflict",
                "对话已在其他请求中更新，请刷新后重试。",
                details={"expected": int(expected_revision), "actual": int(row["revision"])},
            )

    @staticmethod
    def _message_by_client_id(
        connection: sqlite3.Connection,
        conversation_id: str,
        client_message_id: str | None,
    ) -> sqlite3.Row | None:
        if not client_message_id:
            return None
        return connection.execute(
            """
            SELECT * FROM conversation_messages
            WHERE conversation_id = ? AND client_message_id = ?
            """,
            (conversation_id, client_message_id),
        ).fetchone()

    def _conversation_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return conversation_dict(
            row,
            busy_run_states=BUSY_RUN_STATES,
            protected_run_states=PROTECTED_RUN_STATES,
        )

    _message_dict = staticmethod(message_dict)
    _run_summary = staticmethod(run_summary)

    @staticmethod
    def _visible_count(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM conversations WHERE deleted_at IS NULL"
        ).fetchone()
        return int(row["count"])

    @staticmethod
    def _require_mode(value: Any) -> str:
        mode = str(value or "").strip().lower()
        if mode not in EXECUTION_MODES:
            raise ConversationStoreError("invalid_execution_mode", "执行模式必须是 workflow 或 agent。", status=400)
        return mode

    @staticmethod
    def _normalize_id(value: Any) -> str:
        identifier = str(value or "").strip()[:128]
        if not identifier:
            raise ConversationStoreError("invalid_conversation_id", "对话 ID 不能为空。", status=400)
        return identifier

    @staticmethod
    def _title(value: Any) -> str:
        return str(value or "新对话").strip()[:80] or "新对话"

    @staticmethod
    def _title_from_message(value: Any) -> str:
        compact = " ".join(str(value or "").split())
        return compact[:18] or "新对话"

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(8)}"
