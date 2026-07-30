from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai8video.media.ffmpeg_utils import probe_media_video_info, resolve_ffmpeg_bin
from ai8video.media.motion.html_motion_overlay import render_html_motion_artifact_layer
from ai8video.media.motion.html_motion_render_cache import (
    build_html_motion_render_plan,
    layer_matches_render_plan, render_metadata, sync_live_preview_font,
)
from ai8video.media.motion.html_motion_timeline import (
    apply_timeline_chunks as _apply_timeline_chunks,
    timeline_chunks as _timeline_chunks,
)
from ai8video.media.motion.hyperframes_overlay_renderer import build_composition_html
from ai8video.media.motion.hyperframes_runtime import WAAPI_RUNTIME_SOURCE
from ai8video.media.timeline_contract import (
    TIMELINE_SCHEMA_VERSION,
    ensure_expected_revision,
    next_timeline_revision,
    timeline_review_lock,
)
from ai8video.media.video_text_overlay import selected_video_text_overlay_font_path
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


HTML_MOTION_REVIEW_ROOT = (USER_FILE_ROOT / "HTML动效" / "reviews").resolve()


def prepare_html_motion_review(
    video_path: Path,
    relative_key: str,
    render_candidate: Callable[[Path], dict[str, Any]],
    result_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = video_path.resolve()
    review_dir = _review_dir(relative_key)
    review_dir.mkdir(parents=True, exist_ok=True)
    base = review_dir / f"base{source.suffix or '.mp4'}"
    candidate = review_dir / f"candidate{source.suffix or '.mp4'}"
    temporary = review_dir / f"candidate.generating{source.suffix or '.mp4'}"
    layer = review_dir / "overlay.webm"
    if not base.is_file():
        shutil.copy2(source, base)
    # 新任务必须使旧候选失效，避免失败后仍可确认上一轮的动效。
    candidate.unlink(missing_ok=True)
    (review_dir / "review.json").unlink(missing_ok=True)
    layer.unlink(missing_ok=True)
    temporary.unlink(missing_ok=True)
    shutil.copy2(base, temporary)
    try:
        result = {**render_candidate(temporary), **(result_metadata or {})}
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    if result.get("status") != "applied":
        temporary.unlink(missing_ok=True)
        return {
            **result,
            "status": "preview_failed",
            "reviewReady": False,
        }
    temporary.replace(candidate)
    composition_html = result.pop("compositionHtml", None)
    artifact = result.pop("motionArtifact", None)
    motion_media = result.pop("motionMedia", None)
    font_family = str(result.pop("motionFontFamily", "") or "")
    if isinstance(composition_html, str) and composition_html.strip():
        (review_dir / "composition.html").write_text(composition_html, encoding="utf-8")
        shutil.copy2(WAAPI_RUNTIME_SOURCE, review_dir / "waapi-timeline-runtime.js")
        sync_live_preview_font(review_dir, font_family)
    else:
        (review_dir / "composition.html").unlink(missing_ok=True)
    if isinstance(artifact, dict) and isinstance(motion_media, dict):
        _write_json(review_dir / "artifact.json", artifact)
        _write_json(review_dir / "artifact.original.json", artifact)
        _write_json(review_dir / "media.json", motion_media)
    else:
        (review_dir / "artifact.json").unlink(missing_ok=True)
        (review_dir / "artifact.original.json").unlink(missing_ok=True)
        (review_dir / "media.json").unlink(missing_ok=True)
    chunks = _timeline_chunks(artifact)
    review_id = review_dir.name
    prepared_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "schemaVersion": TIMELINE_SCHEMA_VERSION,
        "revision": 1,
        "reviewId": review_id,
        "relativeKey": relative_key,
        "candidateName": candidate.name,
        "preparedAt": prepared_at,
        "renderResult": result,
        "fontFamily": font_family,
        "timelineChunks": chunks,
    }
    if isinstance(artifact, dict) and isinstance(motion_media, dict) and layer.is_file():
        plan = build_html_motion_render_plan(artifact, motion_media, font_family)
        payload.update(render_metadata(plan, layer))
    _write_json(review_dir / "review.json", payload)
    return {
        **result,
        "status": "preview_ready",
        "reason": "HTML 动效预览已生成，等待确认烧录",
        "reviewReady": True,
        "reviewId": review_id,
        "schemaVersion": TIMELINE_SCHEMA_VERSION,
        "revision": 1,
        "preparedAt": prepared_at,
        "previewUrl": f"/api/user-generated-results/html-motion-preview/{review_id}",
        "livePreviewUrl": f"/api/user-generated-results/html-motion-live/{review_id}/composition.html",
        "timelineChunks": chunks,
        "timelineAdjustable": bool(chunks),
    }


