from __future__ import annotations

import re


MAX_QUERY_CHARS = 200


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
