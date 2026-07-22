"""HyperFrames Harness 的确定性视觉方案。"""

from __future__ import annotations

import re
from typing import Any


DESIGN_DIRECTIONS = {"editorial", "signal", "orbit", "grid"}
LAYOUT_RECIPES = {
    "editorial-split", "signal-frame", "orbit-focus", "grid-brief",
}
MOTION_RECIPES = {
    "editorial-reveal", "kinetic-snap", "orbital-drift", "grid-build",
}
COMPONENT_RECIPES = {"message-flow", "timeline-track", "signal-wave", "network-orbit"}
DENSITIES = {"balanced", "rich"}
ANCHORS = {
    "top-left", "top-right", "bottom-left", "bottom-right",
    "left-rail", "right-rail", "top-band", "bottom-band",
}
ANCHOR_ALIASES = {
    "top": "top-band", "bottom": "bottom-band", "left": "left-rail",
    "right": "right-rail", "upper-left": "top-left", "upper-right": "top-right",
    "lower-left": "bottom-left", "lower-right": "bottom-right",
}
PALETTES = {
    "editorial": {"accent": "#F2C14E", "support": "#E76F51", "text": "#FFF8E7"},
    "signal": {"accent": "#78A7FF", "support": "#FFB76B", "text": "#F7F3EC"},
    "orbit": {"accent": "#8EE3EF", "support": "#B8A1FF", "text": "#F4F7FF"},
    "grid": {"accent": "#7DE2D1", "support": "#F4A261", "text": "#F4F1DE"},
}
LAYOUT_BY_DIRECTION = {
    "editorial": "editorial-split", "signal": "signal-frame",
    "orbit": "orbit-focus", "grid": "grid-brief",
}
MOTION_BY_DIRECTION = {
    "editorial": "editorial-reveal", "signal": "kinetic-snap",
    "orbit": "orbital-drift", "grid": "grid-build",
}
COMPONENTS_BY_DIRECTION = {
    "editorial": ("timeline-track", "message-flow"),
    "signal": ("signal-wave", "timeline-track"),
    "orbit": ("network-orbit", "signal-wave"),
    "grid": ("message-flow", "timeline-track"),
}


def normalize_design_plan(value: dict[str, Any]) -> dict[str, Any]:
    direction = _choice(
        value.get("designDirection") or value.get("theme"),
        DESIGN_DIRECTIONS,
        "editorial",
    )
    layout = _choice(value.get("layoutRecipe"), LAYOUT_RECIPES, LAYOUT_BY_DIRECTION[direction])
    motion = _normalize_motion(value, direction)
    components = _normalize_components(value, direction)
    density = _choice(value.get("density"), DENSITIES, "balanced")
    anchor = _normalize_anchor(value.get("anchor") or value.get("layout"))
    return {
        "designDirection": direction,
        "layoutRecipe": layout,
        "motionRecipe": motion,
        "componentRecipes": components,
        "density": density,
        "anchor": anchor,
        "palette": _normalize_palette(value.get("palette"), direction),
    }


def scene_layout(plan: dict[str, Any], index: int) -> str:
    primary = plan["layoutRecipe"]
    alternates = _layout_sequence(primary)
    return alternates[min(index, len(alternates) - 1)]


def scene_anchor(
    plan: dict[str, Any],
    index: int,
    *,
    portrait: bool = False,
    beat_role: str = "",
) -> str:
    role_anchor = {
        "problem": "top-left",
        "change": "top-right",
        # Portrait safe zones are usually upper; keep result on an edge, not mid-frame.
        "result": "bottom-left" if not portrait else "top-left",
    }.get(str(beat_role or "").strip().lower())
    if role_anchor:
        return role_anchor
    primary = plan["anchor"]
    if portrait:
        right_first = primary in {"top-right", "bottom-right", "right-rail"}
        sequence = (
            ("top-right", "top-left", "bottom-right", "bottom-left")
            if right_first else
            ("top-left", "top-right", "bottom-left", "bottom-right")
        )
        return sequence[index % len(sequence)]
    pairs = {
        "top-left": ("top-left", "bottom-right"),
        "top-right": ("top-right", "bottom-left"),
        "bottom-left": ("bottom-left", "top-right"),
        "bottom-right": ("bottom-right", "top-left"),
        "left-rail": ("left-rail", "right-rail"),
        "right-rail": ("right-rail", "left-rail"),
        "top-band": ("top-band", "bottom-band"),
        "bottom-band": ("bottom-band", "top-band"),
    }
    return pairs[primary][index % 2]


def scene_component(plan: dict[str, Any], index: int) -> str:
    recipes = plan["componentRecipes"]
    return recipes[min(index, len(recipes) - 1)]


def _layout_sequence(primary: str) -> tuple[str, ...]:
    groups = {
        "editorial-split": ("editorial-split", "grid-brief"),
        "signal-frame": ("signal-frame", "editorial-split"),
        "orbit-focus": ("orbit-focus", "editorial-split"),
        "grid-brief": ("grid-brief", "signal-frame"),
    }
    return groups[primary]


def _normalize_motion(value: dict[str, Any], direction: str) -> str:
    candidate = value.get("motionRecipe")
    recipes = value.get("motionRecipes")
    if not candidate and isinstance(recipes, list) and recipes:
        candidate = recipes[0]
    legacy = str(value.get("motion") or "").strip().lower()
    legacy_map = {
        "slide": "editorial-reveal", "drift": "grid-build",
        "orbit": "orbital-drift", "pulse": "kinetic-snap",
    }
    candidate = candidate or legacy_map.get(legacy)
    return _choice(candidate, MOTION_RECIPES, MOTION_BY_DIRECTION[direction])


def _normalize_components(value: dict[str, Any], direction: str) -> list[str]:
    raw = value.get("componentRecipes")
    values = raw if isinstance(raw, list) else [value.get("componentRecipe")]
    result: list[str] = []
    for item in values:
        candidate = str(item or "").strip().lower()
        if candidate in COMPONENT_RECIPES and candidate not in result:
            result.append(candidate)
    return result[:3] or list(COMPONENTS_BY_DIRECTION[direction])


def _normalize_anchor(value: Any) -> str:
    raw = re.sub(r"[\s_]+", "-", str(value or "top-right").strip().lower()).strip("-")
    raw = ANCHOR_ALIASES.get(raw, raw)
    return raw if raw in ANCHORS else "top-right"


def _normalize_palette(value: Any, direction: str) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    defaults = PALETTES[direction]
    return {key: _color(source.get(key), defaults[key]) for key in defaults}


def _color(value: Any, default: str) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if re.fullmatch(r"#[0-9A-F]{6}", candidate) else default


def _choice(value: Any, allowed: set[str], default: str) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in allowed else default
