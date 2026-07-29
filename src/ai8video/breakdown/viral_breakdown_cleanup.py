from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ai8video.breakdown import viral_breakdown as vb


MAX_DELETE_VIDEO_COUNT = 200


def delete_viral_breakdown_videos(video_keys: object) -> dict[str, Any]:
    keys = _normalize_video_keys(video_keys)
    resolved: dict[str, Path] = {}
    for key in keys:
        video_path, relative_key = vb.resolve_viral_breakdown_video_path(key)
        if video_path.parent != vb.VIRAL_BREAKDOWN_SOURCE_VIDEO_DIR.resolve():
            raise ValueError("videoKey must point to the source video directory")
        resolved.setdefault(relative_key, video_path)
    plans = [
        _build_delete_plan(video_path, relative_key)
        for relative_key, video_path in resolved.items()
    ]
    results = [_execute_delete_plan(plan) for plan in plans]
    return {
        "ok": True,
        "deletedCount": len(results),
        "deletedBytes": sum(int(item["deletedBytes"]) for item in results),
        "items": results,
    }


def _normalize_video_keys(video_keys: object) -> list[str]:
    if not isinstance(video_keys, list):
        raise ValueError("videoKeys must be an array")
    keys = [str(item or "").strip() for item in video_keys if str(item or "").strip()]
    if not keys:
        raise ValueError("videoKeys is required")
    if len(keys) > MAX_DELETE_VIDEO_COUNT:
        raise ValueError(f"videoKeys supports at most {MAX_DELETE_VIDEO_COUNT} items")
    return list(dict.fromkeys(keys))


def _build_delete_plan(video_path: Path, relative_key: str) -> dict[str, Any]:
    stem = video_path.stem
    if stem in {"", ".", ".."}:
        raise ValueError("source video filename is unsafe")
    draft_path = vb.VIRAL_BREAKDOWN_SCRIPT_DRAFT_DIR / f"{stem}.json"
    draft = vb._read_json(draft_path)
    targets = _private_targets(video_path, draft_path)
    for target, root in targets:
        _validate_delete_target(target, root)
    return {
        "videoPath": video_path,
        "videoKey": relative_key,
        "name": video_path.name,
        "stem": stem,
        "targets": targets,
        "scriptRelativePath": _owned_script_relative_path(draft, relative_key),
    }


def _private_targets(video_path: Path, draft_path: Path) -> list[tuple[Path, Path]]:
    stem = video_path.stem
    targets = [
        (vb.VIRAL_BREAKDOWN_FRAME_DIR / stem, vb.VIRAL_BREAKDOWN_FRAME_DIR),
        (vb.VIRAL_BREAKDOWN_TRANSCRIPT_DIR / f"{stem}.json", vb.VIRAL_BREAKDOWN_TRANSCRIPT_DIR),
        (vb.VIRAL_BREAKDOWN_TRANSCRIPT_DIR / f"{stem}.txt", vb.VIRAL_BREAKDOWN_TRANSCRIPT_DIR),
        (vb.VIRAL_BREAKDOWN_TRANSCRIPT_AUDIO_DIR / stem, vb.VIRAL_BREAKDOWN_TRANSCRIPT_AUDIO_DIR),
        (vb.VIRAL_BREAKDOWN_SHOT_LANGUAGE_DIR / f"{stem}.json", vb.VIRAL_BREAKDOWN_SHOT_LANGUAGE_DIR),
        (draft_path, vb.VIRAL_BREAKDOWN_SCRIPT_DRAFT_DIR),
        (vb.VIRAL_BREAKDOWN_GENERATE_SESSION_DIR / f"{stem}.json", vb.VIRAL_BREAKDOWN_GENERATE_SESSION_DIR),
    ]
    targets.extend((path, vb.VIRAL_BREAKDOWN_GRID_DIR) for path in vb.VIRAL_BREAKDOWN_GRID_DIR.glob(f"{stem}-*.jpg"))
    targets.extend(
        (vb.VIRAL_BREAKDOWN_GENERATED_VIDEO_DIR / f"{stem}{suffix}", vb.VIRAL_BREAKDOWN_GENERATED_VIDEO_DIR)
        for suffix in vb.SUPPORTED_VIRAL_BREAKDOWN_VIDEO_EXTENSIONS
    )
    targets.append((video_path, vb.VIRAL_BREAKDOWN_SOURCE_VIDEO_DIR))
    return targets


