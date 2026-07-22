from __future__ import annotations

import json
from typing import Any

from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


VIDEO_MERGE_MODE_DIR = (USER_FILE_ROOT / "视频合并").resolve()
VIDEO_MERGE_MODE_SETTINGS_PATH = VIDEO_MERGE_MODE_DIR / "settings.json"
SUPPORTED_VIDEO_MERGE_MODES = {"none", "merge2", "merge4"}


def normalize_video_merge_mode(value: object) -> str:
    text = str(value or "").strip()
    if text in SUPPORTED_VIDEO_MERGE_MODES:
        return text
    return "none"


def load_video_merge_mode() -> str:
    data = _read_settings()
    return normalize_video_merge_mode(data.get("mergeMode"))


def video_merge_mode_status() -> dict[str, Any]:
    mode = load_video_merge_mode()
    return {
        "ok": True,
        "mergeMode": mode,
        "supportedModes": sorted(SUPPORTED_VIDEO_MERGE_MODES),
        "path": str(VIDEO_MERGE_MODE_SETTINGS_PATH),
    }


def save_video_merge_mode(mode: object) -> dict[str, Any]:
    normalized = normalize_video_merge_mode(mode)
    _write_settings({"mergeMode": normalized})
    return video_merge_mode_status()


def _read_settings() -> dict[str, Any]:
    try:
        data = json.loads(VIDEO_MERGE_MODE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(data: dict[str, Any]) -> None:
    ensure_user_file_root()
    VIDEO_MERGE_MODE_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_MERGE_MODE_SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
