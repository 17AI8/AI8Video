"""固定画布上的边缘文字动效组件。"""

from __future__ import annotations

import html
import re
from typing import Any


def vertical_glyph_lines(text: str, *, limit: int = 10) -> list[str]:
    """一字一行竖排；拉丁/数字 token 整段保留；！？追加在正文之后，不占正文名额。"""
    raw = re.sub(r"\s+", "", str(text or ""))
    mark = ""
    if raw.endswith(("！", "!")):
        mark, raw = "！", raw[:-1]
    elif raw.endswith(("？", "?")):
        mark, raw = "？", raw[:-1]
    raw = raw.strip("，。；;：:")
    if not raw and not mark:
        return []
    tokens = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", raw)
    # 超长时保留尾窗（卡壳/接住），与语义布局硬截策略一致。
    lines = tokens[-max(1, limit):] if tokens else []
    if mark:
        lines.append(mark)
    return lines


def build_scene_components(
    phrase: str,
    *,
    scene_number: int,
    layout: str,
    anchor: str,
    media: dict[str, Any],
    palette: dict[str, str],
    density: str,
    component_recipe: str,
    text_block: dict[str, str] | None = None,
) -> dict[str, Any]:
    block = text_block or {}
    prefix = f"scene-{scene_number}"
    rich = density == "rich"
    width, height = int(media["width"]), int(media["height"])
    portrait = height >= width
    safe_zone = _safe_zone(media.get("safeZone"))
    stage = _edge_stage(
        safe_zone,
        anchor,
        portrait,
        position_variant=block.get("role") == "dual",
    )
    rail_width = max(1, round(width * stage["width"] / 100))
    rail_height = max(1, round(height * stage["height"] / 100))
    question_lines, result_lines = _trim_dual_stack_to_rail(
        _pair_lines(block, "questionLines", block.get("question") or phrase),
        _pair_lines(block, "resultLines", block.get("result") or ""),
        rail_width=rail_width,
        rail_height=rail_height,
        portrait=portrait,
    )
    fitted = {
        **block,
        "questionLines": question_lines,
        "resultLines": result_lines,
        "lines": question_lines,
        "support": "",
        "eyebrow": "",
    }
    roles = _role_ids(prefix, rich=rich)
    total_lines = max(1, len(question_lines) + len(result_lines))
    return {
        "html": _markup(
            layout, anchor, roles, component_recipe, fitted,
            question_lines=question_lines,
            result_lines=result_lines,
            rich=rich,
        ),
        "css": _css(
            layout, anchor, media, palette, scene_number,
            total_lines,
            component_recipe,
            rich=rich,
            question_line_count=max(1, len(question_lines)),
            result_line_count=max(1, len(result_lines)),
            stage=stage,
        ),
        "ids": [identifier for values in roles.values() for identifier in values],
        "roleIds": roles,
        "roles": ["content", "structure", "decorative", "foreground"],
    }


def _role_ids(prefix: str, *, rich: bool) -> dict[str, list[str]]:
    accents = [f"{prefix}-accent-a", f"{prefix}-accent-b"] if rich else []
    return {
        "question": [f"{prefix}-question"],
        "result": [f"{prefix}-result"],
        "title": [f"{prefix}-question", f"{prefix}-result"],
        "eyebrow": [],
        "support": [],
        "rules": [f"{prefix}-rule-a"] + ([f"{prefix}-rule-b"] if rich else []),
        "frame": [f"{prefix}-frame"] if rich else [],
        "graphic": [f"{prefix}-graphic"] if rich else [],
        "accents": accents,
    }


def _glyph_markup(lines: list[str]) -> str:
    return "".join(
        (
            f"<span class=\"hf-line hf-bang\">{html.escape(line, quote=False)}</span>"
            if line in {"！", "？"}
            else f"<span class=\"hf-line\">{html.escape(line, quote=False)}</span>"
        )
        for line in lines
    )


