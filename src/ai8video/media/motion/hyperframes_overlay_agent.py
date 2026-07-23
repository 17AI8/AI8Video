"""HTML 动效语义 Agent：模型一次返回自审结果与可用语义方案。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ai8video.core.models import VideoPrompt
from ai8video.media.motion.hyperframes_overlay_legacy import _parse_json_object
from ai8video.media.motion.hyperframes_overlay_semantic import (
    normalize_semantic_spec,
    phrase_role_pools,
    target_beat_count,
)


TOOL_NAMES = ("get_context", "validate_semantic", "finalize")
MAX_AGENT_TURNS = 4


@dataclass
class AgentRunResult:
    semantic: dict[str, Any]
    turns: int
    audit: dict[str, Any] = field(default_factory=dict)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)


class HtmlMotionSemanticHarness:
    """让模型先自审再以 JSON 提交，本地只保留硬校验安全门。"""

    def __init__(
        self,
        llm: Callable[[str], str],
        video: VideoPrompt,
        media: dict[str, Any],
        dialogue_text: str = "",
        *,
        max_turns: int = MAX_AGENT_TURNS,
        retry_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._llm = llm
        self._video = video
        self._media = media
        self._dialogue = str(dialogue_text or "").strip()
        self._max_turns = max(1, int(max_turns))
        self._retry_callback = retry_callback
        self._retry_count = 0
        self._trace: list[dict[str, Any]] = []
        self._last_error = ""

    def run(self) -> AgentRunResult:
        self._trace = []
        self._last_error = ""
        for turn in range(1, self._max_turns + 1):
            raw = str(self._llm(self._build_turn_prompt()) or "").strip()
            audit, semantic = _parse_reviewed_semantic(raw)
            error = self._submission_error(audit, semantic)
            self._trace.append({"turn": turn, "audit": audit, "error": error})
            if not error:
                return AgentRunResult(
                    semantic=normalize_semantic_spec(semantic, self._media, self._dialogue),
                    turns=turn,
                    audit=audit,
                    tool_trace=list(self._trace),
                )
            self._last_error = error
            self._notify_retry(turn, audit, error, raw)
        raise ValueError(self._last_error or "AI 未返回可用的 HTML 动效方案")

    def _submission_error(self, audit: dict[str, Any], semantic: dict[str, Any]) -> str:
        if not semantic:
            return "JSON 结构不完整"
        if audit and audit.get("passed") is False:
            return str(audit.get("summary") or "AI 审核未通过")
        try:
            normalize_semantic_spec(semantic, self._media, self._dialogue)
        except (TypeError, ValueError) as exc:
            return str(exc)
        return ""

    def _notify_retry(self, turn: int, audit: dict[str, Any], error: str, raw: str) -> None:
        if turn >= self._max_turns:
            return
        self._retry_count += 1
        if self._retry_callback is None:
            return
        result = _audit_summary(audit.get("summary") if audit else error)
        self._retry_callback({
            "retryCount": self._retry_count,
            "retryLimit": max(0, self._max_turns - 1),
            "auditResult": result,
            "retryReason": result,
            "attemptTrace": {
                "attempt": turn,
                "responseJson": _safe_json_value(raw),
                "aiAudit": audit,
                "localValidationError": error,
            },
        })

    def _tool_get_context(self) -> str:
        duration = float(self._media["durationSeconds"])
        safe = self._media.get("safeZone") if isinstance(self._media.get("safeZone"), dict) else {}
        payload = {
            "videoPrompt": str(self._video.prompt or ""),
            "dialogue": self._dialogue or "（当前无可用台词，只生成图形动效）",
            "canvas": {
                "width": int(self._media["width"]),
                "height": int(self._media["height"]),
                "durationSeconds": duration,
            },
            "safeZone": {
                "x": safe.get("x", 0),
                "y": safe.get("y", 0),
                "width": safe.get("width", 100),
                "height": safe.get("height", 100),
            },
            "beatIntervalSeconds": float(self._media.get("beatIntervalSeconds") or 5),
            "beatIntervalMode": "smart" if self._media.get("smartBeatInterval") else "custom",
            "requiredBeatCount": target_beat_count(
                duration,
                self._dialogue,
                beat_interval_seconds=self._media.get("beatIntervalSeconds", 5),
            ),
            "copyChunks": _dialogue_chunks(self._dialogue),
        }
        questions, results = phrase_role_pools(self._dialogue)
        if questions or results:
            payload["phrasePool"] = {
                "questionCandidates": questions[:8],
                "resultCandidates": results[:8],
            }
            payload["copyHint"] = "各拍 question/result 必须互不相同；优先从 phrasePool 截完整意群"
        return _dump(payload)

    def _build_turn_prompt(self) -> str:
        feedback = f"\n上次本地复核未通过：{self._last_error}\n请修正后重新完整输出。" if self._last_error else ""
        smart_interval = bool(self._media.get("smartBeatInterval"))
        beat_count = target_beat_count(
            float(self._media["durationSeconds"]),
            self._dialogue,
            beat_interval_seconds=self._media.get("beatIntervalSeconds", 5),
        )
        template_count = 3 if smart_interval else beat_count
        beat_template = ",".join(
            f'{{"chunkIndex":{index + 1},"primary":"先出现的一级片段","secondary":"后出现的二级片段"}}'
            for index in range(template_count)
        )
        interval_rule = (
            "1. 当前为智能间隔模式。你必须根据视频时长、copyChunks 的信息密度和叙事节奏，"
            "自行选择 1.0–8.0 秒的 beatIntervalSeconds，允许一位小数；beats 数量必须等于 round(durationSeconds / beatIntervalSeconds)。"
            "首先保证 beats 数量不少于 copyChunks 数量，不得为了延长间隔而省略台词块。"
            if smart_interval else
            "1. beats 数量必须等于 context.requiredBeatCount，不得自行减少。"
        )
        interval_field = '"beatIntervalSeconds":2.2,' if smart_interval else ""
        return f"""你是 HTML 动效文案生成与审核员。先在内部完成生成、逐项审核和修正，最后只返回一个可直接使用的 JSON，不要输出 HTML/CSS 或解释。