def confirm_html_motion_review(video_path: Path, relative_key: str) -> dict[str, Any]:
    source = video_path.resolve()
    candidate = html_motion_review_candidate_path(source, relative_key)
    temporary = source.with_name(f".{source.name}.html-motion-confirming")
    shutil.copy2(candidate, temporary)
    temporary.replace(source)
    return finalize_html_motion_review(relative_key)


def html_motion_review_candidate_path(video_path: Path, relative_key: str) -> Path:
    source = video_path.resolve()
    review_dir = _review_dir(relative_key)
    payload = _load_json(review_dir / "review.json")
    candidate = review_dir / f"candidate{source.suffix or '.mp4'}"
    if (
        payload.get("relativeKey") != relative_key
        or payload.get("confirmedAt")
        or not candidate.is_file()
    ):
        raise LookupError("请先重新生成 HTML 动效预览")
    return candidate


def finalize_html_motion_review(relative_key: str) -> dict[str, Any]:
    review_dir = _review_dir(relative_key)
    payload = _load_json(review_dir / "review.json")
    if payload.get("relativeKey") != relative_key or payload.get("confirmedAt"):
        raise LookupError("请先重新生成 HTML 动效预览")
    render_result = payload.get("renderResult")
    result = dict(render_result) if isinstance(render_result, dict) else {}
    result.update(
        {
            "status": "applied",
            "reason": "HTML 动效已确认烧录",
            "reviewReady": False,
            "reviewId": review_dir.name,
            "confirmedAt": datetime.now(timezone.utc).isoformat(),
        }
    )
    payload["confirmedAt"] = result["confirmedAt"]
    _write_json(review_dir / "review.json", payload)
    return result


def html_motion_review_status(relative_key: str) -> dict[str, Any]:
    review_dir = _review_dir(relative_key)
    payload = _load_json(review_dir / "review.json")
    artifact = _load_json(review_dir / "artifact.json")
    original_artifact = _load_json(review_dir / "artifact.original.json")
    media = _load_json(review_dir / "media.json")
    suffix = Path(str(payload.get("candidateName") or "candidate.mp4")).suffix or ".mp4"
    base = review_dir / f"base{suffix}"
    available = bool(
        payload.get("relativeKey") == relative_key
        and base.is_file()
        and artifact
        and media
    )
    ready = bool(available and not payload.get("confirmedAt"))
    if available and artifact and media:
        composition = build_composition_html(artifact, media, font_family=str(payload.get("fontFamily") or ""))
        (review_dir / "composition.html").write_text(composition, encoding="utf-8")
        shutil.copy2(WAAPI_RUNTIME_SOURCE, review_dir / "waapi-timeline-runtime.js")
    live_ready = bool(
        available
        and (review_dir / "composition.html").is_file()
        and (review_dir / "waapi-timeline-runtime.js").is_file()
    )
    current_chunks = _timeline_chunks(artifact, original_artifact) if available else []
    return {
        "ok": True,
        "schemaVersion": int(payload.get("schemaVersion") or TIMELINE_SCHEMA_VERSION),
        "revision": int(payload.get("revision") or 0),
        "reviewReady": ready,
        "reviewId": review_dir.name if available else "",
        "reviewConfirmed": bool(available and payload.get("confirmedAt")),
        "previewUrl": f"/api/user-generated-results/html-motion-base/{review_dir.name}" if available else "",
        "basePreviewUrl": (
            f"/api/user-generated-results/html-motion-base/{review_dir.name}" if available else ""
        ),
        "livePreviewUrl": (
            f"/api/user-generated-results/html-motion-live/{review_dir.name}/composition.html"
            if live_ready else ""
        ),
        "preparedAt": payload.get("preparedAt") if available else None,
        "durationSeconds": _review_duration(payload) if available else 0.0,
        "timelineChunks": current_chunks,
        "originalTimelineChunks": _timeline_chunks(original_artifact) if available else [],
        "timelineAdjustable": bool(
            available
            and (review_dir / "artifact.json").is_file()
            and payload.get("timelineChunks")
        ),
    }


def html_motion_review_layer_path(relative_key: str) -> Path:
    return _review_dir(relative_key) / "overlay.webm"


def html_motion_review_base_path(video_path: Path, relative_key: str) -> Path:
    source = video_path.resolve()
    base = _review_dir(relative_key) / f"base{source.suffix or '.mp4'}"
    if not base.is_file():
        raise LookupError("HTML 动效纯净基础视频不存在")
    return base