def _markup(
    layout: str,
    anchor: str,
    roles: dict[str, list[str]],
    component_recipe: str,
    text_block: dict[str, str],
    *,
    question_lines: list[str],
    result_lines: list[str],
    rich: bool,
) -> str:
    del text_block
    question_markup = _glyph_markup(question_lines)
    result_markup = _glyph_markup(result_lines)
    structure = ""
    if roles["frame"]:
        structure += f'<i id="{roles["frame"][0]}" class="hf-frame"></i>'
    for identifier in roles["rules"]:
        kind = "hf-rule-a" if identifier.endswith("rule-a") else "hf-rule-b"
        structure += f'<i id="{identifier}" class="hf-rule {kind}"></i>'
    accents = ""
    if roles["accents"]:
        accents = "<div class=\"hf-accents\">" + "".join(
            f'<i id="{identifier}" class="hf-accent hf-accent-{index + 1}"></i>'
            for index, identifier in enumerate(roles["accents"])
        ) + "</div>"
    graphic = ""
    if roles["graphic"]:
        graphic = _graphic_markup(roles["graphic"][0], component_recipe)
    return (
        f'<div class="hf-stage hf-layout-{layout} hf-anchor-{anchor}'
        f'{" hf-density-rich" if rich else " hf-density-balanced"}">'
        f"{structure}{accents}"
        f'<div class="hf-card-content">'
        f'<div class="hf-title-row"><div class="hf-copy">'
        f'<div id="{roles["question"][0]}" class="hf-title hf-question">{question_markup}</div>'
        f'<div id="{roles["result"][0]}" class="hf-title hf-result">{result_markup}</div>'
        f"</div>{graphic}</div>"
        f"</div></div>"
    )


def _graphic_markup(identifier: str, recipe: str) -> str:
    count = 5 if recipe == "signal-wave" else 3
    marks = "".join("<i></i>" for _ in range(count))
    return f'<div id="{identifier}" class="hf-graphic hf-{recipe}" aria-hidden="true">{marks}</div>'


def _css(
    layout: str,
    anchor: str,
    media: dict[str, Any],
    palette: dict[str, str],
    scene_number: int,
    phrase_length: int,
    component_recipe: str,
    *,
    rich: bool,
    question_line_count: int,
    result_line_count: int,
    stage: dict[str, float] | None = None,
) -> str:
    width, height = int(media["width"]), int(media["height"])
    portrait = height >= width
    safe_zone = _safe_zone(media.get("safeZone"))
    stage = stage or _edge_stage(safe_zone, anchor, portrait)
    rail_width = max(1, round(width * stage["width"] / 100))
    rail_height = max(1, round(height * stage["height"] / 100))
    line_count = max(1, question_line_count + result_line_count)
    sizes = _sizes(rail_width, rail_height, portrait, phrase_length, line_count)
    box = _anchor_box(layout, anchor, portrait)
    _fit_text_box(
        box, sizes, rail_width, rail_height,
        question_line_count, result_line_count,
        portrait=portrait,
    )
    scope = f"#hf-scene-{scene_number} "
    accent_alpha = _rgba(palette["accent"], 0.28 if rich else 0.2)
    css = _base_css(scope, stage, box, sizes, accent_alpha, rich=rich)
    if rich:
        css += _graphic_css(scope, component_recipe, sizes, accent_alpha)
    return css + _layout_css(layout, scope, box, sizes, accent_alpha, rich=rich)


def _edge_stage(
    safe_zone: dict[str, float],
    anchor: str,
    portrait: bool,
    *,
    position_variant: bool = False,
) -> dict[str, float]:
    """Side rail strictly inside the user-saved safe zone. Never escape it."""
    right = anchor in {"top-right", "bottom-right", "right-rail"}
    if not portrait:
        return dict(safe_zone)
    zone_x = float(safe_zone["x"])
    zone_y = float(safe_zone["y"])
    zone_w = float(safe_zone["width"])
    zone_h = float(safe_zone["height"])
    # Wider rail for larger glyphs, still fully inside the saved safe zone.
    rail = min(zone_w, max(28.0, round(zone_w * 0.48, 2)))
    left = round(zone_x + zone_w - rail, 2) if right else round(zone_x, 2)
    bottom = anchor in {"bottom-left", "bottom-right", "bottom-band"}
    stage_h = round(zone_h * 0.92, 2) if position_variant else round(zone_h, 2)
    stage_y = round(zone_y + zone_h - stage_h, 2) if position_variant and bottom else round(zone_y, 2)
    return {
        "x": left,
        "y": stage_y,
        "width": round(rail, 2),
        "height": stage_h,
    }


