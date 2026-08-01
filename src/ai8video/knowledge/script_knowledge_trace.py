from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RETRIEVAL_TRACE_PATH = Path("temp/ai8video/script_knowledge_retrieval_traces.jsonl")
_trace_lock = threading.Lock()


def append_retrieval_trace(trace: dict[str, Any]) -> None:
    if not trace:
        return
    record = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        **dict(trace),
    }
    try:
        with _trace_lock:
            RETRIEVAL_TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with RETRIEVAL_TRACE_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # Retrieval must not fail merely because local diagnostics cannot be written.
        return