def save_html_motion_review_timeline(
    relative_key: str,
    chunks: Any,
    *,
    expected_revision: Any = None,
) -> dict[str, Any]:
    review_dir = _review_dir(relative_key)
    with timeline_review_lock("html-motion", relative_key):
        payload = _load_json(review_dir / "review.json")
        artifact_path = review_dir / "artifact.json"
        original_artifact_path = review_dir / "artifact.original.json"
        media = _load_json(review_dir / "media.json")
        if payload.get("relativeKey") != relative_key:
            raise LookupError("请先重新生成 HTML 动效预览")
        ensure_expected_revision(payload, expected_revision)
        if not original_artifact_path.is_file() or not media:
            raise LookupError("HTML 动效编辑源不存在")
        artifact = _load_json(original_artifact_path)
        duration = float(media.get("durationSeconds") or 0.0)
        normalized = _apply_timeline_chunks(artifact, chunks, duration)
        composition = build_composition_html(
            artifact,
            media,
            font_family=str(payload.get("fontFamily") or ""),
        )
        _write_json(artifact_path, artifact)
        (review_dir / "composition.html").write_text(composition, encoding="utf-8")
        shutil.copy2(WAAPI_RUNTIME_SOURCE, review_dir / "waapi-timeline-runtime.js")
        payload["timelineChunks"] = normalized
        payload["schemaVersion"] = TIMELINE_SCHEMA_VERSION
        payload["revision"] = next_timeline_revision(payload)
        payload.pop("confirmedAt", None)
        for key in (
            "renderedHash",
            "renderedAt",
            "renderedOverlayBytes",
            "renderedOverlaySha256",
        ):
            payload.pop(key, None)
        payload["timelineAdjustedAt"] = datetime.now(timezone.utc).isoformat()
        _write_json(review_dir / "review.json", payload)
    return {
        "ok": True,
        "schemaVersion": TIMELINE_SCHEMA_VERSION,
        "revision": int(payload.get("revision") or 0),
        "reviewReady": True,
        "reviewId": review_dir.name,
        "livePreviewUrl": f"/api/user-generated-results/html-motion-live/{review_dir.name}/composition.html",
        "durationSeconds": duration,
        "timelineChunks": normalized,
        "timelineAdjustable": bool(artifact_path.is_file()),
    }


def adjust_html_motion_review_timeline(
    video_path: Path,
    relative_key: str,
    chunks: Any,
    *,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    del ffmpeg_bin
    source = video_path.resolve()
    review_dir = _review_dir(relative_key)
    payload = _load_json(review_dir / "review.json")
    base = review_dir / f"base{source.suffix or '.mp4'}"
    layer = review_dir / "overlay.webm"
    artifact_path = review_dir / "artifact.json"
    original_artifact_path = review_dir / "artifact.original.json"
    media_path = review_dir / "media.json"
    if payload.get("relativeKey") != relative_key or not base.is_file() or not artifact_path.is_file():
        raise LookupError("请先重新生成 HTML 动效预览")
    media = _load_json(media_path) or _review_media(payload, base)
    artifact = _load_json(artifact_path)
    duration = float(media.get("durationSeconds") or 0.0)
    timeline_changed = isinstance(chunks, list) and bool(chunks)
    if timeline_changed:
        if not original_artifact_path.is_file():
            shutil.copy2(artifact_path, original_artifact_path)
        artifact = _load_json(original_artifact_path)
        normalized = _apply_timeline_chunks(artifact, chunks, duration)
    else:
        normalized = _timeline_chunks(artifact)
    if not normalized:
        raise ValueError("chunk 时间轴数据不完整")
    font_family = str(payload.get("fontFamily") or "")
    plan = build_html_motion_render_plan(artifact, media, font_family)
    render_reused = layer_matches_render_plan(payload, layer, plan)
    if not render_reused:
        temporary_layer = review_dir / f"overlay.{uuid.uuid4().hex[:10]}.rendering.webm"
        try:
            render_html_motion_artifact_layer(
                artifact,
                media,
                temporary_layer,
                font_family=font_family,
            )
            temporary_layer.replace(layer)
        finally:
            temporary_layer.unlink(missing_ok=True)
        payload.update(render_metadata(plan, layer))
    _write_json(artifact_path, artifact)
    (review_dir / "composition.html").write_text(plan.composition_html, encoding="utf-8")
    shutil.copy2(WAAPI_RUNTIME_SOURCE, review_dir / "waapi-timeline-runtime.js")
    payload["timelineChunks"] = normalized
    if timeline_changed:
        payload["timelineAdjustedAt"] = datetime.now(timezone.utc).isoformat()
    _write_json(review_dir / "review.json", payload)
    return {
        "ok": True,
        "schemaVersion": int(payload.get("schemaVersion") or TIMELINE_SCHEMA_VERSION),
        "revision": int(payload.get("revision") or 0),
        "reviewReady": True,
        "reviewId": review_dir.name,
        "previewUrl": f"/api/user-generated-results/html-motion-preview/{review_dir.name}",
        "durationSeconds": duration,
        "timelineChunks": normalized,
        "timelineAdjustable": True,
        "renderReused": render_reused,
    }


def sync_html_motion_review_audio(
    video_path: Path,
    relative_key: str,
    *,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    source = video_path.resolve()
    review_dir = _review_dir(relative_key)
    payload = _load_json(review_dir / "review.json")
    candidate = review_dir / str(payload.get("candidateName") or "")
    if payload.get("relativeKey") != relative_key or not candidate.is_file():
        return {"status": "skipped", "reason": "HTML 动效候选不存在"}
    base = review_dir / f"base{source.suffix or '.mp4'}"
    targets = [target for target in (base, candidate) if target.is_file()]
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    try:
        for target in targets:
            _sync_video_audio_from_source(target, source, ffmpeg)
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)[-500:]}
    return {
        "status": "synced",
        "reviewId": review_dir.name,
        "previewUrl": f"/api/user-generated-results/html-motion-preview/{review_dir.name}",
        "syncedTargets": len(targets),
    }