def _base_css(
    scope: str,
    stage: dict[str, float],
    box: dict[str, float | str],
    sizes: dict[str, int],
    accent_alpha: str,
    *,
    rich: bool,
) -> str:
    # balanced：靠阴影可读，避免粗描边发脏；rich 才给极细描边。
    stroke = "max(0.35px, calc(var(--motion-stroke-width) * 0.18))" if rich else "0"
    title_shadow = "0 1px 10px rgba(0,0,0,.34)" if rich else "0 1px 8px rgba(0,0,0,.4)"
    # Fill the edge rail; placement is already constrained by stage = safe-zone rail.
    card_left = 4.0
    card_top = 3.0
    card_width = 92.0
    card_height = min(94.0, max(40.0, float(box["height"])))
    return (
        f"{scope}.hf-fixed-zone{{left:0!important;right:auto!important;top:0!important;bottom:auto!important;"
        "width:100%!important;height:100%!important;overflow:hidden!important;display:block!important;}"
        f"{scope}.hf-stage{{position:absolute;left:{stage['x']}%;top:{stage['y']}%;"
        f"width:{stage['width']}%;height:{stage['height']}%;overflow:hidden;}}"
        f"{scope}.hf-card-content{{position:absolute;left:{card_left}%;top:{card_top}%;width:{card_width}%;"
        f"height:{card_height}%;max-height:94%;display:flex;flex-direction:column;align-items:{box['h_align']};"
        f"text-align:{box['text_align']};z-index:6;padding:0;overflow:hidden;}}"
        f"{scope}.hf-title-row{{display:flex;width:100%;min-width:0;align-items:flex-start;gap:{sizes['graphic_gap']}px;}}"
        f"{scope}.hf-anchor-top-right .hf-title-row,{scope}.hf-anchor-bottom-right .hf-title-row,"
        f"{scope}.hf-anchor-right-rail .hf-title-row{{flex-direction:row-reverse;}}"
        f"{scope}.hf-copy{{min-width:0;flex:1;display:flex;flex-direction:row;align-items:flex-start;"
        f"justify-content:flex-start;gap:{sizes['segment_gap']}px;}}"
        f"{scope}.hf-anchor-top-right .hf-copy,{scope}.hf-anchor-bottom-right .hf-copy,"
        f"{scope}.hf-anchor-right-rail .hf-copy{{flex-direction:row-reverse;}}"
        f"{scope}.hf-title{{font-size:{sizes['headline']}px;line-height:1.08;font-weight:700;letter-spacing:-.01em;"
        f"color:var(--motion-text);-webkit-text-stroke:{stroke} var(--motion-stroke);"
        f"paint-order:stroke fill;text-shadow:{title_shadow};}}"
        f"{scope}.hf-question{{opacity:.98;}}"
        f"{scope}.hf-result{{font-size:{max(24, round(sizes['headline'] * 0.94))}px;"
        f"font-weight:650;opacity:.94;}}"
        f"{scope}.hf-title .hf-line{{display:block;max-width:100%;white-space:nowrap;word-break:normal;"
        f"overflow-wrap:normal;margin:0 0 {sizes['line_gap']}px;}}"
        f"{scope}.hf-title .hf-line:last-child{{margin-bottom:0;}}"
        # 语气符与正文同色，不单独染色。
        f"{scope}.hf-title .hf-bang{{color:inherit;font-weight:inherit;}}"
        f"{scope}.hf-rule{{position:absolute;height:{sizes['rule']}px;background:{accent_alpha};z-index:5;"
        f"transform-origin:left center;opacity:{'.9' if rich else '.75'};}}"
        f"{scope}.hf-rule-a{{left:{card_left}%;top:2%;width:42%;}}"
    )


