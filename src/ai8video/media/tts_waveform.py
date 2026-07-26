from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from array import array
from pathlib import Path
from typing import Any

from ai8video.media.ffmpeg_utils import resolve_ffmpeg_bin


DEFAULT_WAVEFORM_POINT_COUNT = 320
WAVEFORM_SAMPLE_RATE = 8000


def cached_audio_waveform(
    audio_path: Path,
    cache_path: Path,
    *,
    point_count: int = DEFAULT_WAVEFORM_POINT_COUNT,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    if not audio_path.is_file():
        raise FileNotFoundError("配音波形所需的音频不存在")
    normalized_count = min(max(int(point_count), 32), 1024)
    signature = _file_signature(audio_path)
    cached = _load_json(cache_path)
    if cached.get("audioSignature") == signature and cached.get("pointCount") == normalized_count:
        peaks = _normalize_cached_peaks(cached.get("peaks"), normalized_count)
        if peaks:
            return {"status": "ready", "peaks": peaks, "cached": True}
    pcm = _extract_pcm(audio_path, resolve_ffmpeg_bin(ffmpeg_bin))
    peaks = _downsample_pcm(pcm, normalized_count)
    payload = {
        "audioSignature": signature,
        "pointCount": normalized_count,
        "sampleRate": WAVEFORM_SAMPLE_RATE,
        "peaks": peaks,
    }
    _write_json(cache_path, payload)
    return {"status": "ready", "peaks": peaks, "cached": False}


def _extract_pcm(audio_path: Path, ffmpeg: str) -> bytes:
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(audio_path),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(WAVEFORM_SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, timeout=30)
    except subprocess.CalledProcessError as exc:
        raw_message = exc.stderr or exc.stdout or str(exc)
        message = (
            raw_message.decode("utf-8", errors="replace")
            if isinstance(raw_message, bytes)
            else str(raw_message)
        ).strip()
        raise RuntimeError(message[-300:] or "提取配音波形失败") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("提取配音波形超时") from exc
    if not result.stdout:
        raise RuntimeError("配音波形数据为空")
    return result.stdout


def _downsample_pcm(pcm: bytes, point_count: int) -> list[float]:
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise RuntimeError("配音波形数据为空")
    bucket_size = max(1, math.ceil(len(samples) / point_count))
    raw_peaks = [
        max(abs(sample) for sample in samples[start : start + bucket_size]) / 32768.0
        for start in range(0, len(samples), bucket_size)
    ][:point_count]
    reference_index = max(0, math.ceil(len(raw_peaks) * 0.95) - 1)
    reference = sorted(raw_peaks)[reference_index] if raw_peaks else 0.0
    scale = reference if reference > 0.0001 else max(raw_peaks, default=1.0)
    return [round(min(1.0, peak / max(scale, 0.0001)), 3) for peak in raw_peaks]


def _normalize_cached_peaks(value: Any, point_count: int) -> list[float]:
    if not isinstance(value, list) or not value or len(value) > point_count:
        return []
    peaks = []
    for item in value:
        try:
            number = float(item)
        except (TypeError, ValueError):
            return []
        if not math.isfinite(number):
            return []
        peaks.append(round(min(max(number, 0.0), 1.0), 3))
    return peaks


def _file_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path.resolve()), "sizeBytes": stat.st_size, "mtimeNs": stat.st_mtime_ns}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)
