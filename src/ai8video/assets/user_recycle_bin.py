from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ai8video.generation.business_prompt import sanitize_internal_fidelity_notes
from ai8video.core.legacy_payload import normalize_legacy_video_payload
from ai8video.core.models import VideoPrompt, QuickVideoJob
from ai8video.assets.user_files import USER_RECYCLE_BIN_ROOT, ensure_user_file_root
from ai8video.assets.user_generated_results import ensure_user_generated_result_dir
from ai8video.assets.user_generated_previews import generate_preview_for_video, preview_key_for_video


RECYCLE_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
RECYCLE_MANIFEST_NAME = "manifest.json"
RESTORED_RESULT_METADATA_DIR = ".restored-meta"


def ensure_user_recycle_bin_dir() -> Path:
    ensure_user_file_root()
    USER_RECYCLE_BIN_ROOT.mkdir(parents=True, exist_ok=True)
    return USER_RECYCLE_BIN_ROOT


def save_failed_video_task(
    *,
    video: VideoPrompt,
    job: QuickVideoJob | None = None,
    reason: str,
    videos: Iterable[Path | str],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    existing_videos = [Path(item) for item in videos if Path(item).is_file()]
    if not existing_videos:
        return None

    root = ensure_user_recycle_bin_dir()
    created_at = datetime.now(timezone.utc)
    job_id = str(getattr(job, "job_id", "") or f"video-{video.index}").strip()
    title = sanitize_internal_fidelity_notes(video.title or f"第 {video.index} 条")
    folder_name = _unique_folder_name(
        root,
        f"{created_at.strftime('%Y%m%d-%H%M%S')}-{video.index:02d}-{_slugify(title)}-{_slugify(job_id)}",
    )
    folder = root / folder_name
    video_dir = folder / "video"
    video_dir.mkdir(parents=True, exist_ok=True)

    copied = []
    for index, source in enumerate(existing_videos, 1):
        suffix = source.suffix.lower() if source.suffix.lower() in RECYCLE_VIDEO_EXTENSIONS else ".mp4"
        target = video_dir / f"{index:02d}-{_slugify(source.stem) or 'video'}{suffix}"
        target = _unique_file_path(target)
        shutil.copy2(source, target)
        stat = target.stat()
        copied_video = {
            "name": target.name,
            "relativePath": target.relative_to(root).as_posix(),
            "sizeBytes": stat.st_size,
            "updatedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }
        preview = generate_preview_for_video(target, root, copied_video["relativePath"])
        if preview.get("ok"):
            copied_video["previewRelativePath"] = preview["previewKey"]
        copied.append(copied_video)

    manifest = {
        "createdAt": created_at.isoformat(),
        "videoIndex": video.index,
        "videoTitle": title,
        "jobId": job_id,
        "reason": str(reason or "任务失败").strip()[:1000],
        "videos": copied,
        "meta": meta or {},
    }
    (folder / RECYCLE_MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "folder": folder.relative_to(root).as_posix(),
        "path": str(folder),
        "videoCount": len(copied),
        "manifest": manifest,
    }


def list_failed_video_tasks(limit: int = 50) -> dict[str, Any]:
    root = ensure_user_recycle_bin_dir()
    items: list[dict[str, Any]] = []
    for manifest_path in root.rglob(RECYCLE_MANIFEST_NAME):
        if not manifest_path.is_file():
            continue
        try:
            manifest = normalize_legacy_video_payload(json.loads(manifest_path.read_text(encoding="utf-8")))
        except Exception:
            manifest = {}
        if not isinstance(manifest, dict):
            manifest = {}
        folder = manifest_path.parent
        videos = _manifest_videos(root, folder, manifest)
        if not videos:
            continue
        created_at = str(manifest.get("createdAt") or datetime.fromtimestamp(folder.stat().st_mtime, tz=timezone.utc).isoformat())
        raw_reason = str(manifest.get("reason") or "任务失败").strip()
        items.append({
            "createdAt": created_at,
            "folder": folder.relative_to(root).as_posix(),
            "localPath": str(folder.resolve()),
            "videoIndex": manifest.get("videoIndex"),
            "videoTitle": sanitize_internal_fidelity_notes(manifest.get("videoTitle") or folder.name),
            "jobId": manifest.get("jobId") or "",
            "reason": raw_reason,
            "displayReason": humanize_failed_video_reason(raw_reason),
            "videoCount": len(videos),
            "videos": videos,
            "meta": manifest.get("meta") if isinstance(manifest.get("meta"), dict) else {},
        })
    items.sort(key=lambda item: (str(item.get("createdAt") or ""), str(item.get("folder") or "")), reverse=True)
    bounded_limit = max(1, min(200, int(limit or 50)))
    return {
        "root": str(root),
        "count": len(items),
        "items": items[:bounded_limit],
    }


def delete_failed_video_tasks(folders: Iterable[str]) -> dict[str, Any]:
    root = ensure_user_recycle_bin_dir().resolve()
    requested_folders = list(dict.fromkeys(str(folder or "").strip() for folder in folders))
    requested_folders = [folder for folder in requested_folders if folder]
    if not requested_folders:
        raise ValueError("请选择要删除的回收站任务")
    if len(requested_folders) > 200:
        raise ValueError("单次最多删除 200 个回收站任务")

    targets = [_resolve_recycle_task_folder(root, folder) for folder in requested_folders]
    deleted_folders: list[str] = []
    for relative_folder, target in targets:
        if not target.exists():
            continue
        shutil.rmtree(target)
        deleted_folders.append(relative_folder)
    return {
        "ok": True,
        "deletedCount": len(deleted_folders),
        "deletedFolders": deleted_folders,
    }


def restore_failed_video_task(folder: str) -> dict[str, Any]:
    recycle_root = ensure_user_recycle_bin_dir().resolve()
    relative_folder, task_folder = _resolve_recycle_task_folder(recycle_root, folder)
    if not task_folder.is_dir():
        raise FileNotFoundError("回收站任务不存在")
    source_videos = _task_video_files(task_folder)
    if not source_videos:
        raise ValueError("回收站任务中没有可恢复的视频")

    result_root = ensure_user_generated_result_dir().resolve()
    destination_dir = result_root / "video"
    destination_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_task_manifest(task_folder)
    moved_videos: list[tuple[Path, Path]] = []
    moved_previews: list[tuple[Path, Path]] = []
    metadata_paths: list[Path] = []
    try:
        moved_videos = _move_recycle_videos(source_videos, destination_dir)
        moved_previews = _move_recycle_previews(recycle_root, result_root, moved_videos)
        metadata_paths = _save_restored_result_metadata(
            result_root,
            relative_folder,
            manifest,
            moved_videos,
        )
    except Exception:
        _rollback_restored_files(moved_videos, moved_previews, metadata_paths)
        raise
    shutil.rmtree(task_folder)
    restored = [target for _source, target in moved_videos]
    return {
        "ok": True,
        "restoredCount": len(restored),
        "removedFolder": relative_folder,
        "restoredVideos": [
            {
                "name": path.name,
                "userGeneratedKey": path.relative_to(result_root).as_posix(),
                "url": f"/user-generated-results/{path.relative_to(result_root).as_posix()}",
            }
            for path in restored
        ],
    }


def _resolve_recycle_task_folder(root: Path, folder: str) -> tuple[str, Path]:
    relative_folder = Path(str(folder or "").strip())
    if relative_folder.is_absolute() or not relative_folder.parts:
        raise ValueError("回收站任务路径无效")
    target = (root / relative_folder).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("回收站任务路径越界") from error
    if target == root:
        raise ValueError("不能删除回收站根目录")
    if target.exists() and not (target / RECYCLE_MANIFEST_NAME).is_file():
        raise ValueError("目标不是有效的回收站任务")
    return relative_folder.as_posix(), target


def _task_video_files(task_folder: Path) -> list[Path]:
    return [
        path for path in sorted((task_folder / "video").glob("*"))
        if path.is_file() and path.suffix.lower() in RECYCLE_VIDEO_EXTENSIONS
    ]


def _load_task_manifest(task_folder: Path) -> dict[str, Any]:
    manifest_path = task_folder / RECYCLE_MANIFEST_NAME
    try:
        manifest = normalize_legacy_video_payload(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _move_recycle_videos(sources: list[Path], destination_dir: Path) -> list[tuple[Path, Path]]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    moved: list[tuple[Path, Path]] = []
    try:
        for index, source in enumerate(sources, 1):
            target = _unique_file_path(destination_dir / f"恢复-{timestamp}-{index:02d}-{source.name}")
            shutil.move(str(source), str(target))
            moved.append((source, target))
    except Exception:
        for source, target in reversed(moved):
            if target.exists() and not source.exists():
                shutil.move(str(target), str(source))
        raise
    return moved


def _move_recycle_previews(
    recycle_root: Path,
    result_root: Path,
    moved_videos: list[tuple[Path, Path]],
) -> list[tuple[Path, Path]]:
    moved: list[tuple[Path, Path]] = []
    for source, target in moved_videos:
        source_key = source.relative_to(recycle_root).as_posix()
        target_key = target.relative_to(result_root).as_posix()
        source_preview = recycle_root / preview_key_for_video(source_key)
        target_preview = result_root / preview_key_for_video(target_key)
        if source_preview.is_file():
            target_preview.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_preview), str(target_preview))
            moved.append((source_preview, target_preview))
        else:
            generate_preview_for_video(target, result_root, target_key)
    return moved


def _save_restored_result_metadata(
    result_root: Path,
    source_folder: str,
    manifest: dict[str, Any],
    moved_videos: list[tuple[Path, Path]],
) -> list[Path]:
    paths: list[Path] = []
    for source, target in moved_videos:
        relative_key = target.relative_to(result_root).as_posix()
        metadata_path = restored_result_metadata_path(result_root, relative_key)
        payload = _restored_result_metadata_payload(source_folder, manifest, source, relative_key)
        _write_json_file(metadata_path, payload)
        paths.append(metadata_path)
    return paths


def _restored_result_metadata_payload(
    source_folder: str,
    manifest: dict[str, Any],
    source: Path,
    relative_key: str,
) -> dict[str, Any]:
    meta = manifest.get("meta") if isinstance(manifest.get("meta"), dict) else {}
    title = sanitize_internal_fidelity_notes(manifest.get("videoTitle") or source.stem)
    prompt = _manifest_prompt(meta)
    return {
        "schema": "restored-result-v1",
        "restoredAt": datetime.now(timezone.utc).isoformat(),
        "sourceRecycleFolder": source_folder,
        "userGeneratedKey": relative_key,
        "videoIndex": manifest.get("videoIndex"),
        "videoTitle": title,
        "jobId": manifest.get("jobId") or "",
        "reason": manifest.get("reason") or "",
        "prompt": prompt,
        "archiveMeta": meta,
        "generationMeta": meta,
        "sourceVideoName": source.name,
    }


def _manifest_prompt(meta: dict[str, Any]) -> str:
    prompt = str(meta.get("prompt") or "").strip()
    if prompt:
        return prompt
    for record in meta.get("segmentRecords") or []:
        if isinstance(record, dict) and str(record.get("segmentPrompt") or "").strip():
            return str(record["segmentPrompt"]).strip()
    return ""


def restored_result_metadata_path(result_root: Path, video_relative_key: str) -> Path:
    relative_key = Path(str(video_relative_key or "").strip().lstrip("/"))
    if relative_key.is_absolute() or not relative_key.parts:
        raise ValueError("生成结果路径无效")
    root = result_root.resolve()
    target = (root / RESTORED_RESULT_METADATA_DIR / relative_key).with_suffix(
        f"{relative_key.suffix}.json"
    ).resolve()
    try:
        target.relative_to(root / RESTORED_RESULT_METADATA_DIR)
    except ValueError as error:
        raise ValueError("生成结果路径越界") from error
    return target


def load_restored_result_metadata(result_root: Path, video_relative_key: str) -> dict[str, Any]:
    metadata_path = restored_result_metadata_path(result_root, video_relative_key)
    try:
        payload = normalize_legacy_video_payload(json.loads(metadata_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    payload["archiveManifestPath"] = str(metadata_path)
    return payload


def save_restored_result_narration_text(result_root: Path, video_relative_key: str, text: str) -> bool:
    metadata_path = restored_result_metadata_path(result_root, video_relative_key)
    if not metadata_path.is_file():
        return False
    payload = load_restored_result_metadata(result_root, video_relative_key)
    payload.pop("archiveManifestPath", None)
    generation_meta = payload.get("generationMeta")
    generation_meta = dict(generation_meta) if isinstance(generation_meta, dict) else {}
    generation_meta["userTtsNarrationText"] = text
    generation_meta["userTtsNarrationUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    payload["generationMeta"] = generation_meta
    _write_json_file(metadata_path, payload)
    return True


def save_restored_result_html_motion_overlay(
    result_root: Path,
    video_relative_key: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    metadata_path = restored_result_metadata_path(result_root, video_relative_key)
    if not metadata_path.is_file():
        return {}
    payload = load_restored_result_metadata(result_root, video_relative_key)
    payload.pop("archiveManifestPath", None)
    updated_at = datetime.now(timezone.utc).isoformat()
    generation_meta = payload.get("generationMeta")
    generation_meta = dict(generation_meta) if isinstance(generation_meta, dict) else {}
    archive_meta = payload.get("archiveMeta")
    archive_meta = dict(archive_meta) if isinstance(archive_meta, dict) else {}
    generation_meta["htmlMotionOverlayRegeneration"] = {**result, "updatedAt": updated_at}
    if result.get("status") in {"applied", "degraded"}:
        payload["htmlMotionOverlay"] = dict(result)
        archive_meta["htmlMotionOverlay"] = dict(result)
    payload["generationMeta"] = generation_meta
    payload["archiveMeta"] = archive_meta
    _write_json_file(metadata_path, payload)
    return load_restored_result_metadata(result_root, video_relative_key)


def delete_restored_result_metadata(result_root: Path, video_relative_key: str) -> str:
    metadata_path = restored_result_metadata_path(result_root, video_relative_key)
    if not metadata_path.is_file():
        return ""
    metadata_path.unlink()
    return metadata_path.relative_to(result_root).as_posix()


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.writing")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _rollback_restored_files(
    moved_videos: list[tuple[Path, Path]],
    moved_previews: list[tuple[Path, Path]],
    metadata_paths: list[Path],
) -> None:
    for path in metadata_paths:
        path.unlink(missing_ok=True)
    for source, target in reversed(moved_previews):
        if target.exists() and not source.exists():
            shutil.move(str(target), str(source))
    for source, target in reversed(moved_videos):
        if target.exists() and not source.exists():
            shutil.move(str(target), str(source))


def humanize_failed_video_reason(reason: str) -> str:
    text = str(reason or "").strip()
    lowered = text.lower()
    if not text:
        return "视频生成失败，请重新生成。"
    if (
        "remotedisconnected" in lowered
        or "remote end closed connection" in lowered
        or "connection aborted" in lowered
        or "proxyerror" in lowered
        or "ssl eof" in lowered
        or "ssleof" in lowered
    ):
        return (
            "生成服务连接中断，本地没有拿到完整响应。"
            "如果请求已经发到上游，任务可能仍在服务端继续生成或已扣费；请先查看结果区/回收站，"
            "确认没有回填后再重试。"
        )
    if "_mix_video" in text or "preserve_original_audio_override" in text or "mix_background_music" in lowered:
        return "视频后处理失败，背景音乐或原声音轨合成没有完成。请重新生成，或先关闭背景音乐后再试。"
    if "花字" in text or "text overlay" in lowered or "overlay" in lowered:
        return "花字处理失败，视频已经保留在这里。请调整花字设置后重新生成。"
    if "no module named pil" in lowered or "pillow" in lowered:
        return "花字处理失败，缺少图片渲染组件。请先关闭花字或补齐本机组件后重试。"
    if "ffmpeg not found" in lowered:
        return "视频后处理失败，本机没有找到 FFmpeg。请检查视频处理环境后重试。"
    if "timeout" in lowered or "timed out" in lowered or "超时" in text:
        return "生成服务等待超时，本地没有拿到完整响应。请先刷新结果区确认没有回填后再重试。"
    if (
        "only [4, 6, 8] seconds" in lowered
        or "only [4,6,8] seconds" in lowered
        or "4, 6, 8" in text and "seconds" in lowered and "supported" in lowered
    ):
        return "当前模型只支持 4、6 或 8 秒，请把视频时长改成支持的秒数后重试。"
    if (
        "invalid_seconds" in lowered
        or "seconds is invalid" in lowered
        or "must be 4, 8, or 12" in lowered
    ):
        return "当前视频时长不支持，请切换到支持的秒数后重试。"
    if "duration must be 5 or 10 seconds" in lowered or "5 or 10 seconds" in lowered:
        return "视频时长不支持，请切到 5 秒或 10 秒。"
    if (
        "content review" in lowered
        or "content_policy" in lowered
        or "identifiable real person" in lowered
        or "内容审核" in text
    ):
        return "内容审核未通过，请换图或改成非真人风格后重试。"
    if ("上游" in text and "失败" in text) or "生成未成功" in text or "生成状态" in text:
        return "视频生成没有成功，请重新生成这一条。"
    if any(marker in text for marker in ("Traceback", "TypeError", "RuntimeError", "unexpected keyword", "Exception")):
        return "视频处理失败，请重新生成这一条。"
    return text[:180]


def _manifest_videos(root: Path, folder: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    declared = manifest.get("videos") if isinstance(manifest.get("videos"), list) else []
    candidates: list[Path] = []
    for item in declared:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("relativePath") or "").strip()
        if not relative:
            continue
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file() and candidate.suffix.lower() in RECYCLE_VIDEO_EXTENSIONS:
            candidates.append(candidate)
    if not candidates:
        candidates = [
            path for path in sorted((folder / "video").glob("*"))
            if path.is_file() and path.suffix.lower() in RECYCLE_VIDEO_EXTENSIONS
        ]
    videos = []
    for path in candidates:
        stat = path.stat()
        videos.append({
            "name": path.name,
            "relativePath": path.relative_to(root).as_posix(),
            "url": f"/user-recycle-bin/{path.relative_to(root).as_posix()}",
            "sizeBytes": stat.st_size,
            "updatedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return videos


def _unique_folder_name(root: Path, base: str) -> str:
    clean_base = base.strip("-")[:160] or "failed-video"
    candidate = clean_base
    index = 2
    while (root / candidate).exists():
        candidate = f"{clean_base}-{index}"
        index += 1
    return candidate


def _unique_file_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _slugify(value: str) -> str:
    cleaned = []
    for ch in str(value or "").lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif ch in {" ", "-", "_"}:
            cleaned.append("-")
    slug = "".join(cleaned).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:80]