def _graphic_css(scope: str, recipe: str, sizes: dict[str, int], accent_alpha: str) -> str:
    base = (
        f"{scope}.hf-graphic{{position:relative;flex:0 0 auto;width:{sizes['graphic']}px;height:{sizes['graphic']}px;"
        f"margin-top:4px;z-index:7;opacity:.7;}}"
        f"{scope}.hf-frame{{position:absolute;left:auto;right:auto;top:auto;width:{sizes['corner_w']}px;"
        f"height:{sizes['corner_h']}px;border-top:2px solid {accent_alpha};border-left:2px solid {accent_alpha};"
        f"z-index:4;opacity:.65;}}"
        f"{scope}.hf-rule-b{{left:var(--hf-rule-left,8%);top:var(--hf-rule-bottom,40%);width:14%;opacity:.45;}}"
        f"{scope}.hf-accents{{position:absolute;inset:0;z-index:5;pointer-events:none;}}"
        f"{scope}.hf-accent{{position:absolute;display:block;background:var(--support);opacity:.45;}}"
        f"{scope}.hf-accent-1{{width:{sizes['dot']}px;height:{sizes['dot']}px;border-radius:50%;}}"
        f"{scope}.hf-accent-2{{width:{sizes['bar']}px;height:{sizes['rule']}px;}}"
    )
    if recipe == "message-flow":
        return base + (
            f"{scope}.hf-message-flow i{{position:absolute;left:0;height:2px;background:var(--accent);border-radius:9px;opacity:.7}}"
            f"{scope}.hf-message-flow i:nth-child(1){{top:22%;width:92%}}"
            f"{scope}.hf-message-flow i:nth-child(2){{top:48%;width:64%;background:var(--support)}}"
            f"{scope}.hf-message-flow i:nth-child(3){{top:74%;width:38%}}"
        )
    if recipe == "timeline-track":
        return base + (
            f"{scope}.hf-timeline-track:before{{content:'';position:absolute;left:6%;right:6%;top:50%;height:2px;background:{accent_alpha}}}"
            f"{scope}.hf-timeline-track i{{position:absolute;top:calc(50% - 4px);width:8px;height:8px;border-radius:50%;background:var(--accent);opacity:.75}}"
            f"{scope}.hf-timeline-track i:nth-child(1){{left:2%}}{scope}.hf-timeline-track i:nth-child(2){{left:43%;background:var(--support)}}"
            f"{scope}.hf-timeline-track i:nth-child(3){{right:2%}}"
        )
    if recipe == "signal-wave":
        return base + (
            f"{scope}.hf-signal-wave{{display:flex;align-items:center;justify-content:center;gap:3px}}"
            f"{scope}.hf-signal-wave i{{display:block;width:2px;height:34%;background:var(--accent);border-radius:9px;opacity:.7}}"
            f"{scope}.hf-signal-wave i:nth-child(2),{scope}.hf-signal-wave i:nth-child(4){{height:72%;background:var(--support)}}"
            f"{scope}.hf-signal-wave i:nth-child(3){{height:100%}}"
        )
    return base + (
        f"{scope}.hf-network-orbit:before{{content:'';position:absolute;left:10%;right:10%;top:50%;height:2px;background:{accent_alpha}}}"
        f"{scope}.hf-network-orbit i{{position:absolute;width:7px;height:7px;border-radius:50%;background:var(--accent);opacity:.7}}"
        f"{scope}.hf-network-orbit i:nth-child(1){{left:4%;top:42%}}"
        f"{scope}.hf-network-orbit i:nth-child(2){{left:43%;top:18%;background:var(--support)}}"
        f"{scope}.hf-network-orbit i:nth-child(3){{right:4%;top:42%}}"
    )


def _layout_css(
    layout: str,
    scope: str,
    box: dict[str, float | str],
    sizes: dict[str, int],
    accent_alpha: str,
    *,
    rich: bool,
) -> str:
    rule_left = box["rule_left"]
    rule_bottom = box.get("rule_bottom", float(box["top"]) + float(box["height"]))
    shared = f"{scope}.hf-stage{{--hf-rule-left:{rule_left}%;--hf-rule-bottom:{rule_bottom}%;}}"
    if rich:
        shared += (
            f"{scope}.hf-frame{{left:{box['frame_left']}%;top:{box['frame_top']}%;}}"
            f"{scope}.hf-accent-1{{left:{box['accent_left']}%;top:{box['accent_top']}%;}}"
            f"{scope}.hf-accent-2{{left:{box['accent_line_left']}%;top:{box['accent_line_top']}%;}}"
        )
    if layout == "editorial-split":
        return shared + (
            f"{scope}.hf-layout-editorial-split .hf-rule-a{{width:min(56%,{box['rule_width']}%);height:2px;}}"
            f"{scope}.hf-layout-editorial-split .hf-title{{max-width:100%;}}"
        )
    if layout == "signal-frame":
        return shared + (
            f"{scope}.hf-layout-signal-frame .hf-rule-a{{width:min(56%,{box['rule_width']}%);height:2px;}}"
            f"{scope}.hf-layout-signal-frame .hf-frame{{left:{box['frame_left']}%;top:{max(2.0, float(box['top']) - 3)}%;"
            f"width:{sizes['corner_w'] + 8}px;height:{sizes['corner_h'] + 6}px;"
            f"border-right:2px solid {accent_alpha};border-bottom:0;}}"
        )
    if layout == "orbit-focus":
        return shared + (
            f"{scope}.hf-layout-orbit-focus .hf-title{{transform:rotate(-1.2deg);transform-origin:left center;}}"
            f"{scope}.hf-layout-orbit-focus .hf-rule-a{{width:18%;opacity:.55;}}"
            f"{scope}.hf-layout-orbit-focus .hf-graphic{{transform:translateY(8px);}}"
        )
    # grid-brief: stay top-aligned inside the safe-zone rail (no mid-frame drop).
    return shared + (
        f"{scope}.hf-layout-grid-brief .hf-card-content{{top:3%;bottom:auto;height:auto;max-height:94%;}}"
        f"{scope}.hf-layout-grid-brief .hf-title{{-webkit-text-stroke:0;text-shadow:0 1px 8px rgba(0,0,0,.22);font-weight:700;}}"
        f"{scope}.hf-layout-grid-brief .hf-rule-a{{top:2%;bottom:auto;width:42%;}}"
        f"{scope}.hf-layout-grid-brief .hf-support{{letter-spacing:.02em;text-transform:none;}}"
    )


