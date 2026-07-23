"""把现有生成阶段观察结果记录为单进程 Specialist Agent 任务。"""

from __future__ import annotations

import atexit
from dataclasses import replace
import hashlib
import logging
from threading import Event, Lock
from typing import Any

from ai8video.batch.agent_task_scheduler import (
    AgentTaskHandlerSpec,
    AgentTaskScheduler,
)
from ai8video.batch.agent_task_models import (
    TERMINAL_TASK_STATES,
    AgentResult,
    AgentTask,
    AgentTaskSpec,
)
from ai8video.batch.task_ledger import TaskLedger
from ai8video.core.models import VideoPrompt
from ai8video.generation.generation_batch_context import (
    get_current_generation_batch_id,
    get_current_generation_session_id,
)


logger = logging.getLogger(__name__)
_TASK_LEDGER = TaskLedger(timeout_seconds=0.1)
_SHADOW_WORKER_ID = "specialist-shadow-local"
_SCHEDULER_LOCK = Lock()
_SHADOW_SCHEDULER: AgentTaskScheduler | None = None
_SHADOW_SCHEDULER_LEDGER_ID: int | None = None


def observe_planner_shadow(
    videos: list[VideoPrompt],
    *,
    session_id: str | None,
    source_stage: str,
    merge_mode: str | None = None,
) -> None:
    """复用既有规划输出，不触发新的模型请求。"""
    batch_id = str(get_current_generation_batch_id() or "").strip()
    try:
        identity = _current_identity(session_id)
        if identity is None or not videos:
            return
        batch_id, effective_session_id = identity
        _execute_shadow_task(
            AgentTaskSpec(
                task_id=_planner_task_id(batch_id),
                generation_batch_id=batch_id,
                session_id=effective_session_id,
                task_type="video_plan_shadow",
                agent_role="planner",
                input_snapshot=_task_input_snapshot(
                    len(videos),
                    source=source_stage,
                    merge_mode=merge_mode,
                ),
                parent_task_id=batch_id,
                idempotency_key="shadow:planner:v1",
                priority=20,
            ),
            _planner_snapshot(videos, source_stage=source_stage, merge_mode=merge_mode),
        )
    except Exception as exc:
        _log_observer_failure("planner", batch_id, exc)


def observe_reviewer_shadow(
    videos: list[VideoPrompt],
    *,
    session_id: str | None,
    review_source: str,
    merge_mode: str | None = None,
    task_scope: str | None = None,
) -> None:
    """复用已有后审核或确定性检查证据，不改变生成结果。"""
    batch_id = str(get_current_generation_batch_id() or "").strip()
    try:
        identity = _current_identity(session_id)
        if identity is None or not videos:
            return
        batch_id, effective_session_id = identity
        planner = _ensure_planner_task(
            batch_id,
            effective_session_id,
            videos,
            merge_mode=merge_mode,
        )
        _execute_shadow_task(
            AgentTaskSpec(
                task_id=_reviewer_task_id(batch_id, task_scope),
                generation_batch_id=batch_id,
                session_id=effective_session_id,
                task_type="semantic_review_shadow",
                agent_role="reviewer",
                input_snapshot=_task_input_snapshot(
                    len(videos),
                    source=review_source,
                    merge_mode=merge_mode,
                    task_scope=task_scope,
                ),
                parent_task_id=batch_id,
                idempotency_key=f"shadow:reviewer:{_normalize_task_scope(task_scope) or 'batch'}:v1",
                priority=30,
            ),
            _reviewer_snapshot(videos, review_source=review_source, merge_mode=merge_mode),
            dependency_task_id=planner.task_id,
        )
    except Exception as exc:
        _log_observer_failure("reviewer", batch_id, exc)


def _execute_shadow_task(
    spec: AgentTaskSpec,
    output_snapshot: dict[str, Any],
    *,
    dependency_task_id: str | None = None,
) -> AgentTask | None:
    scheduler = start_specialist_agent_scheduler()
    scheduled_spec = replace(
        spec,
        input_snapshot={
            **spec.input_snapshot,
            "resultSnapshot": dict(output_snapshot),
        },
        max_attempts=max(2, spec.max_attempts),
    )
    task = scheduler.enqueue(
        scheduled_spec,
        dependency_task_id=dependency_task_id,
    )
    completed = scheduler.wait_for_terminal(task.task_id, timeout_seconds=0.5)
    if completed is None or completed.state not in TERMINAL_TASK_STATES:
        raise RuntimeError("specialist shadow task did not converge")
    return completed


