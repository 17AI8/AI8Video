"""生成根任务的终态与取消一致性策略。"""

from __future__ import annotations

from dataclasses import replace

from ai8video.batch.agent_task_models import LegacyTaskUpdate


TERMINAL_EXECUTION_STATES = frozenset(
    {"completed", "failed", "cancelled", "canceled"}
)


def resolve_sticky_cancellation(
    update: LegacyTaskUpdate,
    *,
    current_execution_state: str | None,
    current_cancel_requested: bool,
) -> LegacyTaskUpdate:
    """终态到达时，任何已提交的取消都优先收敛为 cancelled。"""
    if update.cancel_requested and update.execution_state not in TERMINAL_EXECUTION_STATES:
        return replace(update, execution_state="cancel_requested")
    if update.execution_state not in TERMINAL_EXECUTION_STATES:
        return update
    current_state = str(current_execution_state or "").strip().lower()
    cancellation_requested = (
        bool(update.cancel_requested)
        or current_cancel_requested
        or current_state == "cancel_requested"
    )
    if not cancellation_requested:
        return update
    return replace(
        update,
        execution_state="cancelled",
        cancel_requested=True,
        result_snapshot=None,
        error_type=None,
        error_message=None,
    )


def execution_update_guard(
    generation_batch_id: str,
    execution_state: str | None,
) -> tuple[str, list[object]]:
    """拒绝终态后的任何迟到执行写入，保证 first-wins（首次写入获胜）。"""
    terminal_states = sorted(TERMINAL_EXECUTION_STATES)
    terminal_placeholders = ", ".join("?" for _ in terminal_states)
    terminal_guard = (
        "generation_batch_id = ? AND "
        f"LOWER(COALESCE(execution_state, '')) NOT IN ({terminal_placeholders})"
    )
    if execution_state is None:
        return terminal_guard, [generation_batch_id, *terminal_states]
    if execution_state in {"created", "queued"}:
        return _allowlist_guard(generation_batch_id, {"", "created", "queued"})
    if execution_state == "running":
        return _allowlist_guard(
            generation_batch_id,
            {"", "created", "queued", "running"},
        )
    return terminal_guard, [generation_batch_id, *terminal_states]


def _allowlist_guard(
    generation_batch_id: str,
    allowed_states: set[str],
) -> tuple[str, list[object]]:
    ordered_states = sorted(allowed_states)
    placeholders = ", ".join("?" for _ in ordered_states)
    return (
        f"generation_batch_id = ? AND "
        f"LOWER(COALESCE(execution_state, '')) IN ({placeholders})",
        [generation_batch_id, *ordered_states],
    )