def _sizes(
    width: int,
    height: int,
    portrait: bool,
    phrase_length: int,
    line_count: int,
) -> dict[str, int]:
    # Prefer large glyphs, but leave headroom so fit can shrink before clipping.
    glyph = max(40, min(96 if portrait else 64, round(width * 0.9)))
    if line_count >= 5:
        glyph = max(40, glyph - 6)
    elif line_count >= 4:
        glyph = max(42, glyph - 4)
    return {
        "headline": glyph,
        "eyebrow": max(30, min(56, round(glyph * .74))),
        "support": max(24, min(42, round(glyph * .52))),
        "support_gap": max(6, round(height * .008)),
        "eyebrow_gap": max(8, round(height * .01)),
        "segment_gap": max(10, round(glyph * 0.28)),
        "line_gap": max(2, round(glyph * 0.03)),
        "graphic": max(22, min(34, round(min(width, height) * .05))),
        "graphic_gap": max(6, round(min(width, height) * .01)),
        "rule": max(2, round(min(width, height) * .0035)),
        "dot": max(5, round(min(width, height) * .01)),
        "bar": max(28, round(min(width, height) * .06)),
        "corner_w": max(16, round(min(width, height) * .035)),
        "corner_h": max(12, round(min(width, height) * .025)),
    }


def _anchor_box(layout: str, anchor: str, portrait: bool) -> dict[str, float | str]:
    right = anchor in {"top-right", "bottom-right", "right-rail"}
    bottom = anchor in {"bottom-left", "bottom-right", "bottom-band"}
    band = anchor in {"top-band", "bottom-band"} or layout == "grid-brief"
    # 中央是禁区：所有 layout 只贴左右边缘，禁止水平居中。
    width = 20 if portrait else 16
    left = 100 - width - 7 if right else 7
    if layout == "signal-frame":
        width = 18 if portrait else 14
        left = 100 - width - 6 if right else 6
        top = 6.0 if not bottom else 38.0
    elif layout == "orbit-focus":
        width = 22 if portrait else 18
        left = 100 - width - 5 if right else 5
        top = 10.0 if not bottom else 40.0
    elif band:
        width = 20 if portrait else 16
        left = 100 - width - 8 if right else 8
        top = 42.0 if bottom or layout == "grid-brief" else 8.0
    elif bottom:
        top = 36.0
    else:
        top = 8.0
    h_align = "flex-end" if right else "flex-start"
    text_align = "right" if right else "left"
    return {
        "left": left, "top": top, "width": width, "height": 36,
        "h_align": h_align, "text_align": text_align,
        "frame_left": left + width - 8 if right else max(1, left - 1),
        "frame_top": max(1, top - 2),
        "rule_left": left,
        "rule_top": max(1, top - 2),
        "rule_width": min(18, width * 0.7),
        "rule_bottom": top + 30,
        "accent_left": left + width - 3 if not right else max(1, left - 1),
        "accent_top": top + 3,
        "accent_line_left": left + 2,
        "accent_line_top": top + 32,
    }