def _validate_delete_target(target: Path, root: Path) -> None:
    resolved_root = root.resolve()
    resolved_target = target.resolve(strict=False)
    if resolved_target == resolved_root or not vb._is_within(resolved_root, resolved_target):
        raise ValueError("refusing to delete outside the owned viral breakdown directory")


def _owned_script_relative_path(draft: dict[str, Any], video_key: str) -> str:
    from ai8video.assets.user_materials import SCRIPT_MATERIAL_EXTENSIONS

    if not draft.get("saved") or str(draft.get("videoKey") or "") != video_key:
        return ""
    relative_path = str(draft.get("relativePath") or "").strip()
    candidate = Path(relative_path)
    if (
        not relative_path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.suffix.lower() not in SCRIPT_MATERIAL_EXTENSIONS
    ):
        return ""
    return relative_path


def _execute_delete_plan(plan: dict[str, Any]) -> dict[str, Any]:
    deleted_files = 0
    deleted_bytes = 0
    for target, root in plan["targets"]:
        file_count, byte_count = _delete_owned_path(target, root)
        deleted_files += file_count
        deleted_bytes += byte_count
    material_result = _delete_owned_materials(plan["stem"], plan["scriptRelativePath"])
    vb._MEDIA_METADATA_CACHE.pop(str(plan["videoPath"].resolve()), None)
    return {
        "name": plan["name"],
        "videoKey": plan["videoKey"],
        "deletedFiles": deleted_files + int(material_result["deletedFiles"]),
        "deletedBytes": deleted_bytes + int(material_result["deletedBytes"]),
        "knowledgeIndex": material_result.get("knowledgeIndex"),
    }


def _delete_owned_path(target: Path, root: Path) -> tuple[int, int]:
    _validate_delete_target(target, root)
    if not target.exists() and not target.is_symlink():
        return 0, 0
    if target.is_symlink() or target.is_file():
        size = target.lstat().st_size
        target.unlink()
        return 1, size
    files = [path for path in target.rglob("*") if path.is_file() or path.is_symlink()]
    size = sum(path.lstat().st_size for path in files)
    shutil.rmtree(target)
    return max(1, len(files)), size


def _delete_owned_materials(video_stem: str, script_relative_path: str) -> dict[str, Any]:
    from ai8video.assets import user_materials
    from ai8video.knowledge.script_knowledge import remove_script_knowledge_document

    deleted_files = 0
    deleted_bytes = 0
    grid_copy = user_materials.USER_IMAGE_MATERIAL_DIR / f"viral-bd-{video_stem}-grid.jpg"
    file_count, byte_count = _delete_owned_path(grid_copy, user_materials.USER_IMAGE_MATERIAL_DIR)
    deleted_files += file_count
    deleted_bytes += byte_count
    knowledge_index = None
    if script_relative_path:
        script_path = user_materials.USER_SCRIPT_MATERIAL_DIR / script_relative_path
        file_count, byte_count = _delete_owned_path(script_path, user_materials.USER_SCRIPT_MATERIAL_DIR)
        deleted_files += file_count
        deleted_bytes += byte_count
        try:
            knowledge_index = remove_script_knowledge_document(script_relative_path)
        except Exception as exc:
            knowledge_index = {"ok": False, "removed": False, "error": str(exc)}
    return {
        "deletedFiles": deleted_files,
        "deletedBytes": deleted_bytes,
        "knowledgeIndex": knowledge_index,
    }
