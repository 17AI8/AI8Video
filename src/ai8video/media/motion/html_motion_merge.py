from __future__ import annotations

import copy
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai8video.media.motion.html_motion_overlay import _resolve_motion_font_family
from ai8video.media.motion.html_motion_render_cache import sync_live_preview_font
from ai8video.media.motion.html_motion_review import HTML_MOTION_REVIEW_ROOT, html_motion_review_status
from ai8video.media.motion.html_motion_timeline import timeline_chunk_id, timeline_chunks
from ai8video.media.motion.hyperframes_overlay_renderer import build_composition_html
from ai8video.media.motion.hyperframes_runtime import WAAPI_RUNTIME_SOURCE
from ai8video.media.timeline_contract import TIMELINE_SCHEMA_VERSION


def html_motion_merge_source(relative_key: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    review_dir = _review_dir(relative_key)
    artifact = _load_json(review_dir / "artifact.json")
    media = _load_json(review_dir / "media.json")
    return (artifact, media) if artifact.get("scenes") and media else None


def merge_html_motion_reviews(
    sources: list[tuple[dict[str, Any], dict[str, Any]] | None],
    relative_key: str,
    video_path: Path,
    video_offsets: list[float],
    video_durations: list[float],
    video_statuses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    available = [(index, source) for index, source in enumerate(sources) if source]
    if not available:
        return {"ok": True, "reviewReady": False, "timelineChunks": []}
    artifact = copy.deepcopy(available[0][1][0])
    artifact["scenes"] = []
    output_scene_index = 0
    for source_index, (source_artifact, _) in available:
        timeline_chunks = (
            video_statuses[source_index].get("timelineChunks") or []
            if video_statuses and source_index < len(video_statuses)
            else []
        )
        scenes = _offset_scenes(
            source_artifact,
            source_index,
            video_offsets[source_index],
            output_scene_index,
            timeline_chunks,
        )
        artifact["scenes"].extend(scenes)
        output_scene_index += len(scenes)
    media = copy.deepcopy(available[0][1][1])
    media["durationSeconds"] = round(sum(video_durations), 3)
    return _write_review(relative_key, video_path, artifact, media)


def _offset_scenes(
    artifact: dict[str, Any],
    source_index: int,
    offset: float,
    output_start_index: int = 0,
    timeline_chunks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    scenes = []
    normalized_chunks = _normalized_timeline_chunks(timeline_chunks)
    for scene_index, raw_scene in enumerate(artifact.get("scenes") or []):
        mapped_range = _map_scene_range(raw_scene, normalized_chunks)
        if mapped_range is None:
            continue
        scene = copy.deepcopy(raw_scene)
        scene["start"] = round(offset + mapped_range[0], 3)
        scene["end"] = round(offset + mapped_range[1], 3)
        scene["_timelineSourceIndex"] = output_start_index + scene_index
        scene["_timelineChunkId"] = f"merged-{source_index + 1}-{timeline_chunk_id(scene, scene_index)}"
        ids = [str(value) for value in scene.get("ids") or [] if str(value)]
        replacements = {value: f"m{source_index + 1}-{scene_index + 1}-{value}" for value in set(ids)}
        replacements[f"hf-scene-{scene_index + 1}"] = f"hf-scene-{output_start_index + scene_index + 1}"
        scenes.append(_replace_ids(scene, replacements))
    return scenes


def _normalized_timeline_chunks(
    timeline_chunks: list[dict[str, Any]] | None,
) -> list[tuple[float, float, float]]:
    normalized = []
    output_cursor = 0.0
    for item in timeline_chunks or []:
        if not isinstance(item, dict):
            continue
        source_start = max(0.0, float(item.get("sourceStartSeconds") or 0))
        source_end = max(source_start, float(item.get("sourceEndSeconds") or 0))
        if source_end <= source_start:
            continue
        normalized.append((source_start, source_end, output_cursor))
        output_cursor += source_end - source_start
    return normalized


def _map_scene_range(
    scene: dict[str, Any],
    timeline_chunks: list[tuple[float, float, float]],
) -> tuple[float, float] | None:
    scene_start = max(0.0, float(scene.get("start") or 0))
    scene_end = max(scene_start, float(scene.get("end") or 0))
    if not timeline_chunks:
        return scene_start, scene_end
    intersections = []
    for source_start, source_end, output_start in timeline_chunks:
        intersection_start = max(scene_start, source_start)
        intersection_end = min(scene_end, source_end)
        if intersection_end <= intersection_start:
            continue
        intersections.append((
            output_start + intersection_start - source_start,
            output_start + intersection_end - source_start,
        ))
    if not intersections:
        return None
    return intersections[0][0], intersections[-1][1]


def _replace_ids(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_replace_ids(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_ids(item, replacements) for key, item in value.items()}
    return value


def _write_review(relative_key: str, video: Path, artifact: dict[str, Any], media: dict[str, Any]) -> dict[str, Any]:
    review_dir = _review_dir(relative_key)
    review_dir.mkdir(parents=True, exist_ok=True)
    font_family = _resolve_motion_font_family()
    shutil.copy2(video, review_dir / f"base{video.suffix or '.mp4'}")
    for name in ("artifact.json", "artifact.original.json"):
        _write_json(review_dir / name, artifact)
    _write_json(review_dir / "media.json", media)
    (review_dir / "composition.html").write_text(
        build_composition_html(artifact, media, font_family=font_family), encoding="utf-8",
    )
    shutil.copy2(WAAPI_RUNTIME_SOURCE, review_dir / "waapi-timeline-runtime.js")
    sync_live_preview_font(review_dir, font_family)
    payload = {
        "schemaVersion": TIMELINE_SCHEMA_VERSION,
        "revision": 1,
        "reviewId": review_dir.name,
        "relativeKey": relative_key,
        "candidateName": f"candidate{video.suffix or '.mp4'}",
        "preparedAt": datetime.now(timezone.utc).isoformat(),
        "renderResult": {"status": "applied", "durationSeconds": media["durationSeconds"]},
        "fontFamily": font_family,
        "timelineChunks": timeline_chunks(artifact),
    }
    _write_json(review_dir / "review.json", payload)
    return html_motion_review_status(relative_key)


def _review_dir(relative_key: str) -> Path:
    review_id = hashlib.sha256(relative_key.encode("utf-8")).hexdigest()[:32]
    return (HTML_MOTION_REVIEW_ROOT / review_id).resolve()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