上下文：{self._tool_get_context()}

审核规则：
{interval_rule}
2. 必须以 context.copyChunks 为唯一文案切块来源；copyChunks 已按逗号、句号、问号和感叹号切分。顿号“、”连接的并列内容默认保留在同一块，只有整块超过 12 字时才会按顿号拆分。
3. 每拍必须选择一个完整 copyChunk，再在该块内拆成 primary（一级）和 secondary（二级）；禁止把两个块的文字拼在同一拍。
4. 每拍必须写正确 chunkIndex；beats 必须按 chunkIndex 从小到大排列，保持原台词的叙事顺序。
5. 每个 copyChunk 都必须至少被一拍使用，禁止跳过开头、中段或收尾块。长块可以拆成多拍，但不能省略。
6. 当 copyChunk.needsSummary=true 时，允许你智能概括：保留事件主体、核心建议/冲突和原意，压缩成 primary/secondary 各 1–6 字。概括不等于删除整块。
7. 当 needsSummary=false 时优先从原块直接截取；当 needsSummary=true 时可以改写为简洁但意思等价的标题。
8. 在选中的 copyChunk 内，primary 必须是语义上更先读的片段，secondary 是其后的补充。动画会严格按 primary 先出场、secondary 后出场，禁止填反。
9. primary/secondary 各 1–6 字，一级是先读的上文，二级是后读的补充。这一项由你完成 AI 审核，本地不替你判断语义。
10. copyChunks 数量足够时每拍使用不同块；所有 primary/secondary 应全局互不相同。
11. 提交前必须自审：所有 chunkIndex 均已覆盖、顺序正确、长块已概括而非省略、primary/secondary 未填反。

严格输出格式：
{{"audit":{{"passed":true,"summary":"审核通过"}},"semantic":{{{interval_field}"designDirection":"signal","layoutRecipe":"signal-frame","componentRecipes":[],"motionRecipe":"kinetic-snap","density":"balanced","anchor":"top-left","palette":{{}},"beats":[{beat_template}]}}}}

