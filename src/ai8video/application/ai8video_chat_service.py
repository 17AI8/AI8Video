from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Empty
from queue import Queue
from typing import Any, Callable

from ai8video.assets.asset_store import JsonlAssetStore
from ai8video.core.config import AI8VideoConfig
from ai8video.generation.generation_batch_context import (
    reset_current_generation_batch_id,
    reset_current_generation_session_id,
    set_current_generation_batch_id,
    set_current_generation_session_id,
)
from ai8video.generation.generation_progress import (
    claim_generation_batch,
    cancel_generation_progress,
    claim_active_generation_jobs,
    clear_generation_progress,
    create_generation_batch_id,
    fail_generation_progress,
    fail_unsubmitted_generation_progress,
    get_generation_batch_family_snapshot,
    get_generation_ledger_snapshot,
    get_generation_progress,
    mark_job_polling,
    record_generation_execution,
)
from ai8video.generation.generation_task_runner import GenerationTask, GenerationTaskRunner
from ai8video.generation.recovered_tail_frame_resume import prepare_recovered_tail_frame_resume
from ai8video.integrations.direct_video_model_client import AI8VideoModelClient
from ai8video.application.runtime import (
    CHAT_BACKEND,
    cancel_smart_split_confirmation as cancel_smart_split_confirmation_in_runtime,
    clear_chat_snapshot,
    get_chat_snapshot,
    get_runtime,
    handle_chat_message,
    restore_smart_split_plan,
)
from ai8video.assets.user_generated_results import (
    USER_GENERATED_RESULT_ROOT,
    build_generation_result_reconciliation,
)
from ai8video.assets.user_recycle_bin import humanize_failed_video_reason


class _RuntimePayloadError(RuntimeError):
    """运行时以错误 payload 返回，而不是抛出异常。"""


