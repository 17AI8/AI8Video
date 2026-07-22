"""把模型的语义选择映射为固定画布 HyperFrames artifact。

文案处理由模型完成；本模块只做字段归一、竖排与硬校验（失败抛错，由 harness 打回模型）。
"""

from __future__ import annotations

import re
from typing import Any

from ai8video.media.motion.hyperframes_overlay_components import build_scene_components, vertical_glyph_lines
from ai8video.media.motion.hyperframes_overlay_design import normalize_design_plan, scene_anchor, scene_component, scene_layout
from ai8video.media.motion.hyperframes_overlay_motion import build_scene_animations


HEADLINE_LIMIT = 6
MIN_BODY_LEN = 4
MAX_LINES = 6
WEAK_HEADLINE_MARKERS = (
    "零障碍", "正式发布", "全面赋能", "完美解决", "降本增效",
    "一站式赋能", "全域增长", "高效协同", "立享返佣", "邀请好友",
)
# 号召/收尾 CTA：绝不能当 question，也不该冒充痛点问答。
CTA_COPY_MARKERS = (
    "邀请好友", "立享返佣", "正式发布", "立即体验", "马上加入",
    "扫码领取", "点击关注", "立即下载", "马上领取",
)
CTA_HINTS = ("邀请", "返佣", "立享", "点击", "关注", "下载", "扫码", "领取")
# 痛点/冲突线索：question 应像卡点，而不是陈述口号。
PAIN_HINTS = (
    "卡", "壳", "堵", "慢", "难", "痛", "乱", "碎", "缺", "等", "停",
    "排", "崩", "烦", "累", "差", "弱", "跳", "散", "丢", "断", "塞",
)
# 切开即碎词的常见双字；用于拒绝「沟通→通总是卡壳」。
KEEP_BIGRAMS = frozenset({
    "沟通", "客户", "反馈", "需求", "支付", "办公", "全球", "聊天", "整合",
    "传译", "跨境", "独立", "邀请", "好友", "返佣", "卡壳", "排队", "门口",
    "复购", "及时", "接住", "同声", "飞讯", "取餐", "高峰", "菜单", "自助",
    "点单", "收工", "明显", "上来", "变得", "障碍", "一年", "一条",
})


