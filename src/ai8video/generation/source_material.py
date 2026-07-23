from __future__ import annotations

import re


MAX_SCRIPT_BLOCKS_FOR_DIRECT_LLM = 12
SCRIPT_CONTEXT_MARGIN_BLOCKS = 2


def prepare_script_for_model(script: str, video_count: int) -> str:
    """缩减超长编号素材，同时保留可追溯的代表性来源块。"""
    raw = str(script or "").strip()
    if not raw:
        return raw
    blocks = _extract_numbered_script_blocks(raw)
    if len(blocks) <= MAX_SCRIPT_BLOCKS_FOR_DIRECT_LLM:
        return raw
    selected = _select_representative_blocks(blocks, video_count)
    if len(selected) >= len(blocks):
        return raw
    header = _script_preface_before_first_block(raw, blocks[0]["start"])
    omitted = len(blocks) - len(selected)
    lines = []
    if header:
        lines.append(header)
    lines.extend([
        f"【长素材预处理】原文包含 {len(blocks)} 个编号脚本块。",
        f"本次目标生成 {video_count} 条，已按原文顺序选取 {len(selected)} 个代表性脚本块，省略 {omitted} 个未选块。",
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
        if text:
            blocks.append({"number": int(match.group("number")), "start": start, "text": text})
    return blocks


def _select_representative_blocks(
    blocks: list[dict[str, object]],
    video_count: int,
) -> list[dict[str, object]]:
    total = len(blocks)
    if total <= MAX_SCRIPT_BLOCKS_FOR_DIRECT_LLM:
        return blocks
    target = min(total, max(video_count + SCRIPT_CONTEXT_MARGIN_BLOCKS, MAX_SCRIPT_BLOCKS_FOR_DIRECT_LLM))
    if target >= total:
        return blocks
    selected_indexes = {0, total - 1}
    if target > 2:
        step = (total - 1) / (target - 1)
        selected_indexes.update(round(index * step) for index in range(target))
    while len(selected_indexes) < target:
        candidate = len(selected_indexes)
        selected_indexes.add(min(total - 1, candidate))
    return [blocks[index] for index in sorted(selected_indexes)[:target]]


def _script_preface_before_first_block(script: str, first_block_start: int) -> str:
    preface = script[:max(0, first_block_start)].strip()
    if len(preface) <= 2000:
        return preface
    return preface[:2000].rstrip() + "\n（前置信息过长，已截取前 2000 字。）"
