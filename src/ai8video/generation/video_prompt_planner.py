from __future__ import annotations

import json
from typing import Callable

from ai8video.generation.business_prompt import (
    business_prompt_block,
    sanitize_internal_fidelity_notes,
)
from ai8video.core.models import VideoPrompt
from ai8video.generation.prompt_trace import append_prompt_trace
from ai8video.generation.source_material import prepare_script_for_model
from ai8video.generation.video_prompt_mocks import mock_expand_seed_messages, mock_plan_video_prompts
from ai8video.generation.video_prompt_support import (
    build_json_array_repair_prompt,
    clean_video_title,
    coerce_seed_strings,
    coerce_text_list,
    format_keyword_guidance_block,
    format_task_constraint_block,
    format_timing_rule,
    normalize_keyword_guidance,
    parse_json_array,
    parse_json_object,
)

LLMCallable = Callable[[str], str]


def build_video_planning_prompt(
    script: str,
    video_count: int,
    style_hint: str | None = None,
    core_keywords: str | None = None,
    keyword_guidance: dict | None = None,
    task_constraints: str | None = None,
    final_duration_seconds: int | None = None,
) -> str:
    style = style_hint or "保持用户原剧本风格，并适配 AI8video 的可生成短视频流程"
    timing_rule = format_timing_rule(final_duration_seconds)
    keyword_rule = (
        f"本轮必须优先围绕这些核心主题 / 关键词规划：{core_keywords}。"
        if core_keywords
        else "如果用户没有单独指定核心主题，你必须先从原文中提炼核心主题，再为每条独立视频选择清晰角度。"
    )
    keyword_guidance_block = format_keyword_guidance_block(keyword_guidance)
    task_constraint_block = format_task_constraint_block(task_constraints)
    return f"""你是AI8video 的批量视频提示词规划器。

你的任务是服务 AI8video 的短视频生成流程。

{business_prompt_block()}
{task_constraint_block}

任务：基于用户给出的素材，规划 {video_count} 条彼此独立、可分别发布的短视频提示词。

批量规划原则：
1. 每条视频都必须自成一体，包含自己的开场钩子、核心表达和明确收束；不能依赖上一条或下一条才能看懂。
2. 不得把批量视频自动理解为连续故事或章节，不要设计跨视频的剧情推进、悬念承接或统一结局。
3. 同一主题要通过受众、痛点、场景、人物视角、开场方式、证据或行动引导等维度形成真实差异，不能只换同义词。
4. 用户素材已有编号、标题或多个脚本时，把它们视为候选信息源；优先覆盖不同来源，但不要把编号解释成连续剧顺序。
5. 当目标数量超过现成信息点时，可以围绕有依据的内容扩展新角度和新场景，但不能虚构用户未提供的品牌、事实、产品能力或营销承诺。
6. {keyword_rule}
7. 如果下方提供了“文本模型提取的关键词指导”，把它作为素材语义地图使用，让不同视频自然覆盖相关关键词和必保事实；某条视频不适合使用某个关键词时，在 omitted_keywords_reason 里说明原因。

文本模型提取的关键词指导：
{keyword_guidance_block}

全篇覆盖原则：
1. 在输出前先在内部完成一张“全篇覆盖地图”：通读用户剧本的开头、中段、后段和结尾，把可用素材按原文顺序归纳成不同来源段。
2. 每条视频必须对应一个明确且不同的来源段、脚本编号、标题或信息点；后半部分输出要优先使用尚未覆盖的中后段内容，不能反复改写前面的同一组素材。
3. 如果用户素材里已有脚本编号、标题、阶段说明或发布计划，优先用它们定位来源，但每条输出仍须是完整独立的视频。
4. 如果目标数量大于可直接使用的信息点数量，要说明每条扩展自哪个原文信息点，并通过新场景、新冲突或新角色视角做区分，不能把前几条换词后重复。
5. 每个 JSON 元素的 source_summary 必须写清该视频来自原文哪个脚本编号、标题、阶段或信息点，方便追踪素材覆盖情况。

要求：
1. 不要按段落、标点或编号机械切分；先理解素材主题、事实和表达目标，再规划独立视频。
2. 每条标题和提示词都要体现自己的独立角度，不要只叫“第几段”或“第几条”。
3. 每条提示词都应能独立提交给“提示词 + 可选参考图 -> 单条视频”的视频模型模板。
4. 每条视频必须先规划可直接口播/对白的中文台词；如果原素材有原句，优先保留有用原句，如果信息不足，再补齐真人能说出口且不虚构事实的口播。
5. 每条提示词必须包含“台词/口播：...”，并把台词嵌入画面执行说明里，不能只写场景和镜头。
6. 每条提示词都要写清主体、场景、动作、表情、情绪、身体状态、语气状态、镜头运动、氛围；口播/对白只写说话情绪和语气，不要添加用户原文没有要求的声线、性别或身份设定，让视频模型根据画面主体自行判断。
7. {timing_rule}
8. 先理解用户原文、风格要求和用户可编辑业务模型系统提示词里的视觉要求、文字要求、排版要求、镜头要求和禁用要求，并把这些要求落实进每条提示词；不要用固定词表机械判断，也不要为某个禁用项临时发明本地替换规则。
9. 如果用户要求画面呈现某类视觉表达，你要判断它是画面内容、口播内容还是被系统提示词限制的内容；可见视觉内容和口播内容不能混淆。
10. 如果系统提示词限制某类内容，必须理解限制的作用域，避免把相近概念、上下位概念、简称、翻译、结构标签或未被禁止的正文内容一起误删。
11. AI8video 只负责理解本轮用户素材并生成短视频方案，不得擅自补入默认行业、品牌、产品卖点或营销主张。
12. 品牌、专名、日期和核心信息必须服从“用户可编辑业务模型系统提示词”，不能从历史轮次或本地默认值里补回；是否保留、删除或改写，只能依据本轮用户输入、参考剧本和系统提示词。
13. 风格要求：{style}
14. 核心主题 / 关键词：{core_keywords or "由模型根据用户原文提炼"}
15. 每条都要在 preserved_keywords 写出实际保留或承接的关键词 / 事实；如果某些关键词没放入该视频，在 omitted_keywords_reason 写清是因为主题不匹配、系统提示词限制，还是为了避免堆砌。

只返回 JSON 数组，不要解释。数组元素格式：
{{"index":1,"title":"...","prompt":"...","source_summary":"...","preserved_keywords":["..."],"omitted_keywords_reason":"..."}}

用户剧本：
{script}
"""


