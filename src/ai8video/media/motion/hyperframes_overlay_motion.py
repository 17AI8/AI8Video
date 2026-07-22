"""HyperFrames Harness 的白名单动画配方。"""

from __future__ import annotations

from typing import Any


def build_scene_animations(
    role_ids: dict[str, list[str]],
    *,
    recipe: str,
    duration: float,
    scene_number: int,
    component_recipe: str,
    density: str = "balanced",
) -> list[dict[str, Any]]:
    animations: list[dict[str, Any]] = []
    animations.extend(_pair_title_motion(role_ids, recipe, duration, scene_number))
    animations.extend(_structure_motion(role_ids, recipe, density))
    if density == "rich":
        animations.extend(_decorative_motion(
            role_ids, recipe, duration, scene_number, component_recipe,
        ))
    return _clamp_animations(animations, duration)


def _pair_title_motion(
    role_ids: dict[str, list[str]],
    recipe: str,
    duration: float,
    index: int,
) -> list[dict[str, Any]]:
    """同拍两段错时出场：先 question？，后 result！。"""
    question_ids = role_ids.get("question") or []
    result_ids = role_ids.get("result") or []
    if not question_ids or not result_ids:
        # 兼容旧单 title
        titles = role_ids.get("title") or []
        if not titles:
            return []
        return _single_title_motion(titles[0], recipe, duration, index)

    entrances = {
        "editorial-reveal": {"x": -22.0 if index % 2 else 22.0, "autoAlpha": 0.0},
        "kinetic-snap": {"y": 28.0, "scale": 0.88, "autoAlpha": 0.0},
        "orbital-drift": {"x": 16.0, "y": 14.0, "scale": 0.92, "autoAlpha": 0.0},
        "grid-build": {"y": -20.0, "autoAlpha": 0.0},
    }
    from_state = entrances[recipe]
    to_state = {
        "x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0,
        "autoAlpha": 1.0, "stagger": 0.07, "ease": "expo.out",
    }
    # 错时：结果段约在本拍时长 30% 处再入场，最短 0.42s，最长 0.85s。
    result_at = round(min(0.85, max(0.42, duration * 0.3)), 3)
    items = [
        _animation(f"{question_ids[0]} .hf-line", "entrance", 0.04, 0.34, from_state, to_state),
        _animation(f"{result_ids[0]} .hf-line", "entrance", result_at, 0.34, from_state, to_state),
    ]
    ambient_end = max(0.9, duration - 0.55)
    for offset, identifier in enumerate((question_ids[0], result_ids[0])):
        items.append(_animation(identifier, "ambient", 0.68 + offset * 0.08, ambient_end - 0.68, {}, {
            "y": -3.0 if (index + offset) % 2 else 3.0, "ease": "sine.inOut",
        }))
        items.append(_animation(identifier, "exit", max(0.72, duration - 0.48), 0.4, {}, {
            "y": -8.0, "autoAlpha": 0.0, "ease": "power2.in",
        }))
    return items


def _single_title_motion(
    identifier: str,
    recipe: str,
    duration: float,
    index: int,
) -> list[dict[str, Any]]:
    entrances = {
        "editorial-reveal": {"x": -22.0 if index % 2 else 22.0, "autoAlpha": 0.0},
        "kinetic-snap": {"y": 28.0, "scale": 0.88, "autoAlpha": 0.0},
        "orbital-drift": {"x": 16.0, "y": 14.0, "scale": 0.92, "autoAlpha": 0.0},
        "grid-build": {"y": -20.0, "autoAlpha": 0.0},
    }
    entrance = _animation(
        f"{identifier} .hf-line",
        "entrance",
        0.04,
        0.34,
        entrances[recipe],
        {
            "x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0,
            "autoAlpha": 1.0, "stagger": 0.08, "ease": "expo.out",
        },
    )
    ambient_end = max(0.9, duration - 0.55)
    ambient = _animation(identifier, "ambient", 0.68, ambient_end - 0.68, {}, {
        "y": -3.0 if index % 2 else 3.0, "ease": "sine.inOut",
    })
    exit_item = _animation(identifier, "exit", max(0.72, duration - 0.48), 0.4, {}, {
        "y": -8.0, "autoAlpha": 0.0, "ease": "power2.in",
    })
    return [entrance, ambient, exit_item]


