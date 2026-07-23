from __future__ import annotations

import re

from ai8video.core.models import VideoPrompt


def mock_plan_video_prompts(
    script: str,
    video_count: int,
    style_hint: str | None = None,
    core_keywords: str | None = None,
) -> list[VideoPrompt]:
    compact = re.sub(r"\s+", " ", script).strip()
    sample = compact[:120] or "用户素材"
    style = style_hint or "商务真实"
    keywords = core_keywords or "用户原文中的核心主题与关键信息"
    return [
        VideoPrompt(
            index=index,
            title=f"视频 {index}：AI8video 生成方案",
            source_summary=sample,
            prompt=(
                f"第 {index} 条视频，{style}风格，围绕{keywords}形成独立完整表达。"
                "画面由与主题匹配的主体在明确场景中自然表达，动作与情绪连续，"
                "镜头缓慢推进，氛围真实克制。视觉、文字、排版和镜头要求以用户原文、风格要求和系统提示词为准。"
                f"原素材依据：{sample}"
            ),
        )
        for index in range(1, video_count + 1)
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
    for index in range(target_count * 4):
        base = compact_seeds[index % len(compact_seeds)]
        scene = scene_bank[index % len(scene_bank)]
        angle = angle_bank[index % len(angle_bank)]
        candidate = (
            f"{base}。补量版本 {index + 1}：{style}风格，老板在{scene}里继续讲{angle}，"
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
