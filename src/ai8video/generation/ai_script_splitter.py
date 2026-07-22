from __future__ import annotations

import json
import re
from typing import Callable

from ai8video.generation.business_prompt import (
    business_prompt_block,
    sanitize_internal_fidelity_notes,
)
from ai8video.core.models import EpisodePrompt
from ai8video.generation.prompt_trace import append_prompt_trace

LLMCallable = Callable[[str], str]
MAX_SCRIPT_BLOCKS_FOR_DIRECT_LLM = 12
SCRIPT_CONTEXT_MARGIN_BLOCKS = 2


def prepare_script_for_model(script: str, episode_count: int) -> str:
    """Reduce oversized structured source material before LLM planning.

    This is not a topic-specific shortcut. If the user provides a long library of
    numbered scripts, sending the whole library through every text-model step is
    fragile. We keep representative contiguous blocks and preserve source labels
    so the model can still plan with traceable source material.
    """
    raw = str(script or "").strip()
    if not raw:
        return raw
    blocks = _extract_numbered_script_blocks(raw)
    if len(blocks) <= MAX_SCRIPT_BLOCKS_FOR_DIRECT_LLM:
        return raw
    selected = _select_representative_blocks(blocks, episode_count)
    if len(selected) >= len(blocks):
        return raw
    header = _script_preface_before_first_block(raw, blocks[0]["start"])
    omitted = len(blocks) - len(selected)
    lines = []
    if header:
        lines.append(header)
    lines.extend([
        f"【长素材预处理】原文包含 {len(blocks)} 个编号脚本块。",
        f"本次目标生成 {episode_count} 条，已按原文顺序选取 {len(selected)} 个代表性脚本块，省略 {omitted} 个未选块。",
        "选材原则：覆盖开头、中段、后段和结尾；保留原脚本编号、标题、正文和金句，供模型继续做语义规划，不做本地改写。",
        "",
    ])
    for block in selected:
        lines.append(str(block["text"]).strip())
        lines.append("")
    return "\n".join(lines).strip()