def _structure_motion(
    role_ids: dict[str, list[str]],
    recipe: str,
    density: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    rules = role_ids.get("rules") or []
    if rules:
        items.append(_animation(rules[0], "entrance", 0.18, 0.42, {
            "scaleX": 0.0, "autoAlpha": 0.0,
        }, {"scaleX": 1.0, "autoAlpha": 1.0, "transformOrigin": "left center", "ease": "circ.out"}))
    frame_ids = role_ids.get("frame") or []
    if density == "rich" and frame_ids:
        items.append(_animation(frame_ids[0], "entrance", 0.36, 0.4, {
            "scale": 0.9, "autoAlpha": 0.0,
        }, {"scale": 1.0, "autoAlpha": 0.7, "ease": "power2.out"}))
    if density == "rich" and len(rules) > 1:
        items.append(_animation(rules[1], "entrance", 0.42, 0.36, {
            "scaleX": 0.0, "autoAlpha": 0.0,
        }, {"scaleX": 1.0, "autoAlpha": 0.55, "transformOrigin": "left center", "ease": "circ.out"}))
    return items


def _decorative_motion(
    role_ids: dict[str, list[str]],
    recipe: str,
    duration: float,
    scene_number: int,
    component_recipe: str,
) -> list[dict[str, Any]]:
    graphic_ids = role_ids.get("graphic") or []
    items: list[dict[str, Any]] = []
    if graphic_ids:
        items.extend(_graphic_motion(
            graphic_ids[0], recipe, component_recipe, duration, scene_number,
        ))
    for index, identifier in enumerate(role_ids.get("accents") or []):
        items.append(_animation(identifier, "entrance", 0.48 + index * 0.1, 0.34, {
            "autoAlpha": 0.0,
        }, {"autoAlpha": 0.55, "ease": "power1.out"}))
        items.append(_animation(identifier, "ambient", 0.95 + index * 0.06, max(0.35, duration - 1.2), {}, {
            "autoAlpha": 0.4 if index % 2 else 0.6,
            "ease": "sine.inOut",
        }))
    return items


def _graphic_motion(
    identifier: str,
    recipe: str,
    component_recipe: str,
    duration: float,
    index: int,
) -> list[dict[str, Any]]:
    del recipe, component_recipe, index
    entrance = _animation(identifier, "entrance", 0.44, 0.4, {
        "autoAlpha": 0.0,
    }, {"autoAlpha": 0.7, "ease": "power2.out"})
    span = max(0.45, duration - 1.15)
    ambient = _animation(identifier, "ambient", 0.95, span, {}, {
        "autoAlpha": 0.55, "ease": "sine.inOut",
    })
    return [entrance, ambient]


def _animation(
    identifier: str,
    kind: str,
    at: float,
    duration: float,
    start: dict[str, Any],
    end: dict[str, Any],
) -> dict[str, Any]:
    if identifier.startswith("#"):
        target = identifier
    elif " " in identifier:
        head, _, rest = identifier.partition(" ")
        target = f"#{head} {rest}"
    else:
        target = f"#{identifier}"
    return {
        "target": target, "kind": kind,
        "at": round(max(0.0, at), 3), "duration": round(max(0.08, duration), 3),
        "from": start, "to": end,
    }


def _clamp_animations(animations: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    maximum = max(0.08, duration)
    for item in animations:
        at = min(float(item["at"]), max(0.0, maximum - 0.08))
        remaining = max(0.08, maximum - at)
        item["at"] = round(at, 3)
        item["duration"] = round(min(float(item["duration"]), remaining), 3)
    return animations
