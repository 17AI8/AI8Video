from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Any

from ai8video.assets.user_generated_results import USER_GENERATED_RESULT_ROOT
from ai8video.core.models import ParsedRequest, VideoPrompt
from ai8video.generation.manual_tail_frame_gate import MANUAL_TAIL_FRAME_DIR
from ai8video.media.video_segment_postprocess import extract_tail_frame


@dataclass
class RecoveredTailFrameResume:
    session_id: str
    source_batch_id: str
    next_video_index: int
    request: ParsedRequest
    videos: list[VideoPrompt]
    predecessor_path: Path
    tail_frame_path: Path

    def refresh(self, predecessor_path: Path | None = None) -> str:
        if predecessor_path is not None:
            self.predecessor_path = predecessor_path
        extract_tail_frame(self.predecessor_path, self.tail_frame_path)
        self.request = replace(self.request, reference_image=str(self.tail_frame_path))
        return self.preview_url()

    def preview_url(self) -> str:
        version = self.tail_frame_path.stat().st_mtime_ns if self.tail_frame_path.is_file() else 0
        return f"/tail-frame-previews/{self.source_batch_id}/{self.tail_frame_path.name}?v={version}"


_CHECKPOINTS: dict[tuple[str, str, int], RecoveredTailFrameResume] = {}
_LOCK = Lock()


def prepare_recovered_tail_frame_resume(
    *,
    session_id: str,
    source_batch_id: str,
    progress: dict[str, Any],
    asset_records: list[dict[str, Any]],
    reuse_existing_tail_frame: bool = False,
) -> RecoveredTailFrameResume | None:
    items = sorted(
        [dict(item) for item in progress.get("items") or [] if isinstance(item, dict)],
        key=lambda item: int(item.get("videoIndex") or 0),
    )
    completed_indexes = _completed_video_indexes(asset_records, session_id)
    explicit_waiting_item = next(
        (
            item
            for item in items
            if str(item.get("status") or "").strip() == "awaiting_tail_frame_continue"
        ),
        None,
    )
    first_unfinished = explicit_waiting_item or next(
        (item for item in items if int(item.get("videoIndex") or 0) not in completed_indexes),
        None,
    )
    if first_unfinished is None:
        return None
    active_job_statuses = {
        "submitting", "submitted", "polling", "archiving", "preparing_first_frame",
    }
    if (
        str(first_unfinished.get("jobId") or "").strip()
        and str(first_unfinished.get("status") or "").strip() in active_job_statuses
    ):
        return None
    next_index = int(first_unfinished.get("videoIndex") or 0)
    completed_indexes.discard(next_index)
    if next_index <= 1 or any(
        int(item.get("videoIndex") or 0) not in completed_indexes
        for item in items
        if int(item.get("videoIndex") or 0) < next_index
    ):
        return None
    predecessor = _latest_predecessor_record(asset_records, session_id, next_index - 1)
    if predecessor is None:
        return None
    remaining = _remaining_videos(items, next_index)
    if not remaining:
        return None
    request = _request_from_record(predecessor, len(remaining))
    output_dir = MANUAL_TAIL_FRAME_DIR / source_batch_id
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = RecoveredTailFrameResume(
        session_id=session_id,
        source_batch_id=source_batch_id,
        next_video_index=next_index,
        request=request,
        videos=remaining,
        predecessor_path=Path(str(predecessor.get("archiveLocalPath"))),
        tail_frame_path=output_dir / f"video-{next_index}-recovered-reference.png",
    )
    if reuse_existing_tail_frame and checkpoint.tail_frame_path.is_file():
        checkpoint.request = replace(checkpoint.request, reference_image=str(checkpoint.tail_frame_path))
    else:
        checkpoint.refresh()
    with _LOCK:
        _CHECKPOINTS[_key(session_id, source_batch_id, next_index)] = checkpoint
    return checkpoint


def prepare_rollback_tail_frame_resume(
    *,
    session_id: str,
    source_batch_id: str,
    video_index: int,
    progress: dict[str, Any],
    asset_records: list[dict[str, Any]],
    target_record: dict[str, Any],
) -> RecoveredTailFrameResume:
    items = sorted(
        [dict(item) for item in progress.get("items") or [] if isinstance(item, dict)],
        key=lambda item: int(item.get("videoIndex") or 0),
    )
    remaining = _remaining_videos(items, video_index)
    predecessor = _latest_predecessor_record(asset_records, session_id, video_index - 1)
    if predecessor is None or not remaining:
        raise LookupError("未找到回退所需的上一条视频或后续提示词")
    output_dir = MANUAL_TAIL_FRAME_DIR / source_batch_id
    output_dir.mkdir(parents=True, exist_ok=True)
    target_first_frame = target_record.get("firstFrame") if isinstance(target_record.get("firstFrame"), dict) else {}
    candidates = [
        Path(str(target_first_frame.get("source") or "")),
        output_dir / f"video-{video_index}-recovered-reference.png",
        output_dir / f"video-{video_index}-reference.png",
    ]
    existing_reference = next((path for path in candidates if str(path) and path.is_file()), None)
    tail_frame_path = output_dir / f"video-{video_index}-recovered-reference.png"
    if existing_reference is not None and existing_reference != tail_frame_path:
        tail_frame_path.write_bytes(existing_reference.read_bytes())
    checkpoint = RecoveredTailFrameResume(
        session_id=session_id,
        source_batch_id=source_batch_id,
        next_video_index=video_index,
        request=_request_from_record(predecessor, len(remaining)),
        videos=remaining,
        predecessor_path=Path(str(predecessor.get("archiveLocalPath"))),
        tail_frame_path=tail_frame_path,
    )
    if checkpoint.tail_frame_path.is_file():
        checkpoint.request = replace(checkpoint.request, reference_image=str(checkpoint.tail_frame_path))
    else:
        checkpoint.refresh()
    with _LOCK:
        _CHECKPOINTS[_key(session_id, source_batch_id, video_index)] = checkpoint
    return checkpoint


