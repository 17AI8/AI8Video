from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root
from ai8video.media.ffmpeg_utils import probe_media_duration_seconds, resolve_ffmpeg_bin
from ai8video.media.timeline_contract import (
    TIMELINE_SCHEMA_VERSION,
    ensure_expected_revision,
    next_timeline_revision,
    normalize_restore_bounds,
    timeline_review_lock,
)
from ai8video.media.tts_waveform import cached_audio_waveform


TTS_TIMELINE_REVIEW_ROOT = (USER_FILE_ROOT / "TTS" / "reviews").resolve()
MAX_TTS_TIMELINE_CHUNKS = 64
MIN_TTS_TIMELINE_CHUNK_SECONDS = 0.12
TTS_TIMELINE_TOLERANCE_SECONDS = 0.08


def tts_timeline_review_status(
    video_path: Path,
    relative_key: str,
    *,
    audio_path: Path | None = None,
    persisted_chunks: Any = None,
    tts_volume: float = 1.0,
) -> dict[str, Any]:
    state = _load_review(relative_key)
    if _stored_state_is_valid(state, relative_key):
        return _public_review(state, pending=state.get("pending") is True)
    if audio_path is None or not audio_path.is_file():
        return _empty_review(relative_key)
    video_duration, audio_duration = _media_durations(video_path, audio_path)
    chunks = normalize_tts_timeline_chunks(
        persisted_chunks,
        audio_duration_seconds=audio_duration,
        video_duration_seconds=video_duration,
    )
    return {
        "ok": True,
        "schemaVersion": TIMELINE_SCHEMA_VERSION,
        "revision": 0,
        "available": True,
        "pending": False,
        "reviewReady": False,
        "reviewId": _review_id(relative_key),
        "relativeKey": relative_key,
        "audioPath": str(audio_path.resolve()),
        "audioDurationSeconds": audio_duration,
        "durationSeconds": video_duration,
        "ttsVolume": _positive_volume(tts_volume),
        "timelineChunks": chunks,
        "previewUrl": "",
        **_waveform_payload(relative_key, audio_path),
    }


