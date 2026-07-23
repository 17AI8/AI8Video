"""视频播放页 HTML 动效预览任务状态机。

任务在本地进程内排队，由单并发 GenerationTaskRunner 承载；真正的
HyperFrames render 再由 task-scoped Node Worker 执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from ai8video.generation.generation_task_runner import GenerationTask, GenerationTaskRunner


HtmlMotionTarget = Callable[..., dict[str, Any]]


@dataclass
class HtmlMotionTask:
    task_id: str
    user_generated_key: str
    status: str = "queued"
    phase: str = "queued"
    message: str = "等待动效任务"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    phase_started_at: float = field(default_factory=time.time)
    phase_timings: dict[str, float] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    retry_count: int = 0
    retry_limit: int = 0
    retry_reason: str = ""
    audit_result: str = ""
    attempt_traces: list[dict[str, Any]] = field(default_factory=list)
    stream_text: str = ""

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "ok": self.status not in {"failed", "cancelled"},
            "taskId": self.task_id,
            "userGeneratedKey": self.user_generated_key,
            "status": self.status,
            "phase": self.phase,
            "message": self.message,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "elapsedSeconds": _round_seconds(now - self.created_at),
            "phaseElapsedSeconds": _round_seconds(now - self.phase_started_at),
            "phaseTimings": dict(self.phase_timings),
            "warnings": list(self.warnings),
            "cancelRequested": self.cancel_requested,
            "retryCount": self.retry_count,
            "retryLimit": self.retry_limit,
            "retryReason": self.retry_reason,
            "auditResult": self.audit_result,
            "attemptTraces": list(self.attempt_traces),
            "streamText": self.stream_text,
            "result": self.result,
            "error": self.error,
        }


class HtmlMotionTaskService:
    _MAX_RETAINED_TASKS = 128

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, HtmlMotionTask] = {}
        self._active_by_key: dict[str, str] = {}
        self._runner = GenerationTaskRunner(
            worker_prefix="html-motion-task",
            max_concurrency=1,
        )

    def submit(self, user_generated_key: str, target: HtmlMotionTarget) -> dict[str, Any]:
        key = str(user_generated_key or "").strip()
        if not key:
            raise ValueError("userGeneratedKey is required")
        with self._lock:
            existing_id = self._active_by_key.get(key)
            existing = self._tasks.get(existing_id or "")
            if existing and not _is_terminal(existing.status):
                return {**existing.snapshot(), "deduplicated": True}
            task_id = uuid4().hex
            task = HtmlMotionTask(task_id=task_id, user_generated_key=key)
            self._tasks[task_id] = task
            self._active_by_key[key] = task_id
            self._prune_locked()
            self._runner.start(
                task_id,
                self._run_target,
                args=(task, target),
                state_callback=self._runner_state,
            )
            return task.snapshot()

    def _prune_locked(self) -> None:
        if len(self._tasks) <= self._MAX_RETAINED_TASKS:
            return
        terminal = sorted(
            (task for task in self._tasks.values() if _is_terminal(task.status)),
            key=lambda task: task.updated_at,
        )
        for task in terminal[: max(0, len(self._tasks) - self._MAX_RETAINED_TASKS)]:
            self._tasks.pop(task.task_id, None)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(str(task_id or "").strip())
            return None if task is None else task.snapshot()

    def get_active(self, user_generated_key: str) -> dict[str, Any] | None:
        """Return the in-flight task for a video key, if any."""
        key = str(user_generated_key or "").strip()
        if not key:
            return None
        with self._lock:
            task_id = self._active_by_key.get(key)
            task = self._tasks.get(task_id or "")
            if task is None or _is_terminal(task.status):
                return None
            return task.snapshot()

    def cancel(self, task_id: str) -> dict[str, Any] | None:
        normalized = str(task_id or "").strip()
        with self._lock:
            task = self._tasks.get(normalized)
            if task is None:
                return None
            if _is_terminal(task.status):
                return task.snapshot()
            task.cancel_requested = True
            _close_current_phase(task, time.time())
            task.status = "cancelled"
            task.phase = "cancelled"
            task.message = "已请求取消 HTML 动效任务"
            task.updated_at = time.time()
            task.phase_started_at = task.updated_at
            if self._active_by_key.get(task.user_generated_key) == task.task_id:
                self._active_by_key.pop(task.user_generated_key, None)
        self._runner.cancel(normalized)
        return task.snapshot()

    def _run_target(self, runner_task: GenerationTask, task: HtmlMotionTask, target: HtmlMotionTarget) -> None:
        self._set_phase(task, "preparing", "准备动效预览")
        try:
            result = target(
                stage_callback=lambda phase, event=None: self._set_phase_from_event(task, phase, event),
                cancel_event=runner_task.cancel_event,
            )
            if runner_task.cancel_requested:
                self._set_terminal(task, "cancelled", "HTML 动效任务已取消", result)
                return
            overlay = result.get("htmlMotionOverlay") if isinstance(result, dict) else {}
            overlay_status = str((overlay or {}).get("status") or "").strip()
            if overlay_status == "preview_ready":
                status = "preview_ready"
                message = "HTML 动效预览已生成，等待用户确认"
            elif overlay_status == "preview_failed":
                status = "preview_failed"
                reason = str((overlay or {}).get("reason") or "HTML 动效预览失败")
                detail = str((overlay or {}).get("detail") or "").strip()
                message = f"{reason}｜{detail}" if detail and detail not in reason else reason
            else:
                status = "failed"
                message = "HTML 动效任务未返回有效预览状态"
            self._set_terminal(task, status, message, result)
        except Exception as exc:
            if runner_task.cancel_requested or str(getattr(exc, "code", "")) == "CANCELLED":
                self._set_terminal(task, "cancelled", "HTML 动效任务已取消", None)
            else:
                message = _friendly_task_error(exc)
                self._set_terminal(task, "failed", message, None, error=message)

    def _runner_state(self, runner_task: GenerationTask) -> None:
        with self._lock:
            task = self._tasks.get(runner_task.generation_batch_id)
            if task is None or _is_terminal(task.status):
                return
            if runner_task.state == "queued":
                _apply_phase_locked(task, "queued", "等待动效处理", status="queued")
            elif runner_task.state == "running":
                _apply_phase_locked(task, "preparing", "准备动效预览", status="preparing")
            elif runner_task.state == "cancel_requested":
                task.cancel_requested = True
                task.updated_at = time.time()

    def _set_phase_from_event(self, task: HtmlMotionTask, phase: str, event: dict[str, Any] | None) -> None:
        payload = event or {}
        stream_delta = payload.get("streamDelta")
        if isinstance(stream_delta, str) and stream_delta:
            with self._lock:
                _append_stream_text(task, stream_delta)
        retry_count = int(payload.get("retryCount") or 0)
        retry_limit = int(payload.get("retryLimit") or 0)
        retry_reason = str(payload.get("retryReason") or "").strip()
        audit_result = str(payload.get("auditResult") or retry_reason).strip()
        attempt_trace = payload.get("attemptTrace")
        if retry_count > 0:
            with self._lock:
                task.retry_count = retry_count
                task.retry_limit = retry_limit
                task.retry_reason = retry_reason
                task.audit_result = audit_result
                if isinstance(attempt_trace, dict):
                    task.attempt_traces.append(dict(attempt_trace))
                    _append_stream_text(task, f"\n\n—— 第 {retry_count + 1} 次方案 ——\n")
        retry_message = (
            f"审核结果：{audit_result}・正在第 {retry_count}/{retry_limit} 次重试"
            if retry_count > 0 else ""
        )
        message = str(payload.get("message") or retry_message or _phase_message(phase))
        self._set_phase(task, phase, message)
        warnings = payload.get("warnings")
        if isinstance(warnings, list):
            with self._lock:
                task.warnings = [str(item) for item in warnings]

    def _set_phase(self, task: HtmlMotionTask, phase: str, message: str) -> None:
        with self._lock:
            if _is_terminal(task.status):
                return
            status = (
                "preparing" if phase in {"preparing", "checking", "generating"}
                else phase if phase in {"rendering", "compositing", "validating"}
                else None
            )
            _apply_phase_locked(task, phase, message, status=status)

    def _set_terminal(
        self,
        task: HtmlMotionTask,
        status: str,
        message: str,
        result: dict[str, Any] | None,
        *,
        error: str | None = None,
    ) -> None:
        with self._lock:
            if _is_terminal(task.status):
                return
            now = time.time()
            _close_current_phase(task, now)
            task.status = status
            task.phase = status
            task.message = message
            task.result = result
            task.error = error
            task.updated_at = now
            task.phase_started_at = now
            if self._active_by_key.get(task.user_generated_key) == task.task_id:
                self._active_by_key.pop(task.user_generated_key, None)


def _round_seconds(value: float) -> float:
    return round(max(0.0, float(value)), 1)


def _close_current_phase(task: HtmlMotionTask, now: float) -> None:
    phase = str(task.phase or "").strip()
    if not phase or _is_terminal(phase):
        return
    elapsed = _round_seconds(now - task.phase_started_at)
    if elapsed <= 0:
        return
    task.phase_timings[phase] = _round_seconds(task.phase_timings.get(phase, 0.0) + elapsed)


def _append_stream_text(task: HtmlMotionTask, value: str) -> None:
    limit = 16_000
    text = task.stream_text + value
    if len(text) <= limit:
        task.stream_text = text
        return
    task.stream_text = text[: limit - 8] + "\n…已截断"


def _apply_phase_locked(
    task: HtmlMotionTask,
    phase: str,
    message: str,
    *,
    status: str | None = None,
) -> None:
    now = time.time()
    if phase != task.phase:
        _close_current_phase(task, now)
        task.phase = phase
        task.phase_started_at = now
    if status is not None:
        task.status = status
    task.message = message
    task.updated_at = now


def _phase_message(phase: str) -> str:
    return {
        "checking": "正在检查动效布局与时间线",
        "generating": "正在生成动效方案",
        "rendering": "正在渲染透明动画",
        "compositing": "正在合成预览画面",
        "validating": "正在检查预览视频",
    }.get(phase, "HTML 动效任务处理中")


def _is_terminal(status: str) -> bool:
    return status in {"preview_ready", "preview_failed", "failed", "cancelled"}


def _friendly_task_error(error: Exception) -> str:
    text = str(error).strip()
    lowered = text.lower()
    if any(token in text for token in ("版式或时间线校验未通过", "透明", "依赖未安装", "无法读取基础视频")):
        return text[:300]
    if any(token in lowered for token in ("timeout", "timed out", "超时")):
        return "动效生成等待超时"
    if any(token in lowered for token in ("connection", "response ended", "reset by peer", "连接中断")):
        return "动效方案服务连接中断"
    if any(token in lowered for token in ("hyperframes", "composition", "artifact", "worker", "node.js")):
        return f"动效渲染未完成：{text[:160]}" if text else "动效渲染未完成"
    return "动效预览未完成"


html_motion_task_service = HtmlMotionTaskService()
