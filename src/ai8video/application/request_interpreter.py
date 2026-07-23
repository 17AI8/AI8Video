from __future__ import annotations

from typing import Any

from ai8video.generation.video_prompt_planner import LLMCallable
from ai8video.generation.video_prompt_support import parse_json_object


def interpret_generation_request_with_ai(
    text: str,
    *,
    llm: LLMCallable | None,
) -> dict[str, Any] | None:
    if llm is None:
        return None
    raw_text = str(text or "").strip()
    if not raw_text:
        return None
    raw = llm(_build_request_interpretation_prompt(raw_text))
    data = parse_json_object(raw)
    return _normalize_interpretation(data)


def _build_request_interpretation_prompt(text: str) -> str:
    return f"""你是AI8video 的员工自然语言请求理解器。

你的任务不是生成视频提示词，而是先理解员工这句话到底要AI8video 做什么。
必须优先理解上下文含义，不要按固定关键词或正则机械匹配。

请把员工消息解析成 JSON 对象，只返回 JSON，不要解释。

字段定义：
- intent: 员工本轮意图。可填：
  - "generation": 普通视频生成或多条生成。
  - "batch_run": 候选池/批量跑量/每日跑量任务。
  - "batch_seed_followup": 员工正在补充批量候选列表。
  - "rewrite": 修改或重做上一轮某一条视频。
  - "content_completion_followup": 员工正在补充台词/口播，或要求AI8智能补全台词。
  - "core_keywords_followup": 员工正在补充或跳过核心主题/关键词。
  - "unknown": 无法判断。
- mode: "batch_videos" 或 "single_video"。如果员工要求多条、多个或批量生成视频，或开头写了“10个，重大消息”这类数量 + 主题，填 "batch_videos"；如果明确只要一条，填 "single_video"。
- video_count: 目标视频数量。员工说“10个，重大消息”“来十条”“生成6个选题”“生成两条视频”都要识别成真实数字，不要擅自截断；普通生成的最多 5 条限制由后续产品策略明确提示。无法确定则填 null。
- duration_seconds: 单条视频时长秒数。没有明确时填 null；普通多条生成后续会统一固定为每条 10 秒。
- concurrent_generation: 员工是否明确要求并发/快速/同时提交。明确要求普通/逐条则填 false；未提则填 null；普通多条生成后续会强制逐条迭代。
- html_motion_overlay: 员工是否明确要求开启 HTML 动效叠加。明确要求关闭/不用则填 false；明确要求开启/使用则填 true；未提填 null。
- reference_image_decision: 明确说不用参考图填 false；明确要用参考图/当前参考图/默认参考图填 true；未提填 null。
- core_keywords: 本轮核心主题或关键词。比如“重大消息”“618 倒计时 5 天”“AI8video 全球发布”等。没有则填 null。
- style_hint: 风格、场景、人物、行业等要求。没有则填 null。
- batch_target_count: 批量跑量目标通过/生成条数。不是批量跑量则填 null。
- batch_seed_messages: 如果消息里包含候选提示词/候选选题/候选剧本列表，填字符串数组；没有则填 []。
- rewrite_video_index: 员工要求重做/修改第几条视频。不是重做则填 null。
- rewrite_instruction: 员工对重做的修改要求。不是重做则填 null。
- needs_content_completion: 如果当前消息只给了条数/素材/风格但缺少可直接生成的视频台词、口播或内容主题，填 true；否则 false。
- needs_core_keywords: 如果批量生成会因为长文档或长素材导致主题发散、且消息里没有核心主题，填 true；如果已经能提取核心主题，填 false。
- confidence: 0 到 1。

员工消息：
{text}
"""


def _normalize_interpretation(data: dict[str, Any]) -> dict[str, Any]:
    intent = str(data.get("intent") or "").strip()
    allowed_intents = {
        "generation",
        "batch_run",
        "batch_seed_followup",
        "rewrite",
        "content_completion_followup",
        "core_keywords_followup",
        "unknown",
    }
    if intent not in allowed_intents:
        intent = "unknown"
    mode = str(data.get("mode") or "").strip()
    if mode not in {"batch_videos", "single_video"}:
        mode = ""
    video_count = _positive_int_or_none(data.get("video_count"))
    duration_seconds = _positive_int_or_none(data.get("duration_seconds"))
    concurrent_generation = _bool_or_none(data.get("concurrent_generation"))
    html_motion_overlay = _bool_or_none(data.get("html_motion_overlay"))
    reference_image_decision = _bool_or_none(data.get("reference_image_decision"))
    confidence = data.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return {
        "intent": intent,
        "mode": mode or None,
        "video_count": video_count,
        "duration_seconds": duration_seconds,
        "concurrent_generation": concurrent_generation,
        "html_motion_overlay": html_motion_overlay,
        "reference_image_decision": reference_image_decision,
        "core_keywords": _clean_text(data.get("core_keywords")),
        "style_hint": _clean_text(data.get("style_hint")),
        "batch_target_count": _positive_int_or_none(data.get("batch_target_count")),
        "batch_seed_messages": _clean_text_list(data.get("batch_seed_messages")),
        "rewrite_video_index": _positive_int_or_none(data.get("rewrite_video_index")),
        "rewrite_instruction": _clean_text(data.get("rewrite_instruction")),
        "needs_content_completion": bool(data.get("needs_content_completion")),
        "needs_core_keywords": bool(data.get("needs_core_keywords")),
        "confidence": confidence,
    }


def _positive_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 1:
        return None
    return number


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "是", "需要", "并发"}:
            return True
        if lowered in {"false", "no", "0", "否", "不用", "普通"}:
            return False
    return None


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items