def build_keyword_extraction_prompt(
    script: str,
    video_count: int,
    style_hint: str | None = None,
    core_keywords: str | None = None,
) -> str:
    style = style_hint or "保持用户原剧本风格，并适配 AI8video 的短视频生成流程"
    return f"""你是AI8video 的素材关键词理解模型。

你的任务是服务后续“批量视频规划”和“视频提示词生成”，只做文本理解，不生成视频提示词。

{business_prompt_block()}

请从 AI 文本理解角度通读用户剧本全文，提取“后续生成视频提示词时应尽可能保留或覆盖”的关键词、专名、日期、产品名、核心事实、反复出现的信息点和阶段性主题。

规则：
1. 这是独立的文本模型工序，不允许用本地词频、正则或固定词表替代你的判断。
2. 你要结合全文上下文、目标视频数量、用户显式核心主题、风格要求和用户可编辑业务模型系统提示词来判断哪些词 / 事实重要。
3. 关键词不是必须硬塞进每条视频；请给出“全局应尽可能覆盖”和“各视频建议覆盖”的结构，让后续规划模型按独立角度自然使用。
4. 如果某个高频词和系统提示词冲突，或更适合口播而不适合画面，请在 usage_policy 或 video_keyword_guidance 中说明。
5. 不要补入历史轮次、默认品牌词或本轮文本没有依据的内容。
6. 用户显式核心主题 / 关键词：{core_keywords or "（用户未单独指定，由你根据全文判断）"}
7. 目标视频数量：{video_count}
8. 风格要求：{style}

只返回严格 JSON 对象，不要解释。格式：
{{
  "global_keywords": ["全篇应尽可能覆盖的关键词或专名"],
  "must_preserve_facts": ["后续提示词应尽可能保留的核心事实"],
  "video_keyword_guidance": [
    {{"index": 1, "source_hint": "来自原文哪个脚本/阶段/信息点", "keywords": ["建议本集覆盖的关键词"], "facts": ["建议本集保留的事实"], "usage_note": "如何自然使用"}}
  ],
  "usage_policy": "整体使用原则，包括哪些内容适合口播、哪些内容受系统提示词限制、哪些内容不能硬塞"
}}

用户剧本：
{script}
"""


