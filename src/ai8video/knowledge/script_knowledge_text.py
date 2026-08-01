from __future__ import annotations

import re
import unicodedata


MAX_QUERY_CHARS = 200
MAX_BM25_QUERY_TERMS = 64
MAX_PROTECTED_TERMS = 24
MAX_TERM_CHARS = 96

_BASE_CHUNK_PATTERN = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]+")
_QUOTED_PHRASE_PATTERN = re.compile(r'["“‘](.{1,80}?)["”’]')
_PROTECTED_TERM_PATTERNS = (
    re.compile(r"(?<![a-z0-9_])c(?:\+\+|#)(?![a-z0-9_])"),
    re.compile(r"(?<![a-z0-9_])第\s*\d+\s*条(?![a-z0-9_])"),
    re.compile(r"(?<![a-z0-9_.])\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?![a-z0-9_])"),
    re.compile(r"(?<![a-z0-9_.])v?\d+(?:\.\d+){1,4}(?![a-z0-9_])"),
    re.compile(r"(?<![a-z0-9_.])\d+(?:\.\d+)?[a-z]+(?:/[a-z]+)+(?![a-z0-9_])"),
    re.compile(r"(?<![a-z0-9_.])\d+(?:\.\d+)?[a-z]{1,12}(?![a-z0-9_])"),
    re.compile(r"(?<![a-z0-9_])(?:gb|iso|iec|en|din|astm|ansi|api)(?:/[a-z]{1,6})?\s*[-:]?\s*\d+(?:[./:-]\d+)*(?![a-z0-9_])"),
    re.compile(r"(?<![a-z0-9_])[a-z]{1,12}(?:[/.-][a-z0-9]+)+(?![a-z0-9_])"),
    re.compile(r"(?<![a-z0-9_])(?=[a-z0-9_]*[a-z])(?=[a-z0-9_]*\d)[a-z0-9_]+(?![a-z0-9_])"),
)


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


def normalize_retrieval_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", " ", normalized).strip()


def extract_protected_terms(value: str, *, limit: int = MAX_PROTECTED_TERMS) -> list[str]:
    normalized = normalize_retrieval_text(value)
    matches: list[tuple[int, int, str]] = []
    for pattern in _PROTECTED_TERM_PATTERNS:
        matches.extend(
            (match.start(), match.end(), _clean_term(match.group(0)))
            for match in pattern.finditer(normalized)
        )
    matches.extend(
        (match.start(1), match.end(1), _clean_term(match.group(1)))
        for match in _QUOTED_PHRASE_PATTERN.finditer(normalized)
    )
    return _unique_terms(matches, limit=max(1, int(limit)))


def tokenize_retrieval_text(value: str) -> list[str]:
    normalized = normalize_retrieval_text(value)
    if not normalized:
        return []
    tokens = _protected_term_occurrences(normalized)
    for chunk in _BASE_CHUNK_PATTERN.findall(normalized):
        if re.fullmatch(r"[\u3400-\u9fff]+", chunk):
            tokens.extend(_tokenize_chinese_chunk(chunk))
        else:
            tokens.append(chunk[:MAX_TERM_CHARS])
    return [token for token in tokens if token]


def build_bm25_query_terms(value: str, *, limit: int = MAX_BM25_QUERY_TERMS) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for token in tokenize_retrieval_text(value):
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
        if len(result) >= max(1, int(limit)):
            break
    return result


def _protected_term_occurrences(normalized: str) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    for pattern in _PROTECTED_TERM_PATTERNS:
        matches.extend(
            (match.start(), match.end(), _clean_term(match.group(0)))
            for match in pattern.finditer(normalized)
        )
    matches.extend(
        (match.start(1), match.end(1), _clean_term(match.group(1)))
        for match in _QUOTED_PHRASE_PATTERN.finditer(normalized)
    )
    unique_matches = _select_non_overlapping_matches(matches)
    return [term for _start, _end, term in unique_matches if term]


def _tokenize_chinese_chunk(chunk: str) -> list[str]:
    if len(chunk) == 1:
        return [chunk]
    tokens = [chunk] if len(chunk) <= 16 else []
    tokens.extend(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return tokens


def _unique_terms(matches: list[tuple[int, int, str]], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for _start, _end, term in _select_non_overlapping_matches(matches):
        if not term or term in seen:
            continue
        seen.add(term)
        result.append(term)
        if len(result) >= limit:
            break
    return result


def _select_non_overlapping_matches(
    matches: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    candidates = sorted(
        set(matches),
        key=lambda item: (item[0], -(item[1] - item[0]), item[2]),
    )
    selected: list[tuple[int, int, str]] = []
    for candidate in candidates:
        start, end, _term = candidate
        overlaps_existing = any(start < selected_end and end > selected_start for selected_start, selected_end, _ in selected)
        if not overlaps_existing:
            selected.append(candidate)
    return sorted(selected, key=lambda item: (item[0], item[1], item[2]))


def _clean_term(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:MAX_TERM_CHARS]


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