audit.summary 最多 12 个中文字；最终方案通过你的自审时必须返回 passed=true。{feedback}
""".strip()


def run_semantic_agent(
    llm: Callable[[str], str],
    video: VideoPrompt,
    media: dict[str, Any],
    dialogue_text: str = "",
    *,
    max_turns: int = MAX_AGENT_TURNS,
    retry_callback: Callable[[dict[str, Any]], None] | None = None,
) -> AgentRunResult:
    return HtmlMotionSemanticHarness(
        llm,
        video,
        media,
        dialogue_text,
        max_turns=max_turns,
        retry_callback=retry_callback,
    ).run()


def _parse_operator_message(raw: str) -> dict[str, Any] | None:
    try:
        value = _parse_json_object(raw)
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    tool = str(value.get("tool") or "").strip()
    if tool in TOOL_NAMES:
        return value
    # {"name":"finalize","arguments":{...}} 兼容
    name = str(value.get("name") or "").strip()
    if name in TOOL_NAMES:
        args = value.get("arguments") if isinstance(value.get("arguments"), dict) else value.get("args")
        return {"tool": name, "args": args if isinstance(args, dict) else {}}
    return None


def _parse_reviewed_semantic(raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        value = _parse_json_object(raw)
    except Exception:
        return {}, {}
    if not isinstance(value, dict):
        return {}, {}
    audit = value.get("audit") if isinstance(value.get("audit"), dict) else {}
    semantic = value.get("semantic") if isinstance(value.get("semantic"), dict) else {}
    if semantic:
        return audit, semantic
    call = _parse_operator_message(raw)
    if call and isinstance(call.get("args"), dict):
        return {"passed": True, "summary": "审核通过"}, call["args"]
    if _looks_like_semantic(raw):
        return {"passed": True, "summary": "审核通过"}, value
    return audit, {}


def _audit_summary(value: Any) -> str:
    text = re.sub(r"[\r\n]+", " ", str(value or "").strip())
    mappings = (
        (("过长", ">6字"), "文案过长"),
        (("痛点", "普通陈述", "伪问题"), "问句缺少真实痛点"),
        (("连续片段", "完整意群", "碎词"), "与原台词不一致"),
        (("beats", "拍数"), "拍数不符合设置"),
        (("重复", "互为截断"), "文案重复"),
        (("CTA", "营销", "邀请好友", "返佣"), "营销话术不合格"),
    )
    for markers, summary in mappings:
        if any(marker in text for marker in markers):
            return summary
    return text[:24] or "AI 审核未通过"


def _safe_json_value(raw: str) -> Any:
    try:
        return _parse_json_object(raw)
    except Exception:
        return {"raw": str(raw or "")[:8000]}


def _dialogue_chunks(value: str) -> list[dict[str, Any]]:
    chunks: list[str] = []
    for item in re.split(r"[，。！？!?；;]+", str(value or "")):
        compact = re.sub(r"\s+", "", item).strip()
        if not compact:
            continue
        chunks.extend(_split_long_enumeration_chunk(compact))
    return [
        {
            "index": index,
            "text": text,
            "charCount": len(text.replace("、", "")),
            "needsSummary": len(text.replace("、", "")) > 12,
        }
        for index, text in enumerate(chunks, start=1)
    ]


def _split_long_enumeration_chunk(value: str, limit: int = 12) -> list[str]:
    if len(value.replace("、", "")) <= limit or "、" not in value:
        return [value]
    parts = [part for part in value.split("、") if part]
    groups: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}、{part}" if current else part
        if current and len(candidate.replace("、", "")) > limit:
            groups.append(current)
            current = part
        else:
            current = candidate
    if current:
        groups.append(current)
    return groups


def _looks_like_semantic(raw: str) -> bool:
    try:
        value = _parse_json_object(raw)
    except Exception:
        return False
    if not isinstance(value, dict) or "tool" in value:
        return False
    if "scenes" in value or "design" in value:
        return False
    return any(key in value for key in (
        "designDirection", "layoutRecipe", "motionRecipe", "beats", "density", "theme", "anchor",
    ))


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