def _fit_text_box(
    box: dict[str, float | str],
    sizes: dict[str, int],
    safe_width: int,
    safe_height: int,
    question_line_count: int,
    result_line_count: int,
    *,
    portrait: bool,
) -> None:
    # Fit completely inside the rail: shrink first; content trim happens upstream.
    copy_width = max(1.0, safe_width * 0.92)
    fit_w = int(copy_width / 1.02)
    floor = 32 if portrait else 24
    sizes["headline"] = max(floor, min(sizes["headline"], fit_w, 96 if portrait else 64))
    sizes["line_gap"] = max(2, round(sizes["headline"] * 0.03))
    sizes["segment_gap"] = max(10, round(sizes["headline"] * 0.28))

    def _requested(headline_px: int) -> float:
        return _dual_stack_height_px(
            question_line_count, result_line_count, headline_px,
        )

    max_height_px = safe_height * 0.92
    while sizes["headline"] > floor and _requested(sizes["headline"]) > max_height_px:
        sizes["headline"] -= 1
        sizes["line_gap"] = max(2, round(sizes["headline"] * 0.03))
        sizes["segment_gap"] = max(8, round(sizes["headline"] * 0.24))
    requested = _requested(sizes["headline"])
    box["top"] = 3.0
    box["height"] = round(min(94.0, max(40.0, requested / max(1, safe_height) * 100)), 2)
    box["rule_bottom"] = min(96.0, float(box["top"]) + float(box["height"]) + 1.0)
    box["accent_line_top"] = min(96.0, float(box["rule_bottom"]) + 1.0)


def _trim_dual_stack_to_rail(
    question_lines: list[str],
    result_lines: list[str],
    *,
    rail_width: int,
    rail_height: int,
    portrait: bool,
) -> tuple[list[str], list[str]]:
    """Shrink type only; text that still cannot fit is a quality failure, never trim it."""
    question = _fit_glyph_budget([line for line in question_lines if line], 6) or ["？"]
    result = _fit_glyph_budget([line for line in result_lines if line], 6)
    floor = 24
    glyph = max(floor, min(96 if portrait else 64, round(rail_width * 0.9)))
    max_h = rail_height * 0.92

    def fits(q: list[str], r: list[str], px: int) -> bool:
        return _dual_stack_height_px(len(q), len(r), px) <= max_h

    while glyph > floor and not fits(question, result, glyph):
        glyph -= 1
    if not fits(question, result, glyph):
        raise ValueError("完整问答文案无法放入当前安全区，请重新生成更短的完整意群")
    return question, result


def _dual_stack_height_px(
    question_count: int,
    result_count: int,
    headline_px: int,
) -> float:
    gap = max(2, round(headline_px * 0.03))
    def stack(count: int) -> float:
        n = max(0, count)
        if n <= 0:
            return 0.0
        return n * headline_px * 1.08 + max(0, n - 1) * gap

    primary_height = stack(question_count)
    secondary_height = stack(result_count) * 0.94
    return max(primary_height, secondary_height)


def _pair_lines(block: dict[str, Any], key: str, fallback: str) -> list[str]:
    raw = block.get(key) if isinstance(block, dict) else None
    if isinstance(raw, list):
        glyphs: list[str] = []
        for item in raw:
            token = str(item).strip()
            if not token:
                continue
            if len(token) == 1 or re.fullmatch(r"[A-Za-z0-9]+", token):
                glyphs.append(token)
            else:
                glyphs.extend(vertical_glyph_lines(token, limit=100))
        return _fit_glyph_budget(glyphs, 6)
    return _fit_glyph_budget(vertical_glyph_lines(str(fallback or ""), limit=100), 6)


def _fit_glyph_budget(glyphs: list[str], body_limit: int) -> list[str]:
    """正文超过布局预算即失败；末尾 ？/！不占正文名额。"""
    cleaned = [item for item in glyphs if item]
    if not cleaned:
        return [""]
    mark = cleaned[-1] if cleaned[-1] in {"！", "？"} else ""
    body = cleaned[:-1] if mark else cleaned
    if len(body) > max(1, body_limit):
        raise ValueError(f"单拍正文超过 {body_limit} 个显示单元，禁止截断")
    return body + ([mark] if mark else [])


def _rgba(color: str, alpha: float) -> str:
    raw = color.lstrip("#")
    red, green, blue = (int(raw[index:index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red},{green},{blue},{alpha:.2f})"


def _safe_zone(value: Any) -> dict[str, float]:
    source = value if isinstance(value, dict) else {}
    width = _number(source.get("width"), 86.0, 20.0, 96.0)
    height = _number(source.get("height"), 48.0, 20.0, 96.0)
    return {
        "x": _number(source.get("x"), 7.0, 0.0, 100.0 - width),
        "y": _number(source.get("y"), 8.0, 0.0, 100.0 - height),
        "width": width,
        "height": height,
    }


def _number(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return round(min(max(number, minimum), maximum), 2)
