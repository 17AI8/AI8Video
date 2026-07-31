from __future__ import annotations

import re


MAX_TTS_TEXT_CHARS = 1800
TAIL_FRAME_MARKER = "所有主体最后一秒尽可能全身正对着镜头"
DIALOGUE_FIELD_RE = re.compile(
    r"(?:台词\s*/\s*口播|台词|口播|旁白|解说|画外音)"
    r"\s*(?:[（(][^）)\n]{0,30}[）)])?\s*[：:]\s*"
)
DIALOGUE_CUE_DOUBLE_QUOTE_RE = re.compile(
    r"(?:口播|旁白|解说|画外音|(?:面对|看向|对着)?镜头说|说道|说)"
    r"[^“”\"\n]{0,50}[“\"]([^”\"\n]+)[”\"]"
)
DIALOGUE_CUE_SINGLE_QUOTE_RE = re.compile(
    r"(?:口播|旁白|解说|画外音|(?:面对|看向|对着)?镜头说|说道|说)"
    r"[^‘’'\n]{0,50}[‘']([^’'\n]+)[’']"
)
SHOT_BOUNDARY_RE = re.compile(
    r"(?:镜头[一二三四五六七八九十百\d]+|第?\d+[集格段镜]?)\s*(?:[（(]|[：:、.\s-])"
)
TIME_BOUNDARY_RE = re.compile(r"\d{1,3}\s*[-—~至到]\s*\d{1,3}\s*(?:秒|s|S)\s*[：:]")


def prepare_narration_text(text: str) -> str:
    raw = str(text or "")
    dialogue_text = extract_dialogue_text(raw)
    if dialogue_text:
        raw = dialogue_text
    lines = [
        clean
        for line in raw.splitlines()
        if (clean := strip_prompt_label(line)) and not looks_like_visual_instruction(clean)
    ]
    joined = " ".join(lines) if lines else strip_prompt_label(raw)
    joined = re.sub(
        r"[（(][^）)]{0,80}(?:秒|镜头|画面|景别|运镜|特写|远景|近景)[^）)]*[）)]",
        "，",
        joined,
    )
    joined = re.sub(r"\s+", " ", joined)
    joined = re.sub(r"[;；]+", "。", joined).strip(" ，。；;")
    if len(joined) > MAX_TTS_TEXT_CHARS:
        joined = joined[:MAX_TTS_TEXT_CHARS].rsplit("。", 1)[0] or joined[:MAX_TTS_TEXT_CHARS]
    return joined.strip()


def extract_dialogue_text(text: str) -> str:
    raw = str(text or "")
    if not raw.strip():
        return ""
    field_stop = (
        "情绪语气", "情绪", "音效建议", "音效", "音乐", "画面", "镜头景别",
        "景别", "场景描述", "场景", "运镜动作", "运镜", "人物动作",
        "动作", "表情", "构图", TAIL_FRAME_MARKER,
    )
    lines = [str(line or "").strip() for line in raw.splitlines()]
    pieces = _collect_dialogue_pieces(lines, field_stop, DIALOGUE_CUE_DOUBLE_QUOTE_RE)
    if not pieces:
        pieces = _collect_dialogue_pieces(lines, field_stop, DIALOGUE_CUE_SINGLE_QUOTE_RE)
    joined = re.sub(r"\s+", " ", " ".join(pieces))
    return joined.strip(" ，。；;")


def _collect_dialogue_pieces(
    lines: list[str],
    field_stop: tuple[str, ...],
    cue_quote_re: re.Pattern[str],
) -> list[str]:
    pieces: list[str] = []
    for line_text in lines:
        _append_dialogue_pieces(pieces, line_text, field_stop, cue_quote_re)
    return pieces


def _append_dialogue_pieces(
    pieces: list[str],
    line_text: str,
    field_stop: tuple[str, ...],
    cue_quote_re: re.Pattern[str],
) -> None:
    if not line_text:
        return
    matches = list(DIALOGUE_FIELD_RE.finditer(line_text))
    if not matches:
        pieces.extend(
            match.group(1).strip()
            for match in cue_quote_re.finditer(line_text)
            if match.group(1).strip()
        )
        return
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(line_text)
        chunk = _trim_dialogue_chunk(line_text[match.end():end], field_stop)
        if chunk:
            pieces.append(chunk)


def _trim_dialogue_chunk(chunk: str, field_stop: tuple[str, ...]) -> str:
    stop_positions = [
        pos
        for marker in field_stop
        for token in (marker, f"{marker}：", f"{marker}:")
        if (pos := chunk.find(token)) >= 0
    ]
    for boundary in (SHOT_BOUNDARY_RE, TIME_BOUNDARY_RE):
        boundary_match = boundary.search(chunk)
        if boundary_match and boundary_match.start() > 0:
            stop_positions.append(boundary_match.start())
    if stop_positions:
        chunk = chunk[:min(stop_positions)]
    return re.sub(r"^[“”‘’\"'：:\s]+|[“”‘’\"'\s]+$", "", chunk).strip()


def strip_prompt_label(line: str) -> str:
    text = str(line or "").strip()
    text = re.sub(r"^\s*(第?\d+[集格段镜]?|镜头[一二三四五六七八九十\d]+)[：:、.\s-]*", "", text)
    text = re.sub(
        r"^\s*[【\[]?[^】\]]{0,12}(?:台词\s*/\s*口播|台词|口播|旁白|解说|画外音|字幕)"
        r"(?:[（(][^）)]{0,30}[）)])?[】\]]?[：:]\s*",
        "",
        text,
    )
    text = re.sub(r"^\s*(画面|动作|表情|运镜|景别|场景|音乐|音效|镜头|构图)[：:]\s*.*$", "", text)
    return re.sub(r"[“”‘’\"']", "", text).strip()


def looks_like_visual_instruction(text: str) -> bool:
    lowered = text.lower()
    markers = [
        "画面", "镜头", "运镜", "景别", "构图", "特写", "远景",
        "近景", "中景", "光效", "字幕", "文字", "logo",
    ]
    return sum(1 for marker in markers if marker in lowered) >= 2
