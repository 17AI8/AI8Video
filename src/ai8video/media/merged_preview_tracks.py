from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ai8video.media.ffmpeg_utils import probe_media_duration_seconds, resolve_ffmpeg_bin


def merged_video_chunks(durations: list[float]) -> list[dict[str, float]]:
    chunks: list[dict[str, float]] = []
    cursor = 0.0
    for duration in durations:
        end = cursor + max(0.0, duration)
        if end > cursor:
            chunks.append({"sourceStartSeconds": round(cursor, 3), "sourceEndSeconds": round(end, 3)})
        cursor = end
    return chunks


def merged_edited_video_chunks(
    statuses: list[dict[str, Any]],
    source_durations: list[float],
) -> list[dict[str, float]]:
    chunks: list[dict[str, float]] = []
    source_offset = 0.0
    for status, source_duration in zip(statuses, source_durations):
        timeline_chunks = status.get("timelineChunks") or []
        if not timeline_chunks:
            timeline_chunks = [{"sourceStartSeconds": 0.0, "sourceEndSeconds": source_duration}]
        for item in timeline_chunks:
            start = max(0.0, float(item.get("sourceStartSeconds") or 0))
            end = min(max(0.0, source_duration), float(item.get("sourceEndSeconds") or 0))
            if end <= start:
                continue
            chunks.append({
                "sourceStartSeconds": round(source_offset + start, 3),
                "sourceEndSeconds": round(source_offset + end, 3),
            })
        source_offset += max(0.0, source_duration)
    return chunks


def edited_video_durations(
    statuses: list[dict[str, Any]],
    source_durations: list[float],
) -> list[float]:
    durations: list[float] = []
    for status, source_duration in zip(statuses, source_durations):
        timeline_chunks = status.get("timelineChunks") or []
        if not timeline_chunks:
            durations.append(max(0.0, source_duration))
            continue
        duration = sum(
            max(
                0.0,
                min(max(0.0, source_duration), float(item.get("sourceEndSeconds") or 0))
                - max(0.0, float(item.get("sourceStartSeconds") or 0)),
            )
            for item in timeline_chunks
            if isinstance(item, dict)
        )
        durations.append(duration)
    return durations


def merged_tts_chunks(statuses: list[dict[str, Any]], video_durations: list[float]) -> list[dict[str, float]]:
    chunks: list[dict[str, float]] = []
    audio_offset = 0.0
    video_offset = 0.0
    for status, video_duration in zip(statuses, video_durations):
        for item in status.get("timelineChunks") or []:
            chunks.append({
                "sourceStartSeconds": round(audio_offset + float(item.get("sourceStartSeconds") or 0), 3),
                "sourceEndSeconds": round(audio_offset + float(item.get("sourceEndSeconds") or 0), 3),
                "startSeconds": round(video_offset + float(item.get("startSeconds") or 0), 3),
            })
        audio_offset += float(status.get("audioDurationSeconds") or 0)
        video_offset += max(0.0, video_duration)
    return chunks


def merge_tts_audio(statuses: list[dict[str, Any]], output: Path, *, ffmpeg_bin: str | None = None) -> bool:
    available = [status for status in statuses if _audio_path(status) is not None]
    if not available:
        return False
    command = [resolve_ffmpeg_bin(ffmpeg_bin), "-y", "-hide_banner", "-loglevel", "error"]
    for status in available:
        command.extend(["-i", str(_audio_path(status))])
    filters = []
    labels = []
    for index, status in enumerate(available):
        duration = max(0.001, float(status.get("audioDurationSeconds") or 0.001))
        volume = float(status.get("ttsVolume") or 1.0)
        filters.append(
            f"[{index}:a]atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,volume={volume:.6g},"
            f"aresample=48000,aformat=channel_layouts=stereo[a{index}]"
        )
        labels.append(f"[a{index}]")
    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[aout]")
    output.parent.mkdir(parents=True, exist_ok=True)
    command.extend(["-filter_complex", ";".join(filters), "-map", "[aout]", "-c:a", "aac", str(output)])
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=240)
    return output.is_file() and bool(probe_media_duration_seconds(output))


def _audio_path(status: dict[str, Any]) -> Path | None:
    path = Path(str(status.get("audioPath") or ""))
    return path if status.get("available") is True and path.is_file() else None