def _extract_numbered_script_blocks(script: str) -> list[dict[str, object]]:
    pattern = re.compile(r"(?m)^(?P<label>\s*脚本\s*(?P<number>\d{1,4})(?:[^\n]*)?)")
    matches = list(pattern.finditer(script))
    blocks: list[dict[str, object]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(script)
        text = script[start:end].strip()
        if not text:
            continue
        blocks.append({
            "number": int(match.group("number")),
            "start": start,
            "text": text,
        })
    return blocks


def _select_representative_blocks(blocks: list[dict[str, object]], episode_count: int) -> list[dict[str, object]]:
    total = len(blocks)
    if total <= MAX_SCRIPT_BLOCKS_FOR_DIRECT_LLM:
        return blocks
    target = min(total, max(episode_count + SCRIPT_CONTEXT_MARGIN_BLOCKS, MAX_SCRIPT_BLOCKS_FOR_DIRECT_LLM))
    if target >= total:
        return blocks
    selected_indexes = {0, total - 1}
    if target > 2:
        step = (total - 1) / (target - 1)
        for idx in range(target):
            selected_indexes.add(round(idx * step))
    while len(selected_indexes) < target:
        candidate = len(selected_indexes)
        selected_indexes.add(min(total - 1, candidate))
    return [blocks[index] for index in sorted(selected_indexes)[:target]]


def _script_preface_before_first_block(script: str, first_block_start: int) -> str:
    preface = script[:max(0, first_block_start)].strip()
    if len(preface) <= 2000:
        return preface
    return preface[:2000].rstrip() + "\n（前置信息过长，已截取前 2000 字。）"


def build_split_prompt(
    script: str,
    episode_count: int,
    style_hint: str | None = None,
    core_keywords: str | None = None,
    keyword_guidance: dict | None = None,
    task_constraints: str | None = None,
    final_duration_seconds: int | None = None,
) -> str:
    style = style_hint or "保持用户原剧本风格，并适配 AI8video 的可生成短视频流程"
    timing_rule = _format_timing_rule(final_duration_seconds)
    keyword_rule = (
        f"本轮必须优先围绕这些核心主题 / 关键词规划：{core_keywords}。"
        if core_keywords
        else "如果用户没有单独指定核心主题，你必须先从原文中提炼核心主题，再按目标集数分配。"
    )
    keyword_guidance_block = _format_keyword_guidance_block(keyword_guidance)
    task_constraint_block = _format_task_constraint_block(task_constraints)
    return f"""你是AI8video 的剧本拆分器。

你的任务是服务 AI8video 的短视频生成流程。

{business_prompt_block()}
{task_constraint_block}

任务：把用户给出的剧本智能拆成 {episode_count} 条独立短视频提示词。

集数规划原则：
1. 先根据目标集数 {episode_count} 规划整组短视频的叙事节奏，再输出每集内容。
2. 如果是 2 集，应形成“强痛点/冲突开场 -> 解决方案/结果收束”的两段结构。
3. 如果是 3 集，应形成“痛点引入 -> 能力展开 -> 结果转化”的三段结构。
4. 如果是 4 集，应形成“痛点引入 -> 第一卖点 -> 第二卖点/升级问题 -> 结果收束”的四段结构。
5. 如果超过 4 集，应先把原剧本的核心信息点按目标集数分配，保证每集有独立主题、连续推进和明确结尾。
6. 可以为了适配集数做合并、重排和补足转场，但不能丢掉原剧本的核心卖点、人物关系、场景信息和情绪递进。
7. {keyword_rule}
8. 如果下方提供了“文本模型提取的关键词指导”，它是上一道 AI 文本工序基于全文给出的理解结果；你必须把它当作全文语义地图使用，尽可能让每集自然覆盖相关高频关键词、高价值关键词和必保事实。只有当用户可编辑业务模型系统提示词或本集叙事功能明确不适合时，才可以不使用某个关键词，并在 omitted_keywords_reason 里说明。

文本模型提取的关键词指导：
{keyword_guidance_block}

全篇覆盖原则：
1. 在输出前先在内部完成一张“全篇覆盖地图”：通读用户剧本的开头、中段、后段和结尾，把可用素材按原文顺序归纳成不同来源段。
2. 每一集必须对应一个明确且不同的来源段、脚本编号、标题或信息点；后半部分集数要优先使用尚未覆盖的中后段内容，不能回到前几集的同一组素材上反复改写。
3. 如果用户剧本里已经有脚本编号、标题、阶段说明、发布节奏或系列划分，必须优先按这些原有结构分配，不要只抽取最前面的几条。
4. 如果目标集数大于可直接使用的信息点数量，也要先说明每集扩展自哪个原文信息点，并通过新场景、新冲突、新角色视角做区分，不能把 1-5 集换词后重复成 6-10 集。
5. 每个 JSON 元素的 source_summary 必须写清这一集来自原文哪个脚本编号、标题、阶段或信息点，方便后续追踪是否遗漏后半段内容。

要求：
1. 不要按段落、标点、编号做机械切分，必须理解剧情起承转合。
2. 每集标题和提示词都要体现这一集在整组规划中的功能，不要只叫“第几段”。
3. 每条提示词都应能独立提交给“提示词 + 可选参考图 -> 单条视频”的视频模型模板。
4. 每集必须先规划可直接口播/对白的中文台词，台词要承接这一集的叙事功能；如果原剧本有原句，优先保留原句和原顺序，如果原句不足，再补齐真人能说出口的口播。
5. 每条提示词必须包含“台词/口播：...”，并把台词嵌入画面执行说明里，不能只写场景和镜头。
6. 每条提示词都要写清主体、场景、动作、表情、情绪、身体状态、语气状态、镜头运动、氛围；口播/对白只写说话情绪和语气，不要添加用户原文没有要求的声线、性别或身份设定，让视频模型根据画面主体自行判断。
7. {timing_rule}
8. 先理解用户原文、风格要求和用户可编辑业务模型系统提示词里的视觉要求、文字要求、排版要求、镜头要求和禁用要求，并把这些要求落实进每一集提示词；不要用固定词表机械判断，也不要为某个禁用项临时发明本地替换规则。
9. 如果用户要求画面呈现某类视觉表达，你要判断它是画面内容、口播内容还是被系统提示词限制的内容；可见视觉内容和口播内容不能混淆。
10. 如果系统提示词限制某类内容，必须理解限制的作用域，避免把相近概念、上下位概念、简称、翻译、结构标签或未被禁止的正文内容一起误删。
11. AI8video 只负责理解本轮用户素材并生成短视频方案，不得擅自补入默认行业、品牌、产品卖点或营销主张。
12. 品牌、专名、日期和核心信息必须服从“用户可编辑业务模型系统提示词”，不能从历史轮次或本地默认值里补回；是否保留、删除或改写，只能依据本轮用户输入、参考剧本和系统提示词。
13. 风格要求：{style}
14. 核心主题 / 关键词：{core_keywords or "由模型根据用户原文提炼"}
15. 每集都要在 preserved_keywords 写出本集实际保留或承接的关键词 / 事实；如果某些关键词没放入本集，在 omitted_keywords_reason 写清是因为本集主题不匹配、系统提示词限制，还是为了避免堆砌。

只返回 JSON 数组，不要解释。数组元素格式：
{{"index":1,"title":"...","prompt":"...","source_summary":"...","preserved_keywords":["..."],"omitted_keywords_reason":"..."}}

用户剧本：
{script}
"""


def build_keyword_extraction_prompt(
    script: str,
    episode_count: int,
    style_hint: str | None = None,
    core_keywords: str | None = None,
) -> str:
    style = style_hint or "保持用户原剧本风格，并适配 AI8video 的短视频生成流程"
    return f"""你是AI8video 的剧本关键词理解模型。

你的任务是服务后续“拆集”和“视频提示词生成”，只做文本理解，不生成视频提示词。

{business_prompt_block()}

请从 AI 文本理解角度通读用户剧本全文，提取“后续生成视频提示词时应尽可能保留或覆盖”的关键词、专名、日期、产品名、核心事实、反复出现的信息点和阶段性主题。

规则：
1. 这是独立的文本模型工序，不允许用本地词频、正则或固定词表替代你的判断。
2. 你要结合全文上下文、目标集数、用户显式核心主题、风格要求和用户可编辑业务模型系统提示词来判断哪些词 / 事实重要。
3. 关键词不是必须硬塞进每一集；请给出“全局应尽可能覆盖”和“分集建议覆盖”的结构，让后续拆集模型按叙事功能自然使用。
4. 如果某个高频词和系统提示词冲突，或更适合口播而不适合画面，请在 usage_policy 或 episode_keyword_guidance 中说明。
5. 不要补入历史轮次、默认品牌词或本轮文本没有依据的内容。
6. 用户显式核心主题 / 关键词：{core_keywords or "（用户未单独指定，由你根据全文判断）"}
7. 目标集数：{episode_count}
8. 风格要求：{style}

只返回严格 JSON 对象，不要解释。格式：
{{
  "global_keywords": ["全篇应尽可能覆盖的关键词或专名"],
  "must_preserve_facts": ["后续提示词应尽可能保留的核心事实"],
  "episode_keyword_guidance": [
    {{"index": 1, "source_hint": "来自原文哪个脚本/阶段/信息点", "keywords": ["建议本集覆盖的关键词"], "facts": ["建议本集保留的事实"], "usage_note": "如何自然使用"}}
  ],
  "usage_policy": "整体使用原则，包括哪些内容适合口播、哪些内容受系统提示词限制、哪些内容不能硬塞"
}}

用户剧本：
{script}
"""


def extract_script_keywords_with_ai(
    script: str,
    episode_count: int,
    style_hint: str | None = None,
    core_keywords: str | None = None,
    *,
    llm: LLMCallable | None = None,
    trace_session_id: str | None = None,
) -> dict | None:
    if llm is None:
        return None
    keyword_prompt = build_keyword_extraction_prompt(script, episode_count, style_hint, core_keywords)
    append_prompt_trace(
        "keyword_model_input",
        session_id=trace_session_id,
        payload={
            "episodeCount": episode_count,
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
        return _normalize_keyword_guidance(_parse_json_object(raw))
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


def split_script_with_ai(
    script: str,
    episode_count: int,
    style_hint: str | None = None,
    core_keywords: str | None = None,
    task_constraints: str | None = None,
    final_duration_seconds: int | None = None,
    llm: LLMCallable | None = None,
    allow_mock: bool = False,
    trace_session_id: str | None = None,
) -> list[EpisodePrompt]:
    if episode_count < 1:
        raise ValueError("episode_count must be >= 1")
    if llm is None:
        if allow_mock:
            return mock_split_script(script, episode_count, style_hint, core_keywords)
        raise RuntimeError("An LLM callable is required for intelligent script splitting")

    model_script = prepare_script_for_model(script, episode_count)
    if model_script != str(script or "").strip():
        append_prompt_trace(
            "script_source_preprocessed",
            session_id=trace_session_id,
            payload={
                "episodeCount": episode_count,
                "originalChars": len(str(script or "")),
                "modelChars": len(model_script),
            },
        )

    keyword_guidance = extract_script_keywords_with_ai(
        model_script,
        episode_count,
        style_hint,
        core_keywords,
        llm=llm,
        trace_session_id=trace_session_id,
    )
    split_prompt = build_split_prompt(
        model_script,
        episode_count,
        style_hint,
        core_keywords,
        keyword_guidance,
        task_constraints=task_constraints,
        final_duration_seconds=final_duration_seconds,
    )
    append_prompt_trace(
        "split_model_input",
        session_id=trace_session_id,
        payload={
            "episodeCount": episode_count,
            "styleHint": style_hint,
            "coreKeywords": core_keywords,
            "keywordGuidance": keyword_guidance,
            "taskConstraints": task_constraints,
            "finalDurationSeconds": final_duration_seconds,
            "prompt": split_prompt,
        },
    )
    raw = llm(split_prompt)
    append_prompt_trace(
        "split_model_output",
        session_id=trace_session_id,
        payload={"raw": raw},
    )
    try:
        data = _parse_json_array(raw)
    except Exception as exc:
        append_prompt_trace(
            "split_model_json_parse_error",
            session_id=trace_session_id,
            payload={
                "errorType": exc.__class__.__name__,
                "error": str(exc),
                "raw": raw,
            },
        )
        repaired_raw = llm(_build_json_array_repair_prompt(raw, episode_count))
        append_prompt_trace(
            "split_model_json_repair_output",
            session_id=trace_session_id,
            payload={"raw": repaired_raw},
        )
        data = _parse_json_array(repaired_raw)
    episodes: list[EpisodePrompt] = []
    for idx, item in enumerate(data, 1):
        title = str(item.get("title") or f"第 {idx} 集")
        prompt = str(item.get("prompt") or "").strip()
        title = _clean_episode_title(title, idx)
        prompt = sanitize_internal_fidelity_notes(prompt)
        episodes.append(EpisodePrompt(
            index=int(item.get("index") or idx),
            title=title,
            prompt=prompt,
            source_summary=str(item.get("source_summary") or "").strip(),
            keyword_guidance={
                "global": keyword_guidance or {},
                "preserved_keywords": _coerce_text_list(item.get("preserved_keywords")),
                "omitted_keywords_reason": str(item.get("omitted_keywords_reason") or "").strip(),
            },
        ))
    if len(episodes) != episode_count:
        raise ValueError(f"LLM returned {len(episodes)} episodes, expected {episode_count}")
    if any(not item.prompt for item in episodes):
        raise ValueError("LLM returned empty prompt")
    return episodes


def single_prompt_to_episode(
    prompt: str,
    style_hint: str | None = None,
    core_keywords: str | None = None,
) -> list[EpisodePrompt]:
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
    return [EpisodePrompt(index=1, title="单条视频", prompt=final_prompt)]


def build_rewrite_prompt(
    episode: EpisodePrompt,
    rewrite_instruction: str,
    style_hint: str | None = None,
    core_keywords: str | None = None,
    task_constraints: str | None = None,
) -> str:
    style = style_hint or "延续当前短视频的主题、视觉与叙事风格"
    keywords = core_keywords or "沿用原提示词的核心主题，不要偏题"
    task_constraint_block = _format_task_constraint_block(task_constraints)
    return f"""你是AI8video 的单集改写器。

你的任务是服务 AI8video 的短视频生成流程。

{business_prompt_block()}
{task_constraint_block}

任务：只改写一集现有视频提示词，让它更适合重新生成。

要求：
1. 必须保留这一集的核心主题，不要把它改成别的集数。
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
14. 当前这一集已有的来源摘要和关键词指导必须作为改写依据；尽可能保留其中的高价值关键词和事实，除非用户修改意见或系统提示词明确要求改写。

只返回 JSON 对象，不要解释。格式：
{{"title":"...","prompt":"...","source_summary":"...","preserved_keywords":["..."],"omitted_keywords_reason":"..."}}

当前集数标题：
{episode.title}

当前来源摘要：
{episode.source_summary or "（无）"}

当前关键词指导：
{json.dumps(episode.keyword_guidance or {}, ensure_ascii=False, indent=2)}

当前提示词：
{episode.prompt}

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


def rewrite_episode_with_ai(
    episode: EpisodePrompt,
    rewrite_instruction: str,
    style_hint: str | None = None,
    core_keywords: str | None = None,
    task_constraints: str | None = None,
    llm: LLMCallable | None = None,
    allow_mock: bool = False,
    trace_session_id: str | None = None,
) -> EpisodePrompt:
    if llm is None:
        if allow_mock:
            return EpisodePrompt(
                index=episode.index,
                title=episode.title,
                prompt=f"{episode.prompt}\n补充重做要求：{rewrite_instruction}",
                source_summary=episode.source_summary,
                keyword_guidance=episode.keyword_guidance,
            )
        raise RuntimeError("An LLM callable is required for intelligent episode rewriting")

    rewrite_prompt = build_rewrite_prompt(
        episode,
        rewrite_instruction,
        style_hint,
        core_keywords,
        task_constraints=task_constraints,
    )
    append_prompt_trace(
        "rewrite_model_input",
        session_id=trace_session_id,
        payload={
            "episodeIndex": episode.index,
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
        payload={"episodeIndex": episode.index, "raw": raw},
    )
    data = _parse_json_object(raw)
    prompt = sanitize_internal_fidelity_notes(str(data.get("prompt") or "").strip())
    if not prompt:
        raise ValueError("LLM returned empty rewritten prompt")
    return EpisodePrompt(
        index=episode.index,
        title=_clean_episode_title(str(data.get("title") or episode.title).strip() or episode.title, episode.index),
        prompt=prompt,
        source_summary=str(data.get("source_summary") or episode.source_summary).strip(),
        keyword_guidance={
            **(episode.keyword_guidance or {}),
            "preserved_keywords": _coerce_text_list(data.get("preserved_keywords")),
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
    data = _parse_json_array(raw)
    expanded = _coerce_seed_strings(data)
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


def mock_split_script(
    script: str,
    episode_count: int,
    style_hint: str | None = None,
    core_keywords: str | None = None,
) -> list[EpisodePrompt]:
    """Dry-run splitter for local wiring tests.

    It is deliberately labeled mock so production callers do not mistake it for
    the required AI-based splitter.
    """
    compact = re.sub(r"\s+", " ", script).strip()
    sample = compact[:120] or "用户剧本"
    style = style_hint or "商务真实"
    keywords = core_keywords or "用户原文中的核心主题与关键信息"
    return [
        EpisodePrompt(
            index=i,
            title=f"第 {i} 集：AI8video 生成片段",
            source_summary=sample,
            prompt=(
                f"第 {i} 集，{style}风格，围绕{keywords}展开。"
                f"画面由与主题匹配的主体在明确场景中自然表达，动作与情绪连续，"
                f"镜头缓慢推进，氛围真实克制。视觉、文字、排版和镜头要求以用户原文、风格要求和系统提示词为准。"
                f"原剧本依据：{sample}"
            ),
        )
        for i in range(1, episode_count + 1)
    ]


def mock_expand_seed_messages(
    seed_messages: list[str],
    target_count: int,
    style_hint: str | None = None,
    failure_reasons: list[str] | None = None,
) -> list[str]:
    compact_seeds = [re.sub(r"\s+", " ", item).strip("。！？!? ") for item in seed_messages if item.strip()]
    if not compact_seeds:
        compact_seeds = ["创作者在会议里讲素材交付压力"]
    style = style_hint or "商务真实"
    avoid = "、".join(item for item in (failure_reasons or []) if item) or "空泛口号和画面禁项"
    scene_bank = ["会议室", "办公室", "创作团队复盘会", "拍摄准备现场", "下班后的复盘时刻", "负责人向团队布置任务"]
    angle_bank = [
        "素材散落导致交付节奏被打断",
        "脚本信息不完整，拍摄现场反复返工",
        "批量任务缺少统一进度，成片容易遗漏",
        "参考图与生成结果没有形成清晰对应关系",
        "团队产出很多，却没有沉淀可复用模板",
        "同一镜头被多人重复处理，协作效率很低",
    ]
    expanded: list[str] = []
    seen = set(seed_messages)
    for idx in range(target_count * 4):
        base = compact_seeds[idx % len(compact_seeds)]
        scene = scene_bank[idx % len(scene_bank)]
        angle = angle_bank[idx % len(angle_bank)]
        candidate = (
            f"{base}。补量版本 {idx + 1}：{style}风格，老板在{scene}里继续讲{angle}，"
            f"人物克制但有压迫感，动作自然，镜头缓慢推进，重点规避{avoid}。"
            "视觉、文字、排版和镜头要求以用户原文、风格要求和系统提示词为准。"
        )
        if candidate in seen:
            continue
        seen.add(candidate)
        expanded.append(candidate)
        if len(expanded) >= target_count:
            break
    if len(expanded) < target_count:
        raise ValueError(f"mock expansion only produced {len(expanded)} seeds, expected {target_count}")
    return expanded


def _parse_json_array(raw: str) -> list[object]:
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


def _build_json_array_repair_prompt(raw: str, episode_count: int) -> str:
    return f"""你是 JSON 格式修复器。

任务：把下面这段模型输出修成严格合法 JSON 数组。

硬性要求：
1. 只能修复 JSON 语法，例如补逗号、转义字符串内部引号、去掉多余说明。
2. 不要改写标题、prompt、台词、source_summary、preserved_keywords 或 omitted_keywords_reason 的内容。
3. 输出必须是 JSON 数组，数组长度必须等于 {episode_count}。
4. 不要输出解释、Markdown 或代码块。

待修复内容：
{raw}
"""


def _coerce_seed_strings(data: list[object]) -> list[str]:
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


def _coerce_text_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            output.append(text)
    return output


def _format_timing_rule(final_duration_seconds: int | None) -> str:
    duration = _clean_positive_int(final_duration_seconds)
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


def _clean_positive_int(value: int | str | None) -> int | None:
    try:
        parsed = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_keyword_guidance(data: dict) -> dict:
    return {
        "global_keywords": _coerce_text_list(data.get("global_keywords")),
        "must_preserve_facts": _coerce_text_list(data.get("must_preserve_facts")),
        "episode_keyword_guidance": _normalize_episode_keyword_guidance(data.get("episode_keyword_guidance")),
        "usage_policy": str(data.get("usage_policy") or data.get("notes") or "").strip(),
    }


def _normalize_episode_keyword_guidance(value: object) -> list[dict]:
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
        output.append(
            {
                "index": index,
                "source_hint": str(item.get("source_hint") or item.get("source") or "").strip(),
                "keywords": _coerce_text_list(item.get("keywords")),
                "facts": _coerce_text_list(item.get("facts")),
                "usage_note": str(item.get("usage_note") or item.get("note") or "").strip(),
            }
        )
    return output


def _format_keyword_guidance_block(keyword_guidance: dict | None) -> str:
    if not keyword_guidance:
        return "（无独立关键词模型结果；拆集模型需自行基于全文提炼，但仍不得使用本地硬编码内容。）"
    return json.dumps(keyword_guidance, ensure_ascii=False, indent=2)


def _format_task_constraint_block(task_constraints: str | None) -> str:
    normalized = str(task_constraints or "").strip()
    if not normalized:
        return ""
    return (
        "\n当前任务附加高优先级约束：\n"
        f"{normalized}\n\n"
        "执行要求：这条约束在本次任务所有分集与后续重做中持续生效；"
        "如果原素材里的默认办公室、会议室、桌面、服装或姿态设定与它冲突，"
        "优先保留原文主题、卖点和情绪，再把视觉场景改写到这条约束上，"
        "直到用户明确修改或删除为止。"
    )


def _parse_json_object(raw: str) -> dict:
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


def _clean_episode_title(title: str, index: int) -> str:
    lines = []
    for raw_line in sanitize_internal_fidelity_notes(title).splitlines():
        line = raw_line.strip(" ：:，,。")
        if not line:
            continue
        if re.match(r"^(品牌保真|信息保真|提示词约束|保真要求)", line):
            continue
        lines.append(line)
    cleaned = " ".join(lines).strip()
    if len(cleaned) > 40:
        cleaned = re.split(r"[；;。]", cleaned, 1)[0].strip() or cleaned[:40].rstrip()
    return cleaned or f"第 {index} 集"
