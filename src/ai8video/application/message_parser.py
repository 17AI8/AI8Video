from __future__ import annotations

import re
from pathlib import Path

from ai8video.media.motion.html_motion_overlay import default_html_motion_overlay_enabled
from ai8video.core.models import ParsedRequest


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def parse_employee_message(message: str) -> ParsedRequest:
    """Extract only routing fields from a natural-language request.

    This parser decides what the employee is asking for. It does not split
    scripts into episodes; that belongs to ai_script_splitter.
    """
    text = message.strip()
    episode_count = _extract_episode_count(text)
    reference_image = _extract_reference_image(text)
    style_hint = _extract_style_hint(text)
    core_keywords = extract_core_keywords(text)
    duration_seconds = _extract_duration_seconds(text)
    concurrent_generation = detect_concurrent_generation_decision(text) is True
    html_motion_overlay = detect_html_motion_overlay_decision(text)
    mode = "multi_episode_script" if episode_count and episode_count > 1 else "single_prompt"
    return ParsedRequest(
        raw_text=text,
        mode=mode,
        episode_count=episode_count,
        reference_image=reference_image,
        style_hint=style_hint,
        core_keywords=core_keywords,
        duration_seconds=duration_seconds,
        concurrent_generation=concurrent_generation,
        html_motion_overlay_enabled=(
            default_html_motion_overlay_enabled()
            if html_motion_overlay is None
            else html_motion_overlay
        ),
    )


def extract_episode_count(text: str) -> int | None:
    return _extract_episode_count(text)


def extract_duration_seconds(text: str) -> int | None:
    return _extract_duration_seconds(text)


def extract_reference_image(text: str) -> str | None:
    return _extract_reference_image(text)


def extract_style_hint(text: str) -> str | None:
    return _extract_style_hint(text)


