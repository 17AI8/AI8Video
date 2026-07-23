from __future__ import annotations

import json
import re

from ai8video.generation.business_prompt import sanitize_internal_fidelity_notes


def parse_json_array(raw: str) -> list[object]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    if not text.startswith("["):
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            text = match.group(0)
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array")
    return data


def build_json_array_repair_prompt(raw: str, video_count: int) -> str:
    return f"""你是 JSON 格式修复器。

任务：把下面这段模型输出修成严格合法 JSON 数组。

硬性要求：
1. 只能修复 JSON 语法，例如补逗号、转义字符串内部引号、去掉多余说明。
2. 不要改写标题、prompt、台词、source_summary、preserved_keywords 或 omitted_keywords_reason 的内容。
3. 输出必须是 JSON 数组，数组长度必须等于 {video_count}。
4. 不要输出解释、Markdown 或代码块。

待修复内容：
{raw}
"""


def coerce_seed_strings(data: list[object]) -> list[str]:
    output: list[str] = []
    for item in data:
        text = ""
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("prompt") or item.get("text") or item.get("title") or "").strip()
        if text:
            output.append(text)
    return output


def coerce_text_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item or "").strip())]


def format_timing_rule(final_duration_seconds: int | None) -> str:
    duration = clean_positive_int(final_duration_seconds)
    if not duration:
        return (
            "每条提示词都要包含两个时间段：0-5 秒、5-10 秒；"
            "每段都写镜头景别、场景描述、运镜动作、人物动作、台词/口播、音效建议。"
        )
    return (
        f"每条提示词必须按最终成片约 {duration} 秒来规划完整时间轴和完整口播；"
        "如果用户剧本或当前任务附加约束已经给出镜头时间模板，必须以该模板为准，"
        "不要退回默认 0-5 秒、5-10 秒结构。每段都写镜头景别、场景描述、运镜动作、"
        "人物动作、台词/口播、音效建议；口播必须在源头就适配最终时长，"
        "不要指望后置 TTS 再压缩或重写正文。"
    )


def clean_positive_int(value: int | str | None) -> int | None:
    try:
        parsed = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def normalize_keyword_guidance(data: dict) -> dict:
    return {
        "global_keywords": coerce_text_list(data.get("global_keywords")),
        "must_preserve_facts": coerce_text_list(data.get("must_preserve_facts")),
        "video_keyword_guidance": normalize_video_keyword_guidance(data.get("video_keyword_guidance")),
        "usage_policy": str(data.get("usage_policy") or data.get("notes") or "").strip(),
    }


def normalize_video_keyword_guidance(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    output: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            index = len(output) + 1
        output.append({
            "index": index,
            "source_hint": str(item.get("source_hint") or item.get("source") or "").strip(),
            "keywords": coerce_text_list(item.get("keywords")),
            "facts": coerce_text_list(item.get("facts")),
            "usage_note": str(item.get("usage_note") or item.get("note") or "").strip(),
        })
    return output


def format_keyword_guidance_block(keyword_guidance: dict | None) -> str:
    if not keyword_guidance:
        return "（无独立关键词模型结果；规划模型需自行基于全文提炼，但仍不得使用本地硬编码内容。）"
    return json.dumps(keyword_guidance, ensure_ascii=False, indent=2)


def format_task_constraint_block(task_constraints: str | None) -> str:
    normalized = str(task_constraints or "").strip()
    if not normalized:
        return ""
    return (
        "\n当前任务附加高优先级约束：\n"
        f"{normalized}\n\n"
        "执行要求：这条约束在本次任务所有视频与后续重做中持续生效；"
        "如果原素材里的默认办公室、会议室、桌面、服装或姿态设定与它冲突，"
        "优先保留原文主题、卖点和情绪，再把视觉场景改写到这条约束上，"
        "直到用户明确修改或删除为止。"
    )


def parse_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    if not text.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            text = match.group(0)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def clean_video_title(title: str, index: int) -> str:
    lines = []
    for raw_line in sanitize_internal_fidelity_notes(title).splitlines():
        line = raw_line.strip(" ：:，,。")
        if not line or re.match(r"^(品牌保真|信息保真|提示词约束|保真要求)", line):
            continue
        lines.append(line)
    cleaned = " ".join(lines).strip()
    if len(cleaned) > 40:
        cleaned = re.split(r"[；;。]", cleaned, 1)[0].strip() or cleaned[:40].rstrip()
    return cleaned or f"视频 {index}"