def extract_script_keywords_with_ai(
    script: str,
    video_count: int,
    style_hint: str | None = None,
    core_keywords: str | None = None,
    *,
    llm: LLMCallable | None = None,
    trace_session_id: str | None = None,
) -> dict | None:
    if llm is None:
        return None
    keyword_prompt = build_keyword_extraction_prompt(script, video_count, style_hint, core_keywords)
    append_prompt_trace(
        "keyword_model_input",
        session_id=trace_session_id,
        payload={
            "videoCount": video_count,
            "styleHint": style_hint,
            "coreKeywords": core_keywords,
            "prompt": keyword_prompt,
        },
    )
    try:
        raw = llm(keyword_prompt)
        append_prompt_trace(
            "keyword_model_output",
            session_id=trace_session_id,
            payload={"raw": raw},
        )
        return normalize_keyword_guidance(parse_json_object(raw))
    except Exception as exc:
        append_prompt_trace(
            "keyword_model_error",
            session_id=trace_session_id,
            payload={
                "errorType": exc.__class__.__name__,
                "error": str(exc),
            },
        )
        return None


def plan_video_prompts_with_ai(
    script: str,
    video_count: int,
    style_hint: str | None = None,
    core_keywords: str | None = None,
    task_constraints: str | None = None,
    final_duration_seconds: int | None = None,
    llm: LLMCallable | None = None,
    allow_mock: bool = False,
    trace_session_id: str | None = None,
) -> list[VideoPrompt]:
    if video_count < 1:
        raise ValueError("video_count must be >= 1")
    if llm is None:
        if allow_mock:
            return mock_plan_video_prompts(script, video_count, style_hint, core_keywords)
        raise RuntimeError("An LLM callable is required for intelligent script splitting")

    model_script = prepare_script_for_model(script, video_count)
    if model_script != str(script or "").strip():
        append_prompt_trace(
            "script_source_preprocessed",
            session_id=trace_session_id,
            payload={
                "videoCount": video_count,
                "originalChars": len(str(script or "")),
                "modelChars": len(model_script),
            },
        )

    keyword_guidance = extract_script_keywords_with_ai(
        model_script,
        video_count,
        style_hint,
        core_keywords,
        llm=llm,
        trace_session_id=trace_session_id,
    )
    planning_prompt = build_video_planning_prompt(
        model_script,
        video_count,
        style_hint,
        core_keywords,
        keyword_guidance,
        task_constraints=task_constraints,
        final_duration_seconds=final_duration_seconds,
    )
    append_prompt_trace(
        "video_planning_model_input",
        session_id=trace_session_id,
        payload={
            "videoCount": video_count,
            "styleHint": style_hint,
            "coreKeywords": core_keywords,
            "keywordGuidance": keyword_guidance,
            "taskConstraints": task_constraints,
            "finalDurationSeconds": final_duration_seconds,
            "prompt": planning_prompt,
        },
    )
    raw = llm(planning_prompt)
    append_prompt_trace(
        "video_planning_model_output",
        session_id=trace_session_id,
        payload={"raw": raw},
    )
    try:
        data = parse_json_array(raw)
    except Exception as exc:
        append_prompt_trace(
            "video_planning_model_json_parse_error",
            session_id=trace_session_id,
            payload={
                "errorType": exc.__class__.__name__,
                "error": str(exc),
                "raw": raw,
            },
        )
        repaired_raw = llm(build_json_array_repair_prompt(raw, video_count))
        append_prompt_trace(
            "video_planning_model_json_repair_output",
            session_id=trace_session_id,
            payload={"raw": repaired_raw},
        )
        data = parse_json_array(repaired_raw)
    videos: list[VideoPrompt] = []
    for idx, item in enumerate(data, 1):
        title = str(item.get("title") or f"视频 {idx}")
        prompt = str(item.get("prompt") or "").strip()
        title = clean_video_title(title, idx)
        prompt = sanitize_internal_fidelity_notes(prompt)
        videos.append(VideoPrompt(
            index=int(item.get("index") or idx),
            title=title,
            prompt=prompt,
            source_summary=str(item.get("source_summary") or "").strip(),
            keyword_guidance={
                "global": keyword_guidance or {},
                "preserved_keywords": coerce_text_list(item.get("preserved_keywords")),
                "omitted_keywords_reason": str(item.get("omitted_keywords_reason") or "").strip(),
            },
        ))
    if len(videos) != video_count:
        raise ValueError(f"LLM returned {len(videos)} videos, expected {video_count}")
    if any(not item.prompt for item in videos):
        raise ValueError("LLM returned empty prompt")
    return videos


