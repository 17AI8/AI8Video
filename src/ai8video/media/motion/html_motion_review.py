from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai8video.media.ffmpeg_utils import probe_media_video_info, resolve_ffmpeg_bin
from ai8video.media.overlay_video_io import composite_transparent_layer, validate_composited_video
from ai8video.media.motion.html_motion_overlay import render_html_motion_artifact_layer
from ai8video.media.motion.hyperframes_runtime import WAAPI_RUNTIME_SOURCE
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
        _copy_live_preview_font(review_dir, font_family)
    else:
        (review_dir / "composition.html").unlink(missing_ok=True)
    if isinstance(artifact, dict) and isinstance(motion_media, dict):
        _write_json(review_dir / "artifact.json", artifact)
        _write_json(review_dir / "media.json", motion_media)
    else:
        (review_dir / "artifact.json").unlink(missing_ok=True)
        (review_dir / "media.json").unlink(missing_ok=True)
    chunks = _timeline_chunks(artifact)
    review_id = review_dir.name
    prepared_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "reviewId": review_id,
        "relativeKey": relative_key,
        "candidateName": candidate.name,
        "preparedAt": prepared_at,
        "renderResult": result,
        "fontFamily": font_family,
        "timelineChunks": chunks,
    }
    _write_json(review_dir / "review.json", payload)
    return {
        **result,
        "status": "preview_ready",
        "reason": "HTML 动效预览已生成，等待确认烧录",
        "reviewReady": True,
        "reviewId": review_id,
        "preparedAt": prepared_at,
        "previewUrl": f"/api/user-generated-results/html-motion-preview/{review_id}",
        "livePreviewUrl": f"/api/user-generated-results/html-motion-live/{review_id}/composition.html",
        "timelineChunks": chunks,
        "timelineAdjustable": bool(layer.is_file() and chunks),
    }


def _copy_live_preview_font(review_dir: Path, font_family: str) -> None:
    for name in ("motion-font.otf", "flower-font.otf"):
        (review_dir / name).unlink(missing_ok=True)
    source = selected_video_text_overlay_font_path()
    if source is None or not source.is_file() or not font_family:
        return
    target_name = "flower-font.otf" if font_family == "AI8VideoFlower" else "motion-font.otf"
    shutil.copy2(source, review_dir / target_name)


def confirm_html_motion_review(video_path: Path, relative_key: str) -> dict[str, Any]:
    source = video_path.resolve()
    review_dir = _review_dir(relative_key)
    payload = _load_json(review_dir / "review.json")
    candidate = review_dir / f"candidate{source.suffix or '.mp4'}"
    if payload.get("relativeKey") != relative_key or not candidate.is_file():
        raise LookupError("请先重新生成 HTML 动效预览")
    temporary = source.with_name(f".{source.name}.html-motion-confirming")
    shutil.copy2(candidate, temporary)
    temporary.replace(source)
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
    candidate = review_dir / str(payload.get("candidateName") or "")
    ready = bool(
        payload.get("relativeKey") == relative_key
        and not payload.get("confirmedAt")
        and candidate.is_file()
    )
    live_ready = bool(
        ready
        and (review_dir / "composition.html").is_file()
        and (review_dir / "waapi-timeline-runtime.js").is_file()
    )
    return {
        "ok": True,
        "reviewReady": ready,
        "reviewId": review_dir.name if ready else "",
        "previewUrl": (
            f"/api/user-generated-results/html-motion-preview/{review_dir.name}" if ready else ""
        ),
        "livePreviewUrl": (
            f"/api/user-generated-results/html-motion-live/{review_dir.name}/composition.html"
            if live_ready else ""
        ),
        "preparedAt": payload.get("preparedAt") if ready else None,
        "durationSeconds": _review_duration(payload) if ready else 0.0,
        "timelineChunks": payload.get("timelineChunks", []) if ready else [],
        "timelineAdjustable": bool(
            ready
            and (review_dir / "overlay.webm").is_file()
            and (review_dir / "artifact.json").is_file()
            and payload.get("timelineChunks")
        ),
    }


def html_motion_review_layer_path(relative_key: str) -> Path:
    return _review_dir(relative_key) / "overlay.webm"