def _sync_video_audio_from_source(target: Path, source: Path, ffmpeg: str) -> None:
    temporary = target.with_name(f"{target.stem}.audio-syncing{target.suffix}")
    temporary.unlink(missing_ok=True)
    cmd = [
        ffmpeg, "-y",
        "-i", str(target),
        "-i", str(source),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "copy",
        "-shortest",
        "-movflags", "+faststart",
        str(temporary),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def resolve_html_motion_review_video(review_id: str) -> Path:
    normalized = str(review_id or "").strip().lower()
    if len(normalized) != 32 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("动效预览标识不合法")
    review_dir = (_review_root() / normalized).resolve()
    _assert_within_review_root(review_dir)
    payload = _load_json(review_dir / "review.json")
    candidate = review_dir / str(payload.get("candidateName") or "")
    if not candidate.is_file():
        raise FileNotFoundError("动效预览不存在")
    return candidate


def resolve_html_motion_review_base_video(review_id: str) -> Path:
    normalized = str(review_id or "").strip().lower()
    if len(normalized) != 32 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("动效预览标识不合法")
    review_dir = (_review_root() / normalized).resolve()
    _assert_within_review_root(review_dir)
    payload = _load_json(review_dir / "review.json")
    suffix = Path(str(payload.get("candidateName") or "candidate.mp4")).suffix or ".mp4"
    base = review_dir / f"base{suffix}"
    if not base.is_file():
        raise FileNotFoundError("动效纯净基础视频不存在")
    return base


def resolve_html_motion_review_live_asset(review_id: str, asset_name: str) -> Path:
    normalized = str(review_id or "").strip().lower()
    allowed = {"composition.html", "waapi-timeline-runtime.js", "motion-font.otf", "flower-font.otf"}
    if len(normalized) != 32 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("动效预览标识不合法")
    if asset_name not in allowed:
        raise FileNotFoundError("动效实时预览资源不存在")
    if asset_name == "waapi-timeline-runtime.js":
        return WAAPI_RUNTIME_SOURCE
    target = (_review_root() / normalized / asset_name).resolve()
    _assert_within_review_root(target)
    if asset_name in {"motion-font.otf", "flower-font.otf"} and not target.is_file():
        font = selected_video_text_overlay_font_path()
        if font is not None and font.is_file():
            return font
    if not target.is_file():
        raise FileNotFoundError("动效实时预览资源不存在")
    return target


def _review_dir(relative_key: str) -> Path:
    ensure_user_file_root()
    root = _review_root()
    root.mkdir(parents=True, exist_ok=True)
    review_id = hashlib.sha256(relative_key.encode("utf-8")).hexdigest()[:32]
    path = (root / review_id).resolve()
    _assert_within_review_root(path)
    return path


def _review_duration(payload: dict[str, Any]) -> float:
    try:
        return max(0.0, float((payload.get("renderResult") or {}).get("durationSeconds") or 0.0))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _review_media(payload: dict[str, Any], base: Path) -> dict[str, Any]:
    media = probe_media_video_info(base)
    render_result = payload.get("renderResult")
    if isinstance(render_result, dict):
        duration = _review_duration(payload)
        if duration > 0:
            media["durationSeconds"] = duration
    return media


def _assert_within_review_root(path: Path) -> None:
    if not path.is_relative_to(_review_root()):
        raise ValueError("HTML 动效预览目录越界")


def _review_root() -> Path:
    return HTML_MOTION_REVIEW_ROOT.resolve()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.writing")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
