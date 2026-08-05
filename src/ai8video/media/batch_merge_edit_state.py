from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


BATCH_MERGE_EDIT_STATE_DIR = ".merged-edit-state"
BATCH_MERGE_EDIT_KEY_PREFIX = "source/video/.merged/"
BATCH_MERGE_EDIT_SCHEMA = "batch-merge-edit-state-v2"


def create_batch_merge_edit_state(
    result_root: Path,
    *,
    source_keys: list[str],
    source_video_chunks: list[dict[str, Any]],
    video_chunks: list[dict[str, Any]],
    source_durations: list[float],
    edited_durations: list[float],
    first_preview_key: str = "",
) -> dict[str, Any]:
    merge_id = uuid4().hex
    relative_key = batch_merge_edit_key(merge_id)
    payload = {
        "schema": BATCH_MERGE_EDIT_SCHEMA,
        "id": merge_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "userGeneratedKey": relative_key,
        "sourceKeys": list(source_keys),
        # These chunks address the temporary raw concatenation of the original
        # sources.  ``videoChunks`` instead addresses the edited cache exposed
        # to the existing one-video preview/editor contract.
        "sourceVideoChunks": list(source_video_chunks),
        "videoChunks": list(video_chunks),
        "sourceDurations": [round(float(value), 3) for value in source_durations],
        "editedDurations": [round(float(value), 3) for value in edited_durations],
        "firstPreviewKey": str(first_preview_key or ""),
    }
    path = batch_merge_edit_state_path(result_root, merge_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, payload)
    return payload


def batch_merge_edit_key(merge_id: str) -> str:
    return f"{BATCH_MERGE_EDIT_KEY_PREFIX}{str(merge_id).strip()}.mp4"


def is_batch_merge_edit_key(relative_key: object) -> bool:
    return str(relative_key or "").strip().lstrip("/").startswith(BATCH_MERGE_EDIT_KEY_PREFIX)


def batch_merge_edit_id(relative_key: object) -> str:
    clean_key = str(relative_key or "").strip().lstrip("/")
    if not is_batch_merge_edit_key(clean_key):
        return ""
    candidate = Path(clean_key).stem
    return candidate if len(candidate) == 32 and all(char in "0123456789abcdef" for char in candidate.lower()) else ""


def batch_merge_edit_state_path(result_root: Path, merge_id: str) -> Path:
    root = Path(result_root).resolve()
    clean_id = str(merge_id or "").strip()
    if len(clean_id) != 32 or any(char not in "0123456789abcdef" for char in clean_id.lower()):
        raise ValueError("合并编辑态标识无效")
    return (root / BATCH_MERGE_EDIT_STATE_DIR / f"{clean_id}.json").resolve()


def load_batch_merge_edit_state(result_root: Path, relative_key: object) -> dict[str, Any]:
    merge_id = batch_merge_edit_id(relative_key)
    if not merge_id:
        return {}
    path = batch_merge_edit_state_path(result_root, merge_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema") != BATCH_MERGE_EDIT_SCHEMA:
        return {}
    if str(payload.get("id") or "") != merge_id:
        return {}
    if str(payload.get("userGeneratedKey") or "") != batch_merge_edit_key(merge_id):
        return {}
    source_keys = payload.get("sourceKeys")
    if not isinstance(source_keys, list) or len(source_keys) < 2:
        return {}
    return payload


def list_batch_merge_edit_states(result_root: Path) -> list[dict[str, Any]]:
    root = Path(result_root).resolve()
    state_root = (root / BATCH_MERGE_EDIT_STATE_DIR).resolve()
    if not state_root.is_dir():
        return []
    states = []
    for path in state_root.glob("*.json"):
        state = load_batch_merge_edit_state(root, batch_merge_edit_key(path.stem))
        if state:
            states.append(state)
    return states


def delete_batch_merge_edit_state(result_root: Path, relative_key: object) -> str:
    merge_id = batch_merge_edit_id(relative_key)
    if not merge_id:
        return ""
    path = batch_merge_edit_state_path(result_root, merge_id)
    if not path.is_file():
        return ""
    path.unlink()
    return path.relative_to(Path(result_root).resolve()).as_posix()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