class _AI8VideoSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.lock = threading.Lock()
        self.config = AI8VideoConfig.from_env()
        self.task_runner = GenerationTaskRunner(worker_prefix=f"ai8video-{session_id}")
        self.worker_thread = None
        self.latest_ai8video_payload = None
        self.latest_error = None
        self.current_display_queue = None
        self.current_message = None
        self.current_started_at = None
        self.current_generation_batch_id = None
        self.background_delivery_pending = False
        self.background_final_payload = None
        self.background_completed_at = None
        self.cancelled_at = None
        self.cancel_reason = None

    def handle_message(self, message: str, timeout_seconds: int | None = None) -> dict:
        with self.lock:
            self._start_task(message=message)
            display_queue = self.current_display_queue

        effective_timeout = timeout_seconds if timeout_seconds is not None else self._default_timeout_seconds()
        deadline = time.time() + max(10, effective_timeout)
        completed_item = None
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                with self.lock:
                    if isinstance(get_generation_progress(self.session_id), dict) or self._worker_running():
                        self.background_delivery_pending = True
                    else:
                        self._mark_completed(_timeout_without_generation_payload(self.session_id))
                raise TimeoutError("AI8video chat timed out before producing a video payload")
            try:
                item = display_queue.get(timeout=min(5, remaining))
            except Empty:
                continue
            if "done" not in item:
                continue
            completed_item = item
            break

        with self.lock:
            completed_item = completed_item if isinstance(completed_item, dict) else {}
            completed_generation_batch_id = str(completed_item.get("generationBatchId") or "").strip()
            payload = completed_item.get("payload")
            if not isinstance(payload, dict):
                payload = self.latest_ai8video_payload
            if isinstance(payload, dict):
                if self._is_current_generation_batch_id(completed_generation_batch_id):
                    self._mark_completed(payload)
                return payload
            completed_error = completed_item.get("error")
            if isinstance(completed_error, BaseException):
                raise completed_error
            if self.latest_error is not None:
                raise self.latest_error
            raise RuntimeError("AI8video completed but did not return a AI8Video payload")

    def cancel_smart_split_confirmation(self) -> bool:
        with self.lock:
            cancel_smart_split_confirmation_in_runtime(self.session_id)
            cancel_reason = "用户取消智能分集确认"
            current_batch_id = self.current_generation_batch_id
            task_cancelled = self._ensure_task_runner().cancel(current_batch_id)
            if current_batch_id and task_cancelled:
                record_generation_execution(
                    session_id=self.session_id,
                    generation_batch_id=current_batch_id,
                    execution_state="cancel_requested",
                    cancel_requested=True,
                )
            cancel_generation_progress(self.session_id, cancel_reason)
            clear_chat_snapshot(self.session_id)
            self.latest_ai8video_payload = None
            self.latest_error = None
            self.background_delivery_pending = False
            self.background_final_payload = None
            self.background_completed_at = None
            self.current_display_queue = None
            self.current_generation_batch_id = None
            self.current_message = None
            self.current_started_at = None
            return True

    def retire(self, reason: str) -> None:
        with self.lock:
            runner = self._ensure_task_runner()
            active_batch_ids = runner.cancel_active()
            for batch_id in active_batch_ids:
                record_generation_execution(
                    session_id=self.session_id,
                    generation_batch_id=batch_id,
                    execution_state="cancel_requested",
                    cancel_requested=True,
                )
            if active_batch_ids:
                cancel_generation_progress(self.session_id, reason)
            clear_chat_snapshot(self.session_id)
            self.latest_ai8video_payload = None
            self.latest_error = None
            self.background_delivery_pending = False
            self.background_final_payload = None
            self.background_completed_at = None
            self.current_display_queue = None
            self.current_generation_batch_id = None
            self.current_message = None
            self.current_started_at = None
        for batch_id in active_batch_ids:
            runner.join(batch_id, timeout=0.25)

    def _default_timeout_seconds(self) -> int:
        poll_budget = int(self.config.max_poll_attempts * self.config.poll_interval_seconds)
        return max(180, poll_budget + 120)

    def snapshot_status(self) -> dict:
        with self.lock:
            self._refresh_active_task_state()
            base = {
                "sessionId": self.session_id,
                "generationBatchId": self.current_generation_batch_id,
                "pendingSince": self._isoformat(self.current_started_at),
                "elapsedSeconds": self._elapsed_seconds(),
            }
            self._attach_ledger_snapshot(base)
            progress = get_generation_progress(self.session_id)
            if getattr(self, "cancelled_at", None) is not None:
                if isinstance(progress, dict):
                    base["generationProgress"] = progress
                    self._attach_result_reconciliation(base, progress)
                return {
                    "status": "cancelled",
                    "phase": "cancelled",
                    "statusLabel": "已强行终止",
                    **base,
                    "cancelledAt": self._isoformat(getattr(self, "cancelled_at", None)),
                    "cancelReason": getattr(self, "cancel_reason", None) or "用户强行终止，本地停止等待结果回填",
                }
            self._refresh_provider_generation_progress()
            self._fail_stale_first_frame_progress()
            progress = get_generation_progress(self.session_id)
            if isinstance(progress, dict):
                base["generationProgress"] = progress
                self._attach_result_reconciliation(base, progress)
            if isinstance(progress, dict) and _has_active_generation_progress(progress):
                self._fail_stale_planning_progress()
                progress = get_generation_progress(self.session_id)
                if isinstance(progress, dict):
                    base["generationProgress"] = progress
                if isinstance(progress, dict) and progress.get("status") in {"failed", "completed_with_error"}:
                    return {
                        "status": "failed",
                        "phase": "failed",
                        "statusLabel": str(progress.get("error") or "本地任务失败"),
                        **base,
                    }
                if _is_pre_submit_planning_progress(progress):
                    return {
                        "status": "pending",
                        "phase": "planning",
                        "statusLabel": "正在分析文档并规划剧本",
                        **base,
                    }
                if _is_post_processing_progress(progress):
                    return {
                        "status": "pending",
                        "phase": "postprocessing",
                        "statusLabel": "后台处理中",
                        **base,
                    }
                return {
                    "status": "pending",
                    "phase": "generating",
                    "statusLabel": _active_generation_status_label(progress),
                    **base,
                }
            if isinstance(self.background_final_payload, dict):
                payload = dict(self.background_final_payload)
                payload.setdefault("chatBackend", CHAT_BACKEND)
                if isinstance(progress, dict):
                    base["generationProgress"] = _settle_generation_progress_for_final_payload(progress)
                payload.update({
                    "status": "completed",
                    **base,
                    "completedAt": self._isoformat(self.background_completed_at),
                })
                return payload
            if isinstance(progress, dict) and progress.get("status") in {"failed", "completed_with_error", "cancelled"}:
                return {
                    "status": "failed" if progress.get("status") != "cancelled" else "cancelled",
                    "phase": "failed" if progress.get("status") != "cancelled" else "cancelled",
                    "statusLabel": str(progress.get("error") or "后台任务已停止"),
                    **base,
                }
            if self.background_delivery_pending:
                if self.current_display_queue is not None and self._worker_running():
                    if isinstance(progress, dict):
                        if _is_pre_submit_planning_progress(progress):
                            return {
                                "status": "pending",
                                "phase": "planning",
                                "statusLabel": "正在分析文档并规划剧本",
                                **base,
                            }
                        if _is_post_processing_progress(progress):
                            return {
                                "status": "pending",
                                "phase": "postprocessing",
                                "statusLabel": "后台处理中",
                                **base,
                            }
                        return {
                            "status": "pending",
                            "phase": "generating",
                            "statusLabel": _active_generation_status_label(progress),
                            **base,
                        }
                    return {
                        "status": "pending",
                        "phase": "planning",
                        "statusLabel": "正在分析文档并规划剧本",
                        **base,
                    }
                if not isinstance(progress, dict):
                    return {
                        "status": "idle",
                        **base,
                        "stalePending": True,
                    }
                return {
                    "status": "pending",
                    **base,
                }
            if self.current_display_queue is not None and self._worker_running():
                return {
                    "status": "pending",
                    "phase": "planning",
                    "statusLabel": "正在理解请求并规划任务",
                    **base,
                }
            return {
                "status": "idle",
                **base,
            }

    def _attach_ledger_snapshot(self, base: dict) -> None:
        try:
            ledger_snapshot = get_generation_ledger_snapshot(
                self.session_id,
                self.current_generation_batch_id,
            )
        except Exception as exc:
            base["ledgerSnapshotError"] = str(exc)
            return
        if isinstance(ledger_snapshot, dict):
            base["ledgerSnapshot"] = ledger_snapshot

    def _attach_result_reconciliation(self, base: dict, progress: dict) -> None:
        reconciliation = _build_result_reconciliation(progress, self.config)
        if isinstance(reconciliation, dict):
            base["resultReconciliation"] = reconciliation

    def _fail_stale_planning_progress(self) -> None:
        progress = get_generation_progress(self.session_id)
        if not isinstance(progress, dict):
            return
        if progress.get("status") != "active":
            return
        items = progress.get("items") or []
        if not items:
            return
        if any(str(item.get("jobId") or "").strip() for item in items):
            return
        statuses = {str(item.get("status") or "").strip() for item in items}
        if not statuses.issubset({"pending_submission", "planning"}):
            return
        updated_at = self._parse_timestamp(progress.get("updatedAt"))
        started_at = self._parse_timestamp(progress.get("startedAt")) or self.current_started_at
        reference = updated_at or started_at
        if not reference:
            return
        base_timeout_seconds = max(90, int(getattr(self.config, "timeout_seconds", 120) or 120))
        total_requested = 0
        try:
            total_requested = int(progress.get("totalRequested") or len(items) or 0)
        except (TypeError, ValueError):
            total_requested = len(items)
        # Long script references are finalized in model batches before any video
        # job exists, so keep the planning window wider than the chat wait.
        timeout_seconds = max(base_timeout_seconds, 180 + max(0, total_requested) * 60)
        if time.time() - reference < timeout_seconds:
            return
        reason = "本地任务超时，视频没有提交给上游生成服务。请重新发送或缩短输入后再试。"
        fail_unsubmitted_generation_progress(self.session_id, reason)
        self.latest_error = TimeoutError(reason)

    def _fail_stale_first_frame_progress(self) -> None:
        progress = get_generation_progress(self.session_id)
        if not isinstance(progress, dict):
            return
        if progress.get("status") != "active":
            return
        items = progress.get("items") or []
        if not items:
            return
        if any(str(item.get("jobId") or "").strip() for item in items):
            return
        statuses = {str(item.get("status") or "").strip() for item in items}
        if not statuses.issubset({"preparing_first_frame", "pending_submission"}):
            return
        updated_at = self._parse_timestamp(progress.get("updatedAt"))
        started_at = self._parse_timestamp(progress.get("startedAt")) or self.current_started_at
        reference = updated_at or started_at
        if not reference:
            return
        try:
            total_requested = int(progress.get("totalRequested") or len(items) or 0)
        except (TypeError, ValueError):
            total_requested = len(items)
        timeout_seconds = max(
            90,
            int(getattr(self.config, "timeout_seconds", 180) or 180) + 60,
            180 + max(0, total_requested) * 60,
        )
        if time.time() - reference < timeout_seconds:
            return
        reason = (
            f"图生图阶段超过 {timeout_seconds} 秒没有任何视频任务提交。"
            "本轮已判定为后台卡死，停止继续显示进行中。"
        )
        fail_generation_progress(self.session_id, reason, skip_pending=False)
        self.latest_error = TimeoutError(reason)

    def _refresh_provider_generation_progress(self) -> None:
        if self.config is None:
            return
        jobs = claim_active_generation_jobs(self.session_id)
        if not jobs:
            return
        client = AI8VideoModelClient(config=self.config)
        for item in jobs:
            job_id = str(item.get("jobId") or "").strip()
            if not job_id:
                continue
            try:
                latest = client.get_job(
                    job_id,
                    video_index=int(item.get("videoIndex") or 1),
                )
            except Exception:
                continue
            latest.segment_index = item.get("segmentIndex")
            latest.segment_label = item.get("segmentLabel")
            mark_job_polling(self.session_id, latest)

    def start_external_generation_task(
        self,
        generation_batch_id: str,
        target: Callable[..., object],
        *,
        args: tuple[Any, ...] = (),
    ) -> GenerationTask:
        with self.lock:
            runner = self._ensure_task_runner()
            previous_batch_ids = runner.cancel_active(except_batch_id=generation_batch_id)
            for previous_batch_id in previous_batch_ids:
                record_generation_execution(
                    session_id=self.session_id,
                    generation_batch_id=previous_batch_id,
                    execution_state="cancel_requested",
                    cancel_requested=True,
                )
            clear_chat_snapshot(self.session_id)
            self.latest_ai8video_payload = None
            self.latest_error = None
            self.current_generation_batch_id = generation_batch_id
            self.current_started_at = time.time()
            self.current_display_queue = None
            self.current_message = None
            self.background_delivery_pending = False
            self.background_final_payload = None
            self.background_completed_at = None
            self.cancelled_at = None
            self.cancel_reason = None
            task = runner.start(generation_batch_id, target, args=args)
            self.worker_thread = task.thread
            return task

    def _start_task(self, message: str) -> None:
        task_runner = self._ensure_task_runner()
        previous_batch_ids = task_runner.cancel_active()
        for previous_batch_id in previous_batch_ids:
            record_generation_execution(
                session_id=self.session_id,
                generation_batch_id=previous_batch_id,
                execution_state="cancel_requested",
                cancel_requested=True,
            )
        self.latest_ai8video_payload = None
        self.latest_error = None
        clear_chat_snapshot(self.session_id)
        clear_generation_progress(self.session_id)
        self.current_generation_batch_id = create_generation_batch_id(self.session_id)
        claim_generation_batch(self.session_id, self.current_generation_batch_id)
        record_generation_execution(
            session_id=self.session_id,
            generation_batch_id=self.current_generation_batch_id,
            execution_state="queued",
            request_snapshot={"message": str(message)},
        )
        self.current_message = message
        self.current_started_at = time.time()
        self.current_display_queue = Queue()
        self.background_delivery_pending = False
        self.background_final_payload = None
        self.background_completed_at = None
        self.cancelled_at = None
        self.cancel_reason = None
        task = task_runner.start(
            self.current_generation_batch_id,
            self._run_generation_task,
            args=(message, self.current_display_queue),
            result_queue=self.current_display_queue,
        )
        self.worker_thread = task.thread
        record_generation_execution(
            session_id=self.session_id,
            generation_batch_id=self.current_generation_batch_id,
            execution_state="running",
            worker_id=task.worker_id,
        )

    def cancel_current(self, reason: str | None = None) -> dict:
        with self.lock:
            self.cancel_reason = str(reason or "").strip() or "用户强行终止，本地停止等待结果回填"
            self.cancelled_at = time.time()
            current_batch_id = self.current_generation_batch_id
            self._ensure_task_runner().cancel(current_batch_id)
            if current_batch_id:
                record_generation_execution(
                    session_id=self.session_id,
                    generation_batch_id=current_batch_id,
                    execution_state="cancel_requested",
                    cancel_requested=True,
                )
            progress = cancel_generation_progress(self.session_id, self.cancel_reason)
            clear_chat_snapshot(self.session_id)
            self.background_delivery_pending = False
            self.background_final_payload = None
            self.background_completed_at = None
            self.current_display_queue = None
            base = {
                "status": "cancelled",
                "phase": "cancelled",
                "statusLabel": "已强行终止",
                "sessionId": self.session_id,
                "generationBatchId": self.current_generation_batch_id,
                "pendingSince": self._isoformat(self.current_started_at),
                "elapsedSeconds": self._elapsed_seconds(),
                "cancelledAt": self._isoformat(self.cancelled_at),
                "cancelReason": self.cancel_reason,
            }
            if isinstance(progress, dict):
                base["generationProgress"] = progress
            return base

    def _run_generation_task(self, task: GenerationTask, message: str, display_queue) -> None:
        self._run_runtime_chat(
            message,
            display_queue,
            task.generation_batch_id,
            cancel_event=task.cancel_event,
            worker_id=task.worker_id,
        )

    def _run_runtime_chat(
        self,
        message: str,
        display_queue,
        generation_batch_id: str | None,
        *,
        cancel_event: threading.Event | None = None,
        worker_id: str | None = None,
    ) -> None:
        batch_context_token = set_current_generation_batch_id(generation_batch_id)
        session_context_token = set_current_generation_session_id(self.session_id)
        payload = None
        runtime_error = None
        try:
            payload = handle_chat_message(
                session_id=self.session_id,
                message=message,
                refresh=False,
            )
            payload.setdefault("chatBackend", CHAT_BACKEND)
            if generation_batch_id:
                payload.setdefault("generationBatchId", generation_batch_id)
        except Exception as exc:
            runtime_error = exc
        finally:
            reset_current_generation_session_id(session_context_token)
            reset_current_generation_batch_id(batch_context_token)
            with self.lock:
                if self._is_current_generation_batch_id(generation_batch_id):
                    if isinstance(payload, dict):
                        self.latest_ai8video_payload = payload
                    elif runtime_error is not None:
                        self.latest_error = runtime_error
            if generation_batch_id:
                cancelled = bool(cancel_event and cancel_event.is_set())
                execution_error = None if cancelled else (
                    runtime_error or _runtime_payload_error(payload)
                )
                execution_state = "cancelled" if cancelled else (
                    "failed" if execution_error is not None else "completed"
                )
                record_generation_execution(
                    session_id=self.session_id,
                    generation_batch_id=generation_batch_id,
                    execution_state=execution_state,
                    worker_id=worker_id,
                    cancel_requested=cancelled,
                    result_snapshot=_execution_result_snapshot(payload),
                    error=execution_error,
                )
            queue_item = {
                "done": True,
                "generationBatchId": generation_batch_id,
            }
            if isinstance(payload, dict):
                queue_item["payload"] = payload
            if runtime_error is not None:
                queue_item["error"] = runtime_error
            display_queue.put(queue_item)

    def _mark_completed(self, payload: dict) -> None:
        if self.current_generation_batch_id:
            payload.setdefault("generationBatchId", self.current_generation_batch_id)
        self.background_final_payload = payload
        self.background_completed_at = time.time()
        self.current_display_queue = None

    def _is_current_generation_batch_id(self, generation_batch_id: str | None) -> bool:
        normalized_generation_batch_id = str(generation_batch_id or "").strip()
        if not normalized_generation_batch_id:
            return True
        current_generation_batch_id = str(self.current_generation_batch_id or "").strip()
        return current_generation_batch_id == normalized_generation_batch_id

    def _refresh_active_task_state(self) -> None:
        queue_obj = self.current_display_queue
        done_seen = False
        if queue_obj is not None:
            while True:
                try:
                    item = queue_obj.get_nowait()
                except Empty:
                    break
                if "done" in item:
                    done_seen = True
            payload = self.latest_ai8video_payload
            if isinstance(payload, dict) and (done_seen or not self._worker_running()):
                self._mark_completed(payload)
                return
            if done_seen and not isinstance(payload, dict):
                if self.latest_error is not None:
                    self._mark_completed(_runtime_error_payload(self.session_id, self.latest_error))
                else:
                    self.current_display_queue = None
                return
        cached = get_chat_snapshot(self.session_id)
        if isinstance(cached, dict) and isinstance(cached.get("reply"), dict):
            self._mark_completed(cached)
            return
        if self.background_delivery_pending and isinstance(self.latest_ai8video_payload, dict):
            self.background_final_payload = self.latest_ai8video_payload
            self.background_completed_at = self.background_completed_at or time.time()
            self.current_display_queue = None

    def _worker_running(self) -> bool:
        worker = getattr(self, "worker_thread", None)
        if worker is not None and hasattr(worker, "is_alive"):
            return bool(worker.is_alive())
        runner = getattr(self, "task_runner", None)
        return bool(runner and runner.is_running(getattr(self, "current_generation_batch_id", None)))

    def _ensure_task_runner(self) -> GenerationTaskRunner:
        runner = getattr(self, "task_runner", None)
        if isinstance(runner, GenerationTaskRunner):
            return runner
        runner = GenerationTaskRunner(worker_prefix=f"ai8video-{self.session_id}")
        self.task_runner = runner
        return runner

    def _elapsed_seconds(self) -> int:
        if not self.current_started_at:
            return 0
        return max(0, int(time.time() - self.current_started_at))

    @staticmethod
    def _isoformat(timestamp: float | None) -> str | None:
        if not timestamp:
            return None
        return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")

    @staticmethod
    def _parse_timestamp(value) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return None