def start_specialist_agent_scheduler() -> AgentTaskScheduler:
    global _SHADOW_SCHEDULER, _SHADOW_SCHEDULER_LEDGER_ID
    ledger_identity = id(_TASK_LEDGER.agent_tasks)
    with _SCHEDULER_LOCK:
        if (
            _SHADOW_SCHEDULER is not None
            and _SHADOW_SCHEDULER_LEDGER_ID == ledger_identity
        ):
            _SHADOW_SCHEDULER.start()
            return _SHADOW_SCHEDULER
        if _SHADOW_SCHEDULER is not None:
            _SHADOW_SCHEDULER.shutdown(grace_seconds=0.2)
        scheduler = _build_shadow_scheduler()
        scheduler.start()
        _SHADOW_SCHEDULER = scheduler
        _SHADOW_SCHEDULER_LEDGER_ID = ledger_identity
        return scheduler


def shutdown_specialist_agent_scheduler(grace_seconds: float = 0.5) -> None:
    global _SHADOW_SCHEDULER, _SHADOW_SCHEDULER_LEDGER_ID
    with _SCHEDULER_LOCK:
        scheduler = _SHADOW_SCHEDULER
        if scheduler is None:
            return
        scheduler.shutdown(grace_seconds=grace_seconds)
        if _SHADOW_SCHEDULER is scheduler:
            _SHADOW_SCHEDULER = None
            _SHADOW_SCHEDULER_LEDGER_ID = None


def _build_shadow_scheduler() -> AgentTaskScheduler:
    scheduler = AgentTaskScheduler(
        _TASK_LEDGER.agent_tasks,
        max_concurrency=2,
        lease_seconds=1,
        heartbeat_interval=0.2,
        poll_interval=0.01,
        worker_prefix=_SHADOW_WORKER_ID,
    )
    handler_spec = AgentTaskHandlerSpec(_shadow_task_handler, replay_safe=True)
    scheduler.register_handler("video_plan_shadow", handler_spec)
    scheduler.register_handler("semantic_review_shadow", handler_spec)
    return scheduler


def _shadow_task_handler(task: AgentTask, cancel_event: Event) -> AgentResult:
    if cancel_event.is_set():
        return AgentResult(
            task.task_id,
            error_type="CancelledByRequest",
            error_message="shadow observation cancelled",
        )
    output_snapshot = task.input_snapshot.get("resultSnapshot")
    if not isinstance(output_snapshot, dict):
        return AgentResult(
            task.task_id,
            error_type="InvalidShadowSnapshot",
            error_message="shadow result snapshot is missing",
        )
    return AgentResult(task.task_id, dict(output_snapshot))


def _ensure_planner_task(
    batch_id: str,
    session_id: str,
    videos: list[VideoPrompt],
    *,
    merge_mode: str | None,
) -> AgentTask:
    task_id = _planner_task_id(batch_id)
    planner = _TASK_LEDGER.agent_tasks.get_task(task_id)
    if planner is not None and planner.state in TERMINAL_TASK_STATES:
        return planner
    completed = _execute_shadow_task(
        AgentTaskSpec(
            task_id=task_id,
            generation_batch_id=batch_id,
            session_id=session_id,
            task_type="video_plan_shadow",
            agent_role="planner",
            input_snapshot=_task_input_snapshot(
                len(videos),
                source="reviewer_recovery",
                merge_mode=merge_mode,
            ),
            parent_task_id=batch_id,
            idempotency_key="shadow:planner:v1",
            priority=20,
        ),
        _planner_snapshot(
            videos,
            source_stage="reviewer_recovery",
            merge_mode=merge_mode,
        ),
    )
    if completed is None:
        raise RuntimeError("planner shadow task did not converge")
    return completed


def _current_identity(session_id: str | None) -> tuple[str, str] | None:
    batch_id = str(get_current_generation_batch_id() or "").strip()
    effective_session_id = str(
        get_current_generation_session_id() or session_id or ""
    ).strip()
    if not batch_id or not effective_session_id:
        return None
    root = _TASK_LEDGER.agent_tasks.get_task(batch_id)
    if root is None or root.session_id != effective_session_id:
        return None
    return batch_id, effective_session_id


