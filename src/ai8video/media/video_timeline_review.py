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
from ai8video.media.ffmpeg_utils import (
    probe_media_duration_seconds,
    probe_media_metadata,
    resolve_ffmpeg_bin,
)
from ai8video.media.video_encoding import append_video_postprocess_encoding_args
from ai8video.media.video_filmstrip import (
    VIDEO_TIMELINE_FILMSTRIP_FRAMES,
    resolve_video_filmstrip,
    video_filmstrip_payload,
)
from ai8video.media.tts_timeline_review import render_tts_timeline_video


VIDEO_TIMELINE_REVIEW_ROOT = (USER_FILE_ROOT / "视频裁剪" / "reviews").resolve()
MAX_VIDEO_TIMELINE_CHUNKS = 64
MIN_VIDEO_TIMELINE_CHUNK_SECONDS = 0.12
VIDEO_TIMELINE_TOLERANCE_SECONDS = 0.08


def video_timeline_review_status(
    video_path: Path,
    relative_key: str,
    *,
    include_filmstrip: bool = False,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    duration = _video_duration(video_path, ffmpeg_bin=ffmpeg_bin)
    state = _load_review(relative_key)
    if _pending_state_is_valid(state, relative_key, video_path):
        review = _public_review(state, pending=True)
    else:
        review = _default_review(video_path, relative_key, duration)
    if include_filmstrip:
        review.update(video_filmstrip_payload(
            video_path,
            _review_dir(relative_key),
            _review_id(relative_key),
            duration,
            ffmpeg_bin=ffmpeg_bin,
        ))
    return review


def save_video_timeline_review(
    video_path: Path,
    relative_key: str,
    chunks: Any,
    *,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    duration = _video_duration(video_path, ffmpeg_bin=ffmpeg_bin)
    normalized = normalize_video_timeline_chunks(chunks, video_duration_seconds=duration)
    review_dir = _review_dir(relative_key)
    review_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "reviewId": review_dir.name,
        "relativeKey": relative_key,
        "sourcePath": str(video_path.resolve()),
        "sourceSignature": _file_signature(video_path),
        "sourceDurationSeconds": duration,
        "outputDurationSeconds": _output_duration(normalized),
        "timelineChunks": normalized,
        "pending": True,
        "preparedAt": datetime.now(timezone.utc).isoformat(),
        "candidateName": "candidate.mp4",
    }
    _write_json(review_dir / "review.json", state)
    return _public_review(state, pending=True)


def reset_video_timeline_review(
    video_path: Path,
    relative_key: str,
    *,
    include_filmstrip: bool = False,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    review_dir = _review_dir(relative_key)
    for name in (
        "review.json",
        "candidate.mp4",
        "candidate.rendering.mp4",
        "candidate.tts-rendering.mp4",
    ):
        (review_dir / name).unlink(missing_ok=True)
    return video_timeline_review_status(
        video_path,
        relative_key,
        include_filmstrip=include_filmstrip,
        ffmpeg_bin=ffmpeg_bin,
    )


def pending_video_timeline_review(relative_key: str, video_path: Path | None = None) -> dict[str, Any]:
    state = _load_review(relative_key)
    if not _pending_state_is_valid(state, relative_key, video_path):
        return {}
    return state


def render_video_timeline_candidate(
    composite_source: Path,
    relative_key: str,
    *,
    preserve_source_audio: bool = True,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    state = pending_video_timeline_review(relative_key)
    if not state:
        raise LookupError("请先编辑视频裁剪预览")
    review_dir = _review_dir(relative_key)
    candidate = review_dir / "candidate.mp4"
    temporary = review_dir / "candidate.rendering.mp4"
    render_video_timeline_video(
        composite_source,
        temporary,
        state["timelineChunks"],
        source_duration_seconds=float(state["sourceDurationSeconds"]),
        preserve_source_audio=preserve_source_audio,
        ffmpeg_bin=ffmpeg_bin,
    )
    temporary.replace(candidate)
    state["candidateName"] = candidate.name
    state["renderedAt"] = datetime.now(timezone.utc).isoformat()
    state["compositeSignature"] = _file_signature(composite_source)
    state["preserveSourceAudio"] = preserve_source_audio
    state.pop("audioPreviewSignature", None)
    _write_json(review_dir / "review.json", state)
    return _public_review(state, pending=True)


def render_video_timeline_tts_preview(
    relative_key: str,
    audio_path: Path,
    chunks: Any,
    *,
    tts_volume: float = 1.0,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    state = pending_video_timeline_review(relative_key)
    if not state:
        raise LookupError("请先编辑视频裁剪预览")
    review_dir = _review_dir(relative_key)
    candidate = review_dir / str(state.get("candidateName") or "candidate.mp4")
    if not candidate.is_file():
        raise FileNotFoundError("视频裁剪预览不存在")
    duration = float(state.get("outputDurationSeconds") or 0)
    clipped = _clip_tts_chunks_for_preview(chunks, duration)
    signature = _tts_preview_signature(audio_path, clipped, duration, tts_volume)
    if state.get("audioPreviewSignature") == signature:
        return _public_review(state, pending=True)
    temporary = review_dir / "candidate.tts-rendering.mp4"
    if clipped:
        render_tts_timeline_video(
            candidate,
            audio_path,
            temporary,
            clipped,
            duration_seconds=duration,
            tts_volume=tts_volume,
            ffmpeg_bin=ffmpeg_bin,
        )
    else:
        _strip_audio_track(candidate, temporary, ffmpeg_bin=ffmpeg_bin)
    temporary.replace(candidate)
    state["audioPreviewSignature"] = signature
    _write_json(review_dir / "review.json", state)
    return _public_review(state, pending=True)


def render_video_timeline_video(
    source: Path,
    target: Path,
    chunks: Any,
    *,
    source_duration_seconds: float | int | None = None,
    preserve_source_audio: bool = True,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError("视频裁剪所需的源视频不存在")
    duration = _positive_duration(
        source_duration_seconds or probe_media_duration_seconds(source, ffmpeg_bin=ffmpeg_bin),
        "无法读取视频时长",
    )
    normalized = normalize_video_timeline_chunks(chunks, video_duration_seconds=duration)
    metadata = probe_media_metadata(source) or {}
    has_audio = preserve_source_audio and int(metadata.get("audioChannels") or 0) > 0
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    command = _video_timeline_ffmpeg_command(source, target, normalized, has_audio, ffmpeg)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=240)
    except subprocess.CalledProcessError as exc:
        target.unlink(missing_ok=True)
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(message[-500:] or "视频裁剪预览生成失败") from exc
    except subprocess.TimeoutExpired as exc:
        target.unlink(missing_ok=True)
        raise RuntimeError("视频裁剪预览生成超时") from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {
        "status": "rendered",
        "video": str(target),
        "timelineChunks": normalized,
        "sourceDurationSeconds": round(duration, 3),
        "outputDurationSeconds": _output_duration(normalized),
    }


def video_timeline_candidate_needs_render(
    composite_source: Path,
    relative_key: str,
    *,
    preserve_source_audio: bool = True,
) -> bool:
    state = pending_video_timeline_review(relative_key)
    if not state:
        return False
    candidate = _review_dir(relative_key) / str(state.get("candidateName") or "candidate.mp4")
    return (
        not candidate.is_file()
        or state.get("compositeSignature") != _file_signature(composite_source)
        or state.get("preserveSourceAudio") is not preserve_source_audio
    )


def mark_video_timeline_review_confirmed(relative_key: str) -> dict[str, Any]:
    state = pending_video_timeline_review(relative_key)
    if not state:
        raise LookupError("请先编辑视频裁剪预览")
    state["pending"] = False
    state["confirmedAt"] = datetime.now(timezone.utc).isoformat()
    _write_json(_review_dir(relative_key) / "review.json", state)
    return {
        **_public_review(state, pending=False),
        "status": "applied",
        "confirmedAt": state["confirmedAt"],
    }


def resolve_video_timeline_review_video(review_id: str) -> Path:
    review_dir = _review_dir_from_id(review_id)
    state = _load_json(review_dir / "review.json")
    candidate = review_dir / str(state.get("candidateName") or "candidate.mp4")
    if not candidate.is_file():
        raise FileNotFoundError("视频裁剪预览不存在")
    return candidate


def resolve_video_timeline_filmstrip(review_id: str) -> Path:
    return resolve_video_filmstrip(_review_dir_from_id(review_id))


def normalize_video_timeline_chunks(
    chunks: Any,
    *,
    video_duration_seconds: float | int | None,
) -> list[dict[str, Any]]:
    duration = _positive_duration(video_duration_seconds, "无法读取视频时长")
    raw_chunks = chunks if isinstance(chunks, list) and chunks else [_default_chunk(duration)]
    if len(raw_chunks) > MAX_VIDEO_TIMELINE_CHUNKS:
        raise ValueError(f"视频最多切成 {MAX_VIDEO_TIMELINE_CHUNKS} 段")
    normalized = [_normalize_chunk(item, duration) for item in raw_chunks]
    normalized.sort(key=lambda item: item["sourceStartSeconds"])
    _validate_source_order(normalized)
    output_start = 0.0
    for index, item in enumerate(normalized):
        item["index"] = index
        item["startSeconds"] = round(output_start, 3)
        item["endSeconds"] = round(output_start + float(item["durationSeconds"]), 3)
        item["label"] = "完整视频" if len(normalized) == 1 and _is_full_chunk(item, duration) else f"片段 {index + 1}"
        output_start += float(item["durationSeconds"])
    return normalized


def remap_timeline_chunks_through_video_cuts(
    timeline_chunks: Any,
    video_chunks: Any,
    *,
    minimum_chunk_seconds: float = MIN_VIDEO_TIMELINE_CHUNK_SECONDS,
) -> list[dict[str, float]]:
    if not isinstance(timeline_chunks, list) or not isinstance(video_chunks, list):
        return []
    remapped: list[dict[str, float]] = []
    for video_chunk in video_chunks:
        video_source_start = float(video_chunk.get("sourceStartSeconds") or 0)
        video_source_end = float(video_chunk.get("sourceEndSeconds") or video_source_start)
        video_output_start = float(video_chunk.get("startSeconds") or 0)
        for timeline_chunk in timeline_chunks:
            timeline_start = float(timeline_chunk.get("startSeconds") or 0)
            source_start = float(timeline_chunk.get("sourceStartSeconds") or 0)
            source_end = float(timeline_chunk.get("sourceEndSeconds") or source_start)
            timeline_duration = float(timeline_chunk.get("durationSeconds") or source_end - source_start)
            timeline_end = timeline_start + timeline_duration
            intersection_start = max(video_source_start, timeline_start)
            intersection_end = min(video_source_end, timeline_end)
            if intersection_end - intersection_start < minimum_chunk_seconds:
                continue
            source_start += intersection_start - timeline_start
            duration = intersection_end - intersection_start
            remapped.append({
                "sourceStartSeconds": round(source_start, 3),
                "sourceEndSeconds": round(source_start + duration, 3),
                "startSeconds": round(video_output_start + intersection_start - video_source_start, 3),
            })
    return remapped


def _normalize_chunk(item: Any, duration: float) -> dict[str, float]:
    if not isinstance(item, dict):
        raise ValueError("视频裁剪时间轴数据不合法")
    source_start = _finite_number(item.get("sourceStartSeconds"), "视频片段起点不合法")
    source_end_value = item.get("sourceEndSeconds")
    if source_end_value is None:
        source_end_value = source_start + _finite_number(item.get("durationSeconds"), "视频片段时长不合法")
    source_end = _finite_number(source_end_value, "视频片段终点不合法")
    source_start = min(max(source_start, 0.0), duration)
    source_end = min(max(source_end, 0.0), duration)
    chunk_duration = source_end - source_start
    if chunk_duration < MIN_VIDEO_TIMELINE_CHUNK_SECONDS:
        raise ValueError("每个视频片段至少保留 0.12 秒")
    return {
        "sourceStartSeconds": round(source_start, 3),
        "sourceEndSeconds": round(source_end, 3),
        "durationSeconds": round(chunk_duration, 3),
    }


def _validate_source_order(chunks: list[dict[str, Any]]) -> None:
    previous_end = 0.0
    for item in chunks:
        source_start = float(item["sourceStartSeconds"])
        if source_start + VIDEO_TIMELINE_TOLERANCE_SECONDS < previous_end:
            raise ValueError("视频片段不能重叠或改变原视频先后顺序")
        previous_end = float(item["sourceEndSeconds"])


def _video_timeline_ffmpeg_command(
    source: Path,
    target: Path,
    chunks: list[dict[str, Any]],
    has_audio: bool,
    ffmpeg: str,
) -> list[str]:
    filters: list[str] = []
    count = len(chunks)
    video_sources = ["[0:v:0]"] if count == 1 else [f"[videoSource{index}]" for index in range(count)]
    audio_sources = ["[0:a:0]"] if count == 1 else [f"[audioSource{index}]" for index in range(count)]
    if count > 1:
        filters.append(f"[0:v:0]split={count}{''.join(video_sources)}")
        if has_audio:
            filters.append(f"[0:a:0]asplit={count}{''.join(audio_sources)}")
    for index, item in enumerate(chunks):
        filters.append(
            f"{video_sources[index]}trim=start={item['sourceStartSeconds']}:end={item['sourceEndSeconds']},"
            f"setpts=PTS-STARTPTS[video{index}]"
        )
        if has_audio:
            filters.append(
                f"{audio_sources[index]}atrim=start={item['sourceStartSeconds']}:end={item['sourceEndSeconds']},"
                f"asetpts=PTS-STARTPTS[audio{index}]"
            )
    video_output = "[video0]"
    audio_output = "[audio0]"
    if count > 1:
        if has_audio:
            concat_inputs = "".join(f"[video{index}][audio{index}]" for index in range(count))
            filters.append(f"{concat_inputs}concat=n={count}:v=1:a=1[videoOut][audioOut]")
            audio_output = "[audioOut]"
        else:
            concat_inputs = "".join(f"[video{index}]" for index in range(count))
            filters.append(f"{concat_inputs}concat=n={count}:v=1:a=0[videoOut]")
        video_output = "[videoOut]"
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-filter_complex", ";".join(filters), "-map", video_output,
    ]
    if has_audio:
        command.extend(["-map", audio_output])
    append_video_postprocess_encoding_args(command)
    command.extend(["-c:a", "aac"] if has_audio else ["-an"])
    command.extend(["-movflags", "+faststart", str(target)])
    return command


def _clip_tts_chunks_for_preview(chunks: Any, duration: float) -> list[dict[str, float]]:
    if not isinstance(chunks, list) or duration <= 0:
        return []
    clipped: list[dict[str, float]] = []
    for item in chunks:
        if not isinstance(item, dict):
            continue
        start = max(0.0, float(item.get("startSeconds") or 0))
        source_start = max(0.0, float(item.get("sourceStartSeconds") or 0))
        source_end = max(source_start, float(item.get("sourceEndSeconds") or source_start))
        retained = min(source_end - source_start, duration - start)
        if retained < MIN_VIDEO_TIMELINE_CHUNK_SECONDS:
            continue
        clipped.append({
            "sourceStartSeconds": round(source_start, 3),
            "sourceEndSeconds": round(source_start + retained, 3),
            "startSeconds": round(start, 3),
        })
    return clipped


def _tts_preview_signature(
    audio_path: Path,
    chunks: list[dict[str, float]],
    duration: float,
    volume: float,
) -> dict[str, Any]:
    return {
        "audio": _file_signature(audio_path),
        "chunks": chunks,
        "durationSeconds": round(duration, 3),
        "ttsVolume": round(float(volume), 6),
    }


def _strip_audio_track(source: Path, target: Path, *, ffmpeg_bin: str | None = None) -> None:
    command = [
        resolve_ffmpeg_bin(ffmpeg_bin), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-map", "0:v:0", "-c:v", "copy", "-an",
        "-movflags", "+faststart", str(target),
    ]
    target.unlink(missing_ok=True)
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        target.unlink(missing_ok=True)
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(message[-500:] or "清理裁剪预览音轨失败") from exc
    except subprocess.TimeoutExpired as exc:
        target.unlink(missing_ok=True)
        raise RuntimeError("清理裁剪预览音轨超时") from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _default_review(video_path: Path, relative_key: str, duration: float) -> dict[str, Any]:
    chunks = normalize_video_timeline_chunks([], video_duration_seconds=duration)
    return {
        "ok": True,
        "available": True,
        "pending": False,
        "reviewReady": False,
        "reviewId": _review_id(relative_key),
        "relativeKey": relative_key,
        "sourcePath": str(video_path.resolve()),
        "sourceDurationSeconds": duration,
        "outputDurationSeconds": duration,
        "durationSeconds": duration,
        "timelineChunks": chunks,
        "previewUrl": "",
        "filmstripStatus": "idle",
        "filmstripUrl": "",
        "filmstripFrameCount": VIDEO_TIMELINE_FILMSTRIP_FRAMES,
    }


def _public_review(state: dict[str, Any], *, pending: bool) -> dict[str, Any]:
    review_id = str(state.get("reviewId") or "")
    candidate = _review_dir(str(state.get("relativeKey") or "")) / str(state.get("candidateName") or "candidate.mp4")
    output_duration = float(state.get("outputDurationSeconds") or 0)
    return {
        "ok": True,
        "available": True,
        "pending": pending,
        "reviewReady": pending,
        "reviewId": review_id,
        "relativeKey": str(state.get("relativeKey") or ""),
        "sourceDurationSeconds": float(state.get("sourceDurationSeconds") or 0),
        "outputDurationSeconds": output_duration,
        "durationSeconds": output_duration,
        "timelineChunks": list(state.get("timelineChunks") or []),
        "previewUrl": (
            f"/api/user-generated-results/video-timeline-preview/{review_id}" if pending and candidate.is_file() else ""
        ),
        "preparedAt": state.get("preparedAt"),
        "filmstripStatus": "idle",
        "filmstripUrl": "",
        "filmstripFrameCount": VIDEO_TIMELINE_FILMSTRIP_FRAMES,
    }


def _pending_state_is_valid(state: dict[str, Any], relative_key: str, video_path: Path | None) -> bool:
    if state.get("pending") is not True or state.get("relativeKey") != relative_key:
        return False
    if video_path is None:
        return True
    return video_path.is_file() and state.get("sourceSignature") == _file_signature(video_path)


def _video_duration(video_path: Path, *, ffmpeg_bin: str | None = None) -> float:
    if not video_path.is_file():
        raise FileNotFoundError("视频裁剪所需的视频不存在")
    return round(
        _positive_duration(probe_media_duration_seconds(video_path, ffmpeg_bin=ffmpeg_bin), "无法读取视频时长"),
        3,
    )


def _output_duration(chunks: list[dict[str, Any]]) -> float:
    return round(sum(float(item.get("durationSeconds") or 0) for item in chunks), 3)


def _default_chunk(duration: float) -> dict[str, float]:
    return {"sourceStartSeconds": 0.0, "sourceEndSeconds": round(duration, 3)}


def _is_full_chunk(item: dict[str, Any], duration: float) -> bool:
    return (
        float(item.get("sourceStartSeconds") or 0) <= VIDEO_TIMELINE_TOLERANCE_SECONDS
        and abs(float(item.get("sourceEndSeconds") or 0) - duration) <= VIDEO_TIMELINE_TOLERANCE_SECONDS
    )


def _positive_duration(value: Any, message: str) -> float:
    number = _finite_number(value, message)
    if number <= 0:
        raise ValueError(message)
    return number


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


def _review_id(relative_key: str) -> str:
    return hashlib.sha256(relative_key.encode("utf-8")).hexdigest()[:32]


def _review_dir(relative_key: str) -> Path:
    ensure_user_file_root()
    root = _review_root()
    root.mkdir(parents=True, exist_ok=True)
    path = (root / _review_id(relative_key)).resolve()
    _assert_within_review_root(path)
    return path


def _review_dir_from_id(review_id: str) -> Path:
    normalized = str(review_id or "").strip().lower()
    if len(normalized) != 32 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("视频裁剪预览标识不合法")
    path = (_review_root() / normalized).resolve()
    _assert_within_review_root(path)
    return path


def _review_root() -> Path:
    return VIDEO_TIMELINE_REVIEW_ROOT.resolve()


def _assert_within_review_root(path: Path) -> None:
    try:
        path.relative_to(_review_root())
    except ValueError as exc:
        raise ValueError("视频裁剪预览路径不合法") from exc


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
