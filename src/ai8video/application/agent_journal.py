"""Persistent Agent run, action and observation journal."""

from __future__ import annotations

from pathlib import Path
import secrets
import sqlite3
from typing import Any

from ai8video.application.conversation_store import (
    ConversationStoreError,
    DEFAULT_CONVERSATION_STORE_PATH,
)
from ai8video.application.conversation_store_schema import (
    dump_json,
    initialize_schema,
    load_json,
    now_iso,
    open_connection,
    stable_payload_hash,
    transaction,
)
from ai8video.application.agent_journal_action_control import AgentJournalActionControlMixin
from ai8video.application.agent_journal_views import action_dict, observation_dict, run_dict


TERMINAL_ACTION_STATES = frozenset({"succeeded", "failed", "cancelled"})
TERMINAL_RUN_STATES = frozenset({"succeeded", "failed", "cancelled"})


class AgentJournal(AgentJournalActionControlMixin):
    def __init__(self, path: str | Path | None = None, *, timeout_seconds: float = 10) -> None:
        self.path = Path(path or DEFAULT_CONVERSATION_STORE_PATH).expanduser().resolve()
        self.timeout_seconds = float(timeout_seconds)
        initialize_schema(self.path, self.timeout_seconds)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with open_connection(self.path, self.timeout_seconds) as connection:
            row = self._required_run(connection, run_id)
        return self._run_dict(row)

    def start_decision(self, run_id: str, *, cost_units: float = 0) -> dict[str, Any]:
        failure: ConversationStoreError | None = None
        with transaction(self.path, self.timeout_seconds) as connection:
            row = self._required_run(connection, run_id)
            self._ensure_run_active(row)
            decisions = int(row["decision_count"]) + 1
            next_cost = float(row["cost_units"]) + max(0.0, float(cost_units))
            if decisions > int(row["max_decisions"]):
                failure = self._fail_run(
                    connection,
                    row,
                    "agent_decision_limit",
                    "Agent 已达到本轮最大决策次数。",
                )
            elif next_cost > float(row["cost_limit"]):
                failure = self._fail_run(
                    connection,
                    row,
                    "agent_cost_limit",
                    "Agent 已达到本轮成本上限。",
                )
            else:
                now = now_iso()
                connection.execute(
                    """
                    UPDATE agent_runs
                    SET state = 'deciding', decision_count = ?, cost_units = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (decisions, next_cost, now, row["run_id"]),
                )
            updated = self._required_run(connection, row["run_id"])
        if failure is not None:
            raise failure
        return self._run_dict(updated)

    def request_action(
        self,
        run_id: str,
        *,
        tool_name: str,
        idempotency_key: str,
        input_payload: dict[str, Any],
        side_effects: bool,
        replay_safe: bool,
        requires_approval: bool = False,
        cost_units: float = 0,
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        tool = str(tool_name or "").strip()
        key = str(idempotency_key or "").strip()[:200]
        if not tool or not key:
            raise ConversationStoreError("invalid_agent_action", "Agent action 缺少工具名或幂等键。", status=400)
        if side_effects and not replay_safe and max_attempts > 1:
            max_attempts = 1
        with transaction(self.path, self.timeout_seconds) as connection:
            run = self._required_run(connection, run_id)
            self._ensure_run_active(run)
            existing = connection.execute(
                """
                SELECT * FROM agent_actions
                WHERE conversation_id = ? AND idempotency_key = ?
                """,
                (run["conversation_id"], key),
            ).fetchone()
            if existing is not None:
                self._check_action_identity(existing, run, tool, input_payload)
                return self._action_dict(existing)
            action_id = self._new_id("act")
            now = now_iso()
            state = "waiting_approval" if requires_approval else "requested"
            connection.execute(
                """
                INSERT INTO agent_actions (
                    action_id, run_id, conversation_id, tool_name, idempotency_key,
                    state, input_json, side_effects, replay_safe, requires_approval,
                    cost_units, max_attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    run["run_id"],
                    run["conversation_id"],
                    tool,
                    key,
                    state,
                    dump_json(input_payload),
                    int(side_effects),
                    int(replay_safe),
                    int(requires_approval),
                    max(0.0, float(cost_units)),
                    max(1, int(max_attempts)),
                    now,
                    now,
                ),
            )
            run_state = "waiting_user" if requires_approval else "running"
            connection.execute(
                """
                UPDATE agent_runs
                SET state = ?, pending_action_id = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (run_state, action_id, now, run["run_id"]),
            )
            action = self._required_action(connection, action_id)
        return self._action_dict(action)

    def approve_action(self, action_id: str, *, approved: bool) -> dict[str, Any]:
        with transaction(self.path, self.timeout_seconds) as connection:
            action = self._required_action(connection, action_id)
            if action["state"] != "waiting_approval":
                return self._action_dict(action)
            now = now_iso()
            state = "requested" if approved else "cancelled"
            connection.execute(
                """
                UPDATE agent_actions
                SET state = ?, error_code = ?, error_message = ?,
                    completed_at = CASE WHEN ? = 'cancelled' THEN ? ELSE NULL END,
                    updated_at = ?
                WHERE action_id = ?
                """,
                (
                    state,
                    None if approved else "user_rejected",
                    None if approved else "用户拒绝了本次操作。",
                    state,
                    now,
                    now,
                    action["action_id"],
                ),
            )
            run_state = "running" if approved else "waiting_user"
            connection.execute(
                "UPDATE agent_runs SET state = ?, updated_at = ? WHERE run_id = ?",
                (run_state, now, action["run_id"]),
            )
            updated = self._required_action(connection, action["action_id"])
        return self._action_dict(updated)

    def mark_action_running(self, action_id: str) -> dict[str, Any]:
        with transaction(self.path, self.timeout_seconds) as connection:
            action = self._required_action(connection, action_id)
            if action["state"] in TERMINAL_ACTION_STATES or action["state"] == "waiting_runtime":
                return self._action_dict(action)
            if action["state"] not in {"requested", "retry_wait"}:
                raise ConversationStoreError("agent_action_not_runnable", "Agent action 当前不能执行。")
            attempt = int(action["attempt"]) + 1
            if attempt > int(action["max_attempts"]):
                raise ConversationStoreError("agent_retry_limit", "Agent action 已达到重试上限。")
            now = now_iso()
            connection.execute(
                """
                UPDATE agent_actions
                SET state = 'running', attempt = ?, updated_at = ?
                WHERE action_id = ?
                """,
                (attempt, now, action["action_id"]),
            )
            updated = self._required_action(connection, action["action_id"])
        return self._action_dict(updated)

    def wait_for_runtime(self, action_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._transition_action(
            action_id,
            state="waiting_runtime",
            output_payload=payload,
            run_state="waiting_runtime",
        )

    def complete_action(self, action_id: str, output_payload: dict[str, Any]) -> dict[str, Any]:
        return self._transition_action(
            action_id,
            state="succeeded",
            output_payload=output_payload,
            run_state="queued",
            completed=True,
        )

    def fail_action(
        self,
        action_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool = False,
    ) -> dict[str, Any]:
        with transaction(self.path, self.timeout_seconds) as connection:
            action = self._required_action(connection, action_id)
            if action["state"] in TERMINAL_ACTION_STATES:
                return self._action_dict(action)
            can_retry = (
                retryable
                and bool(action["replay_safe"])
                and int(action["attempt"]) < int(action["max_attempts"])
            )
            state = "retry_wait" if can_retry else "failed"
            run_state = "queued" if can_retry else "failed"
            now = now_iso()
            connection.execute(
                """
                UPDATE agent_actions
                SET state = ?, error_code = ?, error_message = ?, updated_at = ?,
                    completed_at = CASE WHEN ? = 'failed' THEN ? ELSE NULL END
                WHERE action_id = ?
                """,
                (state, error_code, error_message, now, state, now, action["action_id"]),
            )
            connection.execute(
                """
                UPDATE agent_runs
                SET state = ?, error_code = ?, error_message = ?,
                    completed_at = CASE WHEN ? = 'failed' THEN ? ELSE NULL END,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (run_state, error_code, error_message, run_state, now, now, action["run_id"]),
            )
            updated = self._required_action(connection, action["action_id"])
        return self._action_dict(updated)

    def record_observation(
        self,
        run_id: str,
        *,
        action_id: str | None,
        kind: str,
        state: str,
        payload: dict[str, Any],
        terminal: bool,
    ) -> dict[str, Any]:
        with transaction(self.path, self.timeout_seconds) as connection:
            run = self._required_run(connection, run_id)
            action_tool = ""
            if action_id:
                action = self._required_action(connection, action_id)
                if action["run_id"] != run["run_id"]:
                    raise ConversationStoreError("agent_action_run_mismatch", "Observation 与 action 不属于同一运行。")
                action_tool = str(action["tool_name"] or "")
            progress_hash = stable_payload_hash({
                "kind": str(kind or "runtime"),
                "tool": action_tool,
                "payload": payload,
            })
            observation_id = self._new_id("obs")
            now = now_iso()
            connection.execute(
                """
                INSERT INTO agent_observations (
                    observation_id, run_id, action_id, kind, state,
                    payload_json, progress_hash, terminal, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    run["run_id"],
                    action_id,
                    str(kind or "runtime"),
                    str(state or "unknown"),
                    dump_json(payload),
                    progress_hash,
                    int(terminal),
                    now,
                ),
            )
            repeated = progress_hash == run["no_progress_hash"]
            no_progress_count = int(run["no_progress_count"]) + 1 if repeated else 0
            run_state = "queued" if terminal else "waiting_runtime"
            connection.execute(
                """
                UPDATE agent_runs
                SET state = ?, no_progress_hash = ?, no_progress_count = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (run_state, progress_hash, no_progress_count, now, run["run_id"]),
            )
            row = connection.execute(
                "SELECT * FROM agent_observations WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
        return self._observation_dict(row)

    def finish_run(
        self,
        run_id: str,
        *,
        state: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if state not in TERMINAL_RUN_STATES:
            raise ConversationStoreError("invalid_agent_run_state", "Agent run 只能结束为 succeeded、failed 或 cancelled。")
        with transaction(self.path, self.timeout_seconds) as connection:
            run = self._required_run(connection, run_id)
            now = now_iso()
            connection.execute(
                """
                UPDATE agent_runs
                SET state = ?, pending_action_id = NULL, error_code = ?, error_message = ?,
                    completed_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (state, error_code, error_message, now, now, run["run_id"]),
            )
            updated = self._required_run(connection, run["run_id"])
        return self._run_dict(updated)

    def list_observations(self, run_id: str) -> list[dict[str, Any]]:
        with open_connection(self.path, self.timeout_seconds) as connection:
            self._required_run(connection, run_id)
            rows = connection.execute(
                "SELECT * FROM agent_observations WHERE run_id = ? ORDER BY created_at, rowid",
                (run_id,),
            ).fetchall()
        return [self._observation_dict(row) for row in rows]

    def _transition_action(
        self,
        action_id: str,
        *,
        state: str,
        output_payload: dict[str, Any] | None,
        run_state: str,
        completed: bool = False,
    ) -> dict[str, Any]:
        with transaction(self.path, self.timeout_seconds) as connection:
            action = self._required_action(connection, action_id)
            if action["state"] in TERMINAL_ACTION_STATES:
                return self._action_dict(action)
            now = now_iso()
            connection.execute(
                """
                UPDATE agent_actions
                SET state = ?, output_json = ?, updated_at = ?, completed_at = ?
                WHERE action_id = ?
                """,
                (state, dump_json(output_payload), now, now if completed else None, action["action_id"]),
            )
            connection.execute(
                """
                UPDATE agent_runs
                SET state = ?, pending_action_id = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (run_state, None if completed else action["action_id"], now, action["run_id"]),
            )
            updated = self._required_action(connection, action["action_id"])
        return self._action_dict(updated)

    @staticmethod
    def _check_action_identity(
        action: sqlite3.Row,
        run: sqlite3.Row,
        tool_name: str,
        input_payload: dict[str, Any],
    ) -> None:
        identity_matches = (
            action["run_id"] == run["run_id"]
            and action["tool_name"] == tool_name
            and load_json(action["input_json"]) == input_payload
        )
        if not identity_matches:
            raise ConversationStoreError(
                "agent_idempotency_conflict",
                "幂等键已被不同的 Agent action 使用。",
            )

    def _fail_run(
        self,
        connection: sqlite3.Connection,
        run: sqlite3.Row,
        code: str,
        message: str,
    ) -> ConversationStoreError:
        now = now_iso()
        connection.execute(
            """
            UPDATE agent_runs
            SET state = 'failed', error_code = ?, error_message = ?, completed_at = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (code, message, now, now, run["run_id"]),
        )
        return ConversationStoreError(code, message)

    @staticmethod
    def _ensure_run_active(run: sqlite3.Row) -> None:
        if run["state"] in TERMINAL_RUN_STATES:
            raise ConversationStoreError("agent_run_terminal", "Agent run 已结束。")

    @staticmethod
    def _required_run(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise ConversationStoreError("agent_run_not_found", "Agent run 不存在。", status=404)
        return row

    @staticmethod
    def _required_action(connection: sqlite3.Connection, action_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM agent_actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        if row is None:
            raise ConversationStoreError("agent_action_not_found", "Agent action 不存在。", status=404)
        return row

    _run_dict = staticmethod(run_dict)
    _action_dict = staticmethod(action_dict)
    _observation_dict = staticmethod(observation_dict)

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(8)}"