def adjust_html_motion_review_timeline(
    video_path: Path,
    relative_key: str,
    chunks: Any,
    *,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    source = video_path.resolve()
    review_dir = _review_dir(relative_key)
    payload = _load_json(review_dir / "review.json")
    base = review_dir / f"base{source.suffix or '.mp4'}"
    candidate = review_dir / f"candidate{source.suffix or '.mp4'}"
    layer = review_dir / "overlay.webm"
    artifact_path = review_dir / "artifact.json"
    media_path = review_dir / "media.json"
    if payload.get("relativeKey") != relative_key or not base.is_file() or not artifact_path.is_file():
        raise LookupError("请先重新生成 HTML 动效预览")
    artifact = _load_json(artifact_path)
    media = _load_json(media_path) or _review_media(payload, base)
    duration = float(media.get("durationSeconds") or 0.0)
    normalized = _apply_timeline_chunks(artifact, chunks, duration)
    temporary_layer = review_dir / "overlay.timeline.webm"
    temporary = review_dir / f"candidate.timeline{source.suffix or '.mp4'}"
    temporary_layer.unlink(missing_ok=True)
    temporary.unlink(missing_ok=True)
    try:
        composition_html, _manifest = render_html_motion_artifact_layer(
            artifact,
            media,
            temporary_layer,
            font_family=str(payload.get("fontFamily") or ""),
        )
        shutil.copy2(base, temporary)
        composite_transparent_layer(
            temporary,
            temporary_layer,
            media,
            resolve_ffmpeg_bin(ffmpeg_bin),
        )
        validate_composited_video(probe_media_video_info(temporary), media)
        temporary_layer.replace(layer)
        temporary.replace(candidate)
        artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        (review_dir / "composition.html").write_text(composition_html, encoding="utf-8")
    finally:
        temporary_layer.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
    payload["timelineChunks"] = normalized
    payload["timelineAdjustedAt"] = datetime.now(timezone.utc).isoformat()
    _write_json(review_dir / "review.json", payload)
    return {
        "ok": True,
        "reviewReady": True,
        "reviewId": review_dir.name,
        "previewUrl": f"/api/user-generated-results/html-motion-preview/{review_dir.name}",
        "durationSeconds": duration,
        "timelineChunks": normalized,
        "timelineAdjustable": True,
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
    render_result = payload.get("renderResult")
    if not isinstance(render_result, dict):
        return 0.0
    try:
        return max(0.0, float(render_result.get("durationSeconds") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _review_media(payload: dict[str, Any], base: Path) -> dict[str, Any]:
    media = probe_media_video_info(base)
    render_result = payload.get("renderResult")
    if isinstance(render_result, dict):
        duration = _review_duration(payload)
        if duration > 0:
            media["durationSeconds"] = duration
    return media


def _timeline_chunks(artifact: Any) -> list[dict[str, Any]]:
    scenes = artifact.get("scenes") if isinstance(artifact, dict) else None
    if not isinstance(scenes, list):
        return []
    chunks = []
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or start)
        text = html.unescape(re.sub(r"<[^>]+>", " ", str(scene.get("html") or "")))
        label = re.sub(r"\s+", " ", text).strip()[:32] or f"Chunk {index + 1}"
        chunks.append({
            "index": index,
            "startSeconds": round(start, 3),
            "endSeconds": round(end, 3),
            "durationSeconds": round(max(0.1, end - start), 3),
            "label": label,
        })
    return chunks


def _apply_timeline_chunks(artifact: dict[str, Any], value: Any, duration: float) -> list[dict[str, Any]]:
    scenes = artifact.get("scenes")
    if not isinstance(scenes, list) or not isinstance(value, list) or len(value) != len(scenes):
        raise ValueError("chunk 时间轴数据不完整")
    by_index = {int(item.get("index")): item for item in value if isinstance(item, dict)}
    if len(by_index) != len(scenes):
        raise ValueError("chunk 时间轴数据不完整")
    for index, scene in enumerate(scenes):
        item = by_index.get(index)
        if item is None or not isinstance(scene, dict):
            raise ValueError("chunk 时间轴数据不合法")
        chunk_duration = max(0.1, float(scene.get("end") or 0) - float(scene.get("start") or 0))
        try:
            start = float(item.get("startSeconds"))
        except (TypeError, ValueError) as exc:
            raise ValueError("chunk 起始时间不合法") from exc
        start = min(max(0.0, start), max(0.0, duration - chunk_duration))
        scene["start"] = round(start, 3)
        scene["end"] = round(start + chunk_duration, 3)
    return _timeline_chunks(artifact)


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