def normalize_semantic_spec(
    value: dict[str, Any],
    media: dict[str, Any],
    dialogue_text: str = "",
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("语义动效方案必须是对象")
    duration = max(0.5, float(media["durationSeconds"]))
    beat_interval = _semantic_beat_interval(value, media)
    beat_count = target_beat_count(
        duration,
        dialogue_text,
        beat_interval_seconds=beat_interval,
    )
    text_blocks = _planned_text_blocks(value, dialogue_text, beat_count)
    plan = normalize_design_plan(value)
    return {
        **plan,
        "headline": text_blocks[0]["question"] if text_blocks else "",
        "headlines": [item["question"] for item in text_blocks],
        "textBlocks": text_blocks,
        "durationSeconds": duration,
        "beatIntervalSeconds": beat_interval,
    }


def _semantic_beat_interval(value: dict[str, Any], media: dict[str, Any]) -> float:
    candidate = (
        value.get("beatIntervalSeconds")
        if media.get("smartBeatInterval")
        else media.get("beatIntervalSeconds", 5)
    )
    try:
        interval = float(candidate)
    except (TypeError, ValueError):
        interval = float(media.get("beatIntervalSeconds") or 5)
    return round(min(max(interval, 1.0), 30.0), 1)


def target_beat_count(
    duration_seconds: float,
    dialogue_text: str = "",
    *,
    beat_interval_seconds: Any = 5,
) -> int:
    """拍数只由时长和用户设置的每拍间隔决定，不因候选文案不足偷偷降拍。"""
    duration = max(0.5, float(duration_seconds))
    del dialogue_text
    try:
        interval = float(beat_interval_seconds)
    except (TypeError, ValueError):
        interval = 5
    interval = min(max(interval, 1.0), 30.0)
    return max(1, min(30, round(duration / interval)))


def _phrase_windows(dialogue_text: str) -> list[str]:
    """按句读切开的原子意群（。！？与，、）；不拼接整句，避免虚增拍数。"""
    compact = _dialogue_body(dialogue_text)
    if not compact:
        return []
    windows: list[str] = []
    for clause in re.split(r"[。！？!?；;]+", compact):
        if not clause:
            continue
        parts = [part for part in re.split(r"[，、]+", clause) if part]
        if parts:
            windows.extend(parts)
        elif len(clause) >= MIN_BODY_LEN:
            windows.append(clause)
    # 去重且保序
    seen: set[str] = set()
    ordered: list[str] = []
    for item in windows:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _usable_phrase_units(dialogue_text: str) -> list[str]:
    """可作问答正文的意群：够长、非 CTA。台词里出现的词不因营销词表剔除。"""
    result: list[str] = []
    for window in _phrase_windows(dialogue_text):
        if len(window) < MIN_BODY_LEN:
            continue
        if _is_cta_copy(window):
            continue
        result.append(window)
    return result


def _display_phrase(unit: str, source: str) -> str:
    """只接受原文中本身符合预算的完整意群，绝不为布局截断。"""
    text = str(unit or "")
    if len(text) < MIN_BODY_LEN or len(text) > HEADLINE_LIMIT:
        return ""
    if not _is_phrase_unit(text, source):
        return ""
    return text


def phrase_role_pools(dialogue_text: str) -> tuple[list[str], list[str]]:
    """按台词位置拆出 question / result 候选（展示形态，已去重）。"""
    units = _usable_phrase_units(dialogue_text)
    body = _dialogue_body(dialogue_text)
    if not units or not body:
        return [], []
    questions: list[str] = []
    results: list[str] = []
    seen_q: set[str] = set()
    seen_r: set[str] = set()
    for unit in units:
        at = body.find(unit)
        if at < 0:
            continue
        display = _display_phrase(unit, dialogue_text)
        if not display:
            continue
        # 靠前偏痛点，靠后偏结果；中段可进两边
        if at < len(body) * 0.65 and _has_pain_hint(display) and display not in seen_q:
            seen_q.add(display)
            questions.append(display)
        if at + len(unit) > len(body) * 0.28 and display not in seen_r:
            seen_r.add(display)
            results.append(display)
    return questions, results


def dialogue_phrase(value: str, limit: int = 12) -> str:
    phrases = dialogue_phrases(value, limit=limit, max_phrases=1)
    return phrases[0] if phrases else ""


def dialogue_phrases(value: str, limit: int = 18, max_phrases: int = 4) -> list[str]:
    return [
        item["headline"]
        for item in dialogue_text_blocks(value, max_blocks=max_phrases, limit=limit)
    ]


def dialogue_text_blocks(
    value: str,
    *,
    max_blocks: int = 4,
    limit: int = 18,
) -> list[dict[str, str]]:
    """按完整语义分组台词（遗留辅助；不用于改写模型 headline）。"""
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    if not compact:
        return []
    clauses = [item.strip() for item in re.split(r"(?<=[。！？!?；;])", compact) if item.strip()]
    clauses = _split_long_clauses(clauses, max(limit * 2, 28))
    groups = _balanced_clause_groups(clauses, max_blocks)
    return [_text_block(group, limit) for group in groups if group.strip()]


def compile_semantic_artifact(spec: dict[str, Any], media: dict[str, Any]) -> dict[str, Any]:
    duration = max(0.5, float(media["durationSeconds"]))
    phrases = _headlines(spec) or [""]
    windows = _scene_windows(len(phrases), duration)
    scenes = [
        _build_scene(spec, media, phrase, index, start, end)
        for index, (phrase, (start, end)) in enumerate(zip(phrases, windows), start=1)
    ]
    direction = spec["designDirection"]
    return {
        "layoutMode": "fixed-semantic",
        "design": {
            "candidates": [direction],
            "chosen": direction,
            "concept": "固定画布上的边缘文字节拍编排",
            "palette": spec["palette"],
            "typography": "system-hierarchy",
            "layoutRecipe": spec["layoutRecipe"],
            "motionRecipe": spec["motionRecipe"],
            "componentRecipes": spec["componentRecipes"],
            "density": spec["density"],
        },
        "scenes": scenes,
    }


def _build_scene(
    spec: dict[str, Any],
    media: dict[str, Any],
    phrase: str,
    index: int,
    start: float,
    end: float,
) -> dict[str, Any]:
    layout = scene_layout(spec, index - 1)
    text_block = _scene_text_block(spec, phrase, index - 1)
    anchor = scene_anchor(
        spec,
        index - 1,
        portrait=int(media["height"]) >= int(media["width"]),
        beat_role="pair",
    )
    component = scene_component(spec, index - 1)
    components = build_scene_components(
        phrase,
        scene_number=index,
        layout=layout,
        anchor=anchor,
        media=media,
        palette=spec["palette"],
        density=spec["density"],
        component_recipe=component,
        text_block=text_block,
    )
    animations = build_scene_animations(
        components["roleIds"],
        recipe=spec["motionRecipe"],
        component_recipe=component,
        duration=end - start,
        scene_number=index,
        density=spec["density"],
    )
    return {
        "start": start,
        "end": end,
        "zone": anchor,
        "layoutRecipe": layout,
        "componentRecipe": component,
        "roles": components["roles"],
        "html": components["html"],
        "css": components["css"],
        "animations": animations,
        "ids": components["ids"],
        "textBlock": text_block,
    }


def _split_clauses(clauses: list[str], limit: int) -> list[str]:
    phrases: list[str] = []
    for clause in clauses:
        while len(clause) > limit:
            split_at = max((clause.rfind(mark, 0, limit + 1) for mark in "，,；;：:"), default=0)
            split_at = split_at if split_at >= max(4, limit // 2) else limit
            phrases.append(clause[:split_at].strip("，,；;：:"))
            clause = clause[split_at:].strip()
        if clause:
            phrases.append(clause.strip("。！？!?；;,"))
    return phrases


def _split_long_clauses(clauses: list[str], limit: int) -> list[str]:
    result: list[str] = []
    for clause in clauses:
        if len(clause) <= limit:
            result.append(clause)
            continue
        result.extend(_split_clauses([clause], limit) or [clause])
    return result


def _balanced_clause_groups(clauses: list[str], max_blocks: int) -> list[str]:
    target = max(1, min(max_blocks, len(clauses)))
    target_length = max(1, round(sum(len(item) for item in clauses) / target))
    groups: list[str] = []
    current = ""
    for clause in clauses:
        should_break = (
            current
            and len(current) + len(clause) > target_length
            and len(groups) < target - 1
        )
        if should_break:
            groups.append(current)
            current = ""
        current += clause
    if current:
        groups.append(current)
    return groups


def _text_block(source: str, limit: int) -> dict[str, str]:
    clean = source.strip()
    positions = [clean.find(mark) for mark in "：:，,；;" if 4 <= clean.find(mark) <= limit]
    split_at = min(positions) if positions else -1
    if split_at >= 0:
        headline = clean[:split_at].strip("：:，,；;")
        support = clean[split_at + 1:].strip()
    else:
        headline, support = clean, ""
    lines = _headline_lines(headline)
    return {
        "headline": "".join(lines),
        "support": support,
        "source": clean,
        "lines": lines,
        "role": "",
        "eyebrow": "",
    }


def _planned_text_blocks(
    value: dict[str, Any],
    dialogue_text: str,
    beat_count: int,
) -> list[dict[str, str]]:
    beats = value.get("beats")
    planned = _normalize_beats(beats, beat_count, dialogue_text)
    if planned:
        _validate_beat_copy(planned, dialogue_text)
        if len(planned) != beat_count:
            raise ValueError(
                f"本片需要恰好 {beat_count} 个 beats，"
                f"当前 {len(planned)} 个，请按台词补齐或删减"
            )
        _validate_distinct_beats(planned, dialogue_text)
        return planned
    if dialogue_text.strip():
        raise ValueError("动效方案缺少经过提炼的文案节拍")
    return []


def _normalize_beats(value: Any, beat_count: int, source: str) -> list[dict[str, str]]:
    """新协议每拍只需 text；仍兼容旧 question/result 方案。"""
    if not isinstance(value, list):
        return []
    if len(value) != beat_count:
        raise ValueError(
            f"本片需要恰好 {beat_count} 个 beats，"
            f"当前 {len(value)} 个，请重新生成完整方案"
        )
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if "primary" in item or "secondary" in item:
            primary = _normalize_single_beat(item.get("primary"), source)
            secondary = _normalize_single_beat(item.get("secondary"), source)
            result.append({
                "primary": primary,
                "secondary": secondary,
                "question": primary,
                "result": secondary,
                "headline": primary,
                "support": "",
                "eyebrow": "",
                "source": primary,
                "lines": _headline_lines(primary),
                "questionLines": _headline_lines(primary),
                "resultLines": _headline_lines(secondary),
                "role": "dual",
            })
            continue
        if "text" in item:
            text = _normalize_single_beat(item.get("text"), source)
            result.append(_single_text_block(text))
            continue
        question = _normalize_pair_field(item, "question", mark="？", source=source)
        answer = _normalize_pair_field(item, "result", mark="！", source=source)
        if not question or not answer:
            raise ValueError("每一拍必须同时包含 question（？）与 result（！）两段")
        result.append({
            "question": question,
            "result": answer,
            "headline": question,
            "support": "",
            "eyebrow": "",
            "source": "",
            "lines": _headline_lines(question),
            "questionLines": _headline_lines(question),
            "resultLines": _headline_lines(answer),
            "role": "pair",
        })
    return result


def _single_text_block(text: str) -> dict[str, str]:
    return {
        "text": text, "question": text, "result": "", "headline": text,
        "support": "", "eyebrow": "", "source": text,
        "lines": _headline_lines(text), "questionLines": _headline_lines(text),
        "resultLines": [], "role": "single",
    }


def _normalize_single_beat(value: Any, source: str) -> str:
    del source
    text = re.sub(r"\s+", "", str(value or "")).strip("，。！？!?；;：:")
    if not text:
        raise ValueError("每一拍必须包含 text")
    if len(text) > HEADLINE_LIMIT:
        raise ValueError(f"文案过长（>{HEADLINE_LIMIT}字）：{text}")
    return text


def _validate_distinct_beats(blocks: list[dict[str, str]], source: str = "") -> None:
    if blocks and all(block.get("role") == "dual" for block in blocks):
        # 新协议的文案质量完全由 AI 审核；本地不判断来源、重复或语义。
        return
    if blocks and all(block.get("role") == "single" for block in blocks):
        texts = [str(block.get("text") or "") for block in blocks]
        if len(texts) != len(set(texts)):
            raise ValueError("多拍文案重复，请每拍使用不同台词片段")
        return
    questions: list[str] = []
    results: list[str] = []
    q_pool, r_pool = phrase_role_pools(source) if source else ([], [])
    dialogue = _dialogue_body(source)
    previous_question_at = -1
    previous_result_at = -1
    for block in blocks:
        q_body, _ = _split_slogan_mark(block.get("question"))
        r_body, _ = _split_slogan_mark(block.get("result"))
        if q_body in questions:
            hint = _unused_pool_hint(q_pool, set(questions) | {q_body}, mark="？")
            raise ValueError(
                f"多拍 question 重复，请换不同痛点意群：{q_body}"
                + (f"；可改用：{hint}" if hint else "")
            )
        if r_body in results:
            hint = _unused_pool_hint(r_pool, set(results) | {r_body}, mark="！")
            raise ValueError(
                f"多拍 result 重复，请换不同结果意群：{r_body}"
                + (f"；可改用：{hint}" if hint else "")
            )
        for other in questions:
            if q_body in other or other in q_body:
                raise ValueError(
                    f"多拍 question 互为截断碎片，请改截完整意群：{other} / {q_body}"
                )
        for other in results:
            if r_body in other or other in r_body:
                raise ValueError(
                    f"多拍 result 互为截断碎片，请改截完整意群：{other} / {r_body}"
                )
        if dialogue:
            q_at = dialogue.find(q_body, max(0, previous_question_at + 1))
            r_at = dialogue.find(r_body, max(0, previous_result_at + 1))
            if q_at < 0 or r_at < 0 or q_at >= r_at:
                raise ValueError("多拍文案必须按原台词顺序向后推进，禁止回跳或交叉")
            previous_question_at = q_at
            previous_result_at = r_at
        questions.append(q_body)
        results.append(r_body)


def _unused_pool_hint(pool: list[str], used: set[str], *, mark: str, limit: int = 3) -> str:
    tips = [f"{item}{mark}" for item in pool if item not in used][:limit]
    return " / ".join(tips)


def _normalize_pair_field(
    item: dict[str, Any],
    key: str,
    *,
    mark: str,
    source: str = "",
) -> str:
    raw = str(item.get(key) or "").strip()
    if not raw and key == "question":
        # 兼容旧单行 headline：仅当 role=problem 时映射为 question
        role = str(item.get("role") or "").strip().lower()
        if role in {"problem", "issue", "痛点", "问题"}:
            raw = str(item.get("headline") or "").strip()
    if not raw and key == "result":
        role = str(item.get("role") or "").strip().lower()
        if role in {"result", "change", "conclusion", "结果", "变化", "结论"}:
            raw = str(item.get("headline") or "").strip()
    _body, body_mark = _split_slogan_mark(raw)
    text = _layout_headline(raw, mark, source=source)
    return _ensure_schema_mark(text, mark) if text else ""


def _split_slogan_mark(value: Any) -> tuple[str, str]:
    raw = re.sub(r"\s+", "", str(value or ""))
    if raw.endswith(("！", "!")):
        return raw[:-1].strip("，。；;：:"), "！"
    if raw.endswith(("？", "?")):
        return raw[:-1].strip("，。；;：:"), "？"
    return raw.strip("，。；;：:！!？?"), ""


def _layout_headline(value: Any, preferred_mark: str = "", source: str = "") -> str:
    """布局只校验长度；过长文案必须由模型重新选完整意群。"""
    text, mark = _split_slogan_mark(value)
    if not text:
        return ""
    if len(text) > HEADLINE_LIMIT:
        raise ValueError(f"文案过长（>{HEADLINE_LIMIT}字），请重新选择完整短意群：{text}")
    final_mark = mark or preferred_mark
    return f"{text}{final_mark}" if final_mark else text


def _fit_phrase_budget(text: str, source: str) -> str:
    """把超长正文收到 HEADLINE_LIMIT，且尽量落在未断双字的意群尾窗。"""
    if len(text) <= HEADLINE_LIMIT:
        return text
    # 优先：已是某意群且超长时，取其合法尾窗
    for window in _phrase_windows(source):
        if text != window and text not in window and not window.endswith(text):
            continue
        base = window if window.endswith(text) or text == window else text
        if len(base) <= HEADLINE_LIMIT and _is_phrase_unit(base, source):
            return base
        for size in range(min(HEADLINE_LIMIT, len(base)), MIN_BODY_LEN - 1, -1):
            candidate = base[-size:]
            if _is_phrase_unit(candidate, source):
                return candidate
    for size in range(min(HEADLINE_LIMIT, len(text)), MIN_BODY_LEN - 1, -1):
        candidate = text[-size:]
        if _is_phrase_unit(candidate, source):
            return candidate
    return text[-HEADLINE_LIMIT:]


def _ensure_schema_mark(headline: str, preferred_mark: str) -> str:
    """Schema 补齐语气符；不改正文。"""
    body, mark = _split_slogan_mark(headline)
    if not body:
        return str(headline or "")
    if mark:
        return f"{body}{mark}"
    return f"{body}{preferred_mark}"


def _headline_lines(headline: str) -> list[str]:
    return vertical_glyph_lines(headline, limit=MAX_LINES)


def _clip_pair_lines(lines: list[Any]) -> list[str]:
    """正文最多 MAX_LINES；末尾语气符单独保留。"""
    items = [str(item).strip() for item in lines if str(item).strip()]
    if not items:
        return []
    mark = items[-1] if items[-1] in {"！", "？"} else ""
    body = items[:-1] if mark else items
    body = body[:MAX_LINES]
    return body + ([mark] if mark else [])


def _visual_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip("，。！？!?；;：:")
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit]


def _dialogue_body(source: str) -> str:
    return re.sub(r"\s+", "", str(source or ""))


# 模型常漏写的轻量连接词；只用于对齐回台词原意群，不另造词。
_LIGHT_FILLERS = (
    "总是", "都是", "变得", "已经", "正在", "可以", "能够", "一个", "一些",
    "还有", "以及", "进行",
)


def _strip_light_fillers(value: str) -> str:
    text = str(value or "")
    for filler in _LIGHT_FILLERS:
        text = text.replace(filler, "")
    return text


def _recover_contiguous_phrase(body: str, source: str) -> str:
    """把「客户沟通卡壳」对齐回台词连续意群「客户沟通总是卡壳」。"""
    text = str(body or "")
    if len(text) < MIN_BODY_LEN:
        return ""
    dialogue = _dialogue_body(source)
    if text in dialogue:
        return text
    compressed = _strip_light_fillers(text)
    if len(compressed) < MIN_BODY_LEN:
        return ""
    # (是否精确压缩匹配, 长度) — 精确优先，其次更短完整意群
    best = ""
    best_key: tuple[int, int] | None = None
    for window in _phrase_windows(source):
        window_compact = _strip_light_fillers(window)
        if window_compact != compressed and compressed not in window_compact:
            continue
        candidate = window if len(window) <= HEADLINE_LIMIT else _fit_phrase_budget(window, source)
        if not _is_phrase_unit(candidate, source):
            continue
        candidate_compact = _strip_light_fillers(candidate)
        exact = 1 if candidate_compact == compressed else 0
        if exact == 0 and compressed not in candidate_compact:
            continue
        key = (exact, -len(candidate))
        if best_key is None or key > best_key:
            best = candidate
            best_key = key
    return best


def _format_phrase_hints(source: str, body: str, *, limit: int = 3) -> str:
    """校验失败时给出可截取的台词意群提示。"""
    compact = _strip_light_fillers(body)
    scored: list[tuple[int, str]] = []
    for window in _usable_phrase_units(source):
        tip = window if len(window) <= HEADLINE_LIMIT else _fit_phrase_budget(window, source)
        if len(tip) < MIN_BODY_LEN:
            continue
        overlap = len(set(compact) & set(_strip_light_fillers(tip)))
        scored.append((overlap, tip))
    scored.sort(key=lambda item: (-item[0], len(item[1])))
    tips = []
    seen: set[str] = set()
    for _score, tip in scored:
        if tip in seen:
            continue
        seen.add(tip)
        tips.append(f"{tip}？/！")
        if len(tips) >= limit:
            break
    return " / ".join(tips)


def _is_faithful_to_dialogue(headline: str, source: str) -> bool:
    body, _mark = _split_slogan_mark(headline)
    if len(body) < MIN_BODY_LEN:
        return False
    if body in _dialogue_body(source):
        return True
    # 归一后仍可能带着已对齐正文；压缩匹配只作兜底判定
    return bool(_recover_contiguous_phrase(body, source))


def _is_weak_headline(headline: str, source: str = "") -> bool:
    """空泛营销词拦截；台词里原有的片段（如「沟通变得零障碍」）放行。"""
    text, _mark = _split_slogan_mark(headline)
    if not text:
        return True
    if not any(marker in text for marker in WEAK_HEADLINE_MARKERS):
        return False
    # 模型另造的「零障碍收益」等仍拦截；台词连续片段不误杀
    if source and text in _dialogue_body(source):
        return False
    return True


def _is_cta_copy(body: str) -> bool:
    text = str(body or "")
    if any(marker in text for marker in CTA_COPY_MARKERS):
        return True
    has_cta = any(hint in text for hint in CTA_HINTS)
    has_pain = any(hint in text for hint in PAIN_HINTS)
    return has_cta and not has_pain


def _has_pain_hint(body: str) -> bool:
    return any(hint in str(body or "") for hint in PAIN_HINTS)


def _is_phrase_unit(body: str, source: str) -> bool:
    """正文必须是句读意群本身，或未切断常见双字词的前/尾窗。"""
    text = str(body or "")
    if len(text) < MIN_BODY_LEN:
        return False
    if text not in _dialogue_body(source):
        return False
    windows = _phrase_windows(source)
    if text in windows:
        return True
    for window in windows:
        if window == text:
            return True
        if window.endswith(text):
            cut = len(window) - len(text)
            if cut >= 1 and window[cut - 1: cut + 1] in KEEP_BIGRAMS:
                continue
            return True
        if window.startswith(text):
            cut = len(text)
            if cut < len(window) and window[cut - 1: cut + 1] in KEEP_BIGRAMS:
                continue
            return True
    return False


def _validate_pair_rhetoric(question: str, answer: str, source: str) -> None:
    """拒绝无脑？！硬套：question 须是靠前痛点，result 须是其后变化/结果。"""
    q_body, _q_mark = _split_slogan_mark(question)
    r_body, _r_mark = _split_slogan_mark(answer)
    if _is_cta_copy(q_body):
        raise ValueError(f"question 不能是号召/收尾 CTA，请截台词痛点冲突：{question}")
    if source and not _has_pain_hint(q_body):
        raise ValueError(f"question 缺少真实痛点或冲突，禁止把普通陈述硬加问号：{question}")
    if _is_cta_copy(r_body) and not _has_pain_hint(q_body):
        raise ValueError(f"禁止 CTA 硬套成问答，请重写痛点→结果：{question}/{answer}")
    dialogue = _dialogue_body(source)
    if not dialogue:
        return
    q_at = dialogue.find(q_body)
    r_at = dialogue.find(r_body)
    if q_at < 0 or r_at < 0:
        return
    if q_at >= r_at:
        raise ValueError("question 须截自台词更靠前的痛点，result 须截自其后的变化/结果")
    # 仅拦截收尾 CTA 区的伪问题；台词中段原句（如「沟通变得零障碍」）不因缺痛点字被杀。
    if q_at > len(dialogue) * 0.75 and _is_cta_copy(q_body):
        raise ValueError(f"question 落在收尾号召区，请改截更靠前的卡点：{question}")


def _validate_beat_copy(blocks: list[dict[str, str]], dialogue_text: str) -> None:
    """硬校验：不过则抛错，由 harness 用 repair prompt 打回模型。"""
    source = str(dialogue_text or "").strip()
    for block in blocks:
        if block.get("role") == "dual":
            _normalize_single_beat(block.get("primary"), source)
            _normalize_single_beat(block.get("secondary"), source)
            continue
        if block.get("role") == "single":
            _normalize_single_beat(block.get("text"), source)
            continue
        question = str(block.get("question") or "").strip()
        answer = str(block.get("result") or "").strip()
        if not question or not answer:
            raise ValueError("每一拍必须同时包含 question（？）与 result（！）两段")
        for label, text, mark in (("question", question, "？"), ("result", answer, "！")):
            body, found = _split_slogan_mark(text)
            if len(body) < MIN_BODY_LEN:
                raise ValueError(f"{label} 过短（<{MIN_BODY_LEN}字），禁止碎词：{text}")
            if re.fullmatch(r"\d+", body):
                raise ValueError(f"{label} 不能只是数字")
            if found and found != mark:
                raise ValueError(f"{label} 语气符必须是{mark}")
            faithful = bool(source) and _is_faithful_to_dialogue(text, source)
            if source and not faithful:
                # 另造营销词优先提示「空泛营销」；其它不忠实则提示连续片段 + 可选用意群
                if any(marker in body for marker in WEAK_HEADLINE_MARKERS):
                    raise ValueError(f"文案空泛营销，请按台词重写：{text}")
                hint = _format_phrase_hints(source, body)
                raise ValueError(
                    f"{label} 不是台词连续片段，请重写：{text}"
                    + (f"；可改用：{hint}" if hint else "")
                )
            if _is_weak_headline(text, source):
                raise ValueError(f"文案空泛营销，请按台词重写：{text}")
            if source and not _is_phrase_unit(body, source):
                raise ValueError(
                    f"{label} 不是完整意群（勿拼「支付/通总是卡壳」类碎片），"
                    f"请按逗号句号切开整段截取：{text}"
                )
        _validate_pair_rhetoric(question, answer, source)


def _scene_text_block(spec: dict[str, Any], phrase: str, index: int) -> dict[str, str]:
    blocks = spec.get("textBlocks")
    if isinstance(blocks, list) and index < len(blocks) and isinstance(blocks[index], dict):
        block = blocks[index]
        question = str(block.get("question") or phrase).strip()
        answer = str(block.get("result") or "").strip()
        q_lines = block.get("questionLines")
        r_lines = block.get("resultLines")
        if not isinstance(q_lines, list):
            q_lines = _headline_lines(question)
        if not isinstance(r_lines, list):
            r_lines = _headline_lines(answer)
        question_lines = _clip_pair_lines(q_lines)
        result_lines = _clip_pair_lines(r_lines)
        return {
            "question": question,
            "result": answer,
            "headline": question,
            "support": "",
            "eyebrow": "",
            "source": str(block.get("source") or phrase).strip(),
            "lines": question_lines or [question],
            "questionLines": question_lines,
            "resultLines": result_lines,
            "role": "pair",
        }
    lines = _headline_lines(phrase)
    return {
        "question": phrase,
        "result": "",
        "headline": phrase,
        "support": "",
        "eyebrow": "",
        "source": phrase,
        "lines": lines or [phrase],
        "questionLines": lines or [phrase],
        "resultLines": [],
        "role": "pair",
    }


def _headlines(spec: dict[str, Any]) -> list[str]:
    values = spec.get("headlines")
    if isinstance(values, list):
        result = [str(item).strip() for item in values if str(item).strip()]
        if result:
            return result
    phrase = str(spec.get("headline") or "").strip()
    return [phrase] if phrase else []


def _scene_windows(count: int, duration: float) -> list[tuple[float, float]]:
    slot = duration / max(1, count)
    return [
        (round(index * slot, 3), round(duration if index == count - 1 else (index + 1) * slot, 3))
        for index in range(max(1, count))
    ]
