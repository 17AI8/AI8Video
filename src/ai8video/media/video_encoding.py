from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import subprocess

VIDEO_POSTPROCESS_CODEC = "libx264"
VIDEO_POSTPROCESS_PRESET = "veryfast"
VIDEO_POSTPROCESS_CRF = "16"
VIDEO_POSTPROCESS_PIX_FMT = "yuv420p"
VIDEO_POSTPROCESS_VIDEOTOOLBOX_CODEC = "h264_videotoolbox"
VIDEO_POSTPROCESS_VIDEOTOOLBOX_BITRATE = "6M"


def video_postprocess_encoding_meta(ffmpeg_bin: str | None = None) -> dict[str, str]:
    codec = _resolve_video_postprocess_codec(ffmpeg_bin)
    if codec == VIDEO_POSTPROCESS_CODEC:
        return {
            "codec": codec,
            "preset": VIDEO_POSTPROCESS_PRESET,
            "crf": VIDEO_POSTPROCESS_CRF,
            "pixFmt": VIDEO_POSTPROCESS_PIX_FMT,
        }
    return {
        "codec": codec,
        "bitrate": VIDEO_POSTPROCESS_VIDEOTOOLBOX_BITRATE,
        "pixFmt": VIDEO_POSTPROCESS_PIX_FMT,
    }


def append_video_postprocess_encoding_args(
    cmd: list[str],
    *,
    include_pix_fmt: bool = True,
) -> dict[str, str]:
    meta = video_postprocess_encoding_meta(cmd[0] if cmd else None)
    cmd.extend(["-c:v", meta["codec"]])
    if meta["codec"] == VIDEO_POSTPROCESS_CODEC:
        cmd.extend([
            "-preset",
            VIDEO_POSTPROCESS_PRESET,
            "-crf",
            VIDEO_POSTPROCESS_CRF,
        ])
    else:
        cmd.extend(["-b:v", VIDEO_POSTPROCESS_VIDEOTOOLBOX_BITRATE])
    if include_pix_fmt:
        cmd.extend(["-pix_fmt", VIDEO_POSTPROCESS_PIX_FMT])
    return meta


def _resolve_video_postprocess_codec(ffmpeg_bin: str | None) -> str:
    configured = str(os.getenv("AI8VIDEO_VIDEO_POSTPROCESS_CODEC") or "").strip()
    encoders = _available_video_encoders(_resolve_probe_command(ffmpeg_bin))
    if configured:
        if encoders and configured not in encoders:
            raise RuntimeError(f"当前 FFmpeg 不支持配置的视频编码器：{configured}")
        if configured not in {VIDEO_POSTPROCESS_CODEC, VIDEO_POSTPROCESS_VIDEOTOOLBOX_CODEC}:
            raise RuntimeError(f"AI8video 暂不支持该视频编码器参数配置：{configured}")
        return configured
    if not encoders or VIDEO_POSTPROCESS_CODEC in encoders:
        return VIDEO_POSTPROCESS_CODEC
    if VIDEO_POSTPROCESS_VIDEOTOOLBOX_CODEC in encoders:
        return VIDEO_POSTPROCESS_VIDEOTOOLBOX_CODEC
    raise RuntimeError("当前 FFmpeg 缺少可用的 H.264 编码器（libx264 或 h264_videotoolbox）")


def _resolve_probe_command(ffmpeg_bin: str | None) -> str:
    value = str(ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return str(candidate)
    return shutil.which(value) or ""


@lru_cache(maxsize=8)
def _available_video_encoders(ffmpeg_bin: str) -> frozenset[str]:
    if not ffmpeg_bin:
        return frozenset()
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    return frozenset(re.findall(r"^\s*V\S*\s+(\S+)", output, flags=re.MULTILINE))
