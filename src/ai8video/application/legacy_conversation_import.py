"""One-way, idempotent adoption of browser-local conversation messages."""

from __future__ import annotations

import sqlite3
from typing import Any

from ai8video.application.conversation_store_schema import dump_json


def adopt_legacy_messages(
    connection: sqlite3.Connection,
    conversation_id: str,
    messages: list[Any],
    *,
    created_at: str,
) -> None:
    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "assistant").strip().lower()
        if role not in {"user", "assistant", "system", "tool"}:
            role = "assistant"
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else None
        content = str(item.get("content") or item.get("text") or (payload or {}).get("text") or "")
        metadata: dict[str, Any] = {"legacy": True}
        if role == "assistant" and payload:
            metadata["legacyPayload"] = payload
        if item.get("error"):
            metadata["legacyError"] = str(item.get("error"))
        connection.execute(
            """
            INSERT OR IGNORE INTO conversation_messages (
                message_id, conversation_id, epoch, role, content, metadata_json, created_at
            ) VALUES (?, ?, 0, ?, ?, ?, ?)
            """,
            (
                f"legacy_{conversation_id}_{index}",
                conversation_id,
                role,
                content,
                dump_json(metadata),
                str(item.get("createdAt") or created_at),
            ),
        )
