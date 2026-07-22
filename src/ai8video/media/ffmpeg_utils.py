from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def local_ffmpeg_candidates() -> list[Path]:
    exe_name = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    return [
        Path.home() / ".local" / "bin" / exe_name,
        Path.home() / ".local" / "ai8-ffmpeg-7.1" / "bin" / exe_name,
        Path("/opt/homebrew/bin") / exe_name,
        Path("/usr/local/bin") / exe_name,
    ]


def release_macos_quarantine(path: Path) -> None:
    if sys.platform != "darwin" or not path.exists():
        return
    for attr_name in ("com.apple.quarantine", "com.apple.provenance"):
        try:
            subprocess.run(
                ["xattr", "-dr", attr_name, str(path)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass


def resolve_ffmpeg_bin(preferred: str | None = None) -> str:
    if preferred:
        return preferred
    configured = os.getenv("AI8VIDEO_FFMPEG_BIN")
    if configured:
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in local_ffmpeg_candidates():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "ffmpeg"


def resolve_ffprobe_bin(preferred: str | None = None) -> str:
    if preferred:
        return preferred
    configured = os.getenv("AI8VIDEO_FFPROBE_BIN")
    if configured:
        return configured
    ffmpeg = Path(resolve_ffmpeg_bin())
    exe_name = "ffprobe.exe" if sys.platform.startswith("win") else "ffprobe"
    if ffmpeg.name.lower().startswith("ffmpeg") and ffmpeg.parent.exists():
        sibling = ffmpeg.with_name(exe_name)
        if sibling.is_file():
            return str(sibling)
    found = shutil.which("ffprobe")
    return found or "ffprobe"


def probe_media_video_info(
    media_path: Path | str,
    *,
    ffprobe_bin: str | None = None,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any] | None:
    target = Path(media_path)
    info = _probe_media_video_info_with_ffprobe(target, ffprobe_bin=ffprobe_bin)
    if info is not None and info.get("durationSeconds") is not None:
        return info
    fallback = _probe_media_video_info_with_ffmpeg(target, ffmpeg_bin=ffmpeg_bin)
    return fallback or info


def pixel_format_has_alpha(pixel_format: object) -> bool:
    value = str(pixel_format or "").lower()
    return value.startswith(("yuva", "gbrap", "rgba", "argb", "abgr", "bgra", "ayuv")) or bool(
        re.fullmatch(r"ya\d+(?:le|be)?", value)
    )


def _probe_media_video_info_with_ffprobe(
    media_path: Path,
    *,
    ffprobe_bin: str | None = None,
) -> dict[str, Any] | None:
    cmd = [
        resolve_ffprobe_bin(ffprobe_bin),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(media_path),
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=15)
        data = json.loads(proc.stdout or "{}")
    except Exception:
        return None
    streams = data.get("streams") if isinstance(data, dict) else []
    stream = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), None)
    format_data = data.get("format") if isinstance(data, dict) else None
    duration = format_data.get("duration") if isinstance(format_data, dict) else None
    return _build_video_info(stream, duration)


def _probe_media_video_info_with_ffmpeg(
    media_path: Path,
    *,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any] | None:
    cmd = [resolve_ffmpeg_bin(ffmpeg_bin), "-hide_banner", "-i", str(media_path)]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    return _parse_ffmpeg_video_info(f"{proc.stderr or ''}\n{proc.stdout or ''}")


def _parse_ffmpeg_video_info(output: str) -> dict[str, Any] | None:
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    duration = None if duration_match is None else (
        int(duration_match.group(1)) * 3600
        + int(duration_match.group(2)) * 60
        + float(duration_match.group(3))
    )
    for line in output.splitlines():
        if "Video:" not in line:
            continue
        size = re.search(r"(\d{2,5})x(\d{2,5})", line)
        if size is None:
            continue
        pixel_format = _pixel_format_from_ffmpeg_line(line)
        return _build_video_info(
            {
                "width": size.group(1),
                "height": size.group(2),
                "pix_fmt": pixel_format,
                "tags": {"alpha_mode": _alpha_mode_from_ffmpeg_output(output)},
            },
            duration,
        )
    return None


def _pixel_format_from_ffmpeg_line(line: str) -> str:
    match = re.search(
        r"\b(?:yuv(?:j|a)?\d{3}p(?:\d+(?:le|be))?|gbrap\w*|rgba\w*|argb\w*|abgr\w*|bgra\w*|ayuv\w*|ya\d+\w*)\b",
        line,
        flags=re.IGNORECASE,
    )
    return "" if match is None else match.group(0)


def _alpha_mode_from_ffmpeg_output(output: str) -> str:
    match = re.search(r"^\s*ALPHA_MODE\s*:\s*(\S+)", output, flags=re.IGNORECASE | re.MULTILINE)
    return "" if match is None else match.group(1)


def _build_video_info(stream: dict[str, Any] | None, duration_value: object) -> dict[str, Any] | None:
    try:
        width = int((stream or {}).get("width") or 0)
        height = int((stream or {}).get("height") or 0)
        duration = float(duration_value or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    tags = (stream or {}).get("tags")
    return {
        "width": width,
        "height": height,
        "durationSeconds": duration if duration > 0 else None,
        "pixelFormat": str((stream or {}).get("pix_fmt") or ""),
        "hasAlpha": pixel_format_has_alpha((stream or {}).get("pix_fmt"))
        or _alpha_mode_enabled(tags),
    }


def _alpha_mode_enabled(tags: object) -> bool:
    if not isinstance(tags, dict):
        return False
    return any(
        str(key).lower() == "alpha_mode" and str(value).lower() in {"1", "true"}
        for key, value in tags.items()
    )


def probe_media_duration_seconds(
    media_path: Path | str,
    *,
    ffprobe_bin: str | None = None,
    ffmpeg_bin: str | None = None,
) -> float | None:
    target = Path(media_path)
    duration = _probe_media_duration_with_ffprobe(target, ffprobe_bin=ffprobe_bin)
    if duration and duration > 0:
        return duration
    return _probe_media_duration_with_ffmpeg(target, ffmpeg_bin=ffmpeg_bin)


def _probe_media_duration_with_ffprobe(
    media_path: Path,
    *,
    ffprobe_bin: str | None = None,
) -> float | None:
    cmd = [
        resolve_ffprobe_bin(ffprobe_bin),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(media_path),
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=15)
        data = json.loads(proc.stdout or "{}")
        duration = float((data.get("format") or {}).get("duration") or 0)
    except Exception:
        return None
    return duration if duration > 0 else None


def _probe_media_duration_with_ffmpeg(
    media_path: Path,
    *,
    ffmpeg_bin: str | None = None,
) -> float | None:
    cmd = [
        resolve_ffmpeg_bin(ffmpeg_bin),
        "-hide_banner",
        "-i",
        str(media_path),
    ]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    output = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    duration = hours * 3600 + minutes * 60 + seconds
    return duration if duration > 0 else None