def save_tts_timeline_review(
    video_path: Path,
    relative_key: str,
    audio_path: Path,
    chunks: Any,
    *,
    expected_revision: Any = None,
    tts_volume: float = 1.0,
    tts_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    video_duration, audio_duration = _media_durations(video_path, audio_path)
    normalized = normalize_tts_timeline_chunks(
        chunks,
        audio_duration_seconds=audio_duration,
        video_duration_seconds=video_duration,
    )
    review_dir = _review_dir(relative_key)
    review_dir.mkdir(parents=True, exist_ok=True)
    with timeline_review_lock("tts", relative_key):
        previous = _load_review(relative_key)
        ensure_expected_revision(previous, expected_revision)
        state = {
            "schemaVersion": TIMELINE_SCHEMA_VERSION,
            "revision": next_timeline_revision(previous),
            "reviewId": review_dir.name,
            "relativeKey": relative_key,
            "audioPath": str(audio_path.resolve()),
            "audioDurationSeconds": audio_duration,
            "durationSeconds": video_duration,
            "ttsVolume": _positive_volume(tts_volume),
            "timelineChunks": normalized,
            "ttsResult": dict(tts_result or {}),
            "pending": True,
            "preparedAt": datetime.now(timezone.utc).isoformat(),
            "candidateName": "candidate.mp4",
        }
        _write_json(review_dir / "review.json", state)
    return _public_review(state, pending=True)


def pending_tts_timeline_review(relative_key: str) -> dict[str, Any]:
    state = _load_review(relative_key)
    if not _pending_state_is_valid(state, relative_key):
        return {}
    return state


def render_tts_timeline_candidate(
    visual_source: Path,
    relative_key: str,
    *,
    duration_seconds: float | None = None,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    state = pending_tts_timeline_review(relative_key)
    if not state:
        raise LookupError("请先编辑或重新生成配音预览")
    review_dir = _review_dir(relative_key)
    candidate = review_dir / "candidate.mp4"
    temporary = review_dir / "candidate.rendering.mp4"
    render_tts_timeline_video(
        visual_source,
        Path(str(state["audioPath"])),
        temporary,
        state["timelineChunks"],
        duration_seconds=float(duration_seconds or state["durationSeconds"]),
        tts_volume=float(state.get("ttsVolume") or 1.0),
        ffmpeg_bin=ffmpeg_bin,
    )
    temporary.replace(candidate)
    state["candidateName"] = candidate.name
    state["previewAudioMode"] = "tts-only-v1"
    state["renderedAt"] = datetime.now(timezone.utc).isoformat()
    state["visualSignature"] = _file_signature(visual_source)
    _write_json(review_dir / "review.json", state)
    return _public_review(state, pending=True)


def render_tts_timeline_video(
    visual_source: Path,
    audio_path: Path,
    target: Path,
    chunks: Any,
    *,
    duration_seconds: float,
    tts_volume: float = 1.0,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    if not visual_source.is_file() or not audio_path.is_file():
        raise FileNotFoundError("配音预览所需的视频或音频不存在")
    audio_duration = probe_media_duration_seconds(audio_path, ffmpeg_bin=ffmpeg_bin)
    normalized = normalize_tts_timeline_chunks(
        chunks,
        audio_duration_seconds=audio_duration,
        video_duration_seconds=duration_seconds,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    command = _tts_timeline_ffmpeg_command(
        visual_source,
        audio_path,
        target,
        normalized,
        duration_seconds,
        _positive_volume(tts_volume),
        resolve_ffmpeg_bin(ffmpeg_bin),
    )
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
    except subprocess.CalledProcessError as exc:
        target.unlink(missing_ok=True)
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(message[-500:] or "配音时间轴预览生成失败") from exc
    except subprocess.TimeoutExpired as exc:
        target.unlink(missing_ok=True)
        raise RuntimeError("配音时间轴预览生成超时") from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {
        "status": "rendered",
        "video": str(target),
        "timelineChunks": normalized,
        "durationSeconds": round(float(duration_seconds), 3),
    }


def tts_timeline_candidate_needs_render(visual_source: Path, relative_key: str) -> bool:
    state = pending_tts_timeline_review(relative_key)
    if not state:
        return False
    candidate = _review_dir(relative_key) / str(state.get("candidateName") or "candidate.mp4")
    return (
        not candidate.is_file()
        or state.get("visualSignature") != _file_signature(visual_source)
        or state.get("previewAudioMode") != "tts-only-v1"
    )


def mark_tts_timeline_review_confirmed(relative_key: str) -> dict[str, Any]:
    state = pending_tts_timeline_review(relative_key)
    if not state:
        raise LookupError("请先编辑或重新生成配音预览")
    state["pending"] = False
    state["confirmedAt"] = datetime.now(timezone.utc).isoformat()
    _write_json(_review_dir(relative_key) / "review.json", state)
    return {
        **_public_review(state, pending=False),
        "status": "applied",
        "ttsResult": dict(state.get("ttsResult") or {}),
        "confirmedAt": state["confirmedAt"],
    }


def resolve_tts_timeline_review_video(review_id: str) -> Path:
    normalized = _normalize_review_id(review_id)
    review_dir = (_review_root() / normalized).resolve()
    _assert_within_review_root(review_dir)
    state = _load_json(review_dir / "review.json")
    candidate = review_dir / str(state.get("candidateName") or "candidate.mp4")
    if not candidate.is_file():
        raise FileNotFoundError("配音时间轴预览不存在")
    return candidate


def normalize_tts_timeline_chunks(
    chunks: Any,
    *,
    audio_duration_seconds: float | int | None,
    video_duration_seconds: float | int | None,
) -> list[dict[str, Any]]:
    audio_duration = _positive_duration(audio_duration_seconds, "无法读取配音时长")
    video_duration = _positive_duration(video_duration_seconds, "无法读取视频时长")
    raw_chunks = (
        chunks
        if isinstance(chunks, list) and chunks
        else [_default_chunk(min(audio_duration, video_duration), audio_duration)]
    )
    if len(raw_chunks) > MAX_TTS_TIMELINE_CHUNKS:
        raise ValueError(f"配音最多切成 {MAX_TTS_TIMELINE_CHUNKS} 块")
    normalized = [_normalize_chunk(item, audio_duration, video_duration) for item in raw_chunks]
    normalized.sort(key=lambda item: item["sourceStartSeconds"])
    _validate_source_order(normalized)
    _validate_timeline_order(normalized, video_duration)
    for index, item in enumerate(normalized):
        item["index"] = index
        complete = (
            len(normalized) == 1
            and float(item["sourceStartSeconds"]) <= TTS_TIMELINE_TOLERANCE_SECONDS
            and abs(
                float(item["sourceEndSeconds"]) - min(audio_duration, video_duration)
            ) <= TTS_TIMELINE_TOLERANCE_SECONDS
        )
        item["label"] = "完整配音" if complete else f"配音 {index + 1}"
    return normalized


def _normalize_chunk(item: Any, audio_duration: float, video_duration: float) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("配音时间轴数据不合法")
    source_start = _finite_number(item.get("sourceStartSeconds"), "配音切块起点不合法")
    source_end_value = item.get("sourceEndSeconds")
    if source_end_value is None:
        source_end_value = source_start + _finite_number(item.get("durationSeconds"), "配音切块时长不合法")
    source_end = _finite_number(source_end_value, "配音切块终点不合法")
    start = _finite_number(item.get("startSeconds"), "配音放置时间不合法")
    source_start = min(max(source_start, 0.0), audio_duration)
    source_end = min(max(source_end, 0.0), audio_duration)
    duration = source_end - source_start
    if duration < MIN_TTS_TIMELINE_CHUNK_SECONDS:
        raise ValueError("每个配音切块至少保留 0.12 秒")
    if start < 0 or start + duration > video_duration + TTS_TIMELINE_TOLERANCE_SECONDS:
        raise ValueError("配音切块超出视频时长")
    restore_bounds = normalize_restore_bounds(
        item,
        visible_start_seconds=source_start,
        visible_end_seconds=source_end,
        source_duration_seconds=audio_duration,
    )
    return {
        "sourceStartSeconds": round(source_start, 3),
        "sourceEndSeconds": round(source_end, 3),
        "startSeconds": round(max(0.0, start), 3),
        "durationSeconds": round(duration, 3),
        "endSeconds": round(start + duration, 3),
        **restore_bounds.as_payload(),
    }


def _validate_source_order(chunks: list[dict[str, Any]]) -> None:
    previous_end = 0.0
    for item in chunks:
        source_start = float(item["sourceStartSeconds"])
        if source_start + TTS_TIMELINE_TOLERANCE_SECONDS < previous_end:
            raise ValueError("配音切块不能重复或改变原音频先后顺序")
        previous_end = float(item["sourceEndSeconds"])


def _validate_timeline_order(chunks: list[dict[str, Any]], video_duration: float) -> None:
    previous_end = 0.0
    for item in chunks:
        start = float(item["startSeconds"])
        if start + TTS_TIMELINE_TOLERANCE_SECONDS < previous_end:
            raise ValueError("配音切块不能重叠或改变先后顺序")
        previous_end = start + float(item["durationSeconds"])
    if previous_end > video_duration + TTS_TIMELINE_TOLERANCE_SECONDS:
        raise ValueError("配音时间轴超出视频结尾")


def _tts_timeline_ffmpeg_command(
    visual_source: Path,
    audio_path: Path,
    target: Path,
    chunks: list[dict[str, Any]],
    duration_seconds: float,
    tts_volume: float,
    ffmpeg: str,
) -> list[str]:
    filter_complex = build_tts_timeline_audio_filter(
        chunks,
        duration_seconds,
        tts_volume,
        source_label="[1:a:0]",
    )
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(visual_source), "-i", str(audio_path),
        "-filter_complex", filter_complex,
        "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
        "-t", f"{float(duration_seconds):.3f}", "-movflags", "+faststart", str(target),
    ]


def build_tts_timeline_audio_filter(
    chunks: list[dict[str, Any]],
    duration_seconds: float,
    tts_volume: float,
    *,
    source_label: str = "[0:a:0]",
) -> str:
    filters: list[str] = []
    source_labels = [source_label]
    if len(chunks) > 1:
        source_labels = [f"[source{index}]" for index in range(len(chunks))]
        filters.append(f"{source_label}asplit={len(chunks)}{''.join(source_labels)}")
    output_labels = []
    for index, item in enumerate(chunks):
        label = f"tts{index}"
        delay_ms = max(0, round(float(item["startSeconds"]) * 1000))
        filters.append(
            f"{source_labels[index]}atrim=start={item['sourceStartSeconds']}:end={item['sourceEndSeconds']},"
            f"asetpts=PTS-STARTPTS,volume={tts_volume:.6g},adelay={delay_ms}:all=1[{label}]"
        )
        output_labels.append(f"[{label}]")
    mixed = output_labels[0]
    if len(output_labels) > 1:
        filters.append(
            f"{''.join(output_labels)}amix=inputs={len(output_labels)}:"
            "duration=longest:dropout_transition=0:normalize=0[mixed]"
        )
        mixed = "[mixed]"
    filters.append(
        f"{mixed}apad,atrim=duration={float(duration_seconds):.3f},asetpts=N/SR/TB[aout]"
    )
    return ";".join(filters)


def _media_durations(video_path: Path, audio_path: Path) -> tuple[float, float]:
    if not video_path.is_file() or not audio_path.is_file():
        raise FileNotFoundError("配音编辑所需的视频或音频不存在")
    video_duration = _positive_duration(probe_media_duration_seconds(video_path), "无法读取视频时长")
    audio_duration = _positive_duration(probe_media_duration_seconds(audio_path), "无法读取配音时长")
    return round(video_duration, 3), round(audio_duration, 3)


def _default_chunk(visible_duration: float, audio_duration: float) -> dict[str, float]:
    return {
        "sourceStartSeconds": 0.0,
        "sourceEndSeconds": round(visible_duration, 3),
        "originalSourceStartSeconds": 0.0,
        "originalSourceEndSeconds": round(audio_duration, 3),
        "startSeconds": 0.0,
    }


def _empty_review(relative_key: str) -> dict[str, Any]:
    return {
        "ok": True,
        "schemaVersion": TIMELINE_SCHEMA_VERSION,
        "revision": 0,
        "available": False,
        "pending": False,
        "reviewReady": False,
        "reviewId": _review_id(relative_key),
        "relativeKey": relative_key,
        "audioDurationSeconds": 0.0,
        "durationSeconds": 0.0,
        "timelineChunks": [],
        "previewUrl": "",
        "waveformStatus": "unavailable",
        "waveformPeaks": [],
        "reason": "当前视频没有可编辑的独立 TTS 音频",
    }


def _public_review(state: dict[str, Any], *, pending: bool) -> dict[str, Any]:
    review_id = str(state.get("reviewId") or "")
    candidate = _review_dir(str(state.get("relativeKey") or "")) / str(state.get("candidateName") or "candidate.mp4")
    waveform = _waveform_payload(str(state.get("relativeKey") or ""), Path(str(state.get("audioPath") or "")))
    return {
        "ok": True,
        "schemaVersion": int(state.get("schemaVersion") or TIMELINE_SCHEMA_VERSION),
        "revision": int(state.get("revision") or 0),
        "available": True,
        "pending": pending,
        "reviewReady": pending,
        "reviewId": review_id,
        "relativeKey": str(state.get("relativeKey") or ""),
        "audioPath": str(state.get("audioPath") or ""),
        "audioDurationSeconds": float(state.get("audioDurationSeconds") or 0.0),
        "durationSeconds": float(state.get("durationSeconds") or 0.0),
        "ttsVolume": float(state.get("ttsVolume") or 1.0),
        "timelineChunks": list(state.get("timelineChunks") or []),
        "previewUrl": (
            f"/api/user-generated-results/tts-timeline-preview/{review_id}" if pending and candidate.is_file() else ""
        ),
        "preparedAt": state.get("preparedAt"),
        **waveform,
    }


def _pending_state_is_valid(state: dict[str, Any], relative_key: str) -> bool:
    audio = Path(str(state.get("audioPath") or ""))
    return bool(state.get("pending") is True and state.get("relativeKey") == relative_key and audio.is_file())


def _stored_state_is_valid(state: dict[str, Any], relative_key: str) -> bool:
    audio = Path(str(state.get("audioPath") or ""))
    chunks = state.get("timelineChunks")
    return bool(
        state.get("relativeKey") == relative_key
        and audio.is_file()
        and isinstance(chunks, list)
        and chunks
    )


def _positive_duration(value: Any, message: str) -> float:
    number = _finite_number(value, message)
    if number <= 0:
        raise ValueError(message)
    return number


def _positive_volume(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    return min(max(number, 0.0), 4.0)


def _finite_number(value: Any, message: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(message) from exc
    if not math.isfinite(number):
        raise ValueError(message)
    return number


def _file_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path.resolve()), "sizeBytes": stat.st_size, "mtimeNs": stat.st_mtime_ns}


def _waveform_payload(relative_key: str, audio_path: Path) -> dict[str, Any]:
    try:
        result = cached_audio_waveform(audio_path, _review_dir(relative_key) / "waveform.json")
    except Exception as exc:
        return {
            "waveformStatus": "failed",
            "waveformPeaks": [],
            "waveformReason": str(exc)[:200] or "提取配音波形失败",
        }
    return {"waveformStatus": "ready", "waveformPeaks": list(result.get("peaks") or [])}


def _review_id(relative_key: str) -> str:
    return hashlib.sha256(relative_key.encode("utf-8")).hexdigest()[:32]


def _review_dir(relative_key: str) -> Path:
    ensure_user_file_root()
    root = _review_root()
    root.mkdir(parents=True, exist_ok=True)
    path = (root / _review_id(relative_key)).resolve()
    _assert_within_review_root(path)
    return path


def _review_root() -> Path:
    return TTS_TIMELINE_REVIEW_ROOT.resolve()


def _normalize_review_id(review_id: str) -> str:
    normalized = str(review_id or "").strip().lower()
    if len(normalized) != 32 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("配音预览标识不合法")
    return normalized


def _assert_within_review_root(path: Path) -> None:
    try:
        path.relative_to(_review_root())
    except ValueError as exc:
        raise ValueError("配音预览路径不合法") from exc


def _load_review(relative_key: str) -> dict[str, Any]:
    return _load_json(_review_dir(relative_key) / "review.json")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
