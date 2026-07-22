from __future__ import annotations

import json
import math
import re
from typing import Any

from ai8video.media.motion.hyperframes_overlay_safety import (
    css_has_blocked_pattern,
    inspect_fragment,
    is_host_layout_property,
    normalize_scene_text,
    sanitize_inline_styles,
)

MIN_COVERAGE_RATIO = 0.7
MAX_SCENES = 5
MAX_HTML_LENGTH = 12_000
MAX_CSS_LENGTH = 12_000
ALLOWED_ZONES = {
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "left-rail",
    "right-rail",
    "top-band",
    "bottom-band",
}
ZONE_ALIASES = {
    "top": "top-band", "top-center": "top-band", "top-centre": "top-band",
    "center-top": "top-band", "upper": "top-band", "upper-center": "top-band",
    "bottom": "bottom-band", "bottom-center": "bottom-band", "bottom-centre": "bottom-band",
    "center-bottom": "bottom-band", "lower": "bottom-band", "lower-center": "bottom-band",
    "left": "left-rail", "right": "right-rail",
    "upper-left": "top-left", "upper-right": "top-right",
    "lower-left": "bottom-left", "lower-right": "bottom-right",
    "top-left-corner": "top-left", "top-right-corner": "top-right",
    "bottom-left-corner": "bottom-left", "bottom-right-corner": "bottom-right",
    "左上": "top-left", "右上": "top-right", "左下": "bottom-left", "右下": "bottom-right",
    "顶部": "top-band", "上方": "top-band", "底部": "bottom-band", "下方": "bottom-band",
    "左侧": "left-rail", "右侧": "right-rail",
}
ALLOWED_TWEEN_KEYS = {
    "x", "y", "scale", "scaleX", "scaleY", "rotation", "autoAlpha", "opacity",
    "color", "backgroundColor", "borderRadius", "transformOrigin", "ease",
}
def _parse_artifact(
    raw: str,
    media: dict[str, Any],
    dialogue_text: str | None = None,
) -> dict[str, Any]:
    return _normalize_artifact(_parse_json_object(raw), media, dialogue_text)


def _normalize_artifact(
    value: dict[str, Any],
    media: dict[str, Any],
    dialogue_text: str | None = None,
) -> dict[str, Any]:
    design = _normalize_design(value.get("design"))
    scenes_value = value.get("scenes")
    if not isinstance(scenes_value, list) or not scenes_value:
        raise ValueError("HyperFrames 编排未返回 scenes")
    if len(scenes_value) > MAX_SCENES:
        raise ValueError("HyperFrames 编排场景过多")
    duration = float(media["durationSeconds"])
    scenes = [
        _normalize_scene(scene, duration, index, dialogue_text)
        for index, scene in enumerate(scenes_value)
    ]
    _validate_scene_timeline(scenes, duration)
    return {"design": design, "scenes": scenes}