def single_prompt_to_video(
    prompt: str,
    style_hint: str | None = None,
    core_keywords: str | None = None,
) -> list[VideoPrompt]:
    suffix = ""
    if style_hint:
        suffix = f"\n风格要求：{style_hint}。"
    if core_keywords:
        suffix += f"\n核心主题 / 关键词：{core_keywords}。"
    guardrails = (
        "\n请先理解用户原文、风格要求和用户可编辑业务模型系统提示词里的视觉要求、文字要求、排版要求、镜头要求和禁用要求，并完整落实。"
        "不要用本地固定词表替用户判断内容；可见视觉内容和人物台词/口播内容必须按用户意图与系统提示词作用域区分。"
    )
    final_prompt = sanitize_internal_fidelity_notes(prompt.strip() + suffix + guardrails)
    return [VideoPrompt(index=1, title="单条视频", prompt=final_prompt)]


def build_rewrite_prompt(
    video: VideoPrompt,
    rewrite_instruction: str,
    style_hint: str | None = None,
    core_keywords: str | None = None,
    task_constraints: str | None = None,
) -> str:
    style = style_hint or "延续当前短视频的主题、视觉与叙事风格"
    keywords = core_keywords or "沿用原提示词的核心主题，不要偏题"
    task_constraint_block = format_task_constraint_block(task_constraints)
    return f"""你是AI8video 的单条视频改写器。

你的任务是服务 AI8video 的短视频生成流程。

{business_prompt_block()}
{task_constraint_block}

任务：只改写一条现有视频提示词，让它更适合重新生成。

要求：
1. 必须保留这条视频的核心主题，不要把它改成其他视频的内容。
2. 必须吸收用户这次的修改意见，而不是简单拼接在原文后面。
3. 输出仍然要是可直接提交给“提示词 + 可选参考图 -> 单条视频”的完整提示词。
4. 必须保留或补齐可直接口播/对白的中文台词，并在提示词里明确写出“台词/口播：...”。
5. 要写清主体、场景、动作、表情、情绪、身体状态、语气状态、镜头运动和氛围；口播/对白只写说话情绪和语气，不要添加用户原文没有要求的声线、性别或身份设定，让视频模型根据画面主体自行判断。
6. 要包含两个时间段：0-5 秒、5-10 秒；每段都写镜头景别、场景描述、运镜动作、人物动作、台词/口播、音效建议。
7. 先理解原提示词、用户本次修改意见和用户可编辑业务模型系统提示词里的视觉要求、文字要求、排版要求、镜头要求和禁用要求，并落实到改写结果；不要用固定词表机械判断，也不要为某个禁用项临时发明本地替换规则。
8. 如果用户要求画面呈现某类视觉表达，你要判断它是画面内容、口播内容还是被系统提示词限制的内容；可见视觉内容和人物台词/口播内容不能混淆。
9. 如果系统提示词限制某类内容，必须理解限制的作用域，避免把相近概念、上下位概念、简称、翻译、结构标签或未被禁止的正文内容一起误删。
10. 不得从历史默认值补入任何行业、品牌、产品卖点或营销主张，只能依据本轮用户输入、当前提示词和系统提示词改写。
11. 品牌、专名、日期和核心信息必须服从“用户可编辑业务模型系统提示词”，不能从历史轮次或本地默认值里补回；是否保留、删除或改写，只能依据本轮用户输入、当前提示词和系统提示词。
12. 风格要求：{style}
13. 核心主题 / 关键词：{keywords}
14. 当前视频已有的来源摘要和关键词指导必须作为改写依据；尽可能保留其中的高价值关键词和事实，除非用户修改意见或系统提示词明确要求改写。

只返回 JSON 对象，不要解释。格式：
{{"title":"...","prompt":"...","source_summary":"...","preserved_keywords":["..."],"omitted_keywords_reason":"..."}}

当前视频标题：
{video.title}

当前来源摘要：
{video.source_summary or "（无）"}

当前关键词指导：
{json.dumps(video.keyword_guidance or {}, ensure_ascii=False, indent=2)}

当前提示词：
{video.prompt}

用户本次修改要求：
{rewrite_instruction}
"""