_SESSIONS: dict[str, _AI8VideoSession] = {}
_SESSIONS_LOCK = threading.Lock()


def _get_session(session_id: str, refresh: bool = False) -> _AI8VideoSession:
    if refresh:
        with _SESSIONS_LOCK:
            previous_session = _SESSIONS.pop(session_id, None)
        if previous_session is not None:
            previous_session.retire("新的基础需求已开始，上一轮后台任务已停止")
        get_runtime().reset_conversation_session(session_id)
        clear_chat_snapshot(session_id)
        replacement = _AI8VideoSession(session_id=session_id)
        with _SESSIONS_LOCK:
            _SESSIONS[session_id] = replacement
        return replacement
    with _SESSIONS_LOCK:
        if session_id not in _SESSIONS:
            _SESSIONS[session_id] = _AI8VideoSession(session_id=session_id)
        return _SESSIONS[session_id]


def handle_chat_via_ai8video(
    session_id: str,
    message: str,
    refresh: bool = False,
    timeout_seconds: int | None = None,
) -> dict:
    session = _get_session(session_id=session_id, refresh=refresh)
    return session.handle_message(message=message, timeout_seconds=timeout_seconds)


def reset_chat_session_via_ai8video(session_id: str) -> None:
    _get_session(session_id=session_id, refresh=True)


