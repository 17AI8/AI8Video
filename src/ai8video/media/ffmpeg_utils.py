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


def probe_media_metadata(
    media_path: Path | str,
    *,
    ffprobe_bin: str | None = None,
) -> dict[str, Any] | None:
    """读取时长/分辨率/帧率/编码/码率等媒体元数据。"""
    payload = _read_ffprobe_json(Path(media_path), ffprobe_bin=ffprobe_bin)
    if not payload:
        return None
    return _build_media_metadata(payload)


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
    data = _read_ffprobe_json(media_path, ffprobe_bin=ffprobe_bin)
    if not data:
        return None
    streams = data.get("streams") if isinstance(data, dict) else []
    stream = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), None)
    format_data = data.get("format") if isinstance(data, dict) else None
    duration = format_data.get("duration") if isinstance(format_data, dict) else None
    return _build_video_info(stream, duration)


def _read_ffprobe_json(
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
    return data if isinstance(data, dict) else None


def _build_media_metadata(data: dict[str, Any]) -> dict[str, Any] | None:
    streams = [item for item in list(data.get("streams") or []) if isinstance(item, dict)]
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    format_data = data.get("format") if isinstance(data.get("format"), dict) else {}
    if not video:
        return None
    try:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    duration = _safe_float(format_data.get("duration") or video.get("duration"))
    bitrate = int(_safe_float(format_data.get("bit_rate") or video.get("bit_rate")))
    fps = _parse_frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    display_width, display_height = _apply_rotation(width, height, video)
    return {
        "width": display_width,
        "height": display_height,
        "resolution": f"{display_width}×{display_height}",
        "aspectRatio": _aspect_ratio_label(display_width, display_height),
        "durationSeconds": duration if duration > 0 else None,
        "durationLabel": _format_duration_label(duration) if duration > 0 else "",
        "fps": fps,
        "fpsLabel": f"{fps:g} fps" if fps > 0 else "",
        "videoCodec": str(video.get("codec_name") or "").strip(),
        "audioCodec": str((audio or {}).get("codec_name") or "").strip(),
        "audioChannels": int(_safe_float((audio or {}).get("channels"))),
        "sampleRate": int(_safe_float((audio or {}).get("sample_rate"))),
        "bitrate": bitrate if bitrate > 0 else 0,
        "bitrateLabel": _format_bitrate_label(bitrate) if bitrate > 0 else "",
        "container": str(format_data.get("format_name") or "").split(",")[0].strip(),
        "pixelFormat": str(video.get("pix_fmt") or "").strip(),
        "hasAlpha": pixel_format_has_alpha(video.get("pix_fmt")),
    }


def _parse_frame_rate(value: object) -> float:
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return 0.0
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            numerator = float(left)
            denominator = float(right)
        except ValueError:
            return 0.0
        return round(numerator / denominator, 3) if denominator else 0.0
    try:
        return round(float(text), 3)
    except ValueError:
        return 0.0


def _apply_rotation(width: int, height: int, stream: dict[str, Any]) -> tuple[int, int]:
    rotation = 0.0
    try:
        rotation = abs(float(stream.get("rotation") or 0))
    except (TypeError, ValueError):
        rotation = 0.0
    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
    if not rotation:
        try:
            rotation = abs(float(tags.get("rotate") or 0))
        except (TypeError, ValueError):
            rotation = 0.0
    if int(rotation) % 180 == 90:
        return height, width
    return width, height


def _aspect_ratio_label(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return ""
    ratio = width / height
    presets = (
        (16 / 9, "16:9"),
        (9 / 16, "9:16"),
        (1, "1:1"),
        (4 / 3, "4:3"),
        (3 / 4, "3:4"),
        (21 / 9, "21:9"),
    )
    for target, label in presets:
        if abs(ratio - target) < 0.03:
            return label
    return f"{width}:{height}"


def _format_duration_label(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_bitrate_label(bitrate: int) -> str:
    if bitrate >= 1_000_000:
        return f"{bitrate / 1_000_000:.2f} Mbps"
    if bitrate >= 1_000:
        return f"{bitrate / 1_000:.0f} kbps"
    return f"{bitrate} bps"


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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
