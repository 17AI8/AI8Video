from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai8video.media.ffmpeg_utils import probe_media_duration_seconds, resolve_ffmpeg_bin
from ai8video.assets.upload_utils import resolve_upload_filename
from ai8video.core.paths import PROJECT_ROOT
from ai8video.assets.user_files import (
    USER_BACKGROUND_MUSIC_DIR,
    ensure_user_file_root,
)

DEFAULT_BACKGROUND_MUSIC_DIR = USER_BACKGROUND_MUSIC_DIR
BACKGROUND_MUSIC_EXTENSIONS = {".mp3"}
BACKGROUND_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
BACKGROUND_MUSIC_LIBRARY_DIR_NAME = "素材库"
BACKGROUND_MUSIC_SOURCE_DIR_NAME = "source"
BACKGROUND_MUSIC_ITEMS_NAME = "items.json"
DEFAULT_BACKGROUND_MUSIC_VOLUME = 0.28
DEFAULT_PRESERVE_ORIGINAL_AUDIO = True


def background_music_dir() -> Path:
    configured = os.getenv("AI8VIDEO_BACKGROUND_MUSIC_DIR")
    root = Path(configured) if configured else DEFAULT_BACKGROUND_MUSIC_DIR
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root.resolve()


def ensure_background_music_dir() -> Path:
    root = background_music_dir()
    root.mkdir(parents=True, exist_ok=True)
    if not os.getenv("AI8VIDEO_BACKGROUND_MUSIC_DIR"):
        ensure_user_file_root()
    return root


def background_music_path() -> Path:
    return background_music_dir() / "current.mp3"


def background_music_meta_path() -> Path:
    return background_music_dir() / "current.json"


def background_music_library_dir() -> Path:
    return background_music_dir() / BACKGROUND_MUSIC_LIBRARY_DIR_NAME


def background_music_source_dir() -> Path:
    return background_music_dir() / BACKGROUND_MUSIC_SOURCE_DIR_NAME


def background_music_items_path() -> Path:
    return background_music_dir() / BACKGROUND_MUSIC_ITEMS_NAME


def background_music_volume() -> float:
    return _clean_background_music_volume(_read_background_music_meta().get("volume"))


def preserve_original_audio_enabled() -> bool:
    return _clean_preserve_original_audio(_read_background_music_meta().get("preserveOriginalAudio"))


def background_music_status() -> dict[str, Any]:
    ensure_background_music_dir()
    _sync_background_music_items_from_folder()
    meta = _read_background_music_meta()
    items = _read_background_music_items()
    selected_id = str(meta.get("selectedId") or "")
    display_items = _display_background_music_items(items)
    selected = _find_background_music_item(selected_id, items) if selected_id else None
    selected_path = _selected_background_music_path(selected)
    volume = background_music_volume()
    preserve_original_audio = preserve_original_audio_enabled()
    if not selected_path:
        return {
            "ok": True,
            "enabled": False,
            "name": "",
            "selectedId": "",
            "volume": volume,
            "volumePercent": round(volume * 100),
            "preserveOriginalAudio": preserve_original_audio,
            "sizeBytes": 0,
            "updatedAt": "",
            "items": [_public_background_music_item(item, selected_id) for item in display_items],
        }
    stat = selected_path.stat()
    return {
        "ok": True,
        "enabled": True,
        "id": str(selected.get("id") if selected else selected_id),
        "selectedId": str(selected.get("id") if selected else selected_id),
        "name": str((selected or meta).get("name") or selected_path.name),
        "sourceType": str((selected or meta).get("sourceType") or "audio"),
        "sourceName": str((selected or meta).get("sourceName") or (selected or meta).get("name") or selected_path.name),
        "volume": volume,
        "volumePercent": round(volume * 100),
        "preserveOriginalAudio": preserve_original_audio,
        "sizeBytes": stat.st_size,
        "updatedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "items": [_public_background_music_item(item, selected_id) for item in display_items],
    }


