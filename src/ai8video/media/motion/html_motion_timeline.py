from __future__ import annotations

import copy
import html
import math
import re
from typing import Any

from ai8video.media.timeline_contract import normalize_restore_bounds, optional_seconds


_CHUNK_ID_PATTERN = re.compile(r"[^A-Za-z0-9._:-]+")


def timeline_chunks(
    artifact: Any,
    source_artifact: Any = None,
) -> list[dict[str, Any]]:
    scenes = artifact.get("scenes") if isinstance(artifact, dict) else None
    source_scenes = source_artifact.get("scenes") if isinstance(source_artifact, dict) else None
    if not isinstance(scenes, list):
        return []
    chunks = []
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or start)
        source_index = int(scene.get("_timelineSourceIndex", index))
        source_scene = _source_scene(source_scenes, source_index, scene)
        source_scene_start = float(source_scene.get("start") or 0.0)
        source_scene_end = float(source_scene.get("end") or source_scene_start)
        chunks.append(
            _timeline_chunk(
                scene,
                index=index,
                source_index=source_index,
                start=start,
                end=end,
                source_scene_start=source_scene_start,
                source_scene_end=source_scene_end,
            )
        )
    return chunks


def apply_timeline_chunks(
    artifact: dict[str, Any],
    value: Any,
    duration: float,
) -> list[dict[str, Any]]:
    scenes = artifact.get("scenes")
    if not isinstance(scenes, list) or not scenes or not isinstance(value, list) or not value:
        raise ValueError("chunk 时间轴数据不完整")
    rebuilt = [
        _rebuild_scene(scenes, item, target_index, duration)
        for target_index, item in enumerate(value)
    ]
    artifact["scenes"] = rebuilt
    return timeline_chunks(artifact)


def _timeline_chunk(
    scene: dict[str, Any],
    *,
    index: int,
    source_index: int,
    start: float,
    end: float,
    source_scene_start: float,
    source_scene_end: float,
) -> dict[str, Any]:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(scene.get("html") or "")))
    label = re.sub(r"\s+", " ", text).strip()[:32] or f"Chunk {index + 1}"
    return {
        "index": index,
        "chunkId": timeline_chunk_id(scene, index),
        "sourceIndex": source_index,
        "sourceStartSeconds": round(
            optional_seconds(scene.get("_timelineSourceStartSeconds"), source_scene_start),
            3,
        ),
        "sourceEndSeconds": round(
            optional_seconds(scene.get("_timelineSourceEndSeconds"), source_scene_end),
            3,
        ),
        "originalSourceStartSeconds": round(
            optional_seconds(scene.get("_timelineOriginalSourceStartSeconds"), source_scene_start),
            3,
        ),
        "originalSourceEndSeconds": round(
            optional_seconds(scene.get("_timelineOriginalSourceEndSeconds"), source_scene_end),
            3,
        ),
        "startSeconds": round(start, 3),
        "endSeconds": round(end, 3),
        "durationSeconds": round(max(0.1, end - start), 3),
        "label": label,
        "textPosition": timeline_text_position(scene.get("_timelineTextPosition")),
    }


def timeline_chunk_id(scene: Any, index: int) -> str:
    raw = str(scene.get("_timelineChunkId") or "").strip() if isinstance(scene, dict) else ""
    normalized = _CHUNK_ID_PATTERN.sub("-", raw).strip("-._:")[:96]
    return normalized or f"html-motion-chunk-{index + 1}"


def timeline_text_position(value: Any, *, strict: bool = False) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        if strict:
            raise ValueError("文字位置数据不合法")
        return None
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
    except (TypeError, ValueError):
        if strict:
            raise ValueError("文字位置数据不合法") from None
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        if strict:
            raise ValueError("文字位置数据不合法")
        return None
    return {
        "x": round(min(100.0, max(0.0, x)), 3),
        "y": round(min(100.0, max(0.0, y)), 3),
    }


