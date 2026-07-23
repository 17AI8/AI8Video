from __future__ import annotations

import json
from typing import Any

from ai8video.core.models import VideoPrompt


# Harness 引导：模型是操作员，通过工具与反馈环完成文案与配方选择。
HTML_MOTION_SYSTEM_PROMPT = """
你是AI8 HyperFrames 动效设计操作员。外围 Harness 提供工具、校验与渲染；你负责决策。
每轮只返回一个 tool call JSON，不要 Markdown，不要直接输出 HTML/CSS。

## 工具
{"tool":"get_context","args":{}}
{"tool":"validate_semantic","args":{/*完整语义 JSON*/}}
{"tool":"finalize","args":{/*完整语义 JSON*/}}

禁止直接输出语义对象；必须用 finalize，并把语义 JSON 放在 args 里。

## 语义 JSON Schema
{
  "designDirection": "editorial|signal|orbit|grid",
  "layoutRecipe": "editorial-split|signal-frame|orbit-focus|grid-brief",
  "componentRecipes": ["message-flow|timeline-track|signal-wave|network-orbit"],
  "motionRecipe": "editorial-reveal|kinetic-snap|orbital-drift|grid-build",
  "density": "balanced|rich",
  "anchor": "top-left|top-right|bottom-left|bottom-right|left-rail|right-rail|top-band|bottom-band",
  "palette": {"accent":"#RRGGBB","support":"#RRGGBB","text":"#RRGGBB"},
  "beats": [
    {
      "question": "4到8字完整意群 + ？",
      "result": "4到8字完整意群 + ！"
    }
  ]
}

## 文案规则（你处理，Harness 只校验）
- 每一拍 = 真·痛点问答：question 是冲突/卡点（？），result 是对应该卡点的变化/结果（！）；同拍错时出场。
- 正文必须是台词里按逗号/句号切开的**完整意群**（可读短语），禁止词汇碎片胡拼。
- question 须截自更靠前的痛点意群，result 须截自其后的能力/结果意群；各 ≥4 字、≤8 字。
- 禁止：通总是卡壳？/ 支付！/ 卡壳？/ 全球聊天！ 这类断词或顿号单字拼盘。
- 禁止无脑加标点与 CTA 硬套（邀请好友？立享返佣！）；禁止空泛营销词（零障碍/正式发布）。
- 拍数按时长与可用意群对数决定（1–5），必须交齐；各拍 question/result 不得重复，不得互为截断碎片。
- get_context.phrasePool 给出可截候选；多拍必须换不同 result（如 批量生成！/字幕已对齐！/成片已导出！），禁止多拍共用同一句空泛结论。
- 收尾 CTA（邀请/返佣）不单独成拍。只选白名单 recipe；透明叠加。

## Few-shot（示范「怎么截」，禁止照搬）
台词：开咖啡馆半年，高峰总排队到门口。上了自助点单，菜单支付取餐一条线。复购明显上来，晚上还能准时收工。
正确：
[
  {"question":"总排队到门口？","result":"支付取餐一条线！"},
  {"question":"高峰总排队？","result":"复购明显上来！"}
]
错误（禁止）：
- {"question":"邀请好友？","result":"立享返佣！"}  ← CTA
- {"question":"通总是卡壳？","result":"支付！"}  ← 断词碎词
- {"question":"卡壳？","result":"全球聊天！"}  ← 过短/拼盘
当前任务必须从「当前最新台词」重新截取，不得复用示例原句。
""".strip()


def build_generation_prompt(
    video: VideoPrompt,
    media: dict[str, Any],
    *,
    minimum_coverage_ratio: float,
    dialogue_text: str = "",
) -> str:
    del minimum_coverage_ratio
    duration = float(media["durationSeconds"])
    dialogue = str(dialogue_text or "").strip() or "（当前无可用台词，只生成图形动效）"
    safe_zone = media.get("safeZone") if isinstance(media.get("safeZone"), dict) else {}
    safe_zone_text = (
        f"x={safe_zone.get('x', 0)}%, y={safe_zone.get('y', 0)}%, "
        f"宽={safe_zone.get('width', 100)}%, 高={safe_zone.get('height', 100)}%"
    )
    from ai8video.media.motion.hyperframes_overlay_semantic import target_beat_count

    required_beats = target_beat_count(duration, dialogue)
    return f"""开始设计 HTML 动效语义方案。可先 get_context，或直接 finalize。

视频提示词：{video.prompt}
当前最新台词：{dialogue}
画布：{media['width']}x{media['height']}
时长：{duration:.3f} 秒（必须恰好 {required_beats} 个 beats；每拍 question？+ result！）
HTML 动效安全区：{safe_zone_text}

硬约束：
1. beats 数量必须是 {required_beats}（按时长+台词句数计算），禁止少交。
2. 每一拍 = 痛点？→ 对应该痛点的结果！；同拍错时出场；各拍 question/result 均不重复。
3. question 截台词靠前卡点，result 截其后变化/结果；禁止 CTA 硬套（邀请好友？立享返佣！）；CTA 不单独成拍。
4. 正文都是台词连续片段；可先 get_context 看 phrasePool；只选白名单 recipe；用 finalize 提交。
""".strip()


def build_critique_prompt(
    artifact: dict[str, Any],
    video: VideoPrompt,
    media: dict[str, Any],
    dialogue_text: str = "",
) -> str:
    payload = json.dumps(artifact, ensure_ascii=False, separators=(",", ":"))
    return f"""你是 HyperFrames 视觉总监。评审下面的透明视频叠加编排，只返回严格 JSON。
视频提示词：{video.prompt}
当前最新台词：{str(dialogue_text or '').strip() or '（无）'}
画布：{media['width']}x{media['height']}，时长 {media['durationSeconds']} 秒。
待评审产物：{payload}

返回：{{"scores":{{"clarity":1到5,"hierarchy":1到5,"typography":1到5,"motion":1到5,"brand":1到5}},"notes":["具体问题"],"revisedArtifact":null或完整修订后产物}}
任一维低于 4 时必须返回完整 revisedArtifact；修订后仍须符合原 Schema 和透明叠加安全边界。重点检查：每一拍是否同时有问题？与结果！两段、错时出场是否清楚、标题是否完整可读、装饰是否压过正文。所有可读文字必须来自当前最新台词，不得自行改写。
""".strip()


def build_validation_repair_prompt(
    artifact: dict[str, Any],
    video: VideoPrompt,
    media: dict[str, Any],
    *,
    dialogue_text: str,
    validation_error: str,
) -> str:
    payload = json.dumps(artifact, ensure_ascii=False, separators=(",", ":"))
    return f"""你是AI8 HyperFrames 编排修复器。下面 artifact 已通过安全结构解析，但 HyperFrames 实测未通过。
只返回完整修订后的 artifact JSON，不要解释、不要 Markdown、不要额外字段。

视频提示词：{video.prompt}
当前最新台词：{str(dialogue_text or '').strip() or '（无）'}
画布：{media['width']}x{media['height']}，时长 {media['durationSeconds']} 秒。
实测错误：{validation_error}
原 artifact：{payload}

修复要求：保留透明叠加和场景覆盖；每一拍画面保留问题？与结果！两段错时出场；所有文字自然流排，不用 absolute/fixed、width/height、left/top、font-size 或 line-height；所有元素与动画必须留在所属安全区；位移不超过 48px，缩放不超过 1.12。""".strip()
