from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import requests

from ai8video.media.ffmpeg_utils import resolve_ffmpeg_bin
from ai8video.core.models import QuickVideoJob
from ai8video.media.video_encoding import append_video_postprocess_encoding_args, video_postprocess_encoding_meta


def materialize_segment_video(
    job: QuickVideoJob,
    work_dir: Path,
    *,
    name: str,
    dry_run: bool = False,
    duration_seconds: int = 1,
    timeout_seconds: int = 180,
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / f"{name}.mp4"
    local_path = Path(str(job.local_video_path or "")).expanduser()
    if local_path.is_file():
        shutil.copy2(local_path, target)
        return target

    url = str(job.video_url or "").strip()
    if url and "example.invalid" not in url:
        return download_video_to_path(url, target, timeout_seconds=timeout_seconds)

    if dry_run:
        return create_placeholder_video(target, duration_seconds=max(1, min(int(duration_seconds or 1), 2)))

    raise RuntimeError(f"片段 {job.episode_index} 没有可下载的视频结果")


def download_video_to_path(url: str, target: Path, *, timeout_seconds: int = 180) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(f"{target.suffix}.download")
    try:
        with requests.get(url, stream=True, timeout=timeout_seconds) as response:
            response.raise_for_status()
            with temp_target.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        fh.write(chunk)
        os.replace(temp_target, target)
    except Exception:
        if temp_target.exists():
            temp_target.unlink()
        raise
    return target


def extract_tail_frame(video_path: Path, output_path: Path | None = None, *, ffmpeg_bin: str | None = None) -> Path:
    source = Path(video_path)
    if not source.is_file():
        raise RuntimeError("提取尾帧失败：片段视频不存在")
    requested_target = output_path or source.with_suffix(".tail.png")
    target = requested_target.with_suffix(".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        resolve_ffmpeg_bin(ffmpeg_bin),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-sseof",
        "-0.08",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-update",
        "1",
        "-pix_fmt",
        "rgb24",
        str(target),
    ]
    _run_ffmpeg(cmd, "提取尾帧失败")
    if not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("提取尾帧失败：输出图片为空")
    return target


def extract_frame_at_time(
    video_path: Path,
    output_path: Path,
    *,
    time_seconds: float,
    ffmpeg_bin: str | None = None,
) -> Path:
    source = Path(video_path)
    timestamp = float(time_seconds or 0)
    if not source.is_file():
        raise RuntimeError("保存延长截帧失败：原视频不存在")
    if not math.isfinite(timestamp) or timestamp < 0:
        raise RuntimeError("保存延长截帧失败：时间点无效")
    target = Path(output_path).with_suffix(".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        resolve_ffmpeg_bin(ffmpeg_bin), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-ss", f"{timestamp:.3f}", "-frames:v", "1",
        "-update", "1", "-pix_fmt", "rgb24", str(target),
    ]
    _run_ffmpeg(cmd, "保存延长截帧失败")
    if not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("保存延长截帧失败：输出图片为空")
    return target


def trim_video_end(
    video_path: Path,
    output_path: Path,
    *,
    end_seconds: float,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    source = Path(video_path)
    if not source.is_file():
        raise RuntimeError("截取视频失败：原视频不存在")
    duration = float(end_seconds or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("截取视频失败：截图时间必须大于 0 秒")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        resolve_ffmpeg_bin(ffmpeg_bin), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-t", f"{duration:.3f}",
    ]
    append_video_postprocess_encoding_args(cmd)
    cmd.extend(["-c:a", "aac", "-movflags", "+faststart", str(target)])
    _run_ffmpeg(cmd, "按截图位置截取视频失败")
    if not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("截取视频失败：输出视频为空")
    return {
        "status": "trimmed",
        "outputPath": str(target),
        "endSeconds": round(duration, 3),
        "videoEncoding": video_postprocess_encoding_meta(),
    }


def concat_videos(video_paths: list[Path], output_path: Path, *, ffmpeg_bin: str | None = None) -> dict[str, Any]:
    if len(video_paths) < 2:
        raise RuntimeError("合并视频失败：至少需要两个片段")
    for item in video_paths:
        if not Path(item).is_file():
            raise RuntimeError(f"合并视频失败：片段不存在 {item}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    list_path = output_path.with_suffix(".concat.txt")
    list_path.write_text(
        "".join(f"file '{_escape_concat_path(Path(item))}'\n" for item in video_paths),
        encoding="utf-8",
    )
    copy_cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        _run_ffmpeg(copy_cmd, "concat copy 合并失败")
        method = "concat-copy"
    except RuntimeError as copy_exc:
        if output_path.exists():
            output_path.unlink()
        reencode_cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
        ]
        append_video_postprocess_encoding_args(reencode_cmd)
        reencode_cmd.extend([
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_path),
        ])
        _run_ffmpeg(reencode_cmd, f"重编码合并失败；copy 错误：{copy_exc}")
        method = "reencode"
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("合并视频失败：输出视频为空")
    return {
        "status": "merged",
        "method": method,
        "outputPath": str(output_path),
        "segments": [str(item) for item in video_paths],
        "videoEncoding": None if method == "concat-copy" else video_postprocess_encoding_meta(),
    }


def create_placeholder_video(target: Path, *, duration_seconds: int = 1, ffmpeg_bin: str | None = None) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        resolve_ffmpeg_bin(ffmpeg_bin),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=720x1280:r=24",
        "-t",
        str(max(1, int(duration_seconds or 1))),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(target),
    ]
    _run_ffmpeg(cmd, "创建 dry-run 占位视频失败")
    return target


def _run_ffmpeg(cmd: list[str], message: str) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=240)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or exc.__class__.__name__
        raise RuntimeError(f"{message}：{detail}") from exc
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        raise RuntimeError(f"{message}：{detail}") from exc


def _escape_concat_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")
