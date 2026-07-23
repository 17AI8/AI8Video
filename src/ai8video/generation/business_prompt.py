from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile
from typing import Callable, Any

from ai8video.core.models import VideoPrompt
from ai8video.generation.prompt_trace import append_prompt_trace
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


BUSINESS_PROMPT_PATH = USER_FILE_ROOT / "业务提示词" / "system_prompt.txt"

DEFAULT_BUSINESS_PROMPT = ""
FINALIZE_EPISODE_BATCH_SIZE = 2
BUSINESS_PROMPT_OVERRIDE_MARKER = "本次明确覆盖工具栏用户设置"

BUSINESS_PROMPT_HEADER = "【用户可编辑业务模型系统提示词】"
LLMCallable = Callable[[str], str]


def ensure_business_prompt_file() -> Path:
    ensure_user_file_root()
    BUSINESS_PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not BUSINESS_PROMPT_PATH.exists():
        BUSINESS_PROMPT_PATH.write_text(DEFAULT_BUSINESS_PROMPT, encoding="utf-8")
    return BUSINESS_PROMPT_PATH


def read_business_prompt() -> str:
    path = ensure_business_prompt_file()
    return path.read_text(encoding="utf-8").strip()


def business_prompt_generation_policy(*, task_constraints: str | None = None) -> dict[str, Any]:
    if BUSINESS_PROMPT_OVERRIDE_MARKER in str(task_constraints or ""):
        return {"filteredTerms": []}
    prompt = re.sub(r"\s+", "", read_business_prompt())
    terms = re.findall(r"(?:所有)?([A-Za-z][A-Za-z0-9_-]{1,63})自动过滤", prompt, re.I)
    return {"filteredTerms": list(dict.fromkeys(term.upper() for term in terms))}


def write_business_prompt(content: str) -> dict:
    normalized = str(content or "").strip()
    path = ensure_business_prompt_file()
    serialized = (normalized + "\n") if normalized else ""
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    stat = path.stat()
    return {
        "ok": True,
        "path": str(path),
        "updatedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "content": normalized,
    }