def _source_scene(
    source_scenes: Any,
    source_index: int,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    if (
        isinstance(source_scenes, list)
        and 0 <= source_index < len(source_scenes)
        and isinstance(source_scenes[source_index], dict)
    ):
        return source_scenes[source_index]
    return fallback


def _rebuild_scene(
    scenes: list[Any],
    item: Any,
    target_index: int,
    duration: float,
) -> dict[str, Any]:
    source_index, start = _chunk_location(item, target_index)
    if source_index < 0 or source_index >= len(scenes):
        raise ValueError("chunk 来源片段不存在")
    source_scene = scenes[source_index]
    if not isinstance(source_scene, dict):
        raise ValueError("chunk 来源片段不合法")
    source_scene_start, source_scene_end = _scene_source_bounds(source_scene)
    source_start, source_end = _chunk_source_range(
        item,
        start=start,
        source_scene_start=source_scene_start,
        source_scene_end=source_scene_end,
    )
    # 历史坏数据可能带入零长度边界；只在来源区间内补齐，不能把合并后的
    # 源时间坐标误裁到场景在总时间轴上的 start/end。
    if round(source_end - source_start, 3) < 0.1:
        source_end = min(source_scene_end, source_start + 0.1)
        source_start = max(source_scene_start, source_end - 0.1)
    chunk_duration = round(max(0.1, source_end - source_start), 3)
    restore_bounds = normalize_restore_bounds(
        item,
        visible_start_seconds=source_start,
        visible_end_seconds=source_end,
        source_duration_seconds=source_scene_end,
    )
    start = min(max(0.0, start), max(0.0, duration - chunk_duration))
    scene = copy.deepcopy(source_scene)
    _reindex_scene_references(scene, source_index + 1, target_index + 1)
    scene.update(
        {
            "start": round(start, 3),
            "end": round(start + chunk_duration, 3),
            "_timelineSourceIndex": source_index,
            "_timelineSourceStartSeconds": round(source_start, 3),
            "_timelineSourceEndSeconds": round(source_end, 3),
            "_timelineOriginalSourceStartSeconds": round(
                max(source_scene_start, restore_bounds.start_seconds), 3
            ),
            "_timelineOriginalSourceEndSeconds": round(
                min(source_scene_end, restore_bounds.end_seconds), 3
            ),
            "_timelineChunkId": _chunk_id_from_item(item, source_index, target_index),
        }
    )
    text_position = timeline_text_position(item.get("textPosition"), strict=True)
    if text_position is None:
        scene.pop("_timelineTextPosition", None)
    else:
        scene["_timelineTextPosition"] = text_position
    return scene


def _chunk_id_from_item(item: dict[str, Any], source_index: int, target_index: int) -> str:
    candidate = timeline_chunk_id({"_timelineChunkId": item.get("chunkId")}, target_index)
    if item.get("chunkId"):
        return candidate
    return f"html-motion-source-{source_index + 1}-chunk-{target_index + 1}"


def _chunk_location(item: Any, target_index: int) -> tuple[int, float]:
    if not isinstance(item, dict):
        raise ValueError("chunk 时间轴数据不合法")
    try:
        return (
            int(item.get("sourceIndex", item.get("index", target_index))),
            float(item.get("startSeconds")),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("chunk 时间轴数据不合法") from exc


def _scene_source_bounds(scene: dict[str, Any]) -> tuple[float, float]:
    """返回 scene 所属源时间轴的可恢复范围，而非合并输出时间轴范围。"""
    fallback_start = float(scene.get("start") or 0.0)
    fallback_end = float(scene.get("end") or fallback_start)
    source_start = optional_seconds(
        scene.get("_timelineSourceStartSeconds"), fallback_start
    )
    source_end = optional_seconds(scene.get("_timelineSourceEndSeconds"), fallback_end)
    original_start = optional_seconds(
        scene.get("_timelineOriginalSourceStartSeconds"), source_start
    )
    original_end = optional_seconds(
        scene.get("_timelineOriginalSourceEndSeconds"), source_end
    )
    return min(source_start, original_start), max(source_end, original_end)


def _chunk_source_range(
    item: dict[str, Any],
    *,
    start: float,
    source_scene_start: float,
    source_scene_end: float,
) -> tuple[float, float]:
    source_start = optional_seconds(item.get("sourceStartSeconds"), source_scene_start)
    source_end = item.get("sourceEndSeconds")
    if source_end is None:
        raw_duration = item.get("durationSeconds")
        if raw_duration is None:
            raw_end = item.get("endSeconds")
            raw_duration = (
                float(raw_end) - start
                if raw_end is not None
                else source_scene_end - source_scene_start
            )
        source_end = source_start + float(raw_duration)
    try:
        source_start = float(source_start)
        source_end = float(source_end)
    except (TypeError, ValueError) as exc:
        raise ValueError("chunk 时间轴数据不合法") from exc
    if not math.isfinite(start) or not math.isfinite(source_start) or not math.isfinite(source_end):
        raise ValueError("chunk 时间范围不合法")
    source_start = min(max(source_scene_start, source_start), source_scene_end)
    source_end = min(max(source_start, source_end), source_scene_end)
    return source_start, source_end


def _reindex_scene_references(
    scene: dict[str, Any],
    source_number: int,
    target_number: int,
) -> None:
    if source_number == target_number:
        return
    source_id = f"scene-{source_number}-"
    target_id = f"scene-{target_number}-"
    source_wrapper = f"#hf-scene-{source_number}"
    target_wrapper = f"#hf-scene-{target_number}"

    def replace(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.replace(source_wrapper, target_wrapper).replace(source_id, target_id)

    scene["html"] = replace(scene.get("html"))
    scene["css"] = replace(scene.get("css"))
    scene["ids"] = [replace(value) for value in scene.get("ids", [])]
    for animation in scene.get("animations", []):
        if isinstance(animation, dict):
            animation["target"] = replace(animation.get("target"))