def _planner_snapshot(
    videos: list[VideoPrompt],
    *,
    source_stage: str,
    merge_mode: str | None,
) -> dict[str, Any]:
    items = [
        {
            "videoIndex": int(video.index),
            "titleDigest": _digest(video.title),
            "promptDigest": _digest(video.prompt),
            "hasSourceSummary": bool(str(video.source_summary or "").strip()),
            "keywordKeys": sorted(str(key) for key in (video.keyword_guidance or {}))[:20],
        }
        for video in videos
    ]
    return {
        "observationMode": "shadow",
        "sourceStage": str(source_stage or "planned").strip() or "planned",
        "mergeMode": str(merge_mode or "").strip() or None,
        "videoCount": len(items),
        "videos": items,
    }


def _reviewer_snapshot(
    videos: list[VideoPrompt],
    *,
    review_source: str,
    merge_mode: str | None,
) -> dict[str, Any]:
    items = [_review_item(video) for video in videos]
    return {
        "observationMode": "shadow",
        "reviewSource": str(review_source or "existing_evidence").strip(),
        "mergeMode": str(merge_mode or "").strip() or None,
        "videoCount": len(items),
        "passesCount": sum(1 for item in items if item["passes"]),
        "violationCount": sum(int(item["violationCount"]) for item in items),
        "advisoryCount": sum(int(item["advisoryCount"]) for item in items),
        "issueCount": sum(int(item["issueCount"]) for item in items),
        "improvementCount": sum(int(item["improvementCount"]) for item in items),
        "constraintCount": sum(int(item["constraintCount"]) for item in items),
        "fallbackCount": sum(1 for item in items if item["fallback"]),
        "videos": items,
    }


def _review_item(video: VideoPrompt) -> dict[str, Any]:
    guidance = video.keyword_guidance if isinstance(video.keyword_guidance, dict) else {}
    generated_review = guidance.get("generated_output_review")
    if isinstance(generated_review, dict):
        return {
            "videoIndex": int(video.index),
            "promptDigest": _digest(video.prompt),
            "passes": generated_review.get("passes") is True,
            "violationCount": 0,
            "advisoryCount": 0,
            "issueCount": len(generated_review.get("issues") or []),
            "improvementCount": len(generated_review.get("improvements") or []),
            "constraintCount": len(generated_review.get("nextPromptConstraints") or []),
            "hasNarration": False,
            "fallback": str(generated_review.get("status") or "") != "completed",
        }
    post_review = guidance.get("post_review")
    if not isinstance(post_review, dict):
        return {
            "videoIndex": int(video.index),
            "promptDigest": _digest(video.prompt),
            "passes": bool(str(video.prompt or "").strip()),
            "violationCount": 0,
            "advisoryCount": 0,
            "issueCount": 0,
            "improvementCount": 0,
            "constraintCount": 0,
            "hasNarration": False,
            "fallback": False,
        }
    return {
        "videoIndex": int(video.index),
        "promptDigest": _digest(video.prompt),
        "passes": bool(post_review.get("passes")),
        "violationCount": len(post_review.get("violations") or []),
        "advisoryCount": len(post_review.get("userAdvisories") or []),
        "issueCount": 0,
        "improvementCount": 0,
        "constraintCount": 0,
        "hasNarration": bool(str(post_review.get("narrationText") or "").strip()),
        "fallback": bool(post_review.get("fallback")),
    }


def _task_input_snapshot(
    video_count: int,
    *,
    source: str,
    merge_mode: str | None,
    task_scope: str | None = None,
) -> dict[str, Any]:
    return {
        "observationMode": "shadow",
        "source": str(source or "existing_output").strip() or "existing_output",
        "mergeMode": str(merge_mode or "").strip() or None,
        "videoCount": max(0, int(video_count)),
        "taskScope": _normalize_task_scope(task_scope) or None,
    }


def _planner_task_id(batch_id: str) -> str:
    return f"{batch_id}:planner"


def _reviewer_task_id(batch_id: str, task_scope: str | None = None) -> str:
    scope = _normalize_task_scope(task_scope)
    return f"{batch_id}:reviewer{':' + scope if scope else ''}"


def _normalize_task_scope(value: str | None) -> str:
    return "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in str(value or "").strip()
    )[:80]


def _digest(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _log_observer_failure(role: str, batch_id: str, exc: Exception) -> None:
    logger.warning(
        "ai8video specialist shadow observer failed role=%s batch=%s error_type=%s",
        role,
        batch_id,
        exc.__class__.__name__,
    )


atexit.register(shutdown_specialist_agent_scheduler)