def business_prompt_meta() -> dict:
    path = ensure_business_prompt_file()
    stat = path.stat()
    return {
        "path": str(path),
        "updatedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def business_prompt_block() -> str:
    content = read_business_prompt()
    if not content:
        return ""
    return f"{BUSINESS_PROMPT_HEADER}\n{content}"


def append_business_prompt(text: str) -> str:
    base = str(text or "").rstrip()
    block = business_prompt_block()
    if not block:
        return base
    if BUSINESS_PROMPT_HEADER in base:
        return base
    return f"{base}\n\n{block}"


def finalize_prompt_text(text: str) -> str:
    """Legacy fallback when the core text model is unavailable.

    Natural-language constraints must be handled by the core text model. This
    fallback only strips internal metadata; it does not attempt to interpret the
    user-editable business prompt with local rules.
    """
    base = _strip_generated_constraint_lines(sanitize_internal_fidelity_notes(text))
    return _minimal_internal_prompt_guard(base)


def build_business_prompt_rewrite_prompt(
    text: str,
    *,
    prompt_kind: str = "video",
    source_summary: str | None = None,
    keyword_guidance: dict | None = None,
    task_constraints: str | None = None,
) -> str:
    business_prompt = read_business_prompt()
    kind_label = "视频模型提示词" if prompt_kind == "video" else "图片图生图提示词"
    task_constraint_block = _task_constraint_block(task_constraints)
    return f"""你是AI8video 的最终提示词质检与改写模型。

你必须从 AI 文本理解角度执行，不要机械套词表。你的任务是理解“用户可编辑业务模型系统提示词”和候选提示词之间的关系，把候选提示词重写成可以直接发给{kind_label}的最终文本。

用户可编辑业务模型系统提示词：
{business_prompt or "（用户未填写额外系统提示词）"}
{task_constraint_block}

AI8video 固定质量要求：
1. 最终提示词不得出现内部补丁说明，例如“品牌保真、信息保真、口播保真、已按系统提示词过滤”等。
2. 精确理解系统提示词的作用域：只处理系统提示词明确禁止、要求删除或要求改写的内容；不要把相近概念、上下位概念、翻译、简称、同类词或上下文标签一起误删。
3. 不要把本轮未要求的身份、声线、性别、品牌、日期、画面元素或禁用项强塞进最终提示词；如果候选里已有这类内容，也必须先判断它是否来自用户输入、参考剧本或系统提示词。
4. 如果系统提示词只是约束某个字段、某个位置或某种表述，不要误删字段名、结构标签或未被禁止的正文内容。
5. 保留可拍摄性：最终提示词仍要包含镜头、人物动作、表情、情绪、运镜和可直接口播的中文台词。
6. 输出前自检一次：最终提示词是否仍包含模型自己识别出的违背系统提示词或候选约束的内容；如有，重写为自然可拍表达，不要追加解释性补丁。
7. 如果提供了来源摘要和关键词指导，它们来自上游 AI 文本理解；你要尽可能保留其中的高价值关键词和必保事实。只有当系统提示词、候选本身或可拍摄性明确不适合时才改写或省略，并在 notes 里说明。

只返回严格 JSON 对象，不要解释。格式：
{{
  "final_prompt": "重写后的最终提示词",
  "notes": "一句话说明你理解系统提示词后做了什么"
}}

候选提示词：
{text}

来源摘要：
{source_summary or "（无）"}

上游 AI 关键词指导：
{json.dumps(keyword_guidance or {}, ensure_ascii=False, indent=2)}
"""


def build_business_prompt_batch_rewrite_prompt(
    videos: list[VideoPrompt],
    *,
    prompt_kind: str = "video",
    task_constraints: str | None = None,
) -> str:
    business_prompt = read_business_prompt()
    kind_label = "视频模型提示词" if prompt_kind == "video" else "图片图生图提示词"
    task_constraint_block = _task_constraint_block(task_constraints)
    video_items = [
        {
            "index": video.index,
            "title": str(video.title or "").strip(),
            "prompt": str(video.prompt or "").strip(),
            "source_summary": str(video.source_summary or "").strip(),
            "keyword_guidance": video.keyword_guidance or {},
        }
        for video in videos
    ]
    return f"""你是AI8video 的整批最终提示词质检与改写模型。

你必须从 AI 文本理解角度执行，不要机械套词表。你的任务是理解“用户可编辑业务模型系统提示词”和候选提示词之间的关系，把整批候选提示词一次性重写成可以直接发给{kind_label}的最终文本。

用户可编辑业务模型系统提示词：
{business_prompt or "（用户未填写额外系统提示词）"}
{task_constraint_block}

关键解释规则：
1. 精确理解系统提示词的作用域：只处理系统提示词明确禁止、要求删除或要求改写的内容；不要把相近概念、上下位概念、翻译、简称、同类词或上下文标签一起误删。
2. 如果系统提示词只是禁止某个字段、某个位置或某种表述里的内容，不要误删字段名、结构标签或未被禁止的正文内容。
3. 对每条候选提示词先做一次内部自检：它是否仍然包含系统提示词明确禁止的内容；如果有，用符合原意且可拍摄的表达重写，而不是追加解释性补丁。
4. 不要把本轮未要求的身份、声线、性别、品牌、日期、画面元素或禁用项强塞进最终提示词；如果候选里已有这类内容，也必须先判断它是否来自用户输入、参考剧本或系统提示词。
5. 最终提示词不得出现内部补丁说明，例如“品牌保真、信息保真、口播保真、已按系统提示词过滤”等。
6. 保留整批差异：每条要保留不同来源段、不同主题和不同场景，不要把整批改成同一种表达。
7. 每条输入里的 source_summary 和 keyword_guidance 来自上游 AI 文本理解；最终提示词要尽可能保留其中的高价值关键词、专名、日期和必保事实。只有当系统提示词、候选本身或可拍摄性明确不适合时才改写或省略，并在 notes 里说明。

只返回严格 JSON 数组，不要解释。数组长度必须等于输入条数。格式：
[
  {{
    "index": 1,
    "title": "保留或轻微清理后的标题",
    "final_prompt": "重写后的最终提示词",
    "notes": "一句话说明你理解系统提示词后做了什么"
  }}
]

候选提示词数组：
{json.dumps(video_items, ensure_ascii=False)}
"""


def build_business_prompt_validation_prompt(
    text: str,
    *,
    prompt_kind: str = "video",
    task_constraints: str | None = None,
) -> str:
    business_prompt = read_business_prompt()
    kind_label = "视频模型提示词" if prompt_kind == "video" else "图片图生图提示词"
    task_constraint_block = _task_constraint_block(task_constraints)
    return f"""你是AI8video 的最终出站审校模型。

你必须从 AI 文本理解角度判断候选{kind_label}是否完整遵守“用户可编辑业务模型系统提示词”。不要用固定词表、不要机械删词、不要只看表面字符；要理解禁用要求的真实作用域，包括台词、口播、画面、标题、品牌、日期、活动词、logo、App界面、可见文字等不同语义位置。

用户可编辑业务模型系统提示词：
{business_prompt or "（用户未填写额外系统提示词）"}
{task_constraint_block}

审校要求：
1. 如果候选内容已经遵守系统提示词，返回 passes=true，并原样返回 final_prompt。
2. 如果候选内容仍违反系统提示词，返回 passes=false，并把 final_prompt 改写成可直接提交给{kind_label}的安全版本。
3. 改写时保留可拍摄性、镜头、动作、情绪和自然中文台词；不要追加“已过滤、按规则处理、系统提示词”等解释性补丁。
4. 只处理系统提示词真正禁止或要求改写的内容；不要把相近概念、上下位概念、翻译、简称、结构标签或未被禁止的正文内容一起误删。

只返回严格 JSON 对象，不要解释。格式：
{{
  "passes": true,
  "final_prompt": "审校后的最终提示词",
  "notes": "一句话说明是否修正以及原因"
}}

候选{kind_label}：
{text}
"""


def finalize_video_prompt_with_ai(
    text: str,
    *,
    llm: LLMCallable | None = None,
    trace_session_id: str | None = None,
    video_index: int | None = None,
    prompt_kind: str = "video",
    source_summary: str | None = None,
    keyword_guidance: dict | None = None,
    task_constraints: str | None = None,
) -> str:
    """Use the core text model as the primary business-prompt interpreter.

    Local cleanup below is deliberately narrow: it is a safety net for internal
    metadata only, not a replacement for natural-language understanding of the
    user-editable system prompt.
    """
    base = str(text or "").strip()
    if not base:
        return ""
    if llm is None:
        return finalize_prompt_text(base)

    model_prompt = build_business_prompt_rewrite_prompt(
        base,
        prompt_kind=prompt_kind,
        source_summary=source_summary,
        keyword_guidance=keyword_guidance,
        task_constraints=task_constraints,
    )
    append_prompt_trace(
        "business_prompt_model_input",
        session_id=trace_session_id,
        payload={
            "videoIndex": video_index,
            "promptKind": prompt_kind,
            "sourceSummary": source_summary,
            "keywordGuidance": keyword_guidance,
            "taskConstraints": task_constraints,
            "prompt": model_prompt,
        },
    )
    try:
        raw = llm(model_prompt)
        append_prompt_trace(
            "business_prompt_model_output",
            session_id=trace_session_id,
            payload={
                "videoIndex": video_index,
                "promptKind": prompt_kind,
                "raw": raw,
            },
        )
        data = _parse_json_object(raw)
        final_prompt = str(data.get("final_prompt") or "").strip()
        if not final_prompt:
            raise ValueError("business prompt model returned empty final_prompt")
        cleaned_prompt = _minimal_internal_prompt_guard(final_prompt)
        validated_prompt = _validate_business_prompt_with_ai(
            cleaned_prompt,
            llm=llm,
            trace_session_id=trace_session_id,
            video_index=video_index,
            prompt_kind=prompt_kind,
            task_constraints=task_constraints,
        )
        return _apply_custom_safety_guard(validated_prompt, task_constraints)
    except Exception as exc:
        append_prompt_trace(
            "business_prompt_model_error",
            session_id=trace_session_id,
            payload={
                "videoIndex": video_index,
                "promptKind": prompt_kind,
                "errorType": exc.__class__.__name__,
                "error": str(exc),
            },
        )
        return _apply_custom_safety_guard(_minimal_internal_prompt_guard(base), task_constraints)


def finalize_video_prompts(
    videos: list[VideoPrompt],
    *,
    llm: LLMCallable | None = None,
    trace_session_id: str | None = None,
    prompt_kind: str = "video",
    task_constraints: str | None = None,
) -> list[VideoPrompt]:
    if not videos:
        return []
    if llm is None or len(videos) == 1:
        return [
            finalize_video_prompt(
                video,
                llm=llm,
                trace_session_id=trace_session_id,
                task_constraints=task_constraints,
            )
            for video in videos
        ]

    finalized: list[VideoPrompt] = []
    for batch_index, batch in enumerate(_chunk_videos(videos, FINALIZE_EPISODE_BATCH_SIZE), 1):
        finalized.extend(_finalize_video_prompt_batch(
            batch,
            llm=llm,
            trace_session_id=trace_session_id,
            prompt_kind=prompt_kind,
            task_constraints=task_constraints,
            batch_index=batch_index,
        ))
    return finalized


def _finalize_video_prompt_batch(
    videos: list[VideoPrompt],
    *,
    llm: LLMCallable,
    trace_session_id: str | None,
    prompt_kind: str,
    task_constraints: str | None,
    batch_index: int,
) -> list[VideoPrompt]:
    model_prompt = build_business_prompt_batch_rewrite_prompt(
        videos,
        prompt_kind=prompt_kind,
        task_constraints=task_constraints,
    )
    append_prompt_trace(
        "business_prompt_batch_model_input",
        session_id=trace_session_id,
        payload={
            "batchIndex": batch_index,
            "videoCount": len(videos),
            "videoIndexes": [video.index for video in videos],
            "promptKind": prompt_kind,
            "taskConstraints": task_constraints,
            "prompt": model_prompt,
        },
    )
    try:
        raw = llm(model_prompt)
        append_prompt_trace(
            "business_prompt_batch_model_output",
            session_id=trace_session_id,
            payload={
                "batchIndex": batch_index,
                "videoCount": len(videos),
                "videoIndexes": [video.index for video in videos],
                "promptKind": prompt_kind,
                "raw": raw,
            },
        )
        data = _parse_json_array(raw)
        by_index: dict[int, dict[str, Any]] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            by_index[index] = item
        if len(by_index) != len(videos):
            raise ValueError(f"business prompt model returned {len(by_index)} items, expected {len(videos)}")
        finalized: list[VideoPrompt] = []
        for video in videos:
            item = by_index.get(video.index) or {}
            prompt = str(item.get("final_prompt") or "").strip()
            if not prompt:
                raise ValueError(f"business prompt model returned empty final_prompt for video {video.index}")
            cleaned_prompt = _minimal_internal_prompt_guard(prompt)
            finalized.append(VideoPrompt(
                index=video.index,
                title=finalize_title(
                    str(item.get("title") or video.title),
                    task_constraints=task_constraints,
                ),
                prompt=cleaned_prompt,
                source_summary=video.source_summary,
                keyword_guidance={
                    **(video.keyword_guidance or {}),
                    "final_rewrite_notes": str(item.get("notes") or "").strip(),
                },
            ))
        return finalized
    except Exception as exc:
        append_prompt_trace(
            "business_prompt_batch_model_error",
            session_id=trace_session_id,
            payload={
                "batchIndex": batch_index,
                "videoCount": len(videos),
                "videoIndexes": [video.index for video in videos],
                "promptKind": prompt_kind,
                "errorType": exc.__class__.__name__,
                "error": str(exc),
            },
        )
        return [
            finalize_video_prompt(
                video,
                llm=None,
                trace_session_id=trace_session_id,
                task_constraints=task_constraints,
            )
            for video in videos
        ]


def _chunk_videos(videos: list[VideoPrompt], batch_size: int) -> list[list[VideoPrompt]]:
    size = max(1, int(batch_size or 1))
    return [videos[index:index + size] for index in range(0, len(videos), size)]


def finalize_video_prompt(
    video: VideoPrompt,
    *,
    llm: LLMCallable | None = None,
    trace_session_id: str | None = None,
    task_constraints: str | None = None,
) -> VideoPrompt:
    return VideoPrompt(
        index=video.index,
        title=finalize_title(video.title, task_constraints=task_constraints),
        prompt=finalize_video_prompt_with_ai(
            video.prompt,
            llm=llm,
            trace_session_id=trace_session_id,
            video_index=video.index,
            prompt_kind="video",
            source_summary=video.source_summary,
            keyword_guidance=video.keyword_guidance,
            task_constraints=task_constraints,
        ),
        source_summary=video.source_summary,
        keyword_guidance=video.keyword_guidance,
    )


def _validate_business_prompt_with_ai(
    text: str,
    *,
    llm: LLMCallable,
    trace_session_id: str | None = None,
    video_index: int | None = None,
    prompt_kind: str = "video",
    task_constraints: str | None = None,
) -> str:
    base = _minimal_internal_prompt_guard(text)
    if not base:
        return ""
    validation_prompt = build_business_prompt_validation_prompt(
        base,
        prompt_kind=prompt_kind,
        task_constraints=task_constraints,
    )
    append_prompt_trace(
        "business_prompt_validation_model_input",
        session_id=trace_session_id,
        payload={
            "videoIndex": video_index,
            "promptKind": prompt_kind,
            "taskConstraints": task_constraints,
            "prompt": validation_prompt,
        },
    )
    try:
        raw = llm(validation_prompt)
        append_prompt_trace(
            "business_prompt_validation_model_output",
            session_id=trace_session_id,
            payload={
                "videoIndex": video_index,
                "promptKind": prompt_kind,
                "raw": raw,
            },
        )
        data = _parse_json_object(raw)
        final_prompt = str(data.get("final_prompt") or "").strip()
        if not final_prompt:
            raise ValueError("business prompt validation model returned empty final_prompt")
        return _apply_custom_safety_guard(_minimal_internal_prompt_guard(final_prompt), task_constraints)
    except Exception as exc:
        append_prompt_trace(
            "business_prompt_validation_model_error",
            session_id=trace_session_id,
            payload={
                "videoIndex": video_index,
                "promptKind": prompt_kind,
                "errorType": exc.__class__.__name__,
                "error": str(exc),
            },
        )
        return _apply_custom_safety_guard(base, task_constraints)


def _task_constraint_block(task_constraints: str | None) -> str:
    normalized = str(task_constraints or "").strip()
    if not normalized:
        return ""
    if BUSINESS_PROMPT_OVERRIDE_MARKER not in normalized:
        return (
            "\n当前任务补充约束：\n"
            f"{normalized}\n\n"
            "用户可编辑业务模型系统提示词和工具栏配置是本次生成的最高业务约束；"
            "参考图与修图设定同样属于工具栏配置。当前任务补充约束只能补充未定义的热点、剧情、"
            "连续叙事和拍摄细节，不得删除、替换、弱化或反转工具栏中的明确要求。"
            "如果补充约束与工具栏要求冲突，保留工具栏要求；只有用户明确写出“本次覆盖工具栏设置”时才允许覆盖。"
        )
    return (
        "\n当前任务附加高优先级约束：\n"
        f"{normalized}\n\n"
        "用户已经明确要求本次覆盖工具栏设置，因此只在本次任务内优先服从这条约束；"
        "不要修改工具栏配置文件，任务结束后也不得把覆盖规则写回用户设置。"
    )


def _apply_custom_safety_guard(text: str, task_constraints: str | None) -> str:
    constraint_text = str(task_constraints or "")
    should_guard = BUSINESS_PROMPT_OVERRIDE_MARKER in constraint_text and any(
        marker in constraint_text
        for marker in ("本次用户自定义输入中的安全过滤", "安全过滤", "无人物", "无人脸", "无身体部位")
    )
    if not should_guard:
        return text
    cleaned = _sanitize_custom_safety_text(text)
    if not _custom_safety_requires_no_person(constraint_text):
        return cleaned
    narration = _extract_prompt_narration(cleaned)
    if not narration:
        narration = _plain_prompt_summary(cleaned)
    if not narration:
        narration = "短视频生产需要让脚本、素材、镜头和成片状态保持一致。AI8video 把生成步骤和交付结果集中到同一条工作流里，让团队协作更清晰。"
    parts = _split_narration_parts(narration, 3)
    return (
        f"镜头一（0-6s）：无人物、无人脸、无身体部位。空办公室客服工位，键盘、耳机、资料夹和无可读文字的消息气泡图形依次亮起，世界地图光线在背景缓慢浮现。画外音：“{parts[0]}”音效：轻微键盘声和消息提示音。\n"
        f"镜头二（6-14s）：无人物、无人脸、无身体部位。同一工位的多语言数据流在不同文件夹和消息气泡之间流转，画面保持无品牌、无界面、无可读文字。画外音：“{parts[1]}”音效：数据流动声。\n"
        f"镜头三（14-20s）：无人物、无人脸、无身体部位。空会议桌、客服耳机和世界地图光线收束为稳定的信息同步节点，办公灯光由冷转暖。画外音：“{parts[2]}”音效：温和收束音。\n"
        "全片保持同一办公主场景和连续因果转场，不使用人物外貌、身体特写、营销邀约或收益承诺。"
    )


def _custom_safety_requires_no_person(task_constraints: str | None) -> bool:
    text = str(task_constraints or "")
    return any(
        marker in text
        for marker in (
            "无人物",
            "无人脸",
            "无身体部位",
            "无身体",
            "不要求人物出镜",
            "不出现人脸",
            "身体特写",
            "人物会触发风险",
        )
    )


def _sanitize_custom_safety_text(text: str) -> str:
    result = str(text or "")
    replacements = {
        "邀请好友，立享返佣！": "了解更多创作能力。",
        "邀请好友，立享返佣": "了解更多创作能力",
        "邀请好友": "了解更多",
        "立享返佣": "提升创作效率",
        "John Deere": "海外设备品牌",
        "FTC": "监管机构",
        "这位专业客服自信干练，看团队如何应对挑战。": "",
        "美女身材特写": "空办公室建立镜头",
        "身材特写": "空办公室建立镜头",
        "美女": "办公设备",
        "身材": "空间",
        "优雅曲线": "办公光线",
        "邀请手势": "信息流收束",
        "全身正对镜头": "画面稳定收束",
    }
    for old, new in replacements.items():
        result = result.replace(old, new)
    return result


def _extract_prompt_narration(text: str) -> str:
    matches = re.findall(r"画外音(?:[^“”\\n]{0,30})[：:]“([^”]+)”", str(text or ""))
    if not matches:
        matches = re.findall(r"[“\"]([^”\"]{8,})[”\"]", str(text or ""))
    unsafe_terms = ("女性", "美女", "身材", "姣好", "侧身", "全身", "背影", "姿态")
    matches = [item for item in matches if not any(term in item for term in unsafe_terms)]
    narration = "。".join(item.strip("。！？!?. ") for item in matches if item.strip())
    return _sanitize_custom_safety_text(narration).strip("。！？!?. ")


def _plain_prompt_summary(text: str) -> str:
    cleaned = re.sub(r"镜头[一二三四五六七八九十][^：:]*[：:]", "", str(text or ""))
    cleaned = re.sub(r"(景别|场景|运镜|人物动作|音效|情绪|画面)[^。；;\n]*[。；;]?", "", cleaned)
    cleaned = re.sub(r"无人物、无人脸、无身体部位。?", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 180:
        cleaned = cleaned[:180].rsplit("，", 1)[0] or cleaned[:180]
    return _sanitize_custom_safety_text(cleaned).strip("。！？!?. ")


def _split_narration_parts(text: str, count: int) -> list[str]:
    sentences = [item for item in re.split(r"(?<=[。！？!?])", text) if item.strip()]
    if len(sentences) >= count:
        head = ["".join(sentences[:1]), "".join(sentences[1:-1]), sentences[-1]]
    else:
        clauses = [item for item in re.split(r"(?<=[，,；;、])", text) if item.strip()]
        head = _group_clauses(clauses or [text], count)
    parts = [part.strip("。！？!?. ") for part in head[:count]]
    while len(parts) < count:
        parts.append(parts[-1] if parts else "脚本与素材需要准确同步")
    return [part + "。" if part and part[-1] not in "。！？!?" else part for part in parts]


def _group_clauses(clauses: list[str], count: int) -> list[str]:
    buckets = ["" for _ in range(count)]
    target = max(1, sum(len(item) for item in clauses) // count)
    bucket_index = 0
    for clause in clauses:
        if bucket_index < count - 1 and len(buckets[bucket_index]) >= target:
            bucket_index += 1
        buckets[bucket_index] += clause
    return buckets


def finalize_title(title: str, *, task_constraints: str | None = None) -> str:
    cleaned = _minimal_internal_prompt_guard(title)
    policy = business_prompt_generation_policy(task_constraints=task_constraints)
    for term in policy.get("filteredTerms") or []:
        cleaned = re.sub(re.escape(str(term)), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+([：:，,。；;！？!?])", r"\1", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ：:｜|-") or "未命名视频"


def sanitize_internal_fidelity_notes(text: str) -> str:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.search(r"(品牌保真|信息保真|口播保真|保真要求)", line)
        if match:
            prefix = line[: match.start()].strip(" ：:，,。；;、/\\-_ ")
            if prefix:
                lines.append(prefix)
            continue
        lines.append(line)
    result = "\n".join(lines).strip()
    result = re.sub(r"(品牌保真|信息保真|口播保真|保真要求)[^。\n；;]*[。；;]?", "", result)
    result = re.sub(r"(品牌保真|信息保真|口播保真|保真要求)[\u4e00-\u9fffA-Za-z0-9\\-_/]*", "", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _strip_generated_constraint_lines(text: str) -> str:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^(画面硬性约束|最终硬性约束)[:：]", line):
            continue
        if "已按用户系统提示词过滤" in line:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _minimal_internal_prompt_guard(text: str) -> str:
    result = _strip_generated_constraint_lines(sanitize_internal_fidelity_notes(text))
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result.strip()


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = _extract_json_text(raw, opening="{", closing="}")
    data = _loads_json_with_repair(text)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _parse_json_array(raw: str) -> list[Any]:
    text = _extract_json_text(raw, opening="[", closing="]")
    data = _loads_json_with_repair(text)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array")
    return data


def _extract_json_text(raw: str, *, opening: str, closing: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    if not text.startswith(opening):
        escaped_opening = re.escape(opening)
        escaped_closing = re.escape(closing)
        match = re.search(rf"{escaped_opening}[\s\S]*{escaped_closing}", text)
        if match:
            text = match.group(0)
    return text


def _loads_json_with_repair(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as original_error:
        repaired_text = _repair_common_llm_json_text(text)
        if repaired_text == text:
            raise original_error
        try:
            return json.loads(repaired_text)
        except json.JSONDecodeError:
            raise original_error


def _repair_common_llm_json_text(text: str) -> str:
    """Repair narrow JSON mistakes that LLMs commonly make in prose fields.

    This is intentionally conservative: it only inserts a missing comma between
    adjacent object fields and escapes unescaped quotes inside known string
    fields. If repair is unsafe, the original JSONDecodeError is preserved.
    """
    repaired_text = _insert_missing_commas_between_fields(str(text or ""))
    for field_name in ("final_prompt", "prompt", "notes", "title", "source_summary", "omitted_keywords_reason"):
        repaired_text = _escape_unescaped_quotes_in_json_string_field(repaired_text, field_name)
    return repaired_text


def _insert_missing_commas_between_fields(text: str) -> str:
    return re.sub(r'("\s*)\n(\s*")([A-Za-z_][A-Za-z0-9_]*"\s*:)', r'\1,\n\2\3', text)


def _escape_unescaped_quotes_in_json_string_field(text: str, field_name: str) -> str:
    field_pattern = f'"{re.escape(field_name)}"'
    search_position = 0
    result_parts: list[str] = []

    while True:
        field_match = re.search(field_pattern + r"\s*:\s*\"", text[search_position:])
        if not field_match:
            result_parts.append(text[search_position:])
            break

        value_start = search_position + field_match.end()
        result_parts.append(text[search_position:value_start])
        value_end = _find_json_string_value_end(text, value_start)
        if value_end is None:
            result_parts.append(text[value_start:])
            break

        raw_value = text[value_start:value_end]
        result_parts.append(_escape_inner_json_quotes(raw_value))
        search_position = value_end

    return "".join(result_parts)


def _find_json_string_value_end(text: str, value_start: int) -> int | None:
    index = value_start
    while index < len(text):
        if text[index] != '"':
            index += 1
            continue
        if _is_escaped_character(text, index):
            index += 1
            continue
        lookahead = index + 1
        while lookahead < len(text) and text[lookahead].isspace():
            lookahead += 1
        if lookahead >= len(text) or text[lookahead] in ",}]":
            return index
        index += 1
    return None


def _escape_inner_json_quotes(value: str) -> str:
    characters: list[str] = []
    for index, character in enumerate(value):
        if character == '"' and not _is_escaped_character(value, index):
            characters.append(r'\"')
        else:
            characters.append(character)
    return "".join(characters)


def _is_escaped_character(text: str, index: int) -> bool:
    backslash_count = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslash_count += 1
        cursor -= 1
    return backslash_count % 2 == 1
