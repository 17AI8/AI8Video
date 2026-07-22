from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from ai8video.media.local_tts import prepare_narration_text
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


NARRATION_REVIEW_DIR = (USER_FILE_ROOT / "台词审核").resolve()
NARRATION_REVIEW_SETTINGS_PATH = NARRATION_REVIEW_DIR / "settings.json"
NARRATION_REVIEW_COUNT_DEFAULT = 2
NARRATION_REVIEW_COUNT_MIN = 0
NARRATION_REVIEW_COUNT_MAX = 10


def narration_review_status() -> dict[str, Any]:
    settings = _read_settings()
    return {
        "ok": True,
        "reviewCount": normalize_narration_review_count(settings.get("reviewCount")),
    }


def update_narration_review_count(value: Any) -> dict[str, Any]:
    settings = _read_settings()
    settings["reviewCount"] = normalize_narration_review_count(value)
    _write_settings(settings)
    return narration_review_status()


def normalize_narration_review_count(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = NARRATION_REVIEW_COUNT_DEFAULT
    return min(max(number, NARRATION_REVIEW_COUNT_MIN), NARRATION_REVIEW_COUNT_MAX)


def review_narration_text(
    llm: Callable[[str], str],
    *,
    video_prompt: str,
    candidate_text: str,
    business_prompt: str,
    review_count: int,
) -> dict[str, Any]:
    current = prepare_narration_text(candidate_text)
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, normalize_narration_review_count(review_count) + 1):
        raw = llm(_build_review_prompt(video_prompt, current, business_prompt))
        result = _parse_review_result(raw)
        attempts.append({"attempt": attempt, **result})
        approved = prepare_narration_text(result.get("approvedText") or "")
        if result["passes"] and approved:
            return {"passes": True, "text": approved, "attempts": attempts}
        if approved:
            current = approved
    return {"passes": False, "text": "", "attempts": attempts}


def _build_review_prompt(video_prompt: str, candidate_text: str, business_prompt: str) -> str:
    return f"""你是短视频 TTS 台词审核模型。只返回严格 JSON，不要解释。

审核目标：确认候选台词只包含观众应该听到的人声正文，不包含任何制作说明或系统约束。

必须拦截：
1. 镜头、景别、场景、人物动作、服装、运镜、情绪、音效、音乐和时长说明。
2. “全片无任何……”“已过滤……”“符合要求……”“禁止出现……”等合规总结或内部补丁说明。
3. 候选台词没有依据却新增的品牌、日期、数字、事实、承诺或禁用内容。
4. 台词、口播、旁白、画外音等字段标签本身。

如果候选包含污染内容，删除污染内容但不要润色、扩写或新增正文；修正后返回 passes=false，供下一轮复审。
如果候选已经纯净，原样返回并设置 passes=true。
如果删除污染内容后没有可信台词，approved_text 返回空字符串。

返回格式：
{{"passes":true或false,"issues":[{{"type":"问题类型","text":"问题文本","reason":"原因"}}],"approved_text":"审核后的纯台词"}}

用户业务系统提示词：
{business_prompt or '（无）'}

视频模型提示词：
{video_prompt}

候选 TTS 台词：
{candidate_text}
"""


def _parse_review_result(raw: str) -> dict[str, Any]:
    data = _parse_json_object(raw)
    issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    approved = data.get("approved_text") or data.get("approvedText") or ""
    return {
        "passes": data.get("passes") is True,
        "approvedText": str(approved).strip(),
        "issues": [item for item in issues if isinstance(item, dict)][:20],
    }


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("台词审核模型未返回 JSON 对象")
    return data


def _read_settings() -> dict[str, Any]:
    try:
        data = json.loads(NARRATION_REVIEW_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(data: dict[str, Any]) -> None:
    ensure_user_file_root()
    NARRATION_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    NARRATION_REVIEW_SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