def get_recovered_tail_frame_resume(
    session_id: str, source_batch_id: str, video_index: int
) -> RecoveredTailFrameResume:
    with _LOCK:
        checkpoint = _CHECKPOINTS.get(_key(session_id, source_batch_id, video_index))
        if checkpoint is None:
            checkpoint = _find_unique_checkpoint(session_id, video_index)
    if checkpoint is None:
        raise LookupError("当前历史任务没有可继续的尾帧检查点")
    return checkpoint


def take_recovered_tail_frame_resume(
    session_id: str, source_batch_id: str, video_index: int
) -> RecoveredTailFrameResume:
    with _LOCK:
        checkpoint = _CHECKPOINTS.pop(_key(session_id, source_batch_id, video_index), None)
        if checkpoint is None:
            checkpoint = _find_unique_checkpoint(session_id, video_index)
            if checkpoint is not None:
                _CHECKPOINTS.pop(
                    _key(session_id, checkpoint.source_batch_id, video_index),
                    None,
                )
    if checkpoint is None:
        raise LookupError("当前历史任务没有可继续的尾帧检查点")
    return checkpoint


def _find_unique_checkpoint(
    session_id: str,
    video_index: int,
) -> RecoveredTailFrameResume | None:
    normalized_session_id = str(session_id).strip()
    normalized_video_index = int(video_index)
    matches = [
        checkpoint
        for key, checkpoint in _CHECKPOINTS.items()
        if key[0] == normalized_session_id and key[2] == normalized_video_index
    ]
    return matches[0] if len(matches) == 1 else None


def refresh_recovered_tail_frame_resume(
    session_id: str,
    source_batch_id: str,
    video_index: int,
    asset_records: list[dict[str, Any]],
) -> dict[str, Any]:
    checkpoint = get_recovered_tail_frame_resume(session_id, source_batch_id, video_index)
    predecessor = _latest_predecessor_record(asset_records, session_id, video_index - 1)
    if predecessor is None:
        raise FileNotFoundError("未找到上一条视频的最新本地成片")
    preview_url = checkpoint.refresh(Path(str(predecessor.get("archiveLocalPath"))))
    return {
        "ok": True,
        "sessionId": session_id,
        "generationBatchId": source_batch_id,
        "videoIndex": video_index,
        "tailFramePreviewUrl": preview_url,
        "recoveredResume": True,
    }


def update_recovered_tail_frame_prompt(
    session_id: str, source_batch_id: str, video_index: int, prompt: str
) -> bool:
    with _LOCK:
        checkpoints = [
            checkpoint for key, checkpoint in _CHECKPOINTS.items()
            if key[0] == str(session_id).strip() and key[1] == str(source_batch_id).strip()
        ]
    updated = False
    for checkpoint in checkpoints:
        for video in checkpoint.videos:
            if int(video.index) == int(video_index):
                video.prompt = str(prompt)
                updated = True
    return updated


def _remaining_videos(items: list[dict[str, Any]], next_index: int) -> list[VideoPrompt]:
    videos = []
    for item in items:
        index = int(item.get("videoIndex") or 0)
        prompt = str(item.get("videoPrompt") or "").strip()
        if index >= next_index and prompt:
            videos.append(
                VideoPrompt(
                    index=index,
                    title=str(item.get("title") or f"视频 {index}"),
                    prompt=prompt,
                )
            )
    return videos


def _latest_predecessor_record(
    records: list[dict[str, Any]], session_id: str, video_index: int
) -> dict | None:
    matches = []
    for record in records:
        archive_path = _resolve_migrated_archive_path(record)
        if (
            str(record.get("sessionId") or "") == session_id
            and int(record.get("videoIndex") or 0) == video_index
            and str(record.get("generationStatus") or "") == "generated"
            and archive_path is not None
        ):
            matches.append({**record, "archiveLocalPath": str(archive_path)})
    return matches[-1] if matches else None


def _completed_video_indexes(records: list[dict[str, Any]], session_id: str) -> set[int]:
    return {
        int(record.get("videoIndex") or 0)
        for record in records
        if str(record.get("sessionId") or "") == session_id
        and str(record.get("generationStatus") or "") == "generated"
        and _resolve_migrated_archive_path(record) is not None
    }


def _resolve_migrated_archive_path(record: dict[str, Any]) -> Path | None:
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


def _request_from_record(record: dict[str, Any], video_count: int) -> ParsedRequest:
    settings = record.get("request") if isinstance(record.get("request"), dict) else {}
    return ParsedRequest(
        raw_text="恢复被中断的传尾帧任务",
        mode="batch_videos" if video_count > 1 else "single_video",
        video_count=video_count,
        duration_seconds=int(settings.get("durationSeconds") or 10),
        ratio=str(settings.get("ratio") or "9:16"),
        resolution=str(settings.get("resolution") or "480p"),
        preset=str(settings.get("preset") or "custom"),
        tail_frame_chaining=True,
        tail_frame_chaining_mode="manual",
        html_motion_overlay_enabled=bool(settings.get("htmlMotionOverlayEnabled")),
        smart_split_reason="恢复中断任务，复用已定稿视频提示词",
    )


def _key(session_id: str, source_batch_id: str, video_index: int) -> tuple[str, str, int]:
    return str(session_id).strip(), str(source_batch_id).strip(), int(video_index)
