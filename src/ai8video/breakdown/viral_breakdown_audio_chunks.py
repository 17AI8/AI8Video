from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def create_transcript_audio_chunks(
    video_path: Path,
    output_dir: Path,
    segments: list[dict[str, Any]],
    *,
    ffmpeg_bin: str,
) -> list[dict[str, Any]]:
    building_dir = output_dir.with_name(f".{output_dir.name}-building")
    shutil.rmtree(building_dir, ignore_errors=True)
    building_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[dict[str, Any]] = []
    cursor = 0.0
    try:
        for index, segment in enumerate(segments):
            chunk = _create_audio_chunk(video_path, building_dir, segment, index, ffmpeg_bin)
            duration = chunk["durationSeconds"]
            chunks.append({**segment, **chunk, "start": round(cursor, 3), "end": round(cursor + duration, 3)})
            cursor += duration
        shutil.rmtree(output_dir, ignore_errors=True)
        os.replace(building_dir, output_dir)
        return chunks
    except Exception:
        shutil.rmtree(building_dir, ignore_errors=True)
        raise


def _create_audio_chunk(
    video_path: Path,
    output_dir: Path,
    segment: dict[str, Any],
    index: int,
    ffmpeg_bin: str,
) -> dict[str, Any]:
    source_start = max(0.0, float(segment.get("start") or 0.0))
    source_end = max(source_start, float(segment.get("end") or source_start))
    duration = source_end - source_start
    if duration <= 0.01:
        raise RuntimeError("台词时间段无效，无法切割原音频")
    chunk_id = _chunk_id(index, source_start, source_end, str(segment.get("text") or ""))
    output_path = output_dir / f"{chunk_id}.m4a"
    command = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{source_start:.3f}", "-t", f"{duration:.3f}", "-i", str(video_path),
        "-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "128k", str(output_path),
    ]
    process = subprocess.run(command, check=False, capture_output=True, text=True)
    if process.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
        error = (process.stderr or process.stdout or "ffmpeg failed").strip()
        raise RuntimeError(f"台词原音频切块失败：{error}")
    return {
        "chunkId": chunk_id,
        "sourceStart": round(source_start, 3),
        "sourceEnd": round(source_end, 3),
        "durationSeconds": round(duration, 3),
        "fileName": output_path.name,
    }


def _chunk_id(index: int, start: float, end: float, text: str) -> str:
    signature = f"{index}:{start:.3f}:{end:.3f}:{text}".encode("utf-8")
    digest = hashlib.sha1(signature).hexdigest()[:10]
    return f"chunk-{index + 1:04d}-{digest}"