def restore_smart_split_plan_via_ai8video(session_id: str, videos: list[dict]) -> bool:
    _get_session(session_id=session_id)
    return restore_smart_split_plan(session_id, videos)


def start_external_generation_task(
    session_id: str,
    generation_batch_id: str,
    target: Callable[..., object],
    *,
    args: tuple[Any, ...] = (),
) -> GenerationTask:
    session = _get_session(session_id=session_id)
    return session.start_external_generation_task(
        generation_batch_id,
        target,
        args=args,
    )


def get_chat_status_via_ai8video(session_id: str, generation_batch_id: str | None = None) -> dict:
    requested_generation_batch_id = str(generation_batch_id or "").strip()
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(session_id)
    if session is None:
        return _recover_chat_status_from_ledger(session_id, requested_generation_batch_id)
    status = session.snapshot_status()
    current_generation_batch_id = _status_generation_batch_id(status)
    family = get_generation_batch_family_snapshot(
        session_id,
        requested_generation_batch_id or current_generation_batch_id,
    )
    if isinstance(family, dict) and family.get("childBatches"):
        return _merge_live_status_with_generation_family(status, family)
    if not requested_generation_batch_id or current_generation_batch_id == requested_generation_batch_id:
        return status
    return _unknown_generation_batch_status(
        session_id,
        requested_generation_batch_id,
        current_generation_batch_id,
    )


def _recover_chat_status_from_ledger(session_id: str, generation_batch_id: str | None) -> dict:
    try:
        ledger_snapshot = get_generation_batch_family_snapshot(session_id, generation_batch_id)
    except Exception as exc:
        body = _idle_chat_status(session_id, generation_batch_id)
        body["ledgerSnapshotError"] = str(exc)
        return body
    if not isinstance(ledger_snapshot, dict):
        if generation_batch_id:
            return _unknown_generation_batch_status(session_id, generation_batch_id)
        return _idle_chat_status(session_id)

    recovered_progress = ledger_snapshot.get("progress")
    recovered_progress = dict(recovered_progress) if isinstance(recovered_progress, dict) else {}
    recovered_progress["items"] = [dict(item) for item in recovered_progress.get("items") or []]
    recovered_generation_batch_id = str(ledger_snapshot.get("generationBatchId") or "").strip()
    recovered_progress.setdefault("sessionId", session_id)
    recovered_progress.setdefault("generationBatchId", recovered_generation_batch_id)
    recovered_progress["readOnlyRecovery"] = True
    recovered_progress["willResumeGeneration"] = False
    recovered_progress["statelessProgress"] = True
    body = {
        "status": "recovered",
        "phase": "read_only_recovery",
        "statusLabel": "已从任务账本恢复历史进度，不会自动继续生成",
        "sessionId": session_id,
        "generationBatchId": recovered_generation_batch_id,
        "generationProgress": recovered_progress,
        "ledgerSnapshot": ledger_snapshot,
        "readOnlyRecovery": True,
        "willResumeGeneration": False,
        "statelessProgress": True,
        "stalePending": True,
    }
    asset_records: list[dict[str, Any]] = []
    try:
        asset_records = JsonlAssetStore(AI8VideoConfig.from_env().asset_store_path).read_all()
        checkpoint = prepare_recovered_tail_frame_resume(
            session_id=session_id,
            source_batch_id=recovered_generation_batch_id,
            progress=recovered_progress,
            asset_records=asset_records,
        )
    except (FileNotFoundError, LookupError, RuntimeError, ValueError):
        checkpoint = None
    if checkpoint is not None:
        _restore_completed_recovery_items(
            recovered_progress,
            asset_records,
            session_id,
            checkpoint.next_video_index,
        )
        for item in recovered_progress["items"]:
            video_index = int(item.get("videoIndex") or 0)
            if video_index == checkpoint.next_video_index:
                item.update(
                    status="awaiting_tail_frame_continue",
                    statusLabel="检测到中间产物，等待继续",
                    error="",
                    tailFramePreviewUrl=checkpoint.preview_url(),
                    generationBatchId=recovered_generation_batch_id,
                )
                item.pop("historicalSnapshot", None)
            elif video_index > checkpoint.next_video_index:
                item.update(
                    status="pending_submission",
                    statusLabel="等待前序视频",
                    error="",
                    jobId=None,
                    providerProgress=0,
                    percent=0,
                    generationBatchId=recovered_generation_batch_id,
                )
                item.pop("historicalSnapshot", None)
        recovered_progress["events"] = _recovered_manual_wait_events(
            recovered_progress.get("events"), checkpoint.next_video_index
        )
        recovered_progress.update(
            status="active",
            readOnlyRecovery=False,
            willResumeGeneration=True,
            statelessProgress=False,
        )
        _with_generation_progress_counts(recovered_progress)
        body.update(
            status="pending",
            phase="awaiting_tail_frame_continue",
            statusLabel="检测到可继续的中间产物，已切换为手动传尾帧",
            readOnlyRecovery=False,
            willResumeGeneration=True,
            statelessProgress=False,
            stalePending=False,
        )
    result_reconciliation = _build_result_reconciliation(recovered_progress)
    if isinstance(result_reconciliation, dict):
        body["resultReconciliation"] = result_reconciliation
    return body


def _merge_live_status_with_generation_family(status: dict, family: dict) -> dict:
    progress = dict(family.get("progress") or {})
    root_batch_id = str(family.get("generationBatchId") or "").strip()
    child_batches = [
        child
        for child in family.get("childBatches") or []
        if isinstance(child, dict)
    ]
    active_target_video_indexes = [
        int(child.get("targetVideoIndex") or 0)
        for child in child_batches
        if str(child.get("status") or "").strip() == "active"
        and int(child.get("targetVideoIndex") or 0) > 0
    ]
    session_id = str(status.get("sessionId") or "").strip()
    if session_id and active_target_video_indexes:
        try:
            asset_records = JsonlAssetStore(
                AI8VideoConfig.from_env().asset_store_path
            ).read_all()
            _restore_completed_recovery_items(
                progress,
                asset_records,
                session_id,
                max(active_target_video_indexes),
            )
            _with_generation_progress_counts(progress)
        except (FileNotFoundError, OSError, ValueError):
            pass
    active = str(progress.get("status") or "").strip() == "active"
    return {
        **status,
        "status": "pending" if active else status.get("status"),
        "phase": family.get("phase") or status.get("phase"),
        "generationBatchId": root_batch_id,
        "generationProgress": progress,
        "generationBatchFamily": {
            "rootGenerationBatchId": root_batch_id,
            "children": child_batches,
        },
    }


def _restore_completed_recovery_items(
    progress: dict[str, Any],
    asset_records: list[dict[str, Any]],
    session_id: str,
    next_video_index: int,
) -> None:
    items = [dict(item) for item in progress.get("items") or [] if isinstance(item, dict)]
    items_by_index = {int(item.get("videoIndex") or 0): item for item in items}
    latest_records: dict[int, dict[str, Any]] = {}
    for record in asset_records:
        video_index = int(record.get("videoIndex") or 0)
        archive_path = _resolve_recovered_archive_path(record)
        if (
            str(record.get("sessionId") or "") == session_id
            and 0 < video_index < next_video_index
            and str(record.get("generationStatus") or "") == "generated"
            and archive_path is not None
        ):
            latest_records[video_index] = {
                **record,
                "archiveLocalPath": str(archive_path),
            }
    for video_index, record in latest_records.items():
        previous = items_by_index.get(video_index, {})
        items_by_index[video_index] = {
            **previous,
            "videoIndex": video_index,
            "title": str(previous.get("title") or record.get("videoTitle") or f"视频 {video_index}"),
            "status": "succeeded",
            "statusLabel": "已生成",
            "jobId": record.get("jobId"),
            "videoUrl": record.get("archiveUrl") or record.get("videoUrl"),
            "assetRecord": record,
            "hasLocalAsset": True,
        }
        items_by_index[video_index].pop("historicalSnapshot", None)
        items_by_index[video_index].pop("error", None)
    progress["items"] = [items_by_index[index] for index in sorted(items_by_index) if index > 0]


def _resolve_recovered_archive_path(record: dict[str, Any]) -> Path | None:
    archive_path = Path(str(record.get("archiveLocalPath") or ""))
    if archive_path.is_file():
        return archive_path
    try:
        legacy_relative_path = archive_path.relative_to(USER_GENERATED_RESULT_ROOT)
    except ValueError:
        return None
    if legacy_relative_path.parts[:1] != ("video",):
        return None
    source_path = USER_GENERATED_RESULT_ROOT / "source" / legacy_relative_path
    return source_path if source_path.is_file() else None


def _recovered_manual_wait_events(events: Any, next_video_index: int) -> list[dict[str, Any]]:
    retained = [
        dict(event)
        for event in events or []
        if isinstance(event, dict)
        and int(event.get("videoIndex") or 0) < next_video_index
    ]
    retained.append({
        "at": datetime.now().isoformat(timespec="seconds"),
        "kind": "awaiting_tail_frame_continue",
        "status": "awaiting_tail_frame_continue",
        "title": "后台任务",
        "videoIndex": next_video_index,
        "message": "尾帧已恢复，等待用户点击继续",
    })
    return retained


def _build_result_reconciliation(
    progress: dict,
    config: AI8VideoConfig | None = None,
) -> dict:
    generation_batch_id = str(progress.get("generationBatchId") or "").strip() or None
    try:
        effective_config = config or AI8VideoConfig.from_env()
        asset_records = JsonlAssetStore(effective_config.asset_store_path).read_all()
        return build_generation_result_reconciliation(progress, asset_records)
    except Exception as exc:
        return {
            "generationBatchId": generation_batch_id,
            "error": str(exc),
        }


def _idle_chat_status(session_id: str, generation_batch_id: str | None = None) -> dict:
    body = {
        "status": "idle",
        "sessionId": session_id,
        "stalePending": True,
    }
    if generation_batch_id:
        body["generationBatchId"] = generation_batch_id
    return body


def _unknown_generation_batch_status(
    session_id: str,
    requested_generation_batch_id: str,
    current_generation_batch_id: str | None = None,
) -> dict:
    return {
        "status": "not_found",
        "phase": "unknown_generation_batch",
        "statusLabel": "未找到当前会话下的生成批次",
        "sessionId": session_id,
        "generationBatchId": requested_generation_batch_id,
        "currentGenerationBatchId": current_generation_batch_id,
    }


def cancel_chat_via_ai8video(
    session_id: str,
    reason: str | None = None,
    generation_batch_id: str | None = None,
) -> dict:
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(session_id)
    if session is None:
        progress = cancel_generation_progress(
            session_id,
            reason or "用户强行终止，本地停止等待结果回填",
            generation_batch_id=generation_batch_id,
        )
        return {
            "status": "cancelled" if progress else "idle",
            "phase": "cancelled" if progress else "idle",
            "statusLabel": "已强行终止" if progress else "后台没有对应会话",
            "sessionId": session_id,
            "generationProgress": progress,
        }
    return session.cancel_current(reason=reason)


def cancel_smart_split_confirmation_via_ai8video(session_id: str) -> dict:
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(session_id)
    if session is None:
        cancel_smart_split_confirmation_in_runtime(session_id)
        cancel_generation_progress(session_id, "用户取消智能分集确认")
        clear_chat_snapshot(session_id)
        cancelled = True
    else:
        cancelled = session.cancel_smart_split_confirmation()
    return {
        "ok": True,
        "cancelled": cancelled,
        "sessionId": session_id,
    }


def _status_generation_batch_id(status: dict) -> str | None:
    direct_generation_batch_id = str(status.get("generationBatchId") or "").strip()
    if direct_generation_batch_id:
        return direct_generation_batch_id
    progress = status.get("generationProgress")
    if not isinstance(progress, dict):
        return None
    progress_generation_batch_id = str(progress.get("generationBatchId") or "").strip()
    return progress_generation_batch_id or None


def _is_pre_submit_planning_progress(progress: dict) -> bool:
    items = progress.get("items") or []
    if not items:
        return False
    if any(str(item.get("jobId") or "").strip() for item in items):
        return False
    submitted = int(progress.get("submittedCount") or 0)
    running = int(progress.get("runningCount") or 0)
    succeeded = int(progress.get("succeededCount") or 0)
    failed = int(progress.get("failedCount") or 0)
    if submitted or running or succeeded or failed:
        return False
    statuses = {str(item.get("status") or "").strip() for item in items}
    return statuses.issubset({"pending_submission"})


def _has_active_generation_progress(progress: dict) -> bool:
    if str(progress.get("status") or "").strip() == "cancelled":
        return False
    running = int(progress.get("runningCount") or 0)
    waiting = int(progress.get("waitingCount") or 0)
    if running > 0 or waiting > 0:
        return True
    terminal_statuses = {"succeeded", "failed", "skipped", "cancelled", "canceled"}
    items = progress.get("items") or []
    return any(str(item.get("status") or "").strip() not in terminal_statuses for item in items)


def _active_generation_status_label(progress: dict) -> str:
    failed = int(progress.get("failedCount") or 0)
    if failed > 0:
        return "真实视频生成中，部分任务已失败"
    return "真实视频生成中"


def _settle_generation_progress_for_final_payload(progress: dict) -> dict:
    settled = {
        **progress,
        "items": [dict(item) for item in progress.get("items") or []],
    }
    terminal_status = str(settled.get("status") or "").strip()
    if terminal_status not in {"failed", "completed", "completed_with_error", "cancelled"}:
        return _with_generation_progress_counts(settled)
    for item in settled.get("items") or []:
        item_status = str(item.get("status") or "").strip()
        if item_status in {"succeeded", "failed", "skipped", "cancelled", "canceled", "deleted"}:
            continue
        if terminal_status == "cancelled":
            item["status"] = "skipped"
            item["statusLabel"] = "已取消"
        elif terminal_status == "completed":
            item["status"] = "succeeded"
            item["statusLabel"] = "已生成"
        else:
            item["status"] = "failed"
            item["statusLabel"] = "生成失败"
            item.setdefault("error", str(settled.get("error") or "生成失败"))
    return _with_generation_progress_counts(settled)


def _with_generation_progress_counts(progress: dict) -> dict:
    items = progress.get("items") or []
    running_statuses = {"submitting", "preparing_first_frame", "submitted", "polling", "archiving"}
    progress["submittedCount"] = sum(1 for item in items if _has_video_submission(item))
    progress["runningCount"] = sum(1 for item in items if item.get("status") in running_statuses)
    progress["postProcessingCount"] = sum(1 for item in items if item.get("status") == "archiving")
    waiting_statuses = {"pending_submission", "awaiting_tail_frame_continue"}
    progress["waitingCount"] = sum(1 for item in items if item.get("status") in waiting_statuses)
    progress["succeededCount"] = sum(1 for item in items if item.get("status") == "succeeded")
    progress["failedCount"] = sum(1 for item in items if item.get("status") == "failed")
    progress["skippedCount"] = sum(1 for item in items if item.get("status") == "skipped")
    progress["totalRequested"] = int(progress.get("totalRequested") or len(items) or 0)
    return progress


def _has_video_submission(item: dict) -> bool:
    status = str(item.get("status") or "").strip()
    if status not in {"submitted", "polling", "archiving", "succeeded", "failed", "deleted"}:
        return False
    provider_status = str(item.get("providerStatus") or "").strip()
    if provider_status in {"first_frame_failed", "first_frame_response_lost", "local_interrupted"}:
        return False
    job_id = str(item.get("jobId") or "").strip()
    if job_id.startswith(("create-failed-", "first-frame-failed-", "interrupted-before-submit-", "merge2-failed-")):
        return False
    return True


def _is_post_processing_progress(progress: dict) -> bool:
    items = progress.get("items") or []
    if not items:
        return False
    running = int(progress.get("runningCount") or 0)
    post_processing = int(progress.get("postProcessingCount") or 0)
    if not running or not post_processing:
        return False
    active_statuses = {"submitting", "preparing_first_frame", "submitted", "polling"}
    return not any(str(item.get("status") or "").strip() in active_statuses for item in items)


def _execution_result_snapshot(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    reply = payload.get("reply") if isinstance(payload.get("reply"), dict) else {}
    result = payload.get("result")
    return {
        "stage": str(reply.get("stage") or payload.get("status") or "").strip() or None,
        "hasResult": isinstance(result, dict),
        "videoCount": len(result.get("videos") or []) if isinstance(result, dict) else 0,
    }


def _runtime_payload_error(payload: dict | None) -> _RuntimePayloadError | None:
    if not isinstance(payload, dict):
        return None
    reply = payload.get("reply") if isinstance(payload.get("reply"), dict) else {}
    stage = str(reply.get("stage") or payload.get("status") or "").strip().lower()
    error_payload = payload.get("error")
    if stage not in {"error", "failed"} and not error_payload:
        return None
    if isinstance(error_payload, dict):
        raw_message = str(error_payload.get("message") or "").strip()
    else:
        raw_message = str(error_payload or "").strip()
    raw_message = raw_message or str(reply.get("text") or "").strip() or "运行时返回失败结果"
    return _RuntimePayloadError(humanize_failed_video_reason(raw_message)[:500])


def _timeout_without_generation_payload(session_id: str) -> dict:
    return {
        "reply": {
            "text": (
                "AI8video 规划超时，本轮没有进入真实视频提交阶段。"
                "已停止等待，避免显示假进度或重复创建任务。"
            ),
            "stage": "error",
            "awaiting": None,
            "draft": None,
            "meta": {
                "operation": "timeout",
                "errorType": "AI8VIDEO_CHAT_TIMEOUT_NO_GENERATION",
            },
            "result": None,
        },
        "status": "failed",
        "sessionId": session_id,
        "chatBackend": "ai8video-timeout",
    }


def _runtime_error_payload(session_id: str, exc: BaseException) -> dict:
    raw_message = str(exc).strip() or exc.__class__.__name__
    message = humanize_failed_video_reason(raw_message)
    return {
        "reply": {
            "text": message,
            "stage": "error",
            "awaiting": None,
            "draft": None,
            "meta": {
                "operation": "error",
                "errorType": exc.__class__.__name__,
            },
            "result": None,
        },
        "error": {
            "type": exc.__class__.__name__,
            "message": message,
            "rawMessage": raw_message,
        },
        "status": "failed",
        "sessionId": session_id,
        "chatBackend": CHAT_BACKEND,
    }
