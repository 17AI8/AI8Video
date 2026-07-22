"""已通过 harness 契约校验的 HyperFrames composition 渲染器。

本模块只把确定性的 artifact 编译成 index.html 和 motion manifest，
不启动 CLI、不处理进程、不执行 FFmpeg。
"""

from __future__ import annotations

import html
import json
import re
from typing import Any


def build_composition_html(
    artifact: dict[str, Any],
    media: dict[str, Any],
    *,
    font_family: str = "",
) -> str:
    width, height = int(media["width"]), int(media["height"])
    duration = float(media["durationSeconds"])
    palette = artifact["design"]["palette"]
    fixed = artifact.get("layoutMode") == "fixed-semantic"
    scene_markup = "\n".join(
        _scene_markup(scene, index, fixed=fixed) for index, scene in enumerate(artifact["scenes"])
    )
    scene_css = "\n".join(scene["css"] for scene in artifact["scenes"])
    motion_plan = _motion_plan(artifact, duration)
    css = _base_css(width, height, palette, scene_css, font_family, media.get("textStyle"))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width={width}, height={height}"><script src="./waapi-timeline-runtime.js"></script>
<style>{css}</style></head>
<body><div id="root" data-composition-id="html-motion" data-no-timeline data-start="0" data-width="{width}" data-height="{height}" data-duration="{duration:.3f}">{scene_markup}</div>
<script>window.AI8WaapiTimeline.mount({_safe_json(motion_plan)});</script></body></html>"""


def _base_css(
    width: int,
    height: int,
    palette: dict[str, str],
    scene_css: str,
    font_family: str,
    text_style: dict[str, Any] | None = None,
) -> str:
    font_css = ""
    if font_family == "AI8VideoFlower":
        font_css = "@font-face{font-family:'AI8VideoFlower';src:url('./flower-font.otf');font-display:block;}"
        active_font = "'AI8VideoFlower',sans-serif"
    elif font_family:
        # Declared motion font must ship with matching local file for HyperFrames lint.
        font_css = (
            f"@font-face{{font-family:'{font_family}';src:url('./motion-font.otf');font-display:block;}}"
        )
        active_font = f"'{font_family}',sans-serif"
    else:
        # Generic only — named system CJK stacks fail HyperFrames font lint.
        active_font = "sans-serif"
    style = text_style if isinstance(text_style, dict) else {}
    motion_text = _safe_css_color(style.get("textColor"), palette["text"])
    motion_stroke = _safe_css_color(style.get("strokeColor"), "#121826")
    motion_stroke_width = _safe_stroke_width(style.get("strokeWidth"))
    return f"""{font_css}html,body{{margin:0;width:{width}px;height:{height}px;background:transparent;overflow:hidden}}
*{{box-sizing:border-box}}#root{{position:relative;width:{width}px;height:{height}px;overflow:hidden;background:transparent;--accent:{palette['accent']};--support:{palette['support']};--text:{palette['text']};--motion-text:{motion_text};--motion-stroke:{motion_stroke};--motion-stroke-width:{motion_stroke_width}px}}.clip{{position:absolute;inset:0}}
.hf-zone{{position:absolute;display:block;overflow:hidden!important;isolation:isolate;color:var(--text);font-family:{active_font}}}
.hf-zone h1,.hf-zone h2,.hf-zone h3,.hf-zone p,.hf-zone small{{position:relative!important;left:auto!important;right:auto!important;top:auto!important;bottom:auto!important;width:auto!important;height:auto!important;margin:0 0 10px;max-width:100%!important;overflow-wrap:anywhere;word-break:break-word;white-space:normal!important;text-wrap:balance}}
.hf-zone h1,.hf-zone h2,.hf-zone h3{{font-weight:800;line-height:1.1!important}}.hf-zone p{{line-height:1.3!important}}.hf-zone small{{line-height:1.3!important}}
.hf-zone h1{{font-size:30px!important}}.hf-zone h2{{font-size:26px!important}}.hf-zone h3{{font-size:22px!important}}.hf-zone p{{font-size:18px!important}}.hf-zone small{{font-size:14px!important}}
.hf-zone svg{{display:block;max-width:100%!important;max-height:100%!important;overflow:hidden!important;contain:paint}}.hf-zone svg *{{vector-effect:non-scaling-stroke}}
.zone-top-left{{left:4%;top:5%;width:30%;height:18%}}.zone-top-right{{right:4%;top:5%;width:30%;height:18%}}.zone-bottom-left{{left:4%;bottom:8%;width:30%;height:18%}}.zone-bottom-right{{right:4%;bottom:8%;width:30%;height:18%}}.zone-left-rail{{left:3%;top:24%;width:22%;height:50%}}.zone-right-rail{{right:3%;top:24%;width:22%;height:50%}}.zone-top-band{{left:8%;top:4%;width:84%;height:14%}}.zone-bottom-band{{left:8%;bottom:6%;width:84%;height:14%}}{scene_css}"""


def _safe_css_color(value: Any, fallback: str) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if re.fullmatch(r"#[0-9A-F]{6}", candidate) else fallback


def _safe_stroke_width(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.8
    if number > 3.0:
        number = min(1.2, number / 8.0)
    return round(min(1.5, max(0.0, number)), 2)


def build_motion_manifest(artifact: dict[str, Any], media: dict[str, Any]) -> dict[str, Any]:
    assertions: list[dict[str, Any]] = []
    for scene in artifact["scenes"]:
        entrance = next(
            (item for item in scene["animations"] if item["kind"] == "entrance"),
            None,
        )
        if entrance is None:
            raise ValueError("HyperFrames 场景缺少 entrance 动画")
        selector = str(entrance["target"] or "")
        # Staggered glyph lines match multiple nodes; assert on the first line only.
        if selector.endswith(" .hf-line"):
            selector = f"{selector}:first-child"
        assertions.append({
            "kind": "appearsBy",
            "selector": selector,
            "bySec": round(scene["start"] + entrance["at"] + entrance["duration"], 3),
        })
        assertions.extend(
            {"kind": "staysInFrame", "selector": f"#{identifier}"}
            for identifier in scene["ids"]
        )
    return {"duration": float(media["durationSeconds"]), "assertions": assertions}


def _scene_markup(scene: dict[str, Any], index: int, *, fixed: bool = False) -> str:
    scene_duration = scene["end"] - scene["start"]
    zone_class = f"hf-zone zone-{html.escape(scene['zone'])}"
    if fixed:
        zone_class += " hf-fixed-zone"
    return (
        f'<section id="hf-scene-{index + 1}" class="hf-scene clip" '
        f'data-start="{scene["start"]:.3f}" data-duration="{scene_duration:.3f}" data-track-index="1">'
        f'<div class="{zone_class}">{scene["html"]}</div></section>'
    )


def _motion_plan(artifact: dict[str, Any], duration: float) -> dict[str, Any]:
    animations: list[dict[str, Any]] = []
    for scene in artifact["scenes"]:
        for animation in scene["animations"]:
            animations.append({
                "target": animation["target"],
                "kind": animation["kind"],
                "at": round(scene["start"] + animation["at"], 3),
                "duration": animation["duration"],
                "from": animation["from"],
                "to": animation["to"],
            })
        for target in sorted({item["target"] for item in scene["animations"]}):
            animations.append({
                "target": target,
                "kind": "scene-end",
                "at": round(scene["end"], 3),
                "duration": 0.001,
                "from": {},
                "to": {"autoAlpha": 0.0, "ease": "linear"},
            })
    return {"runtime": "waapi-v1", "duration": duration, "animations": animations}


def _safe_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
