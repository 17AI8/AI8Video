"""透明层与基础视频的媒体 runtime，独立于 HyperFrames Worker。"""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Callable

from ai8video.media.ffmpeg_utils import pixel_format_has_alpha
from ai8video.media.video_encoding import append_video_postprocess_encoding_args


def assert_transparent_layer(media: dict[str, Any] | None) -> None:
    if media is None or not (media.get("hasAlpha") or pixel_format_has_alpha(media.get("pixelFormat"))):
        raise RuntimeError("HyperFrames 输出不含透明视频流")


def composite_transparent_layer(
    source: Path,
    layer: Path,
    media: dict[str, Any],
    ffmpeg_bin: str,
    *,
    run: Callable[..., Any] = subprocess.run,
    before_replace: Callable[[], None] | None = None,
) -> None:
    target = source.with_name(f"{source.stem}.html-motion.tmp{source.suffix or '.mp4'}")
    width, height = int(media["width"]), int(media["height"])
    duration = float(media["durationSeconds"])
    filter_graph = (
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x00000000[overlay];"
        "[0:v][overlay]overlay=eof_action=pass:shortest=0:format=auto[v]"
    )
    command = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-c:v", "libvpx-vp9", "-i", str(layer),
        "-filter_complex", filter_graph, "-map", "[v]", "-map", "0:a?",
        "-t", f"{duration:.3f}",
    ]
    append_video_postprocess_encoding_args(command)
    command.extend(["-c:a", "copy", "-movflags", "+faststart", str(target)])
    try:
        run(command, check=True, capture_output=True, text=True, timeout=120)
        if not target.is_file() or target.stat().st_size <= 0:
            raise RuntimeError("HTML 动效叠加输出为空")
        if before_replace is not None:
            before_replace()
        target.replace(source)
    finally:
        target.unlink(missing_ok=True)


def validate_composited_video(
    media: dict[str, Any] | None,
    expected: dict[str, Any],
) -> list[str]:
    if not isinstance(media, dict):
        raise RuntimeError("FFmpeg candidate 无法读取媒体信息")
    errors: list[str] = []
    if int(media.get("width") or 0) != int(expected.get("width") or 0):
        errors.append("candidate 尺寸宽度不匹配")
    if int(media.get("height") or 0) != int(expected.get("height") or 0):
        errors.append("candidate 尺寸高度不匹配")
    expected_duration = float(expected.get("durationSeconds") or 0)
    actual_duration = float(media.get("durationSeconds") or 0)
    if expected_duration > 0 and actual_duration < expected_duration * 0.7:
        errors.append("candidate 时长少于基础视频的 70%")
    if errors:
        raise RuntimeError("；".join(errors))
    return errors
