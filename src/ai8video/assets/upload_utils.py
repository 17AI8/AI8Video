from __future__ import annotations

import re
from typing import Any


def resolve_upload_filename(upload: Any) -> str:
    for attr in ("raw_filename", "filename"):
        candidate = _clean_upload_filename(getattr(upload, attr, ""))
        if candidate:
            return candidate
    return ""


def _clean_upload_filename(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = text.replace("\\", "/").split("/")[-1]
    text = re.sub(r"[\x00-\x1f]", "", text).strip()
    if text in {"", ".", ".."}:
        return ""
    return text[:255]
