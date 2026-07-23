from __future__ import annotations

import json
import re
from typing import Any, Callable

from ai8video.generation.business_prompt import read_business_prompt
from ai8video.media.local_tts import extract_dialogue_text, prepare_narration_text
from ai8video.core.models import VideoPrompt
from ai8video.generation.prompt_trace import append_prompt_trace


LLMCallable = Callable[[str], str]


def review_final_outputs(
    videos: list[VideoPrompt],
    *,
    llm: LLMCallable | None,
    trace_session_id: str | None = None,
) -> list[VideoPrompt]:
    if not videos:
        return []
    if llm is None:
        return [_fallback_video(video) for video in videos]
    prompt = _build_review_prompt(videos)
    append_prompt_trace("output_review_model_input", session_id=trace_session_id, payload={"prompt": prompt})
    try:
        raw = llm(prompt)
        append_prompt_trace("output_review_model_output", session_id=trace_session_id, payload={"raw": raw})
        return _apply_review(videos, raw)
    except Exception as exc:
        append_prompt_trace(
            "output_review_model_error",
            session_id=trace_session_id,
            payload={"errorType": exc.__class__.__name__, "error": str(exc)},
        )
        return [_fallback_video(video) for video in videos]


def _build_review_prompt(videos: list[VideoPrompt]) -> str:
    payload = [
        {"index": item.index, "title": item.title, "video_prompt": item.prompt}
        for item in videos
    ]
    return f"""你是AI8video 的最终输出后审核模型。

用户系统提示词：
{read_business_prompt() or '（无）'}

请审核每条最终视频提示词，并直接返回可执行的修正结果。
规则：
1. narration_text 只能包含观众实际听到的台词或画外音，禁止出现 source_summary、选材说明、审核说明、脚本编号来源或 notes。
2. corrected_video_prompt 必须保留镜头与可拍摄性，同时修正违反系统提示词或明显容易触发内容审核的表达。
3. 用户系统提示词是最高业务约束。即使某项要求可能提高上游内容审核风险，也不得擅自删除、弱化或替换；必须写入 user_advisories 提醒用户。只有系统提示词明确授权“自动安全改写”时，才可以修改该项要求。
4. 只修正与用户系统提示词冲突的内容、内部字段泄漏、明显逻辑错误和非用户指定的风险表达。
5. user_advisories 默认必须是空数组。只有风险确实需要用户权衡、可能改变用户决策时才填写；不要为了证明做过审核而生成空泛建议。同批相同风险使用完全相同的简短表述，供界面合并展示。
6. passes 表示原输入是否无需修改；即使 passes=false，也必须返回修正结果。
7. 只返回严格 JSON 数组：
[{{"index":1,"passes":true,"corrected_video_prompt":"...","narration_text":"...","violations":["已自动修正的问题"],"user_advisories":["保留但需用户知情的风险"]}}]

候选：
{json.dumps(payload, ensure_ascii=False)}
"""


def _apply_review(videos: list[VideoPrompt], raw: str) -> list[VideoPrompt]:
    data = json.loads(_extract_json_array(raw))
    by_index = {int(item.get("index")): item for item in data if isinstance(item, dict) and item.get("index")}
    if len(by_index) != len(videos):
        raise ValueError("后审核返回条数不完整")
    return [_reviewed_video(video, by_index[video.index]) for video in videos]


def _reviewed_video(video: VideoPrompt, item: dict[str, Any]) -> VideoPrompt:
    prompt = str(item.get("corrected_video_prompt") or "").strip()
    if not prompt:
        raise ValueError(f"第 {video.index} 条后审核缺少 corrected_video_prompt")
    narration = prepare_narration_text(str(item.get("narration_text") or ""))
    guidance = dict(video.keyword_guidance or {})
    guidance["post_review"] = {
        "passes": bool(item.get("passes")),
        "narrationText": narration,
        "violations": _string_list(item.get("violations")),
        "userAdvisories": _string_list(item.get("user_advisories")),
    }
    return VideoPrompt(
        index=video.index,
        title=video.title,
        prompt=prompt,
        source_summary=video.source_summary,
        keyword_guidance=guidance,
    )


def _fallback_video(video: VideoPrompt) -> VideoPrompt:
    narration = prepare_narration_text(extract_dialogue_text(video.prompt))
    guidance = dict(video.keyword_guidance or {})
    guidance["post_review"] = {
        "passes": True,
        "narrationText": narration,
        "violations": [],
        "userAdvisories": [],
        "fallback": True,
    }
    return VideoPrompt(
        index=video.index,
        title=video.title,
        prompt=video.prompt,
        source_summary=video.source_summary,
        keyword_guidance=guidance,
    )


def _extract_json_array(raw: str) -> str:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.I)
    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        raise ValueError("后审核未返回 JSON 数组")
    return match.group(0)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:180] for item in value if str(item).strip()][:10]
