from __future__ import annotations

import math
from typing import Any


TIMELINE_BOUNDARY_TOLERANCE_SECONDS = 0.08


def timeline_boundary_status(
    video_duration_seconds: float | int | None,
    *,
    tts_chunks: Any = None,
    html_motion_chunks: Any = None,
) -> dict[str, Any]:
    duration = _non_negative_number(video_duration_seconds, "视频时间轴时长不合法")
    if duration <= 0:
        return _status_payload(duration, [], [])
    tts_overflow = _overflow_indexes(tts_chunks, duration, "配音")
    html_overflow = _overflow_indexes(html_motion_chunks, duration, "动效")
    return _status_payload(duration, tts_overflow, html_overflow)


def ensure_timeline_chunks_within_video(
    video_duration_seconds: float | int | None,
    *,
    tts_chunks: Any = None,
    html_motion_chunks: Any = None,
) -> dict[str, Any]:
    status = timeline_boundary_status(
        video_duration_seconds,
        tts_chunks=tts_chunks,
        html_motion_chunks=html_motion_chunks,
    )
    if status["videoDurationSeconds"] <= 0:
        raise ValueError("裁剪后视频时长不合法")
    if status["valid"] is not True:
        raise ValueError(str(status["reason"]))
    return status


def _overflow_indexes(chunks: Any, duration: float, label: str) -> list[int]:
    if chunks is None:
        return []
    if not isinstance(chunks, list):
        raise ValueError(f"{label}时间轴数据不合法")
    overflow = []
    for position, item in enumerate(chunks):
        if not isinstance(item, dict):
            raise ValueError(f"{label}片段数据不合法")
        end = _chunk_end_seconds(item, label)
        if end > duration + TIMELINE_BOUNDARY_TOLERANCE_SECONDS:
            overflow.append(_chunk_index(item, position))
    return overflow


def _chunk_end_seconds(item: dict[str, Any], label: str) -> float:
    start = _non_negative_number(item.get("startSeconds"), f"{label}片段起点不合法")
    end_value = item.get("endSeconds")
    if end_value is None:
        duration = _non_negative_number(item.get("durationSeconds"), f"{label}片段时长不合法")
        end_value = start + duration
    end = _non_negative_number(end_value, f"{label}片段终点不合法")
    if end + TIMELINE_BOUNDARY_TOLERANCE_SECONDS < start:
        raise ValueError(f"{label}片段终点早于起点")
    return end


def _chunk_index(item: dict[str, Any], position: int) -> int:
    try:
        return int(item.get("index", position))
    except (TypeError, ValueError):
        return position


def _status_payload(duration: float, tts_overflow: list[int], html_overflow: list[int]) -> dict[str, Any]:
    parts = []
    if tts_overflow:
        parts.append(f"{len(tts_overflow)} 个配音片段")
    if html_overflow:
        parts.append(f"{len(html_overflow)} 个动效片段")
    valid = not parts
    reason = ""
    if not valid:
        reason = f"{'、'.join(parts)}超出裁剪后视频的 {duration:.1f} 秒结尾，请删除、裁短或拖回有效范围"
    return {
        "valid": valid,
        "videoDurationSeconds": round(duration, 3),
        "ttsOverflowIndexes": tts_overflow,
        "htmlMotionOverflowIndexes": html_overflow,
        "reason": reason,
    }


def _non_negative_number(value: Any, message: str) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(message) from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(message)
    return number
