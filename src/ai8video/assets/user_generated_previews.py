from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from ai8video.media.ffmpeg_utils import resolve_ffmpeg_bin

PREVIEW_DIR_NAME = "preview"


def preview_key_for_video(video_relative_key: str) -> str:
    rel_path = Path(str(video_relative_key or "").strip().lstrip("/"))
    parts = list(rel_path.parts)
    stem = rel_path.stem or "preview"
    if "video" in parts:
        video_index = parts.index("video")
        return (Path(*parts[:video_index], PREVIEW_DIR_NAME) / f"{stem}.jpg").as_posix()
    return (Path(PREVIEW_DIR_NAME) / f"{stem}.jpg").as_posix()


def find_preview_key(root: Path, video_relative_key: str) -> str:
    key = preview_key_for_video(video_relative_key)
    return key if (root / key).is_file() else ""


def delete_preview_for_video(root: Path, video_relative_key: str) -> str:
    key = preview_key_for_video(video_relative_key)
    target = (root / key).resolve()
    root = root.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return ""
    if target.is_file():
        target.unlink()
        return key
    return ""


def generate_preview_for_video(
    video_path: Path,
    root: Path,
    video_relative_key: str,
    *,
    ffmpeg_bin: str | None = None,
) -> dict:
    root = root.resolve()
    source = video_path.resolve()
    preview_key = preview_key_for_video(video_relative_key)
    target = (root / preview_key).resolve()
    try:
        source.relative_to(root)
        target.relative_to(root)
    except ValueError:
        return {"ok": False, "previewKey": preview_key, "error": "path outside generated results"}
    if not source.is_file():
        return {"ok": False, "previewKey": preview_key, "error": "video missing"}
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_name(f"{target.stem}.generating{target.suffix}")
    if temp_target.exists():
        temp_target.unlink()
    cmd = [
        resolve_ffmpeg_bin(ffmpeg_bin),
        "-y",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(temp_target),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        os.replace(temp_target, target)
    except Exception as exc:
        if temp_target.exists():
            temp_target.unlink()
        return {"ok": False, "previewKey": preview_key, "error": str(exc)[-500:]}
    return {
        "ok": True,
        "previewKey": preview_key,
        "path": str(target),
        "sizeBytes": target.stat().st_size,
    }


def regenerate_previews_for_videos(root: Path, video_extensions: Iterable[str]) -> dict:
    root = root.resolve()
    preview_root = root / PREVIEW_DIR_NAME
    if preview_root.exists():
        shutil.rmtree(preview_root)
    preview_root.mkdir(parents=True, exist_ok=True)
    extensions = {str(item).lower() for item in video_extensions}
    videos = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in extensions
        and PREVIEW_DIR_NAME not in path.relative_to(root).parts
    ]
    generated = 0
    failed: list[dict] = []
    for video in sorted(videos):
        relative_key = video.relative_to(root).as_posix()
        result = generate_preview_for_video(video, root, relative_key)
        if result.get("ok"):
            generated += 1
        else:
            failed.append(result)
    return {
        "ok": True,
        "videoCount": len(videos),
        "generatedCount": generated,
        "failedCount": len(failed),
        "failed": failed[:20],
        "previewDir": str(preview_root),
    }