def _normalize_design(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("HyperFrames 编排缺少设计方向")
    candidates = [_short_text(item, 40) for item in value.get("candidates", []) if _short_text(item, 40)]
    if not 3 <= len(candidates) <= 5:
        raise ValueError("HyperFrames 编排必须先产生 3 到 5 个设计方向")
    chosen = _short_text(value.get("chosen"), 40)
    if not chosen:
        raise ValueError("HyperFrames 编排未选定设计方向")
    chosen = _resolve_chosen_direction(chosen, candidates)
    palette = value.get("palette")
    if not isinstance(palette, dict):
        raise ValueError("HyperFrames 编排缺少调色板")
    colors = {key: _normalize_color(palette.get(key)) for key in ("accent", "support", "text")}
    return {
        "candidates": candidates,
        "chosen": chosen,
        "concept": _short_text(value.get("concept"), 120),
        "palette": colors,
        "typography": _short_text(value.get("typography"), 24) or "humanist",
    }


def _normalize_scene(
    value: Any,
    duration: float,
    index: int,
    dialogue_text: str | None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("HyperFrames 场景必须是对象")
    start = _seconds(value.get("start"), duration, "start")
    end = _seconds(value.get("end"), duration, "end")
    if end - start < min(0.5, duration):
        raise ValueError("HyperFrames 场景时长过短")
    zone = _normalize_zone(value.get("zone"))
    roles = {str(item).strip().lower() for item in value.get("roles", [])}
    if not {"content", "structure", "decorative"}.issubset(roles):
        roles.update({"content", "structure", "decorative"})
    fragment, ids, element_count, id_map = _normalize_html(value.get("html"), index, dialogue_text)
    if not 1 <= element_count <= 24:
        raise ValueError("HyperFrames 场景视觉元素数量不合理")
    css = _normalize_css(value.get("css"), id_map)
    animations = _normalize_animations(value.get("animations"), ids, end - start, id_map)
    animations = _ensure_ambient_motion(animations, end - start)
    _validate_motion_craft(animations)
    return {
        "start": start, "end": end, "zone": zone, "roles": sorted(roles),
        "html": fragment, "css": css, "animations": animations, "ids": sorted(ids),
    }


def _normalize_zone(value: Any) -> str:
    raw = str(value or "").strip().lower()
    normalized = re.sub(r"[\s_]+", "-", raw).strip("-")
    normalized = re.sub(r"^zone-", "", normalized)
    zone = ZONE_ALIASES.get(normalized, normalized)
    if zone not in ALLOWED_ZONES:
        raise ValueError("HyperFrames 场景安全区域不合法")
    return zone


def _normalize_html(
    value: Any,
    scene_index: int,
    dialogue_text: str | None,
) -> tuple[str, set[str], int, dict[str, str]]:
    fragment = str(value or "").strip()
    if not fragment or len(fragment) > MAX_HTML_LENGTH:
        raise ValueError("HyperFrames 场景 HTML 为空或过长")
    fragment = normalize_scene_text(sanitize_inline_styles(fragment), dialogue_text)
    inspector = inspect_fragment(fragment)
    if not inspector.ids:
        raise ValueError("HyperFrames 场景没有可编排的 id 元素")
    id_map = {
        identifier: _scene_scoped_id(identifier, scene_index)
        for identifier in inspector.ids
    }
    for original, scoped in sorted(id_map.items(), key=lambda item: -len(item[0])):
        fragment = re.sub(
            rf"(\bid\s*=\s*['\"]){re.escape(original)}(['\"])",
            rf"\g<1>{scoped}\2",
            fragment,
        )
    return fragment, set(id_map.values()), inspector.element_count, id_map


def _normalize_css(value: Any, id_map: dict[str, str]) -> str:
    css = str(value or "").strip()
    if len(css) > MAX_CSS_LENGTH:
        raise ValueError("HyperFrames 场景 CSS 过长")
    if not css:
        return ""
    if css_has_blocked_pattern(css):
        return ""
    css = re.sub(r"font-family\s*:[^;}{]+;?", "", css, flags=re.IGNORECASE)
    for original, scoped in sorted(id_map.items(), key=lambda item: -len(item[0])):
        css = re.sub(rf"#{re.escape(original)}(?![a-z0-9-])", f"#{scoped}", css)
    return re.sub(r";{2,}", ";", css).replace("{;", "{")


def _strip_host_layout_css(css: str) -> str:
    return re.sub(
        r"([;{])\s*([\w-]+)\s*:[^;{}]*(?=;|})",
        lambda match: match.group(1) if is_host_layout_property(match.group(2)) else match.group(0),
        css,
        flags=re.IGNORECASE,
    )


def _normalize_animations(
    value: Any,
    ids: set[str],
    scene_duration: float,
    id_map: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("HyperFrames 场景缺少动画编排")
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("HyperFrames 动画必须是对象")
        target = _resolve_animation_target(item.get("target"), id_map, ids)
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in {"entrance", "ambient", "exit"}:
            raise ValueError("HyperFrames 动画 kind 不合法")
        max_at = max(0.0, scene_duration - 0.05)
        at = _bounded_float(item.get("at"), 0.0, 0.0, max_at)
        duration = _bounded_float(item.get("duration"), 0.5, 0.05, scene_duration - at)
        from_vars = _normalize_tween_vars(item.get("from"), allow_empty=kind != "entrance")
        to_vars = _normalize_tween_vars(item.get("to"), allow_empty=False)
        if kind == "entrance":
            from_vars["autoAlpha"] = 0.0
            to_vars["autoAlpha"] = 1.0
        normalized.append({
            "target": target, "kind": kind, "at": round(at, 3),
            "duration": round(duration, 3), "from": from_vars, "to": to_vars,
        })
    return normalized


def _resolve_animation_target(
    value: Any,
    id_map: dict[str, str],
    ids: set[str],
) -> str:
    raw = str(value or "").strip().lstrip("#")
    direct = id_map.get(raw, raw)
    if direct in ids:
        return f"#{direct}"
    token = _target_token(raw)
    matches = sorted(identifier for identifier in ids if _target_token(identifier) == token)
    if matches:
        return f"#{matches[0]}"
    preferred = next((
        identifier for identifier in sorted(ids)
        if re.search(r"(?:title|text|label|copy)", _target_token(identifier))
    ), None)
    return f"#{preferred or sorted(ids)[0]}"


def _target_token(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return re.sub(r"^scene-[0-9]+-", "", normalized)


def _ensure_ambient_motion(
    animations: list[dict[str, Any]],
    scene_duration: float,
) -> list[dict[str, Any]]:
    minimum_duration = round(scene_duration * 0.4, 3)
    ambient = [item for item in animations if item["kind"] == "ambient"]
    if ambient:
        item = max(ambient, key=lambda entry: entry["duration"])
        item["at"] = min(item["at"], scene_duration - minimum_duration)
        item["duration"] = round(max(item["duration"], minimum_duration), 3)
        if not {"x", "y", "scale", "scaleX", "scaleY", "rotation"} & set(item["to"]):
            item["to"].setdefault("scale", 1.03)
            item["to"].setdefault("ease", "sine.inOut")
        return animations

    source = next((item for item in animations if item["kind"] == "entrance"), None)
    if source is None:
        return animations
    at = min(source["at"] + source["duration"], scene_duration - minimum_duration)
    animations.append({
        "target": source["target"], "kind": "ambient", "at": round(at, 3),
        "duration": minimum_duration, "from": {},
        "to": {"scale": 1.03, "ease": "sine.inOut"},
    })
    return animations


def _scene_scoped_id(identifier: str, scene_index: int) -> str:
    prefix = f"scene-{scene_index + 1}-"
    normalized = identifier.lower().replace("_", "-").replace(":", "-")
    if normalized.startswith(prefix):
        return normalized
    slug = re.sub(r"^scene-[0-9]+-", "", normalized)
    return f"{prefix}{slug}"


def _resolve_chosen_direction(chosen: str, candidates: list[str]) -> str:
    if chosen in candidates:
        return chosen
    for candidate in candidates:
        if chosen in candidate or candidate in chosen:
            return candidate
    return candidates[0]


def _normalize_tween_vars(value: Any, *, allow_empty: bool) -> dict[str, Any]:
    if value is None and allow_empty:
        return {}
    if not isinstance(value, dict) or (not value and not allow_empty):
        raise ValueError("HyperFrames 动画参数不合法")
    if any(key not in ALLOWED_TWEEN_KEYS for key in value):
        raise ValueError("HyperFrames 动画包含未允许属性")
    return {key: _normalize_tween_value(key, item) for key, item in value.items()}


def _normalize_tween_value(key: str, value: Any) -> Any:
    if key in {"color", "backgroundColor"}:
        return _normalize_color(value)
    if key in {"ease", "transformOrigin"}:
        text = _short_text(value, 40)
        if not text or not re.fullmatch(r"[a-zA-Z0-9().,% -]+", text):
            raise ValueError("HyperFrames 动画字符串参数不合法")
        return text
    if key in {"x", "y"}:
        return _bounded_float(value, 0.0, -48.0, 48.0)
    if key in {"scale", "scaleX", "scaleY"}:
        return _bounded_float(value, 1.0, 0.85, 1.12)
    if key == "rotation":
        return _bounded_float(value, 0.0, -45.0, 45.0)
    return _bounded_float(value, 0.0, 0.0, 1.0)


def _validate_motion_craft(animations: list[dict[str, Any]]) -> None:
    kinds = {item["kind"] for item in animations}
    if "entrance" not in kinds or "ambient" not in kinds:
        raise ValueError("HyperFrames 每场必须同时有入场和持续运动")


def _validate_scene_timeline(scenes: list[dict[str, Any]], duration: float) -> None:
    cursor = 0.0
    covered = 0.0
    for scene in scenes:
        if scene["start"] < cursor - 0.05:
            raise ValueError("HyperFrames 场景时间不能重叠")
        cursor = scene["end"]
        covered += scene["end"] - scene["start"]
    if covered < duration * MIN_COVERAGE_RATIO:
        raise ValueError("HyperFrames 场景覆盖不足视频时长的 70%")
    eases = {
        item["to"].get("ease")
        for scene in scenes for item in scene["animations"] if item["to"].get("ease")
    }
    if len(eases) < 3:
        raise ValueError("HyperFrames 全片动画缺少足够的节奏变化")


def _parse_critique(raw: str) -> dict[str, Any]:
    value = _parse_json_object(raw)
    scores = value.get("scores")
    axes = ("clarity", "hierarchy", "typography", "motion", "brand")
    if not isinstance(scores, dict) or any(axis not in scores for axis in axes):
        raise ValueError("HyperFrames 视觉评审结果不完整")
    normalized_scores = {axis: int(_bounded_float(scores[axis], 0, 1, 5)) for axis in axes}
    return {
        "scores": normalized_scores,
        "notes": [_short_text(item, 160) for item in value.get("notes", []) if _short_text(item, 160)],
        "revisedArtifact": value.get("revisedArtifact"),
    }


def _artifact_summary(
    artifact: dict[str, Any],
    media: dict[str, Any],
    critique: dict[str, Any],
) -> dict[str, Any]:
    scenes = artifact["scenes"]
    covered = sum(scene["end"] - scene["start"] for scene in scenes)
    duration = float(media["durationSeconds"])
    return {
        "harness": "hyperframes-overlay-v1",
        "designDirection": artifact["design"]["chosen"],
        "sceneCount": len(scenes),
        "elementCount": sum(len(scene["ids"]) for scene in scenes),
        "animationCount": sum(len(scene["animations"]) for scene in scenes),
        "coveredDurationSeconds": round(covered, 3),
        "coverageRatio": round(covered / duration, 3),
        "critiqueScores": critique["scores"],
        "critiqueNotes": critique["notes"],
        "critiqueConverged": None if critique.get("skipped") else bool(critique["converged"]),
        "critiqueSkipped": bool(critique.get("skipped")),
    }


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("HyperFrames 模型输出不是 JSON 对象")
    return value


def _normalize_color(value: Any) -> str:
    color = str(value or "").strip().upper()
    if not re.fullmatch(r"#[0-9A-F]{6}", color):
        raise ValueError("HyperFrames 调色板颜色不合法")
    return color


def _seconds(value: Any, maximum: float, field: str) -> float:
    number = _bounded_float(value, -1.0, 0.0, maximum)
    if number < 0:
        raise ValueError(f"HyperFrames 场景 {field} 不合法")
    return round(number, 3)


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        raise ValueError("HyperFrames 数值参数不是有限数")
    return min(max(number, minimum), maximum)


def _short_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]

