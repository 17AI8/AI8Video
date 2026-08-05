from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai8video.assets.user_files import USER_GENERATED_RESULT_ROOT, ensure_user_file_root
from ai8video.core.legacy_payload import normalize_legacy_video_payload

RESULT_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
RESULT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
RESULT_MEDIA_EXTENSIONS = RESULT_VIDEO_EXTENSIONS | RESULT_IMAGE_EXTENSIONS
SOURCE_VIDEO_PREFIX = "source/video"
LEGACY_VIDEO_PREFIX = "video"
BURNED_VIDEO_PREFIX = "burned/video"
logger = logging.getLogger(__name__)
_BURNED_COPY_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai8-burned-copy")
_BURNED_COPY_LOCKS: dict[str, threading.Lock] = {}
_BURNED_COPY_LOCKS_GUARD = threading.Lock()
_UNCHECKED_TARGET_STATE = object()


def is_simulated_user_generated_result_path(source: Path) -> bool:
    return "dry-model-" in Path(source).name.lower()


def ensure_user_generated_result_dir() -> Path:
    ensure_user_file_root()
    USER_GENERATED_RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    return USER_GENERATED_RESULT_ROOT


def migrate_legacy_result_layout(result_root: Path = USER_GENERATED_RESULT_ROOT) -> dict[str, Any]:
    """幂等迁移旧 video/ 原片，并保留独立 burned 成片。"""
    root = Path(result_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    legacy_video_root = root / LEGACY_VIDEO_PREFIX
    source_video_root = root / SOURCE_VIDEO_PREFIX
    source_video_root.mkdir(parents=True, exist_ok=True)
    moved_keys: dict[str, str] = {}
    moved_videos: list[str] = []
    for source in sorted(root.iterdir()):
        if source.is_file() and source.suffix.lower() in RESULT_VIDEO_EXTENSIONS:
            _move_result_video(source, source_video_root, root, moved_keys, moved_videos)
    if legacy_video_root.is_dir():
        for source in sorted(legacy_video_root.rglob("*")):
            if source.is_file() and source.suffix.lower() in RESULT_VIDEO_EXTENSIONS:
                _move_result_video(source, source_video_root, root, moved_keys, moved_videos)
    moved_sidecars = _quarantine_result_video_sidecars(legacy_video_root, root)
    moved_metadata = _migrate_result_metadata(root, moved_keys)
    _prune_result_subdirectories(legacy_video_root)
    return {
        "movedVideos": moved_videos,
        "movedSidecars": moved_sidecars,
        "movedMetadata": moved_metadata,
        "resultVideoRoot": str(source_video_root),
    }


def _move_result_video(
    source: Path,
    video_root: Path,
    result_root: Path,
    moved_keys: dict[str, str],
    moved_videos: list[str],
) -> None:
    old_key = source.relative_to(result_root).as_posix()
    target = _next_result_file_path(video_root, source.name)
    shutil.move(str(source), str(target))
    canonical_key = target.relative_to(result_root).as_posix()
    moved_keys[old_key] = canonical_key
    moved_videos.append(canonical_key)


def _next_result_file_path(directory: Path, filename: str) -> Path:
    candidate = directory / Path(filename).name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(1, 1000):
        numbered = directory / f"{stem}-{index}{suffix}"
        if not numbered.exists():
            return numbered
    raise RuntimeError("用户生成结果目录中同名文件过多")


def _quarantine_result_video_sidecars(video_root: Path, result_root: Path) -> list[str]:
    quarantine_root = result_root / "misc" / "video"
    moved: list[str] = []
    for source in sorted(video_root.rglob("*")):
        if not source.is_file() or source.suffix.lower() in RESULT_VIDEO_EXTENSIONS:
            continue
        if source.name == ".DS_Store":
            continue
        relative = source.relative_to(video_root)
        target = quarantine_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = _next_result_file_path(target.parent, target.name)
        shutil.move(str(source), str(target))
        moved.append(target.relative_to(result_root).as_posix())
    return moved


def _migrate_result_metadata(root: Path, moved_keys: dict[str, str]) -> list[str]:
    metadata_root = root / ".restored-meta"
    if not metadata_root.is_dir():
        return []
    target_root = metadata_root / SOURCE_VIDEO_PREFIX
    target_root.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for source in sorted(metadata_root.rglob("*.json")):
        if source.is_relative_to(target_root):
            continue
        old_video_key = source.relative_to(metadata_root).with_suffix("").as_posix()
        if old_video_key.startswith(f"{LEGACY_VIDEO_PREFIX}/"):
            default_key = f"{SOURCE_VIDEO_PREFIX}/{Path(old_video_key).name}"
        else:
            default_key = f"{SOURCE_VIDEO_PREFIX}/{Path(old_video_key).name}"
        new_video_key = moved_keys.get(old_video_key, default_key)
        target = (target_root / Path(new_video_key).name).with_suffix(
            f"{Path(new_video_key).suffix}.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = _read_json_object(source)
        if payload:
            payload["userGeneratedKey"] = new_video_key
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            source.unlink()
        else:
            shutil.move(str(source), str(target))
        moved.append(target.relative_to(root).as_posix())
    return moved


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = normalize_legacy_video_payload(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _prune_result_subdirectories(root: Path) -> None:
    if not root.exists():
        return
    for item in sorted(root.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        if item.is_file() and item.name == ".DS_Store":
            item.unlink(missing_ok=True)
        elif item.is_dir():
            try:
                item.rmdir()
            except OSError:
                pass
    try:
        root.rmdir()
    except OSError:
        pass


def burned_result_lock(target: Path) -> threading.Lock:
    key = str(Path(target).resolve())
    with _BURNED_COPY_LOCKS_GUARD:
        return _BURNED_COPY_LOCKS.setdefault(key, threading.Lock())


def burned_result_path(source: Path, *, result_root: Path | None = None) -> Path | None:
    root = Path(result_root or ensure_user_generated_result_dir()).resolve()
    resolved = Path(source).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return None
    if relative.parts[:2] != ("source", "video"):
        return None
    if resolved.suffix.lower() not in RESULT_VIDEO_EXTENSIONS:
        return None
    return (root / BURNED_VIDEO_PREFIX / Path(*relative.parts[2:])).resolve()


def source_key_for_result_key(raw_key: object) -> str:
    """Map burned and legacy keys to the canonical editable source key."""
    clean_key = str(raw_key or "").strip().lstrip("/")
    if clean_key.startswith(f"{BURNED_VIDEO_PREFIX}/"):
        return f"{SOURCE_VIDEO_PREFIX}/{clean_key.removeprefix(f'{BURNED_VIDEO_PREFIX}/')}"
    if clean_key.startswith(f"{LEGACY_VIDEO_PREFIX}/"):
        return f"source/{clean_key}"
    return clean_key


def burned_key_for_source_key(raw_key: object) -> str:
    source_key = source_key_for_result_key(raw_key)
    if not source_key.startswith(f"{SOURCE_VIDEO_PREFIX}/"):
        return ""
    return f"{BURNED_VIDEO_PREFIX}/{source_key.removeprefix(f'{SOURCE_VIDEO_PREFIX}/')}"


def _result_file_state(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def mirror_generated_result_file(
    source: Path,
    *,
    archive_root: Path | None = None,
    overwrite: bool = False,
    expected_target_state: tuple[int, int, int, int] | None | object = _UNCHECKED_TARGET_STATE,
) -> Path | None:
    resolved_source = Path(source).resolve()
    target = burned_result_path(resolved_source, result_root=archive_root)
    if target is None or not resolved_source.is_file():
        return None
    with burned_result_lock(target):
        if (
            expected_target_state is not _UNCHECKED_TARGET_STATE
            and _result_file_state(target) != expected_target_state
        ):
            return target if target.is_file() else None
        if target.is_file() and not overwrite:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.copying-{uuid.uuid4().hex}")
        try:
            shutil.copy2(resolved_source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def schedule_burned_result_copy(
    source: Path,
    *,
    result_root: Path | None = None,
    overwrite: bool = False,
) -> Future[Path | None] | None:
    target = burned_result_path(source, result_root=result_root)
    if target is None:
        return None
    expected_target_state = _result_file_state(target) if overwrite else _UNCHECKED_TARGET_STATE
    future = _BURNED_COPY_EXECUTOR.submit(
        mirror_generated_result_file,
        source,
        archive_root=result_root,
        overwrite=overwrite,
        expected_target_state=expected_target_state,
    )

    def report_failure(completed: Future[Path | None]) -> None:
        try:
            completed.result()
        except Exception:
            logger.exception("创建烧录初始副本失败：%s", target)

    future.add_done_callback(report_failure)
    return future


def schedule_missing_burned_result_copies(
    result_root: Path | None = None,
) -> list[Future[Path | None]]:
    root = Path(result_root or ensure_user_generated_result_dir()).resolve()
    video_root = root / SOURCE_VIDEO_PREFIX
    if not video_root.is_dir():
        return []
    scheduled: list[Future[Path | None]] = []
    for source in sorted(video_root.rglob("*")):
        target = burned_result_path(source, result_root=root)
        if target is None or target.is_file():
            continue
        future = schedule_burned_result_copy(source, result_root=root, overwrite=False)
        if future is not None:
            scheduled.append(future)
    return scheduled


def sync_generated_results_from_archive_root(archive_root: Path) -> Path:
    return ensure_user_generated_result_dir()


def build_generation_result_reconciliation(
    generation_progress: dict[str, Any] | None,
    asset_records: list[dict[str, Any]],
    *,
    result_root: Path = USER_GENERATED_RESULT_ROOT,
) -> dict[str, Any]:
    progress = generation_progress if isinstance(generation_progress, dict) else {}
    progress_items = [item for item in progress.get("items") or [] if isinstance(item, dict)]
    resolved_result_root = result_root.resolve()
    result_files = _scan_result_video_files(resolved_result_root)
    assets_by_job_id = _index_assets_by_job_id(
        asset_records,
        generation_batch_id=_normalized_text(progress.get("generationBatchId")),
        session_id=_normalized_text(progress.get("sessionId")),
    )
    reconciled_items, matched_asset_ids = _reconcile_progress_items(
        progress_items,
        assets_by_job_id,
        resolved_result_root,
        result_files,
    )
    known_asset_result_paths = _collect_asset_result_paths(
        asset_records,
        resolved_result_root,
        result_files,
    )
    orphan_results = _build_orphan_results(
        result_files,
        known_asset_result_paths,
        resolved_result_root,
    )
    return {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "generationBatchId": _normalized_text(progress.get("generationBatchId")) or None,
        "summary": {
            "taskItems": len(progress_items),
            "assetRecords": len(matched_asset_ids),
            "scannedAssetRecords": len(asset_records),
            "availableResults": len(result_files),
            "conflicts": _count_conflicts(reconciled_items, orphan_results),
        },
        "items": reconciled_items,
        "orphanResults": orphan_results,
        "unmatchedAssets": [],
    }


def _reconcile_progress_items(
    progress_items: list[dict[str, Any]],
    assets_by_job_id: dict[str, dict[str, Any]],
    result_root: Path,
    result_files: set[Path],
) -> tuple[list[dict[str, Any]], set[int]]:
    reconciled_items = []
    matched_asset_ids: set[int] = set()
    for progress_item in progress_items:
        asset_record = assets_by_job_id.get(_normalized_text(progress_item.get("jobId")))
        if asset_record is not None:
            matched_asset_ids.add(id(asset_record))
        reconciled_item = _reconcile_progress_item(
            progress_item,
            asset_record,
            result_root,
            result_files,
        )
        reconciled_items.append(reconciled_item)
    return reconciled_items, matched_asset_ids


def _collect_asset_result_paths(
    asset_records: list[dict[str, Any]],
    result_root: Path,
    result_files: set[Path],
) -> set[Path]:
    known_result_paths: set[Path] = set()
    for asset_record in asset_records:
        result_path = _resolve_asset_result_path(asset_record, result_root, result_files)
        if result_path is not None:
            known_result_paths.add(result_path)
    return known_result_paths


def _build_orphan_results(
    result_files: set[Path],
    claimed_result_paths: set[Path],
    result_root: Path,
) -> list[dict[str, Any]]:
    return [
        {
            "resultPath": str(result_path),
            "relativePath": result_path.relative_to(result_root).as_posix(),
            "conflicts": ["result_without_asset"],
        }
        for result_path in sorted(result_files)
        if result_path not in claimed_result_paths
    ]


def _count_conflicts(*groups: list[dict[str, Any]]) -> int:
    return sum(len(item.get("conflicts") or []) for group in groups for item in group)


def _scan_result_video_files(result_root: Path) -> set[Path]:
    if not result_root.exists():
        return set()
    result_files: set[Path] = set()
    for candidate in result_root.rglob("*"):
        if candidate.suffix.lower() not in RESULT_VIDEO_EXTENSIONS or not candidate.is_file():
            continue
        if is_simulated_user_generated_result_path(candidate):
            continue
        resolved_candidate = candidate.resolve()
        if resolved_candidate.is_relative_to(result_root):
            result_files.add(resolved_candidate)
    return result_files


def _index_assets_by_job_id(
    asset_records: list[dict[str, Any]],
    *,
    generation_batch_id: str,
    session_id: str,
) -> dict[str, dict[str, Any]]:
    assets_by_job_id: dict[str, dict[str, Any]] = {}
    asset_priorities: dict[str, int] = {}
    for asset_record in asset_records:
        if not isinstance(asset_record, dict):
            continue
        job_id = _normalized_text(asset_record.get("jobId"))
        asset_priority = _asset_identity_priority(
            asset_record,
            generation_batch_id=generation_batch_id,
            session_id=session_id,
        )
        if asset_priority < 0:
            continue
        if job_id and asset_priority >= asset_priorities.get(job_id, -1):
            assets_by_job_id[job_id] = asset_record
            asset_priorities[job_id] = asset_priority
    return assets_by_job_id


def _asset_identity_priority(
    asset_record: dict[str, Any],
    *,
    generation_batch_id: str,
    session_id: str,
) -> int:
    asset_batch_id = _normalized_text(asset_record.get("generationBatchId"))
    asset_session_id = _normalized_text(asset_record.get("sessionId"))
    if generation_batch_id and asset_batch_id:
        return 3 if asset_batch_id == generation_batch_id else -1
    if session_id and asset_session_id:
        return 2 if asset_session_id == session_id else -1
    return 1


def _reconcile_progress_item(
    progress_item: dict[str, Any],
    asset_record: dict[str, Any] | None,
    result_root: Path,
    result_files: set[Path],
) -> dict[str, Any]:
    task_status = _normalized_text(progress_item.get("status")) or "unknown"
    result_path = _resolve_asset_result_path(asset_record, result_root, result_files)
    asset_state = "recorded" if asset_record is not None else "missing"
    result_state = "available" if result_path is not None else "missing"
    conflicts: list[str] = []
    if task_status == "succeeded" and asset_record is None:
        conflicts.append("succeeded_without_asset")
    if task_status == "succeeded" and result_path is None:
        conflicts.append("succeeded_without_result")
    if task_status != "succeeded" and result_path is None:
        result_state = "not_expected"
        if asset_record is None:
            asset_state = "not_expected"
    if task_status != "succeeded" and result_path is not None:
        conflicts.append("result_for_non_succeeded_task")
    return {
        "videoIndex": progress_item.get("videoIndex"),
        "jobId": _normalized_text(progress_item.get("jobId")) or None,
        "taskStatus": task_status,
        "assetState": asset_state,
        "resultState": result_state,
        "archiveKey": _normalized_text((asset_record or {}).get("archiveKey")) or None,
        "htmlMotionOverlay": _asset_html_motion_overlay(asset_record),
        "resultPath": str(result_path) if result_path is not None else None,
        "conflicts": conflicts,
    }


def _asset_html_motion_overlay(asset_record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(asset_record, dict):
        return None
    direct = asset_record.get("htmlMotionOverlay")
    if isinstance(direct, dict):
        return direct
    archive_meta = asset_record.get("archiveMeta")
    if isinstance(archive_meta, dict) and isinstance(archive_meta.get("htmlMotionOverlay"), dict):
        return archive_meta["htmlMotionOverlay"]
    return None


def _resolve_asset_result_path(
    asset_record: dict[str, Any] | None,
    result_root: Path,
    result_files: set[Path],
) -> Path | None:
    if not isinstance(asset_record, dict):
        return None
    for raw_path in (asset_record.get("archiveLocalPath"), asset_record.get("archiveKey")):
        candidate = _safe_result_path(raw_path, result_root)
        if candidate in result_files:
            return candidate
        legacy_candidate = _flat_legacy_result_path(raw_path, result_root, result_files)
        if legacy_candidate is not None:
            return legacy_candidate
    return None


def _flat_legacy_result_path(raw_path: Any, result_root: Path, result_files: set[Path]) -> Path | None:
    text = _normalized_text(raw_path)
    if not text:
        return None
    raw_candidate = Path(text)
    if raw_candidate.is_absolute():
        safe_candidate = raw_candidate.resolve()
        if not safe_candidate.is_relative_to(result_root):
            return None
    elif ".." in raw_candidate.parts:
        return None
    candidates = [
        (result_root / SOURCE_VIDEO_PREFIX / raw_candidate.name).resolve(),
        (result_root / LEGACY_VIDEO_PREFIX / raw_candidate.name).resolve(),
        (result_root / raw_candidate.name).resolve(),
    ]
    for candidate in candidates:
        if candidate in result_files:
            return candidate
    return None


def _safe_result_path(raw_path: Any, result_root: Path) -> Path | None:
    path_text = _normalized_text(raw_path)
    if not path_text:
        return None
    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = result_root / candidate
    resolved_candidate = candidate.resolve()
    return resolved_candidate if resolved_candidate.is_relative_to(result_root) else None


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()