def extract_core_keywords(text: str) -> str | None:
    patterns = [
        r"(?:核心主题|主题|关键词|核心关键词|重点|主打)\s*[:：是为]?\s*([^\n。！？!?；;]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = _clean_core_keywords(match.group(1))
        if value:
            return value
    return None


def detect_batch_mode(text: str) -> bool:
    if re.search(r"(批量|每日|每天|候选池).{0,20}([一二三四五六七八九十两零百\d]+)\s*条", text):
        return True
    if re.search(r"(今日|今天).{0,20}(批量|跑|冲|做).{0,8}([一二三四五六七八九十两零百\d]+)\s*条", text):
        return True
    if re.search(r"目标.{0,12}([一二三四五六七八九十两零百\d]+)\s*条", text):
        return True
    return False


def extract_batch_target_count(text: str) -> int | None:
    patterns = [
        r"(?:批量|每日|每天|候选池).{0,20}([一二三四五六七八九十两零百\d]+)\s*条",
        r"(?:今日|今天).{0,20}(?:批量|跑|冲|做).{0,8}([一二三四五六七八九十两零百\d]+)\s*条",
        r"目标.{0,12}([一二三四五六七八九十两零百\d]+)\s*条",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = _parse_small_int(match.group(1))
        if value is None:
            continue
        if 1 <= value <= 500:
            return value
    return None


def extract_batch_seed_messages(text: str) -> list[str]:
    items: list[str] = []

    numbered_inline = re.findall(r"(?:^|[\n；;])\s*\d+[.)、]\s*([^\n；;]+)", text)
    if numbered_inline:
        for item in numbered_inline:
            cleaned = _clean_batch_seed_line(item)
            if cleaned:
                items.append(cleaned)
        return _dedupe_keep_order(items)

    inline_payload = _extract_inline_batch_payload(text)
    if inline_payload:
        for chunk in re.split(r"[\n；;]+", inline_payload):
            cleaned = _clean_batch_seed_line(chunk)
            if cleaned:
                items.append(cleaned)
        if items:
            return _dedupe_keep_order(items)

    for raw_line in text.splitlines():
        cleaned = _clean_batch_seed_line(raw_line)
        if cleaned:
            items.append(cleaned)

    if items:
        return _dedupe_keep_order(items)
    return []


def detect_mode_hint(text: str) -> str | None:
    if re.search(r"(剧本|拆成|分成|第\s*[一二三四五六七八九十\d]+\s*集)", text):
        return "multi_episode_script"
    if re.search(r"(提示词|一条视频|单条视频|直接生成|生成一条)", text):
        return "single_prompt"
    return None


def detect_reference_decision(text: str) -> bool | None:
    if re.search(r"(不用|不需要|无需|没有)\s*(参考图|图片|首帧)", text):
        return False
    if "参考图" in text or "首帧" in text:
        return True
    return None


def detect_concurrent_generation_decision(text: str) -> bool | None:
    if re.search(r"(不用|不要|关闭|禁用|不开|别)\s*(并发|同时提交|批量提交)", text):
        return False
    if re.search(r"(串行|逐条|一条一条|普通模式|稳妥模式)", text):
        return False
    if re.search(r"(并发|同时提交|一起提交|批量提交|加速模式|快速模式)", text):
        return True
    return None


def detect_html_motion_overlay_decision(text: str) -> bool | None:
    if re.search(r"(不用|不要|关闭|禁用|不开|别)\s*(HTML\s*动效|动效叠加|透明动效)", text, flags=re.IGNORECASE):
        return False
    if re.search(r"(开启|打开|启用|需要|使用|加上)\s*(HTML\s*动效|动效叠加|透明动效)", text, flags=re.IGNORECASE):
        return True
    return None


def extract_episode_index(text: str) -> int | None:
    patterns = [
        r"第\s*([一二三四五六七八九十两零\d]+)\s*[集条]",
        r"重做\s*第\s*([一二三四五六七八九十两零\d]+)\s*[集条]",
        r"第\s*([一二三四五六七八九十两零\d]+)\s*[集条]\s*重做",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = _parse_small_int(match.group(1))
        if value and 1 <= value <= 100:
            return value
    return None


def detect_rewrite_instruction(text: str) -> str | None:
    if extract_episode_index(text) is None:
        return None
    if not re.search(r"(重做|改|调整|优化|不要|改成|更像|重来|重新生成)", text):
        return None
    instruction = re.sub(r"第\s*[一二三四五六七八九十两零\d]+\s*[集条]", "", text)
    instruction = re.sub(r"^(帮我|麻烦|请|把|将)\s*", "", instruction)
    instruction = re.sub(r"(重做这一条|重做这条|重做|重新生成这一条|重新生成这条|重新生成)\s*$", "", instruction)
    instruction = instruction.strip(" ，,。")
    return instruction or "沿用原目标重做这一集，但按本轮要求调整画面与语气。"


def _extract_episode_count(text: str) -> int | None:
    patterns = [
        r"^\s*([一二三四五六七八九十两零百\d]+)\s*[集条个]\s*$",
        r"^\s*([一二三四五六七八九十两零百\d]+)\s*[集条个]\s*[，,、\s]+",
        r"拆成\s*([一二三四五六七八九十两零百\d]+)\s*[集条个]",
        r"按\s*([一二三四五六七八九十两零百\d]+)\s*[集条个]",
        r"([一二三四五六七八九十两零百\d]+)\s*集剧本",
        r"([一二三四五六七八九十两零百\d]+)\s*条短视频",
        r"生成\s*([一二三四五六七八九十两零百\d]+)\s*条",
        r"生成\s*([一二三四五六七八九十两零百\d]+)\s*个\s*\d+\s*[sS秒]\s*$",
        r"生成\s*([一二三四五六七八九十两零百\d]+)\s*个\s*\d+\s*[sS秒]\s*(?:短视频|视频)",
        r"([一二三四五六七八九十两零百\d]+)\s*个\s*\d+\s*[sS秒]\s*(?:短视频|视频)",
        r"生成\s*([一二三四五六七八九十两零百\d]+)\s*(?:个|条|集)\s*(?:短视频|视频)",
        r"([一二三四五六七八九十两零百\d]+)\s*(?:个|条|集)\s*(?:短视频|视频)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = _parse_small_int(match.group(1))
            if value and 1 <= value <= 100:
                return value
    return None


def _extract_duration_seconds(text: str) -> int | None:
    match = re.search(r"(\d+)\s*(?:秒|[sS])", text)
    if not match:
        return 10
    value = int(match.group(1))
    return value if 3 <= value <= 120 else 10


def _extract_reference_image(text: str) -> str | None:
    url = re.search(r"https?://[^\s，。]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\s，。]+)?", text, re.I)
    if url:
        return url.group(0)

    desktop_file = re.search(r"(?:桌面|Desktop)[，,\s]*(?:文件名)?(?:叫|为|是)?\s*([\w\u4e00-\u9fff ._-]+\.(?:jpg|jpeg|png|webp))", text, re.I)
    if desktop_file:
        return str(Path.home() / "Desktop" / desktop_file.group(1).strip())

    path_like = re.search(r"((?:/|[A-Za-z]:\\)[^\s，。]+?\.(?:jpg|jpeg|png|webp))", text, re.I)
    if path_like:
        return path_like.group(1)

    plain_file = re.search(r"([\w\u4e00-\u9fff ._-]+\.(?:jpg|jpeg|png|webp))", text, re.I)
    if plain_file:
        name = plain_file.group(1).strip()
        if Path(name).suffix.lower() in _IMAGE_SUFFIXES:
            return str(Path.home() / "Desktop" / name)
    return None


def _extract_style_hint(text: str) -> str | None:
    hints = []
    for key in ["商务", "真实", "口播", "剧情", "客户见证", "纪录片", "电商", "探店"]:
        if key in text:
            hints.append(key)
    hints.extend(_extract_freeform_style_clauses(text))
    return "、".join(_dedupe_keep_order(hints)) if hints else None


def _clean_core_keywords(text: str) -> str | None:
    value = re.sub(r"\s+", " ", text or "").strip(" ：:，,。")
    value = re.sub(r"(生成|做|拆成).*$", "", value).strip(" ：:，,。")
    value = re.sub(r"(?:，|,|、)?\s*(?:并发模式|普通模式|串行模式|逐条生成)\s*$", "", value).strip(" ：:，,。")
    if not value or len(value) < 2:
        return None
    if len(value) > 80:
        value = value[:80].rstrip(" ，,。")
    return value


def _extract_freeform_style_clauses(text: str) -> list[str]:
    clauses: list[str] = []
    for raw_clause in re.split(r"[。！？!?；;\n]", text):
        for clause in re.split(r"[，,]", raw_clause):
            cleaned = clause.strip()
            if not cleaned or len(cleaned) > 80:
                continue
            if _looks_like_material_or_path(cleaned):
                continue
            if _is_style_or_visual_clause(cleaned):
                clauses.append(cleaned)
    return clauses[:6]


def _is_style_or_visual_clause(text: str) -> bool:
    return bool(
        re.search(r"(风格|质感|调性|视觉|画面|镜头|运镜|构图|排版|版式|节奏|氛围)", text)
        or re.search(r"(文字|标题|字卡|字幕|标语|口号|关键词|重点词|屏幕字|贴纸|弹幕)", text)
        or re.search(r"(营销号|纪录片|访谈|真人|商务|电商|探店|客户见证)", text)
    )


def _looks_like_material_or_path(text: str) -> bool:
    if "@" in text:
        return True
    if re.search(r"[/\\].+\.(?:docx|txt|md|jpg|jpeg|png|webp|mp4)", text, re.I):
        return True
    return False


def _clean_batch_seed_line(text: str) -> str | None:
    line = text.strip()
    if not line:
        return None
    line = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", line).strip()
    if not line:
        return None
    if re.fullmatch(r"(候选|候选如下|题材如下|方向如下|批量如下|请按下面执行|如下)", line):
        return None
    if detect_batch_mode(line):
        return None
    if len(line) < 6:
        return None
    return line


def _extract_inline_batch_payload(text: str) -> str | None:
    match = re.search(
        r"(?:候选|候选如下|题材|题材如下|方向|方向如下|内容|内容如下|批量如下)\s*[:：]\s*(.+)",
        text,
        re.S,
    )
    if match:
        return match.group(1).strip()
    if detect_batch_mode(text):
        match = re.search(r"[:：]\s*(.+)", text, re.S)
        if match:
            return match.group(1).strip()
    return None


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _parse_small_int(raw: str) -> int | None:
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if raw == "百":
        return 100
    if "百" in raw:
        left, _, right = raw.partition("百")
        hundreds = 1 if not left else digits.get(left)
        if hundreds is None:
            return None
        if not right:
            return hundreds * 100
        tail = _parse_small_int(right)
        if tail is None:
            return None
        return hundreds * 100 + tail
    if raw == "十":
        return 10
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = 1 if not left else digits.get(left)
        ones = 0 if not right else digits.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    value = 0
    for ch in raw:
        digit = digits.get(ch)
        if digit is None:
            return None
        value = value * 10 + digit
    return value or None
