"""Durable conversation and Agent journal schema."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    execution_mode TEXT NOT NULL DEFAULT 'workflow',
    mode_locked INTEGER NOT NULL DEFAULT 0,
    locked_at TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    epoch INTEGER NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    active_run_id TEXT,
    model_binding_json TEXT NOT NULL DEFAULT '{}',
    legacy_adopted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    CHECK(execution_mode IN ('workflow', 'agent'))
);
CREATE INDEX IF NOT EXISTS idx_conversations_visible
    ON conversations(deleted_at, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    client_message_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_messages_client
    ON conversation_messages(conversation_id, client_message_id)
    WHERE client_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversation_messages_epoch
    ON conversation_messages(conversation_id, epoch, created_at);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    state TEXT NOT NULL,
    decision_count INTEGER NOT NULL DEFAULT 0,
    max_decisions INTEGER NOT NULL DEFAULT 8,
    cost_units REAL NOT NULL DEFAULT 0,
    cost_limit REAL NOT NULL DEFAULT 8,
    pending_action_id TEXT,
    no_progress_hash TEXT,
    no_progress_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation
    ON agent_runs(conversation_id, epoch, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_run_contexts (
    run_id TEXT PRIMARY KEY,
    context_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
);

CREATE TABLE IF NOT EXISTS agent_actions (
    action_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT,
    side_effects INTEGER NOT NULL DEFAULT 0,
    replay_safe INTEGER NOT NULL DEFAULT 1,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    cost_units REAL NOT NULL DEFAULT 0,
    cost_charged INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id),
    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
    UNIQUE(conversation_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_agent_actions_run
    ON agent_actions(run_id, created_at);

CREATE TABLE IF NOT EXISTS agent_observations (
    observation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    action_id TEXT,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    progress_hash TEXT,
    terminal INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id),
    FOREIGN KEY(action_id) REFERENCES agent_actions(action_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_observations_run
    ON agent_observations(run_id, created_at);
"""


def initialize_schema(path: Path, timeout_seconds: float = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_connection(path, timeout_seconds) as connection:
        connection.executescript(SCHEMA_SQL)
        _ensure_column(connection, "agent_actions", "cost_units", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "agent_actions", "cost_charged", "INTEGER NOT NULL DEFAULT 0")


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


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


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def dump_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def load_json(value: Any, fallback: Any = None) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {} if fallback is None else fallback


def stable_payload_hash(value: Any) -> str:
    normalized = _strip_progress_noise(value)
    payload = dump_json(normalized).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _strip_progress_noise(value: Any) -> Any:
    noisy_keys = {
        "elapsed",
        "elapsedSeconds",
        "eta",
        "etaSeconds",
        "percent",
        "percentage",
        "pollCount",
        "polledAt",
        "progress",
        "updatedAt",
    }
    if isinstance(value, dict):
        return {
            str(key): _strip_progress_noise(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in noisy_keys
        }
    if isinstance(value, list):
        return [_strip_progress_noise(item) for item in value]
    return value


def _connect(path: Path, timeout_seconds: float) -> sqlite3.Connection:
    timeout = max(0.0, float(timeout_seconds))
    connection = sqlite3.connect(path, timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {max(0, int(timeout * 1000))}")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection
