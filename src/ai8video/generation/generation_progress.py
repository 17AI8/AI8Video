from __future__ import annotations

from datetime import datetime, timezone
import time
import re
from threading import Lock
from typing import Any
from uuid import uuid4

from ai8video.generation.generation_batch_context import get_current_generation_batch_id
from ai8video.core.models import VideoPrompt, QuickVideoJob
from ai8video.batch.task_ledger import TaskLedger


_PROGRESS: dict[str, dict[str, Any]] = {}
_LATEST_BATCH_IDS: dict[str, str] = {}
_LOCK = Lock()
_TASK_LEDGER = TaskLedger()
_TERMINAL_ITEM_STATUSES = {"succeeded", "failed", "skipped", "cancelled", "canceled", "deleted"}
_MAX_EXECUTION_EVENTS = 80


class GenerationCancelled(RuntimeError):
    pass


_TERMINAL_PROGRESS_STATUSES = {"failed", "completed", "completed_with_error", "cancelled", "canceled"}


def _normalize_generation_batch_id(generation_batch_id: str | None) -> str | None:
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "-", str(generation_batch_id or "").strip()).strip("-_")
    return normalized[:160] or None


def _normalize_batch_id_part(value: str | None) -> str | None:
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "-", str(value or "").strip()).strip("-_")
    return normalized[:80] or None


def _batch_context_matches(progress: dict[str, Any] | None) -> bool:
    if not isinstance(progress, dict):
        return True
    context_batch_id = _normalize_generation_batch_id(get_current_generation_batch_id())
    if not context_batch_id:
        return True
    progress_batch_id = _normalize_generation_batch_id(progress.get("generationBatchId"))
    latest_batch_id = _LATEST_BATCH_IDS.get(str(progress.get("sessionId") or "").strip())
    return context_batch_id == progress_batch_id and (
        not latest_batch_id or latest_batch_id == context_batch_id
    )


def create_generation_batch_id(session_id: str | None = None) -> str:
    normalized_session_id = _normalize_batch_id_part(session_id) or "session"
    timestamp_part = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    random_part = uuid4().hex[:10]
    return f"gb-{normalized_session_id}-{timestamp_part}-{random_part}"


def claim_generation_batch(session_id: str | None, generation_batch_id: str | None) -> str | None:
    """声明某个会话的当前批次，阻止旧线程重新创建或污染新进度。"""
    normalized_session_id = _normalize_session_id(session_id)
    normalized_batch_id = _normalize_generation_batch_id(generation_batch_id)
    if not normalized_session_id or not normalized_batch_id:
        return None
    with _LOCK:
        _LATEST_BATCH_IDS[normalized_session_id] = normalized_batch_id
    return normalized_batch_id


def record_generation_execution(
    *,
    session_id: str,
    generation_batch_id: str,
    task_type: str = "chat_generation",
    execution_state: str | None = None,
    worker_id: str | None = None,
    cancel_requested: bool | None = None,
    request_snapshot: dict[str, Any] | None = None,
    result_snapshot: dict[str, Any] | None = None,
    error: BaseException | str | None = None,
) -> None:
    """把执行器状态写入账本；账本故障只记录到当前进度，不阻断生成。"""
    normalized_session_id = _normalize_session_id(session_id)
    normalized_batch_id = _normalize_generation_batch_id(generation_batch_id)
    if not normalized_session_id or not normalized_batch_id:
        return
    try:
        _TASK_LEDGER.ensure_generation_batch(
            session_id=normalized_session_id,
            generation_batch_id=normalized_batch_id,
            task_type=task_type,
            request_snapshot=request_snapshot,
        )
        _TASK_LEDGER.update_generation_execution(
            normalized_batch_id,
            task_type=task_type,
            execution_state=execution_state,
            worker_id=worker_id,
            cancel_requested=cancel_requested,
            request_snapshot=request_snapshot,
            result_snapshot=result_snapshot,
            error=error,
        )
    except Exception as exc:
        _mark_task_ledger_error(normalized_session_id, exc)


def get_generation_ledger_snapshot(
    session_id: str | None,
    generation_batch_id: str | None = None,
) -> dict[str, Any] | None:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return None
    ledger_path = getattr(_TASK_LEDGER, "path", None)
    if ledger_path is not None and not ledger_path.exists():
        return None
    normalized_generation_batch_id = _normalize_generation_batch_id(generation_batch_id)
    if normalized_generation_batch_id:
        record = _TASK_LEDGER.get_generation_batch(normalized_generation_batch_id)
        if not isinstance(record, dict):
            return None
        if str(record.get("sessionId") or "").strip() != normalized_session_id:
            return None
        return record
    return _TASK_LEDGER.get_latest_generation_batch_for_session(normalized_session_id)


def start_generation_progress(
    session_id: str | None,
    videos: list[VideoPrompt],
    *,
    concurrent: bool = False,
    generation_batch_id: str | None = None,
) -> None:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return
    normalized_generation_batch_id = _normalize_generation_batch_id(
        generation_batch_id or get_current_generation_batch_id()
    )
    now = time.time()
    snapshot: dict[str, Any] | None = None
    with _LOCK:
        existing = _PROGRESS.get(normalized_session_id)
        latest_batch_id = _LATEST_BATCH_IDS.get(normalized_session_id)
        existing_batch_id = _normalize_generation_batch_id(
            existing.get("generationBatchId") if isinstance(existing, dict) else None
        )
        if not normalized_generation_batch_id:
            normalized_generation_batch_id = existing_batch_id or latest_batch_id
        if latest_batch_id and normalized_generation_batch_id and latest_batch_id != normalized_generation_batch_id:
            return
        if existing_batch_id and normalized_generation_batch_id and existing_batch_id != normalized_generation_batch_id:
            return
        if existing and str(existing.get("status") or "").strip() in _TERMINAL_PROGRESS_STATUSES:
            return
        if not normalized_generation_batch_id:
            normalized_generation_batch_id = create_generation_batch_id(normalized_session_id)
        _LATEST_BATCH_IDS[normalized_session_id] = normalized_generation_batch_id
        _PROGRESS[normalized_session_id] = {
            "sessionId": normalized_session_id,
            "generationBatchId": normalized_generation_batch_id,
            "status": "active",
            "startedAt": _isoformat(now),
            "updatedAt": _isoformat(now),
            "concurrent": bool(concurrent),
            "totalRequested": len(videos),
            "items": [
                {
                    "videoIndex": video.index,
                    "title": video.title or f"视频 {video.index}",
                    "status": "pending_submission",
                    "statusLabel": "正在生成视频方案",
                    "jobId": None,
                    "updatedAt": _isoformat(now),
                }
                for video in videos
            ],
            "events": [{
                "at": _isoformat(now),
                "kind": "batch_started",
                "message": f"已创建 {len(videos)} 个视频任务，正在生成视频方案。",
            }],
        }
        snapshot = _copy_progress(_PROGRESS[normalized_session_id])
    _persist_progress_snapshot(snapshot)


def mark_job_submitted(session_id: str | None, video: VideoPrompt, job: QuickVideoJob) -> None:
    segment_values = _segment_values_from_job(job) or _segment_values_from_video(video)
    if segment_values:
        job.segment_index = segment_values.get("segmentIndex")
        job.segment_label = segment_values.get("segmentLabel")
    _update_item(
        session_id,
        video.index,
        {
            "title": video.title or f"视频 {video.index}",
            "status": "submitted",
            "statusLabel": _with_segment_label("已提交", segment_values.get("segmentLabel") if segment_values else ""),
            "jobId": job.job_id,
            **segment_values,
        },
    )


def mark_job_submitting(session_id: str | None, video: VideoPrompt) -> None:
    segment_values = _segment_values_from_video(video)
    _update_item(
        session_id,
        video.index,
        {
            "title": video.title or f"视频 {video.index}",
            "status": "submitting",
            "statusLabel": _with_segment_label("提交中", segment_values.get("segmentLabel") if segment_values else ""),
            **segment_values,
        },
    )


def mark_job_preparing_first_frame(session_id: str | None, video: VideoPrompt) -> None:
    now = time.time()
    current_started_at = _current_item_field(session_id, video.index, "firstFrameStartedAt")
    _update_item(
        session_id,
        video.index,
        {
            "title": video.title or f"视频 {video.index}",
            "status": "preparing_first_frame",
            "statusLabel": "正在生成首帧图",
            "firstFrameStartedAt": current_started_at or _isoformat(now),
        },
    )


def mark_job_polling(session_id: str | None, job: QuickVideoJob) -> None:
    provider_status = str(job.provider_status or "").strip()
    provider_progress = _normalize_provider_progress(job.provider_progress)
    status_label = str(job.stage_label or "").strip()
    segment_values = _segment_values_from_job(job)
    status = "polling"
    clear_provider_state = False
    if str(job.status or "").strip().lower() in {"succeeded", "completed"}:
        clear_provider_state = True
        if segment_values:
            status_label = "片段已生成，正在准备后续处理"
        else:
            status = "archiving"
            status_label = "后台处理中"
    elif job.status == "failed":
        status = "failed"
        status_label = "生成失败"
    elif provider_progress is not None:
        status_label = f"真实生成进度 {provider_progress}%"
    elif provider_status:
        status_label = f"上游状态：{provider_status}"
    elif not status_label:
        status_label = "接口轮询中"
    elif provider_progress is None:
        clear_provider_state = True
    status_label = _with_segment_label(status_label, segment_values.get("segmentLabel") if segment_values else "")
    values: dict[str, Any] = {
        "status": status,
        "statusLabel": status_label,
        "jobId": job.job_id,
        **segment_values,
    }
    if clear_provider_state or status in {"archiving", "failed"}:
        values["_clearProviderState"] = True
    if status == "polling" and status_label and not segment_values and provider_progress is None and not provider_status:
        values["_clearSegmentContext"] = True
    if provider_status:
        values["providerStatus"] = provider_status
    if status == "polling" and not clear_provider_state and provider_progress is not None:
        values["providerProgress"] = provider_progress
    if job.video_url:
        values["videoUrl"] = job.video_url
    if job.error:
        values["error"] = job.error
    _update_item(
        session_id,
        job.video_index,
        values,
    )


def mark_job_archiving(session_id: str | None, job: QuickVideoJob) -> None:
    segment_values = _segment_values_from_job(job)
    _update_item(
        session_id,
        job.video_index,
        {
            "status": "archiving",
            "statusLabel": _with_segment_label("后台处理中", segment_values.get("segmentLabel") if segment_values else ""),
            "jobId": job.job_id,
            "_clearProviderState": True,
            "_clearSegmentContext": not bool(segment_values),
            **segment_values,
        },
    )


def mark_job_reviewing(session_id: str | None, job: QuickVideoJob) -> None:
    _update_item(
        session_id,
        job.video_index,
        {
            "status": "archiving",
            "statusLabel": "正在审查成片并提炼下一条优化",
            "jobId": job.job_id,
            "_clearProviderState": True,
            "_clearSegmentContext": True,
        },
    )


def mark_job_html_motion_overlay(
    session_id: str | None,
    job: QuickVideoJob,
    *,
    stage: str,
    result: dict[str, Any] | None = None,
) -> None:
    segment_values = _segment_values_from_job(job)
    values: dict[str, Any] = {
        "status": "archiving",
        "statusLabel": _with_segment_label(
            _html_motion_overlay_label(stage, result),
            segment_values.get("segmentLabel") if segment_values else "",
        ),
        "jobId": job.job_id,
        **segment_values,
    }
    if result is not None:
        values["htmlMotionOverlay"] = result
    _update_item(session_id, job.video_index, values)


def mark_job_succeeded(session_id: str | None, job: QuickVideoJob, asset_record: dict[str, Any] | None = None) -> None:
    segment_values = _segment_values_from_job(job)
    status_label = _success_label_with_html_motion(asset_record)
    _update_item(
        session_id,
        job.video_index,
        {
            "status": "succeeded",
            "statusLabel": _with_segment_label(status_label, segment_values.get("segmentLabel") if segment_values else ""),
            "jobId": job.job_id,
            "assetRecord": asset_record or {},
            "videoUrl": job.video_url or None,
            "_clearProviderState": True,
            "_clearSegmentContext": not bool(segment_values),
            **segment_values,
        },
    )


def mark_job_failed(
    session_id: str | None,
    video_index: int,
    error: Exception | str,
    *,
    job_id: str | None = None,
    asset_record: dict[str, Any] | None = None,
) -> None:
    raw_error = str(error)
    previous_status = _current_item_status(session_id, video_index)
    previous_provider_status = _current_item_field(session_id, video_index, "providerStatus")
    first_frame_lost = (
        (previous_status == "preparing_first_frame" and _is_lost_first_frame_response(raw_error))
        or previous_provider_status == "first_frame_response_lost"
    )
    first_frame_failed = previous_status == "preparing_first_frame" or previous_provider_status in {
        "first_frame_failed",
        "first_frame_response_lost",
    }
    video_create_lost = (
        previous_status == "submitting"
        and not str(job_id or "").strip()
        and _is_lost_video_create_response(raw_error)
    )
    if video_create_lost:
        _update_item(
            session_id,
            video_index,
            {
                "status": "polling",
                "statusLabel": "已提交上游，等待任务号回填",
                "providerStatus": "video_create_response_lost",
                "providerProgress": 1,
                "error": _video_create_response_lost_message(),
                "rawError": raw_error,
            },
        )
        return
    local_postprocess_failed = previous_status == "archiving" or _is_local_video_postprocess_failure(raw_error)
    values: dict[str, Any] = {
        "status": "failed",
        "statusLabel": (
            "首帧图未回填"
            if first_frame_lost
            else "首帧图生成失败"
            if first_frame_failed
            else "本地后处理失败"
            if local_postprocess_failed
            else "生成失败"
        ),
        "error": _humanize_failed_progress_error(raw_error, first_frame_lost=first_frame_lost),
        "_clearProviderState": True,
        "_clearSegmentContext": previous_status == "archiving",
    }
    if first_frame_lost:
        values["providerStatus"] = "first_frame_response_lost"
        values["providerProgress"] = 100
        values["rawError"] = raw_error
    elif first_frame_failed:
        values["providerStatus"] = "first_frame_failed"
        values["providerProgress"] = 100
        values["rawError"] = raw_error
    existing_job_id = str(_current_item_field(session_id, video_index, "jobId") or "").strip()
    final_job_id = str(job_id or "").strip()
    if final_job_id and _is_local_failure_job_id(final_job_id) and existing_job_id and not _is_local_failure_job_id(existing_job_id):
        final_job_id = existing_job_id
    if final_job_id:
        values["jobId"] = final_job_id
    if asset_record:
        values["assetRecord"] = asset_record
    _update_item(session_id, video_index, values)


def fail_generation_progress(
    session_id: str | None,
    error: Exception | str | None = None,
    *,
    skip_pending: bool = True,
    pending_error: str | None = None,
) -> None:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return
    now = time.time()
    snapshot: dict[str, Any] | None = None
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id)
        if not progress:
            return
        if not _batch_context_matches(progress):
            return
        if progress.get("status") in {"cancelled", "canceled"}:
            return
        if skip_pending:
            for item in progress.get("items") or []:
                if item.get("status") == "pending_submission":
                    item["status"] = "skipped"
                    item["statusLabel"] = "已跳过"
                    item["error"] = pending_error or "前序视频提交失败，本批次已停止"
                    item["updatedAt"] = _isoformat(now)
        items = progress.get("items") or []
        has_unfinished = any(str(item.get("status") or "").strip() not in _TERMINAL_ITEM_STATUSES for item in items)
        has_failed = any(str(item.get("status") or "").strip() in {"failed", "skipped"} for item in items)
        has_succeeded = any(str(item.get("status") or "").strip() == "succeeded" for item in items)
        if has_unfinished:
            progress["status"] = "active"
            progress.pop("completedAt", None)
            progress.pop("error", None)
        elif has_failed:
            progress["status"] = "completed_with_error" if has_succeeded else "failed"
            progress["completedAt"] = _isoformat(now)
        else:
            progress["status"] = "completed"
            progress["completedAt"] = _isoformat(now)
        if error is not None:
            if progress["status"] == "active":
                progress.pop("error", None)
            else:
                progress["error"] = _humanize_failed_progress_error(
                    str(error),
                    first_frame_lost=_progress_has_first_frame_response_lost(progress),
                )
        progress["updatedAt"] = _isoformat(now)
        snapshot = _copy_progress(progress)
    _persist_progress_snapshot(snapshot)


def fail_unsubmitted_generation_progress(
    session_id: str | None,
    error: Exception | str,
) -> dict[str, Any] | None:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return None
    now = time.time()
    reason = _humanize_failed_progress_error(str(error))
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id)
        if not progress:
            return None
        if not _batch_context_matches(progress):
            return None
        if progress.get("status") in {"cancelled", "canceled"}:
            return None
        for item in progress.get("items") or []:
            if item.get("status") in _TERMINAL_ITEM_STATUSES:
                continue
            item["status"] = "failed"
            item["statusLabel"] = "生成失败"
            item["providerStatus"] = "local_timeout"
            item["providerProgress"] = 100
            item["error"] = reason
            item["updatedAt"] = _isoformat(now)
        progress["status"] = "failed"
        progress["error"] = reason
        progress["updatedAt"] = _isoformat(now)
        progress["completedAt"] = _isoformat(now)
        snapshot = {
            **progress,
            "items": [dict(item) for item in progress.get("items") or []],
        }
    result = _with_counts(snapshot)
    _persist_progress_snapshot(result)
    return result


def settle_stale_first_frame_progress(
    session_id: str | None,
    *,
    timeout_seconds: int = 240,
) -> dict[str, Any] | None:
    """Mark a stuck first-frame preprocessing batch as failed.

    This runs from polling/status paths too, so a dead background worker cannot
    leave the UI permanently stuck at "正在生成首帧图" before any video job exists.
    """
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return None
    now = time.time()
    snapshot: dict[str, Any] | None = None
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id)
        if not progress or progress.get("status") != "active":
            return None
        if not _batch_context_matches(progress):
            return None
        items = progress.get("items") or []
        if not items:
            return None
        if any(str(item.get("jobId") or "").strip() for item in items):
            return None
        statuses = {str(item.get("status") or "").strip() for item in items}
        if not statuses.issubset({"preparing_first_frame", "pending_submission"}):
            return None
        preparing_items = [item for item in items if str(item.get("status") or "").strip() == "preparing_first_frame"]
        if not preparing_items:
            return None
        total_requested = _coerce_positive_int(progress.get("totalRequested")) or len(items)
        timeout_seconds = max(90, int(timeout_seconds or 240), 180 + max(0, total_requested) * 60)
        reason = f"图生图阶段超过 {timeout_seconds} 秒没有任何视频任务提交。请检查图片模型或关闭参考图重绘后重试。"
        oldest_reference = min(
            (
                _parse_progress_timestamp(
                    item.get("firstFrameStartedAt") or item.get("updatedAt") or progress.get("startedAt")
                )
                for item in preparing_items
            ),
            default=None,
        )
        if oldest_reference is None or now - oldest_reference < timeout_seconds:
            return None
        for item in items:
            status = str(item.get("status") or "").strip()
            if status == "preparing_first_frame":
                item["status"] = "failed"
                item["statusLabel"] = "首帧图生图超时"
                item["providerStatus"] = "first_frame_timeout"
                item["providerProgress"] = 100
                item["error"] = reason
                item["updatedAt"] = _isoformat(now)
            elif status == "pending_submission":
                item["status"] = "skipped"
                item["statusLabel"] = "已跳过"
                item["error"] = "前序首帧图生图超时，本批次已停止"
                item["updatedAt"] = _isoformat(now)
        progress["status"] = "failed"
        progress["error"] = reason
        progress["completedAt"] = _isoformat(now)
        progress["updatedAt"] = _isoformat(now)
        snapshot = _copy_progress(progress)
    result = _with_counts(snapshot)
    _persist_progress_snapshot(result)
    return result


def _parse_progress_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def stop_unsubmitted_generation_progress(
    session_id: str | None,
    progress_snapshot: dict[str, Any] | None,
    error: Exception | str,
) -> dict[str, Any] | None:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return None
    now = time.time()
    reason = _humanize_failed_progress_error(str(error))
    source = progress_snapshot if isinstance(progress_snapshot, dict) else {}
    with _LOCK:
        existing = _PROGRESS.get(normalized_session_id)
        if existing and not _batch_context_matches(existing):
            return None
        generation_batch_id = _resolve_terminal_generation_batch_id(
            normalized_session_id,
            source,
            existing,
        )
        if existing and str(existing.get("status") or "").strip() in _TERMINAL_PROGRESS_STATUSES:
            existing["generationBatchId"] = generation_batch_id
            snapshot = _copy_progress(existing)
        else:
            progress = _build_unsubmitted_failure_progress(
                session_id=normalized_session_id,
                generation_batch_id=generation_batch_id,
                source=source,
                reason=reason,
                now=now,
            )
            _PROGRESS[normalized_session_id] = progress
            snapshot = _copy_progress(progress)
    result = _with_counts(snapshot)
    _persist_progress_snapshot(result)
    return result


def _resolve_terminal_generation_batch_id(
    session_id: str,
    source: dict[str, Any],
    existing: dict[str, Any] | None,
) -> str:
    candidates = (
        existing.get("generationBatchId") if isinstance(existing, dict) else None,
        source.get("generationBatchId"),
        get_current_generation_batch_id(),
    )
    for candidate in candidates:
        generation_batch_id = _normalize_generation_batch_id(candidate)
        if generation_batch_id:
            return generation_batch_id
    return create_generation_batch_id(session_id)


def _build_unsubmitted_failure_progress(
    *,
    session_id: str,
    generation_batch_id: str,
    source: dict[str, Any],
    reason: str,
    now: float,
) -> dict[str, Any]:
    source_items = source.get("items") if isinstance(source.get("items"), list) else []
    total_requested = _coerce_positive_int(source.get("totalRequested")) or len(source_items) or 1
    items = [
        _build_unsubmitted_failure_item(item, index, reason, now)
        for index, item in enumerate(source_items, 1)
    ]
    if not items:
        items = [
            _build_unsubmitted_failure_item({}, index, reason, now)
            for index in range(1, total_requested + 1)
        ]
    return {
        **source,
        "sessionId": session_id,
        "generationBatchId": generation_batch_id,
        "status": "failed",
        "items": items,
        "totalRequested": total_requested,
        "error": reason,
        "updatedAt": _isoformat(now),
        "completedAt": _isoformat(now),
    }


def _build_unsubmitted_failure_item(
    source_item: Any,
    fallback_video_index: int,
    reason: str,
    now: float,
) -> dict[str, Any]:
    item = dict(source_item) if isinstance(source_item, dict) else {}
    video_index = _coerce_positive_int(item.get("videoIndex")) or fallback_video_index
    return {
        **item,
        "videoIndex": video_index,
        "title": str(item.get("title") or f"视频 {video_index}"),
        "jobId": None,
        "status": "failed",
        "statusLabel": "生成失败",
        "providerStatus": "local_timeout",
        "providerProgress": 100,
        "error": reason,
        "updatedAt": _isoformat(now),
    }


def finish_generation_progress(session_id: str | None) -> None:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return
    now = time.time()
    snapshot: dict[str, Any] | None = None
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id)
        if not progress:
            return
        if not _batch_context_matches(progress):
            return
        if progress.get("status") in _TERMINAL_PROGRESS_STATUSES:
            snapshot = _copy_progress(progress)
        else:
            items = progress.get("items") or []
            has_unfinished_items = any(
                str(item.get("status") or "").strip() not in _TERMINAL_ITEM_STATUSES
                for item in items
            )
            if has_unfinished_items:
                progress["status"] = "active"
                progress["updatedAt"] = _isoformat(now)
                progress.pop("completedAt", None)
            else:
                has_failed = any(item.get("status") in {"failed", "skipped"} for item in items)
                has_succeeded = any(item.get("status") == "succeeded" for item in items)
                progress["status"] = (
                    "completed_with_error"
                    if has_failed and has_succeeded
                    else "failed" if has_failed else "completed"
                )
                progress["updatedAt"] = _isoformat(now)
                progress["completedAt"] = _isoformat(now)
            snapshot = _copy_progress(progress)
    _persist_progress_snapshot(snapshot)


def clear_generation_progress(session_id: str | None) -> None:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return
    with _LOCK:
        _PROGRESS.pop(normalized_session_id, None)
        _LATEST_BATCH_IDS.pop(normalized_session_id, None)


def cancel_generation_progress(
    session_id: str | None,
    reason: str = "用户强行终止，本地停止等待结果回填",
) -> dict[str, Any] | None:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return None
    now = time.time()
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id)
        if not progress:
            return None
        if not _batch_context_matches(progress):
            return None
        for item in progress.get("items") or []:
            if item.get("status") in {"succeeded", "failed", "skipped"}:
                continue
            item["status"] = "skipped"
            item["statusLabel"] = "已取消"
            item["error"] = reason
            item["updatedAt"] = _isoformat(now)
        progress["status"] = "cancelled"
        progress["cancelledAt"] = _isoformat(now)
        progress["completedAt"] = _isoformat(now)
        progress["updatedAt"] = _isoformat(now)
        progress["error"] = reason
        snapshot = {
            **progress,
            "items": [dict(item) for item in progress.get("items") or []],
        }
    result = _with_counts(snapshot)
    _persist_progress_snapshot(result)
    return result


def get_generation_progress(session_id: str | None) -> dict[str, Any] | None:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return None
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id)
        if not progress:
            return None
        snapshot = {
            **progress,
            "items": [dict(item) for item in progress.get("items") or []],
        }
    return _with_counts(snapshot)


def _copy_progress(progress: dict[str, Any]) -> dict[str, Any]:
    return {
        **progress,
        "items": [dict(item) for item in progress.get("items") or []],
        "events": [dict(event) for event in progress.get("events") or [] if isinstance(event, dict)],
    }


def _record_item_execution_event(
    progress: dict[str, Any],
    current_item: dict[str, Any],
    next_values: dict[str, Any],
    now: float,
) -> None:
    status = str(next_values.get("status") or current_item.get("status") or "").strip()
    label = str(next_values.get("statusLabel") or current_item.get("statusLabel") or "").strip()
    video_index = _coerce_positive_int(current_item.get("videoIndex"))
    segment_index = _coerce_positive_int(next_values.get("segmentIndex"))
    segment_label = str(next_values.get("segmentLabel") or "").strip()
    provider_progress = next_values.get("providerProgress") if status == "polling" else None
    previous = {
        "status": str(current_item.get("status") or "").strip(),
        "label": str(current_item.get("statusLabel") or "").strip(),
        "providerProgress": current_item.get("providerProgress") if status == "polling" else None,
        "segmentIndex": _coerce_positive_int(current_item.get("segmentIndex")),
    }
    current = {
        "status": status,
        "label": label,
        "providerProgress": provider_progress,
        "segmentIndex": segment_index,
    }
    if not label or previous == current:
        return
    events = progress.get("events") if isinstance(progress.get("events"), list) else []
    public_message = _public_execution_message(
        status,
        provider_progress,
        _without_segment_label(label, segment_label),
    )
    event_kind = _execution_event_kind(status, provider_progress, public_message)
    event = {
        "at": _isoformat(now),
        "kind": event_kind,
        "title": "后台任务",
        "status": status,
        "message": public_message,
        **({"videoIndex": video_index} if video_index else {}),
        **({"segmentIndex": segment_index} if segment_index else {}),
        **({"segmentLabel": segment_label} if segment_label else {}),
        **({"providerProgress": provider_progress} if provider_progress is not None else {}),
    }
    if status:
        event_key = (video_index, segment_index, status, event_kind)
        for index in range(len(events) - 1, -1, -1):
            previous_event = events[index]
            previous_key = (
                _coerce_positive_int(previous_event.get("videoIndex")),
                _coerce_positive_int(previous_event.get("segmentIndex")),
                str(previous_event.get("status") or "").strip(),
                str(previous_event.get("kind") or "").strip(),
            )
            if previous_key == event_key:
                events[index] = event
                progress["events"] = events[-_MAX_EXECUTION_EVENTS:]
                return
    events.append(event)
    progress["events"] = events[-_MAX_EXECUTION_EVENTS:]


def _execution_event_kind(status: str, provider_progress: Any, message: str) -> str:
    normalized_status = str(status or "").strip().lower()
    if normalized_status == "polling" and provider_progress is not None:
        return "provider_progress"
    if normalized_status in {"polling", "archiving"}:
        return f"{normalized_status}:{str(message or '').strip()}"
    return normalized_status or "status_update"


def _public_execution_message(status: str, provider_progress: Any, status_label: str = "") -> str:
    normalized_status = str(status or "").strip().lower()
    public_label = str(status_label or "").strip()
    if normalized_status == "preparing_first_frame":
        return "正在准备首帧图"
    if normalized_status == "submitting":
        return "正在提交生成任务"
    if normalized_status == "submitted":
        return "生成任务已提交"
    if normalized_status == "polling":
        if provider_progress is not None:
            return "视频生成中"
        if public_label:
            return public_label
        return "正在等待视频生成结果"
    if normalized_status == "archiving":
        return public_label or "正在整理生成结果"
    if normalized_status == "succeeded":
        return "视频已生成"
    if normalized_status in {"failed", "skipped", "cancelled", "canceled"}:
        return "生成未完成"
    return "后台任务状态已更新"


def _persist_progress_snapshot(progress_snapshot: dict[str, Any] | None) -> None:
    if not isinstance(progress_snapshot, dict):
        return
    session_id = str(progress_snapshot.get("sessionId") or "").strip()
    generation_batch_id = str(progress_snapshot.get("generationBatchId") or "").strip()
    status = str(progress_snapshot.get("status") or "").strip()
    if not session_id or not generation_batch_id or not status:
        return
    try:
        _TASK_LEDGER.upsert_generation_batch(
            session_id=session_id,
            generation_batch_id=generation_batch_id,
            status=status,
            phase=str(progress_snapshot.get("phase") or "").strip() or None,
            progress=_with_counts(progress_snapshot),
        )
    except Exception as exc:
        _mark_task_ledger_error(session_id, exc)


def _mark_task_ledger_error(session_id: str, error: Exception) -> None:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id)
        if not progress:
            return
        progress["taskLedgerError"] = str(error)


def is_generation_cancelled(session_id: str | None) -> bool:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return False
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id)
        return bool(progress and progress.get("status") in {"cancelled", "canceled"})


def is_generation_stopped(session_id: str | None) -> bool:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return False
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id)
        return bool(progress and str(progress.get("status") or "").strip() in _TERMINAL_PROGRESS_STATUSES)


def generation_stop_reason(session_id: str | None) -> str:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return "本轮任务已停止"
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id) or {}
        status = str(progress.get("status") or "").strip()
        error = str(progress.get("error") or "").strip()
    if status in {"cancelled", "canceled"}:
        return error or "用户强行终止，本地停止等待结果回填"
    if status:
        return error or "本轮任务已结束，后台停止继续生成"
    return "本轮任务已停止"


def claim_active_generation_jobs(
    session_id: str | None,
    *,
    min_interval_seconds: float = 3.0,
) -> list[dict[str, Any]]:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return []
    now = time.time()
    claimed: list[dict[str, Any]] = []
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id)
        if not progress:
            return []
        if not _batch_context_matches(progress):
            return []
        for item in progress.get("items") or []:
            if item.get("status") not in {"submitted", "polling", "archiving"}:
                continue
            job_id = str(item.get("jobId") or "").strip()
            if not job_id:
                continue
            last_refresh = float(item.get("_lastProviderRefreshAt") or 0)
            if now - last_refresh < min_interval_seconds:
                continue
            item["_lastProviderRefreshAt"] = now
            claimed.append(dict(item))
    return claimed


def _update_item(session_id: str | None, video_index: int, values: dict[str, Any]) -> None:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return
    now = time.time()
    snapshot: dict[str, Any] | None = None
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id)
        if not progress:
            return
        context_batch_id = _normalize_generation_batch_id(get_current_generation_batch_id())
        progress_batch_id = _normalize_generation_batch_id(progress.get("generationBatchId"))
        latest_batch_id = _LATEST_BATCH_IDS.get(normalized_session_id)
        if context_batch_id and (
            context_batch_id != progress_batch_id
            or (latest_batch_id and latest_batch_id != context_batch_id)
        ):
            return
        if progress.get("status") in _TERMINAL_PROGRESS_STATUSES:
            return
        items = progress.get("items") or []
        target = None
        for item in items:
            if int(item.get("videoIndex") or 0) == int(video_index):
                target = item
                break
        if target is None:
            target = {
                "videoIndex": int(video_index),
                "title": f"视频 {video_index}",
            }
            items.append(target)
            progress["items"] = items
            progress["totalRequested"] = max(int(progress.get("totalRequested") or 0), len(items))
        next_values = dict(values)
        clear_provider_state = bool(next_values.pop("_clearProviderState", False))
        clear_segment_context = bool(next_values.pop("_clearSegmentContext", False))
        next_segment_index = _coerce_positive_int(next_values.get("segmentIndex"))
        current_segment_index = _coerce_positive_int(target.get("segmentIndex"))
        segment_changed = bool(
            next_segment_index
            and current_segment_index
            and next_segment_index != current_segment_index
        )
        next_status = str(next_values.get("status") or "").strip()
        if next_status and next_status != "polling" and "providerProgress" not in next_values:
            clear_provider_state = True
        if segment_changed:
            for field in (
                "providerProgress",
                "providerStatus",
                "hasAcceptedProgressRollback",
                "jobId",
                "videoUrl",
                "error",
                "rawError",
            ):
                target.pop(field, None)
        if clear_provider_state:
            for field in ("providerProgress", "providerStatus", "hasAcceptedProgressRollback"):
                target.pop(field, None)
        if clear_segment_context:
            target.pop("segmentIndex", None)
            target.pop("segmentLabel", None)
        if "providerProgress" in next_values:
            resolved_progress, accepted_rollback = _resolve_provider_progress(
                target.get("providerProgress"),
                next_values.get("providerProgress"),
                bool(target.get("hasAcceptedProgressRollback")),
            )
            if resolved_progress is None:
                next_values.pop("providerProgress", None)
            else:
                next_values["providerProgress"] = resolved_progress
                if "真实生成进度 " in str(next_values.get("statusLabel") or ""):
                    next_values["statusLabel"] = _with_segment_label(
                        f"真实生成进度 {resolved_progress}%",
                        next_values.get("segmentLabel"),
                    )
            if accepted_rollback:
                next_values["hasAcceptedProgressRollback"] = True
        _update_segment_status(
            target,
            next_values,
            now,
            clear_provider_state=clear_provider_state,
        )
        _record_item_execution_event(progress, target, next_values, now)
        target.update(next_values)
        target["updatedAt"] = _isoformat(now)
        progress["updatedAt"] = _isoformat(now)
        snapshot = _copy_progress(progress)
    _persist_progress_snapshot(snapshot)


def _current_item_status(session_id: str | None, video_index: int) -> str:
    return str(_current_item_field(session_id, video_index, "status") or "").strip()


def _current_item_field(session_id: str | None, video_index: int, field: str) -> Any:
    normalized_session_id = _normalize_session_id(session_id)
    if not normalized_session_id:
        return None
    with _LOCK:
        progress = _PROGRESS.get(normalized_session_id) or {}
        for item in progress.get("items") or []:
            if int(item.get("videoIndex") or 0) == int(video_index):
                return item.get(field)
    return None


def _segment_values_from_video(video: VideoPrompt) -> dict[str, Any]:
    guidance = video.keyword_guidance if isinstance(video.keyword_guidance, dict) else {}
    segment_index = _coerce_positive_int(guidance.get("segmentIndex"))
    if not segment_index:
        return {}
    return {
        "segmentIndex": segment_index,
        "segmentLabel": f"片段 {segment_index}",
    }


def _segment_values_from_job(job: QuickVideoJob) -> dict[str, Any]:
    segment_index = _coerce_positive_int(getattr(job, "segment_index", None))
    segment_label = str(getattr(job, "segment_label", "") or "").strip()
    if not segment_index and segment_label:
        match = re.search(r"(\d+)", segment_label)
        if match:
            segment_index = _coerce_positive_int(match.group(1))
    if not segment_index:
        return {}
    return {
        "segmentIndex": segment_index,
        "segmentLabel": segment_label or f"片段 {segment_index}",
    }


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _with_segment_label(label: str, segment_label: str | None) -> str:
    clean_label = str(label or "").strip()
    clean_segment = str(segment_label or "").strip()
    if not clean_segment:
        return clean_label
    if clean_label.startswith(clean_segment):
        return clean_label
    return f"{clean_segment}：{clean_label}" if clean_label else clean_segment


def _without_segment_label(label: str, segment_label: str | None) -> str:
    clean_label = str(label or "").strip()
    clean_segment = str(segment_label or "").strip()
    if not clean_segment or not clean_label.startswith(clean_segment):
        return clean_label
    return clean_label[len(clean_segment):].lstrip("：: ")


def _html_motion_overlay_label(stage: str, result: dict[str, Any] | None) -> str:
    if stage == "generating":
        return "正在生成 HTML 动效"
    if stage == "compositing":
        return "正在叠加 HTML 动效"
    status = str((result or {}).get("status") or "").strip().lower()
    if status == "applied":
        return "HTML 动效已叠加"
    if status == "degraded":
        return "基础视频保留，HTML 动效未叠加"
    return "HTML 动效未开启"


def _success_label_with_html_motion(asset_record: dict[str, Any] | None) -> str:
    record = asset_record or {}
    overlay = record.get("htmlMotionOverlay")
    if not isinstance(overlay, dict):
        overlay = (record.get("archiveMeta") or {}).get("htmlMotionOverlay")
    status = str((overlay or {}).get("status") or "").strip().lower()
    if status == "applied":
        return "已生成（HTML 动效已叠加）"
    if status == "degraded":
        return "基础视频保留，HTML 动效未叠加"
    return "已生成"


def _update_segment_status(
    target: dict[str, Any],
    next_values: dict[str, Any],
    now: float,
    *,
    clear_provider_state: bool = False,
) -> None:
    segment_index = _coerce_positive_int(next_values.get("segmentIndex"))
    if not segment_index:
        return
    segment_label = str(next_values.get("segmentLabel") or f"片段 {segment_index}").strip()
    segments = target.get("segmentStatus")
    if not isinstance(segments, list):
        segments = []
    current: dict[str, Any] | None = None
    for segment in segments:
        if isinstance(segment, dict) and _coerce_positive_int(segment.get("segmentIndex")) == segment_index:
            current = segment
            break
    if current is None:
        current = {
            "segmentIndex": segment_index,
            "segmentLabel": segment_label,
        }
        segments.append(current)
        segments.sort(key=lambda item: _coerce_positive_int(item.get("segmentIndex")) or 0)
    if clear_provider_state:
        current.pop("providerProgress", None)
        current.pop("providerStatus", None)
    current.update({
        "segmentIndex": segment_index,
        "segmentLabel": segment_label,
        "status": next_values.get("status") or current.get("status"),
        "statusLabel": next_values.get("statusLabel") or current.get("statusLabel"),
        "jobId": next_values.get("jobId") or current.get("jobId"),
        "videoUrl": next_values.get("videoUrl") or current.get("videoUrl"),
        "error": next_values.get("error") or current.get("error"),
        "updatedAt": _isoformat(now),
    })
    if next_values.get("providerStatus"):
        current["providerStatus"] = next_values["providerStatus"]
    if next_values.get("providerProgress") is not None:
        current["providerProgress"] = next_values["providerProgress"]
    target["segmentStatus"] = segments


def _with_counts(progress: dict[str, Any]) -> dict[str, Any]:
    items = progress.get("items") or []
    running_statuses = {"submitting", "preparing_first_frame", "submitted", "polling", "archiving"}
    progress["submittedCount"] = sum(1 for item in items if _has_video_submission(item))
    progress["runningCount"] = sum(1 for item in items if item.get("status") in running_statuses)
    progress["postProcessingCount"] = sum(1 for item in items if item.get("status") == "archiving")
    progress["waitingCount"] = sum(1 for item in items if item.get("status") == "pending_submission")
    progress["succeededCount"] = sum(1 for item in items if item.get("status") == "succeeded")
    progress["failedCount"] = sum(1 for item in items if item.get("status") == "failed")
    progress["skippedCount"] = sum(1 for item in items if item.get("status") == "skipped")
    progress["totalRequested"] = int(progress.get("totalRequested") or len(items) or 0)
    return progress


def _progress_has_first_frame_response_lost(progress: dict[str, Any]) -> bool:
    return any(
        str(item.get("providerStatus") or "").strip() == "first_frame_response_lost"
        for item in progress.get("items") or []
        if isinstance(item, dict)
    )


def _has_video_submission(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").strip()
    if status not in {"submitted", "polling", "archiving", "succeeded", "failed", "deleted"}:
        return False
    provider_status = str(item.get("providerStatus") or "").strip()
    if provider_status in {"first_frame_failed", "first_frame_response_lost", "local_interrupted", "local_timeout"}:
        return False
    job_id = str(item.get("jobId") or "").strip()
    if _is_local_failure_job_id(job_id):
        return False
    return True


def _is_local_failure_job_id(job_id: str) -> bool:
    return str(job_id or "").strip().startswith((
        "create-failed-",
        "first-frame-failed-",
        "interrupted-before-submit-",
        "merge2-failed-",
    ))


def _is_local_video_postprocess_failure(error: str) -> bool:
    text = str(error or "").strip()
    lowered = text.lower()
    return any(marker in text for marker in (
        "视频开头裁剪失败",
        "归档或后处理失败",
        "视频后处理失败",
        "花字烧录失败",
        "HTML 动效",
        "提取尾帧失败",
        "保存延长截帧失败",
        "截取视频失败",
        "合并视频失败",
        "重新混入背景音乐失败",
    )) or any(marker in lowered for marker in (
        "ffmpeg",
        "_mix_video",
        "text overlay",
    ))


def _is_lost_first_frame_response(error: str) -> bool:
    lowered = str(error or "").lower()
    return any(marker in lowered for marker in (
        "remotedisconnected",
        "remote end closed connection",
        "gateway timeout",
        "read timed out",
        "ssl: unexpected_eof_while_reading",
    ))


def _is_lost_video_create_response(error: str) -> bool:
    lowered = str(error or "").lower()
    return any(marker in lowered for marker in (
        "创建视频任务超时",
        "可能已经接收请求",
        "后台生成",
        "read timeout",
        "read timed out",
        "remotedisconnected",
        "remote end closed connection",
        "connection aborted",
        "gateway timeout",
    ))


def _video_create_response_lost_message() -> str:
    return (
        "创建视频任务的响应没有回填到本地，但请求已经发给上游。"
        "上游后台可能仍在生成；请以上游后台任务状态为准，不要立刻重复提交。"
    )


def _humanize_failed_progress_error(error: str, *, first_frame_lost: bool = False) -> str:
    text = str(error or "").strip()
    if first_frame_lost:
        return (
            "首帧图接口在等待图生图结果时断开，本地没有拿到图片 URL。"
            "这类长同步图片请求上游后台可能仍会完成并扣费，但本地没有可轮询的图片任务号，"
            "不会用原图冒充成功；请改用更快的图片模型或关闭参考图图生图后再生成。"
        )
    lowered = text.lower()
    if "invalid image base64" in lowered:
        return "参考图图片数据无效，图生图失败；视频任务没有提交。请重新选择或上传有效图片后再试。"
    return text


def _normalize_session_id(session_id: str | None) -> str:
    return str(session_id or "").strip()


def _isoformat(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(timestamp))


def _normalize_provider_progress(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        text = value.strip().rstrip("%")
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if not (number == number) or number in (float("inf"), float("-inf")):
        return None
    rounded = int(number)
    if rounded < 1:
        return None
    return min(99, rounded)


def _resolve_provider_progress(
    previous_value: Any,
    next_value: Any,
    has_accepted_rollback: bool = False,
) -> tuple[int | None, bool]:
    previous_progress = _normalize_provider_progress(previous_value)
    next_progress = _normalize_provider_progress(next_value)
    if next_progress is None:
        return previous_progress, has_accepted_rollback
    if previous_progress is not None and next_progress < previous_progress:
        is_significant_rollback = next_progress + 8 < previous_progress
        if not has_accepted_rollback and is_significant_rollback:
            return next_progress, True
        return previous_progress, has_accepted_rollback or is_significant_rollback
    return next_progress, has_accepted_rollback
