from __future__ import annotations

import re


MAX_QUERY_CHARS = 200
MAX_SECTION_CHARS = 1200
_PRIMARY_HEADING_PATTERNS = (
    r"^#{1,6}\s+\S",
    r"^脚本\s*[0-9一二三四五六七八九十百]+\s*[《〈]",
    r"^第\s*[0-9一二三四五六七八九十百]+\s*(?:集|章|节|幕|部分)(?:[《〈:：\s]|$)",
    r"^第\s*[0-9一二三四五六七八九十百]+批(?:[《〈:：\s]|$)",
    r"^[🔥⭐]\s*\S+",
    r"^《[^》]{2,80}》$",
)


def split_sections(content: str) -> list[dict[str, str]]:
    blocks = _split_structural_blocks(content)
    sections: list[dict[str, str]] = []
    for heading, body in blocks:
        parts = _split_text_chunks(body)
        if not parts:
            parts = [""]
        for index, part in enumerate(parts):
            section_heading = heading
            if heading and len(parts) > 1:
                section_heading = f"{heading}（{index + 1}）"
            sections.append({"heading": section_heading, "content": part})
    return sections


def build_search_terms(value: str) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    for chunk in re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", str(value or "").lower()):
        candidates = [chunk]
        if re.fullmatch(r"[\u3400-\u9fff]+", chunk) and len(chunk) > 1:
            candidates.extend(chunk[index:index + 2] for index in range(len(chunk) - 1))
        for token in candidates:
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
    return " ".join(tokens)


def build_ts_query(query: str) -> str:
    tokens = build_search_terms(query).split()
    return " | ".join(tokens[:80])


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "")).strip()[:MAX_QUERY_CHARS]


def normalize_tags(tags: list[str]) -> list[str]:
    result: list[str] = []
    for tag in tags if isinstance(tags, list) else []:
        clean = re.sub(r"\s+", " ", str(tag or "")).strip()[:40]
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= 20:
            break
    return result


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def preview(content: str) -> str:
    return re.sub(r"\s+", " ", str(content or "")).strip()[:240]


def _split_structural_blocks(content: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    heading = ""
    body_lines: list[str] = []
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if _is_separator(line):
            _append_block(blocks, heading, body_lines)
            heading, body_lines = "", []
            continue
        if _is_primary_heading(line):
            _append_block(blocks, heading, body_lines)
            heading, body_lines = _clean_heading(line), []
            continue
        body_lines.append(line)
    _append_block(blocks, heading, body_lines)
    if not blocks and str(content or "").strip():
        blocks.append(("", str(content).strip()))
    return blocks


def _append_block(
    blocks: list[tuple[str, str]],
    heading: str,
    body_lines: list[str],
) -> None:
    body = "\n".join(body_lines)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if heading or body:
        blocks.append((heading, body))


def _is_primary_heading(line: str) -> bool:
    if not line:
        return False
    return any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in _PRIMARY_HEADING_PATTERNS)


def _is_separator(line: str) -> bool:
    return bool(line and re.fullmatch(r"[-=_*]{3,}", line))


def _clean_heading(line: str) -> str:
    return re.sub(r"^#{1,6}\s*", "", line).strip()


def _split_text_chunks(text: str) -> list[str]:
    clean_text = str(text or "").strip()
    if not clean_text:
        return []
    if len(clean_text) <= MAX_SECTION_CHARS:
        return [clean_text]
    units = [part.strip() for part in re.split(r"(?<=[。！？!?；;])|\n{2,}", clean_text) if part.strip()]
    if not units:
        return _hard_split(clean_text)
    chunks: list[str] = []
    buffer = ""
    for unit in units:
        if len(unit) > MAX_SECTION_CHARS:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(_hard_split(unit))
        elif buffer and len(buffer) + len(unit) + 1 > MAX_SECTION_CHARS:
            chunks.append(buffer)
            buffer = unit
        else:
            buffer = f"{buffer}\n{unit}".strip()
    if buffer:
        chunks.append(buffer)
    return chunks


def _hard_split(text: str) -> list[str]:
    return [text[index:index + MAX_SECTION_CHARS] for index in range(0, len(text), MAX_SECTION_CHARS)]
