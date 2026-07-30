from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Any

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
) -> RecoveredTailFrameResume | None:
    items = sorted(
        [dict(item) for item in progress.get("items") or [] if isinstance(item, dict)],
        key=lambda item: int(item.get("videoIndex") or 0),
    )
    completed_indexes = _completed_video_indexes(asset_records, session_id)
    first_unfinished = next(
        (item for item in items if int(item.get("videoIndex") or 0) not in completed_indexes),
        None,
    )
    if first_unfinished is None:
        return None
    next_index = int(first_unfinished.get("videoIndex") or 0)
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
    checkpoint.refresh()
    with _LOCK:
        _CHECKPOINTS[_key(session_id, source_batch_id, next_index)] = checkpoint
    return checkpoint


def get_recovered_tail_frame_resume(
    session_id: str, source_batch_id: str, video_index: int
) -> RecoveredTailFrameResume:
    with _LOCK:
        checkpoint = _CHECKPOINTS.get(_key(session_id, source_batch_id, video_index))
    if checkpoint is None:
        raise LookupError("当前历史任务没有可继续的尾帧检查点")
    return checkpoint


def take_recovered_tail_frame_resume(
    session_id: str, source_batch_id: str, video_index: int
) -> RecoveredTailFrameResume:
    with _LOCK:
        checkpoint = _CHECKPOINTS.pop(_key(session_id, source_batch_id, video_index), None)
    if checkpoint is None:
        raise LookupError("当前历史任务没有可继续的尾帧检查点")
    return checkpoint


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
    matches = [
        record for record in records
        if str(record.get("sessionId") or "") == session_id
        and int(record.get("videoIndex") or 0) == video_index
        and str(record.get("generationStatus") or "") == "generated"
        and Path(str(record.get("archiveLocalPath") or "")).is_file()
    ]
    return matches[-1] if matches else None


def _completed_video_indexes(records: list[dict[str, Any]], session_id: str) -> set[int]:
    return {
        int(record.get("videoIndex") or 0)
        for record in records
        if str(record.get("sessionId") or "") == session_id
        and str(record.get("generationStatus") or "") == "generated"
        and Path(str(record.get("archiveLocalPath") or "")).is_file()
    }


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
