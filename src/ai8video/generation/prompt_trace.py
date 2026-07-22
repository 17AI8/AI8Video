from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


TRACE_PATH = Path("temp/ai8video/prompt_traces.jsonl")


def append_prompt_trace(event: str, *, session_id: str | None = None, payload: dict[str, Any] | None = None) -> None:
    event_name = str(event or "").strip()
    if not event_name:
        return
    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "event": event_name,
        "sessionId": str(session_id or "").strip() or None,
        "payload": payload or {},
    }
    with TRACE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