def save_background_music_upload(upload: Any) -> dict[str, Any]:
    source_name = resolve_upload_filename(upload)
    if not source_name:
        raise ValueError("请选择 MP3 或视频文件")
    suffix = Path(source_name).suffix.lower()
    if suffix not in BACKGROUND_MUSIC_EXTENSIONS and suffix not in BACKGROUND_VIDEO_EXTENSIONS:
        raise ValueError("背景音乐只支持 MP3 或常见视频文件")
    root = ensure_background_music_dir()
    library = background_music_library_dir()
    source_dir = background_music_source_dir()
    library.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)
    item_id = _next_background_music_id(source_name)
    target = library / f"{item_id}.mp3"
    if suffix in BACKGROUND_MUSIC_EXTENSIONS:
        temp_target = library / f"{item_id}.uploading.mp3"
        if temp_target.exists():
            temp_target.unlink()
        upload.save(str(temp_target), overwrite=True)
        os.replace(temp_target, target)
        item = _upsert_background_music_item({
            "id": item_id,
            "name": source_name,
            "sourceName": source_name,
            "sourceType": "audio",
            "path": str(target),
        })
        return select_background_music(item["id"])
    source_target = source_dir / f"{item_id}{suffix}"
    temp_source = source_dir / f"{item_id}.uploading{suffix}"
    for candidate in (source_target, temp_source):
        if candidate.exists():
            candidate.unlink()
    upload.save(str(temp_source), overwrite=True)
    os.replace(temp_source, source_target)
    item = _upsert_background_music_item({
        "id": item_id,
        "name": source_name,
        "sourceName": source_name,
        "sourceType": "video",
        "path": str(source_target),
    })
    return select_background_music(item["id"])


def select_background_music(item_id: str) -> dict[str, Any]:
    ensure_background_music_dir()
    _sync_background_music_items_from_folder()
    clean_id = str(item_id or "").strip()
    if not clean_id:
        raise ValueError("请选择背景音乐")
    items = _read_background_music_items()
    item = _find_background_music_item(clean_id, items)
    if not item:
        raise ValueError("背景音乐不存在")
    source = Path(str(item.get("path") or ""))
    if not source.is_file():
        raise ValueError("背景音乐文件不存在")
    _write_background_music_meta({
        "selectedId": item["id"],
        "name": item.get("name") or source.name,
        "sourceName": item.get("sourceName") or item.get("name") or source.name,
        "sourceType": item.get("sourceType") or "audio",
        "volume": background_music_volume(),
        "preserveOriginalAudio": preserve_original_audio_enabled(),
    })
    return background_music_status()


def clear_background_music_selection() -> dict[str, Any]:
    ensure_background_music_dir()
    _sync_background_music_items_from_folder()
    volume = background_music_volume()
    for path in (background_music_path(), background_music_path().with_name("current.selecting.mp3")):
        if path.exists():
            path.unlink()
    _write_background_music_meta({
        "selectedId": "",
        "name": "",
        "sourceName": "",
        "sourceType": "",
        "volume": volume,
        "preserveOriginalAudio": preserve_original_audio_enabled(),
    })
    return background_music_status()


def update_background_music_volume(volume: int | float | str) -> dict[str, Any]:
    ensure_background_music_dir()
    meta = _read_background_music_meta()
    meta["volume"] = _clean_background_music_volume(volume)
    _write_background_music_meta(meta)
    return background_music_status()


def update_preserve_original_audio(value: Any) -> dict[str, Any]:
    ensure_background_music_dir()
    meta = _read_background_music_meta()
    meta["preserveOriginalAudio"] = _clean_preserve_original_audio(value)
    _write_background_music_meta(meta)
    return background_music_status()


