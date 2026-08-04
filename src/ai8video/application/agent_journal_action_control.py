"""Cost, retry and listing operations for the persistent Agent journal."""

from __future__ import annotations

from typing import Any

from ai8video.application.conversation_store import ConversationStoreError
from ai8video.application.conversation_store_schema import now_iso, open_connection, transaction


class AgentJournalActionControlMixin:
    path: Any
    timeout_seconds: float

    def list_actions(self, run_id: str) -> list[dict[str, Any]]:
        with open_connection(self.path, self.timeout_seconds) as connection:
            self._required_run(connection, run_id)
            rows = connection.execute(
                "SELECT * FROM agent_actions WHERE run_id = ? ORDER BY created_at, rowid",
                (run_id,),
            ).fetchall()
        return [self._action_dict(row) for row in rows]

    def charge_action_cost(self, action_id: str) -> dict[str, Any]:
        with transaction(self.path, self.timeout_seconds) as connection:
            action = self._required_action(connection, action_id)
            if bool(action["cost_charged"]) or float(action["cost_units"]) <= 0:
                return self._action_dict(action)
            run = self._required_run(connection, action["run_id"])
            self._ensure_run_active(run)
            next_cost = float(run["cost_units"]) + float(action["cost_units"])
            if next_cost > float(run["cost_limit"]):
                raise ConversationStoreError(
                    "agent_cost_limit",
                    "本轮继续执行会超过 Agent 成本上限，需要用户确认新的处理方式。",
                    details={
                        "current": float(run["cost_units"]),
                        "action": float(action["cost_units"]),
                        "limit": float(run["cost_limit"]),
                    },
                )
            now = now_iso()
            connection.execute(
                "UPDATE agent_runs SET cost_units = ?, updated_at = ? WHERE run_id = ?",
                (next_cost, now, run["run_id"]),
            )
            connection.execute(
                "UPDATE agent_actions SET cost_charged = 1, updated_at = ? WHERE action_id = ?",
                (now, action["action_id"]),
            )
            updated = self._required_action(connection, action["action_id"])
        return self._action_dict(updated)

    def schedule_retry(self, action_id: str, *, requires_approval: bool) -> dict[str, Any]:
        with transaction(self.path, self.timeout_seconds) as connection:
            action = self._required_action(connection, action_id)
            if action["state"] != "failed":
                return self._action_dict(action)
            if not bool(action["replay_safe"]) or int(action["attempt"]) >= int(action["max_attempts"]):
                return self._action_dict(action)
            run = self._required_run(connection, action["run_id"])
            if run["state"] in {"succeeded", "cancelled"}:
                return self._action_dict(action)
            next_state = "waiting_approval" if requires_approval else "retry_wait"
            run_state = "waiting_user" if requires_approval else "running"
            now = now_iso()
            connection.execute(
                """
                UPDATE agent_actions
                SET state = ?, requires_approval = ?, cost_charged = 0,
                    error_code = NULL, error_message = NULL, completed_at = NULL, updated_at = ?
                WHERE action_id = ?
                """,
                (next_state, int(requires_approval), now, action["action_id"]),
            )
            connection.execute(
                """
                UPDATE agent_runs
                SET state = ?, pending_action_id = ?, error_code = NULL,
                    error_message = NULL, completed_at = NULL, updated_at = ?
                WHERE run_id = ?
                """,
                (run_state, action["action_id"], now, run["run_id"]),
            )
            updated = self._required_action(connection, action["action_id"])
        return self._action_dict(updated)