def build_batch_seed_expansion_prompt(
    seed_messages: list[str],
    target_count: int,
    style_hint: str | None = None,
    failure_reasons: list[str] | None = None,
) -> str:
    style = style_hint or "保持主题真实、场景明确，并适配 AI8video 的批量生成流程"
    reason_text = "、".join(item for item in (failure_reasons or []) if item) or "暂时没有失败原因"
    seed_block = "\n".join(f"{idx}. {item}" for idx, item in enumerate(seed_messages, 1))
    return f"""你是AI8video 的批量候选扩写器。

你的任务是服务 AI8video 的短视频生成流程。

{business_prompt_block()}

任务：基于现有候选内容，继续扩写出 {target_count} 条新的短视频候选提示词。

要求：
1. 这些候选是给批量生产调度器继续补量用的，不是解释说明，也不是总结。
2. 每条都必须能独立提交给“提示词 + 可选参考图 -> 单条视频”的视频模型模板。
3. 不要和已有候选重复，不要只是换几个近义词，要补充新的切入角度、冲突点、场景或表达方式。
4. 每条都要围绕对应候选的原始主题扩写，不得默认绑定特定行业、品牌、产品卖点或营销话术。
5. 每条都要写清主体、场景、动作、情绪、镜头运动和氛围，避免空泛口号。
6. 先理解风格要求、候选内容和用户可编辑业务模型系统提示词里的视觉要求、文字要求、排版要求、镜头要求和禁用要求；不要用固定词表机械判断，也不要为某个禁用项临时发明本地替换规则。
7. 风格要求：{style}
8. 如果历史失败原因里提到具体问题，新的候选要主动规避：{reason_text}

只返回 JSON 数组，不要解释。格式：
["候选提示词1","候选提示词2"]

现有候选：
{seed_block}
"""


def rewrite_video_with_ai(
    video: VideoPrompt,
    rewrite_instruction: str,
    style_hint: str | None = None,
    core_keywords: str | None = None,
    task_constraints: str | None = None,
    llm: LLMCallable | None = None,
    allow_mock: bool = False,
    trace_session_id: str | None = None,
) -> VideoPrompt:
    if llm is None:
        if allow_mock:
            return VideoPrompt(
                index=video.index,
                title=video.title,
                prompt=f"{video.prompt}\n补充重做要求：{rewrite_instruction}",
                source_summary=video.source_summary,
                keyword_guidance=video.keyword_guidance,
            )
        raise RuntimeError("An LLM callable is required for intelligent video rewriting")

    rewrite_prompt = build_rewrite_prompt(
        video,
        rewrite_instruction,
        style_hint,
        core_keywords,
        task_constraints=task_constraints,
    )
    append_prompt_trace(
        "rewrite_model_input",
        session_id=trace_session_id,
        payload={
            "videoIndex": video.index,
            "rewriteInstruction": rewrite_instruction,
            "styleHint": style_hint,
            "coreKeywords": core_keywords,
            "taskConstraints": task_constraints,
            "prompt": rewrite_prompt,
        },
    )
    raw = llm(rewrite_prompt)
    append_prompt_trace(
        "rewrite_model_output",
        session_id=trace_session_id,
        payload={"videoIndex": video.index, "raw": raw},
    )
    data = parse_json_object(raw)
    prompt = sanitize_internal_fidelity_notes(str(data.get("prompt") or "").strip())
    if not prompt:
        raise ValueError("LLM returned empty rewritten prompt")
    return VideoPrompt(
        index=video.index,
        title=clean_video_title(str(data.get("title") or video.title).strip() or video.title, video.index),
        prompt=prompt,
        source_summary=str(data.get("source_summary") or video.source_summary).strip(),
        keyword_guidance={
            **(video.keyword_guidance or {}),
            "preserved_keywords": coerce_text_list(data.get("preserved_keywords")),
            "omitted_keywords_reason": str(data.get("omitted_keywords_reason") or "").strip(),
        },
    )


def expand_batch_seed_messages_with_ai(
    seed_messages: list[str],
    target_count: int,
    style_hint: str | None = None,
    failure_reasons: list[str] | None = None,
    llm: LLMCallable | None = None,
    allow_mock: bool = False,
) -> list[str]:
    cleaned_seeds = [item.strip() for item in seed_messages if item and item.strip()]
    if target_count < 1:
        raise ValueError("target_count must be >= 1")
    if not cleaned_seeds:
        raise ValueError("seed_messages is required for batch expansion")
    if llm is None:
        if allow_mock:
            return mock_expand_seed_messages(cleaned_seeds, target_count, style_hint, failure_reasons)
        raise RuntimeError("An LLM callable is required for intelligent batch expansion")

    raw = llm(build_batch_seed_expansion_prompt(cleaned_seeds, target_count, style_hint, failure_reasons))
    data = parse_json_array(raw)
    expanded = coerce_seed_strings(data)
    if len(expanded) < target_count:
        raise ValueError(f"LLM returned {len(expanded)} expanded seeds, expected at least {target_count}")
    unique: list[str] = []
    seen: set[str] = set(cleaned_seeds)
    for item in expanded:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
        if len(unique) >= target_count:
            break
    if len(unique) < target_count:
        raise ValueError(f"LLM returned too many duplicate expanded seeds, only {len(unique)} usable")
    return unique