def extract_background_music_from_video(
    video_path: Path | str,
    output_path: Path | str | None = None,
    *,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    source = Path(video_path)
    if not source.is_file():
        raise ValueError("视频文件不存在")
    target = Path(output_path) if output_path else background_music_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_name(f"{target.stem}.extracting.tmp{target.suffix or '.mp3'}")
    if temp_target.exists():
        temp_target.unlink()
    cmd = [
        resolve_ffmpeg_bin(ffmpeg_bin),
        "-y",
        "-i",
        str(source),
        "-vn",
        "-map",
        "0:a:0",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(temp_target),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        os.replace(temp_target, target)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found") from exc
    except subprocess.CalledProcessError as exc:
        if temp_target.exists():
            temp_target.unlink()
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(message[-500:] or "视频音频提取失败") from exc
    except Exception:
        if temp_target.exists():
            temp_target.unlink()
        raise
    return {
        "ok": True,
        "source": str(source),
        "path": str(target),
        "sizeBytes": target.stat().st_size,
    }


def mix_background_music(
    video_path: Path | str,
    music_path: Path | str | None = None,
    *,
    ffmpeg_bin: str | None = None,
    preserve_original_audio_override: bool | None = None,
    preserved_audio_volume_override: int | float | str | None = None,
) -> dict[str, Any]:
    video = Path(video_path)
    ensure_background_music_dir()
    music = Path(music_path) if music_path else _selected_background_music_path()
    preserve_original_audio = (
        preserve_original_audio_enabled()
        if preserve_original_audio_override is None
        else bool(preserve_original_audio_override)
    )
    if not video.is_file():
        return {"enabled": True, "status": "skipped", "reason": "video file missing"}
    if not music or not music.is_file():
        if preserve_original_audio:
            return {"enabled": False, "status": "skipped", "reason": "no background music"}
        return mute_original_audio(video, ffmpeg_bin=ffmpeg_bin)
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    volume = background_music_volume()
    volume_text = _format_volume_arg(volume)
    preserved_volume = (
        None
        if preserved_audio_volume_override is None
        else _clean_preserved_audio_volume(preserved_audio_volume_override)
    )
    preserved_volume_text = "" if preserved_volume is None else _format_volume_arg(preserved_volume)
    video_duration = probe_media_duration_seconds(video, ffmpeg_bin=ffmpeg)
    if not video_duration:
        return {"enabled": True, "status": "failed", "reason": "无法读取视频时长，未执行背景音乐混音"}
    temp_video = video.with_name(f"{video.stem}.with-music.tmp{video.suffix or '.mp4'}")
    if temp_video.exists():
        temp_video.unlink()
    if not preserve_original_audio:
        return _mix_background_music_only(video, music, volume, volume_text, temp_video, ffmpeg, video_duration)
    if preserved_volume is None:
        filter_complex = (
            f"[0:a:0]apad[orig];"
            f"[1:a:0]volume={volume_text}[bgm];"
            "[orig][bgm]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]"
        )
    else:
        filter_complex = (
            f"[0:a:0]volume={preserved_volume_text},apad[orig];"
            f"[1:a:0]volume={volume_text}[bgm];"
            "[orig][bgm]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]"
        )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-stream_loop",
        "-1",
        "-i",
        str(music),
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-t",
        f"{video_duration:.3f}",
        "-movflags",
        "+faststart",
        str(temp_video),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        os.replace(temp_video, video)
    except FileNotFoundError:
        return {"enabled": True, "status": "failed", "reason": "ffmpeg not found"}
    except subprocess.CalledProcessError as exc:
        if temp_video.exists():
            temp_video.unlink()
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        if _looks_like_missing_original_audio(message):
            fallback = [
                ffmpeg,
                "-y",
                "-i",
                str(video),
                "-stream_loop",
                "-1",
                "-i",
                str(music),
                "-filter_complex",
                f"[1:a:0]volume={volume_text}[aout]",
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-t",
                f"{video_duration:.3f}",
                "-movflags",
                "+faststart",
                str(temp_video),
            ]
            try:
                subprocess.run(fallback, check=True, capture_output=True, text=True)
                os.replace(temp_video, video)
            except Exception as fallback_exc:
                if temp_video.exists():
                    temp_video.unlink()
                return {"enabled": True, "status": "failed", "reason": str(fallback_exc)[-500:]}
            return {
                "enabled": True,
                "status": "mixed",
                "musicName": background_music_status().get("name") or music.name,
                "video": str(video),
                "originalAudio": "missing",
                "fallback": "background_music_only",
                "backgroundMusicVolume": volume,
                **({"preservedAudioVolume": preserved_volume} if preserved_volume is not None else {}),
            }
        return {"enabled": True, "status": "failed", "reason": message[-500:]}
    except Exception as exc:
        if temp_video.exists():
            temp_video.unlink()
        return {"enabled": True, "status": "failed", "reason": str(exc)}
    return {
        "enabled": True,
        "status": "mixed",
        "musicName": background_music_status().get("name") or music.name,
        "video": str(video),
        "originalAudio": "preserved",
        "backgroundMusicVolume": volume,
        **({"preservedAudioVolume": preserved_volume} if preserved_volume is not None else {}),
    }


def _looks_like_missing_original_audio(message: str) -> bool:
    text = str(message or "").lower()
    return (
        "matches no streams" in text
        or "stream specifier ':a" in text
        or "stream specifier a" in text
        or "0:a:0" in text and "not" in text and "match" in text
    )


def mute_original_audio(
    video_path: Path | str,
    *,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    video = Path(video_path)
    if not video.is_file():
        return {"enabled": True, "status": "skipped", "reason": "video file missing"}
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    temp_video = video.with_name(f"{video.stem}.muted.tmp{video.suffix or '.mp4'}")
    if temp_video.exists():
        temp_video.unlink()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-an",
        "-movflags",
        "+faststart",
        str(temp_video),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        os.replace(temp_video, video)
    except FileNotFoundError:
        return {"enabled": True, "status": "failed", "reason": "ffmpeg not found"}
    except subprocess.CalledProcessError as exc:
        if temp_video.exists():
            temp_video.unlink()
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        return {"enabled": True, "status": "failed", "reason": message[-500:]}
    except Exception as exc:
        if temp_video.exists():
            temp_video.unlink()
        return {"enabled": True, "status": "failed", "reason": str(exc)}
    return {
        "enabled": True,
        "status": "muted",
        "video": str(video),
        "originalAudio": "muted",
        "backgroundMusicVolume": 0,
    }


def _mix_background_music_only(
    video: Path,
    music: Path,
    volume: float,
    volume_text: str,
    temp_video: Path,
    ffmpeg: str,
    video_duration: float,
) -> dict[str, Any]:
    fallback = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-stream_loop",
        "-1",
        "-i",
        str(music),
        "-filter_complex",
        f"[1:a:0]volume={volume_text}[aout]",
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-t",
        f"{video_duration:.3f}",
        "-movflags",
        "+faststart",
        str(temp_video),
    ]
    try:
        subprocess.run(fallback, check=True, capture_output=True, text=True)
        os.replace(temp_video, video)
    except FileNotFoundError:
        return {"enabled": True, "status": "failed", "reason": "ffmpeg not found"}
    except subprocess.CalledProcessError as exc:
        if temp_video.exists():
            temp_video.unlink()
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        return {"enabled": True, "status": "failed", "reason": message[-500:]}
    except Exception as exc:
        if temp_video.exists():
            temp_video.unlink()
        return {"enabled": True, "status": "failed", "reason": str(exc)[-500:]}
    return {
        "enabled": True,
        "status": "mixed",
        "musicName": background_music_status().get("name") or music.name,
        "video": str(video),
        "originalAudio": "muted",
        "backgroundMusicVolume": volume,
    }


def file_meta(path: Path | str) -> dict[str, Any]:
    target = Path(path)
    sha = hashlib.sha256()
    size_bytes = 0
    with target.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            sha.update(chunk)
            size_bytes += len(chunk)
    return {"sha256": sha.hexdigest(), "size_bytes": size_bytes}


def _read_background_music_meta() -> dict[str, Any]:
    path = background_music_meta_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_background_music_meta(payload: dict[str, Any]) -> None:
    path = background_music_meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["volume"] = _clean_background_music_volume(data.get("volume"))
    data["preserveOriginalAudio"] = _clean_preserve_original_audio(data.get("preserveOriginalAudio"))
    data["updatedAt"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_background_music_volume(value: Any) -> float:
    try:
        number = float(str(value))
    except Exception:
        return DEFAULT_BACKGROUND_MUSIC_VOLUME
    if number > 1:
        number = number / 100
    return round(min(1.0, max(0.0, number)), 2)


def _clean_preserved_audio_volume(value: Any) -> float:
    try:
        number = float(str(value))
    except Exception:
        return 1.0
    if number > 100:
        number = number / 100
    return round(min(2.0, max(0.0, number)), 2)


def _clean_preserve_original_audio(value: Any) -> bool:
    if value is None:
        return DEFAULT_PRESERVE_ORIGINAL_AUDIO
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "off", "unchecked", "静音", "关闭"}:
        return False
    if text in {"1", "true", "yes", "on", "checked", "保留", "开启"}:
        return True
    return DEFAULT_PRESERVE_ORIGINAL_AUDIO


def _format_volume_arg(value: float) -> str:
    return f"{_clean_background_music_volume(value):.2f}".rstrip("0").rstrip(".")


def _read_background_music_items() -> list[dict[str, Any]]:
    path = background_music_items_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        path_value = str(item.get("path") or "").strip()
        if not item_id or not path_value:
            continue
        normalized.append(dict(item))
    return sorted(normalized, key=lambda item: str(item.get("updatedAt") or ""), reverse=True)


def _write_background_music_items(items: list[dict[str, Any]]) -> None:
    path = background_music_items_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"items": sorted(items, key=lambda item: str(item.get("updatedAt") or ""), reverse=True)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sync_background_music_items_from_folder() -> None:
    root = background_music_dir()
    library = background_music_library_dir()
    library.mkdir(parents=True, exist_ok=True)
    existing = _read_background_music_items()
    tracked_paths = {str(Path(str(item.get("path") or "")).resolve()) for item in existing}
    tracked_sources = {str(Path(str(item.get("sourcePath") or "")).resolve()) for item in existing if item.get("sourcePath")}
    discovered: list[dict[str, Any]] = []
    for folder in (library, root):
        for candidate in sorted(folder.iterdir(), key=lambda item: item.name.lower() if item.exists() else ""):
            if not candidate.is_file() or candidate.name.startswith("."):
                continue
            if _is_background_system_file(candidate):
                continue
            suffix = candidate.suffix.lower()
            if suffix not in BACKGROUND_MUSIC_EXTENSIONS and suffix not in BACKGROUND_VIDEO_EXTENSIONS:
                continue
            resolved = str(candidate.resolve())
            if resolved in tracked_paths or resolved in tracked_sources:
                continue
            item_id = _manual_background_music_id(candidate)
            if suffix in BACKGROUND_MUSIC_EXTENSIONS:
                discovered.append({
                    "id": item_id,
                    "name": candidate.name,
                    "sourceName": candidate.name,
                    "sourceType": "audio",
                    "path": str(candidate.resolve()),
                })
            else:
                discovered.append({
                    "id": item_id,
                    "name": f"{candidate.stem}.mp3",
                    "sourceName": candidate.name,
                    "sourceType": "video",
                    "sourcePath": str(candidate.resolve()),
                    "path": str(candidate.resolve()),
                })
    for item in discovered:
        _upsert_background_music_item(item)
    _dedupe_background_music_items()


def _upsert_background_music_item(item: dict[str, Any]) -> dict[str, Any]:
    items = _read_background_music_items()
    now = datetime.now(timezone.utc).isoformat()
    target = Path(str(item.get("path") or ""))
    data = {
        **item,
        "sizeBytes": target.stat().st_size if target.is_file() else int(item.get("sizeBytes") or 0),
        "createdAt": item.get("createdAt") or now,
        "updatedAt": now,
    }
    replaced = False
    next_items: list[dict[str, Any]] = []
    for existing in items:
        if str(existing.get("id") or "") == str(data.get("id") or ""):
            next_items.append({**existing, **data})
            replaced = True
        else:
            next_items.append(existing)
    if not replaced:
        next_items.append(data)
    _write_background_music_items(next_items)
    return data


def _dedupe_background_music_items() -> None:
    items = _read_background_music_items()
    if not items:
        return
    meta = _read_background_music_meta()
    selected_id = str(meta.get("selectedId") or "")
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "")
        if item_id == "legacy-current":
            passthrough.append(item)
            continue
        key = (
            str(item.get("sourceType") or "audio").lower(),
            str(item.get("name") or "").casefold(),
            str(item.get("sourceName") or item.get("name") or "").casefold(),
        )
        grouped.setdefault(key, []).append(item)

    next_items = list(passthrough)
    replacement_selected_id = ""
    for group in grouped.values():
        keep = _best_background_music_duplicate(group, selected_id)
        next_items.append(keep)
        if selected_id and any(str(item.get("id") or "") == selected_id for item in group):
            replacement_selected_id = str(keep.get("id") or "")

    if replacement_selected_id and replacement_selected_id != selected_id:
        meta["selectedId"] = replacement_selected_id
        selected_item = _find_background_music_item(replacement_selected_id, next_items)
        if selected_item:
            meta["name"] = selected_item.get("name") or meta.get("name")
            meta["sourceName"] = selected_item.get("sourceName") or meta.get("sourceName")
            meta["sourceType"] = selected_item.get("sourceType") or meta.get("sourceType")
        _write_background_music_meta(meta)

    if _background_music_items_signature(next_items) != _background_music_items_signature(items):
        _write_background_music_items(next_items)


def _best_background_music_duplicate(items: list[dict[str, Any]], selected_id: str = "") -> dict[str, Any]:
    def score(item: dict[str, Any]) -> tuple[int, int, str]:
        path = Path(str(item.get("path") or ""))
        source_path = Path(str(item.get("sourcePath") or ""))
        has_existing_path = path.is_file() or source_path.is_file()
        is_selected = bool(selected_id and str(item.get("id") or "") == selected_id)
        return (
            1 if has_existing_path else 0,
            1 if is_selected else 0,
            str(item.get("updatedAt") or ""),
        )

    return max(items, key=score)


def _background_music_items_signature(items: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    return sorted(
        (
            str(item.get("id") or ""),
            str(item.get("name") or ""),
            str(item.get("sourceName") or ""),
            str(item.get("path") or ""),
        )
        for item in items
    )


def _find_background_music_item(item_id: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    clean_id = str(item_id or "").strip()
    for item in items:
        if str(item.get("id") or "") == clean_id:
            return item
    return None


def _selected_background_music_path(item: dict[str, Any] | None = None) -> Path | None:
    if item is None:
        meta = _read_background_music_meta()
        selected_id = str(meta.get("selectedId") or "")
        if not selected_id:
            return None
        item = _find_background_music_item(selected_id, _read_background_music_items())
    if not item:
        return None
    path = Path(str(item.get("path") or ""))
    if path.is_file():
        return path
    return None


def _display_background_music_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if any(str(item.get("id") or "") != "legacy-current" for item in items):
        return [item for item in items if str(item.get("id") or "") != "legacy-current"]
    return items


def _public_background_music_item(item: dict[str, Any], selected_id: str = "") -> dict[str, Any]:
    item_id = str(item.get("id") or "")
    return {
        "id": item_id,
        "name": str(item.get("name") or "背景音乐"),
        "sourceType": str(item.get("sourceType") or "audio"),
        "sourceName": str(item.get("sourceName") or item.get("name") or ""),
        "sizeBytes": int(item.get("sizeBytes") or 0),
        "createdAt": str(item.get("createdAt") or ""),
        "updatedAt": str(item.get("updatedAt") or ""),
        "selected": bool(selected_id and item_id == selected_id),
    }


def _next_background_music_id(source_name: str) -> str:
    stem = _safe_background_music_stem(Path(source_name).stem or "bgm")
    prefix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    candidate = f"{prefix}-{stem}"[:88].rstrip("-")
    existing_ids = {str(item.get("id") or "") for item in _read_background_music_items()}
    if candidate not in existing_ids:
        return candidate
    index = 2
    while f"{candidate}-{index}" in existing_ids:
        index += 1
    return f"{candidate}-{index}"


def _safe_background_music_stem(value: str) -> str:
    clean = re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip(".-_")
    return clean or "bgm"


def _manual_background_music_id(path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"manual-{_safe_background_music_stem(path.stem)}-{digest}"


def _is_background_video_file(path: Path) -> bool:
    return path.suffix.lower() in BACKGROUND_VIDEO_EXTENSIONS


def _is_background_system_file(path: Path) -> bool:
    root = background_music_dir()
    system_names = {BACKGROUND_MUSIC_ITEMS_NAME, "current.json", "current.mp3", "current.selecting.mp3"}
    if path.parent.resolve() == root.resolve() and path.name in system_names:
        return True
    if path.parent.resolve() == root.resolve() and path.name.startswith("current."):
        return True
    if path.parent.resolve() == root.resolve() and path.stem == "source":
        return True
    return False
