from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ai8video.media.ffmpeg_utils import resolve_ffmpeg_bin


VIDEO_TIMELINE_FILMSTRIP_FRAMES = 16


def video_filmstrip_payload(
    video_path: Path,
    review_dir: Path,
    review_id: str,
    duration_seconds: float,
    *,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    try:
        ensure_video_filmstrip(
            video_path,
            review_dir,
            duration_seconds,
            ffmpeg_bin=ffmpeg_bin,
        )
    except Exception as exc:
        return {
            "filmstripStatus": "failed",
            "filmstripUrl": "",
            "filmstripFrameCount": VIDEO_TIMELINE_FILMSTRIP_FRAMES,
            "filmstripReason": str(exc)[:200] or "视频缩略图读取失败",
        }
    return {
        "filmstripStatus": "ready",
        "filmstripUrl": f"/api/user-generated-results/video-timeline-filmstrip/{review_id}",
        "filmstripFrameCount": VIDEO_TIMELINE_FILMSTRIP_FRAMES,
    }


def ensure_video_filmstrip(
    video_path: Path,
    review_dir: Path,
    duration_seconds: float,
    *,
    ffmpeg_bin: str | None = None,
) -> Path:
    review_dir.mkdir(parents=True, exist_ok=True)
    target = review_dir / "filmstrip.jpg"
    meta_path = review_dir / "filmstrip.json"
    signature = _file_signature(video_path)
    meta = _load_json(meta_path)
    if target.is_file() and meta.get("sourceSignature") == signature:
        return target
    temporary = review_dir / "filmstrip.rendering.jpg"
    temporary.unlink(missing_ok=True)
    fps = VIDEO_TIMELINE_FILMSTRIP_FRAMES / max(float(duration_seconds), 0.001)
    video_filter = (
        f"fps={fps:.8f},scale=160:90:force_original_aspect_ratio=decrease,"
        f"pad=160:90:(ow-iw)/2:(oh-ih)/2,tile={VIDEO_TIMELINE_FILMSTRIP_FRAMES}x1"
    )
    command = [
        resolve_ffmpeg_bin(ffmpeg_bin),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        video_filter,
        "-frames:v",
        "1",
        "-q:v",
        "4",
        "-an",
        str(temporary),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=90)
        temporary.replace(target)
    except subprocess.CalledProcessError as exc:
        temporary.unlink(missing_ok=True)
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(message[-300:] or "视频缩略图生成失败") from exc
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    _write_json(meta_path, {"sourceSignature": signature, "frameCount": VIDEO_TIMELINE_FILMSTRIP_FRAMES})
    return target


def resolve_video_filmstrip(review_dir: Path) -> Path:
    target = review_dir / "filmstrip.jpg"
    if not target.is_file():
        raise FileNotFoundError("视频缩略图条不存在")
    return target


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
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
