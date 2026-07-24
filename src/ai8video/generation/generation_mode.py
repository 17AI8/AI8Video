from __future__ import annotations

import json
from typing import Any

from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


GENERATION_MODE_DIR = (USER_FILE_ROOT / "生成模式").resolve()
GENERATION_MODE_SETTINGS_PATH = GENERATION_MODE_DIR / "settings.json"


def generation_mode_status() -> dict[str, Any]:
    data = _read_settings()
    return {
        "ok": True,
        "concurrentGeneration": bool(data.get("concurrentGeneration")),
        "smartSplit": bool(data.get("smartSplit")),
        "confirmSmartSplit": bool(data.get("confirmSmartSplit")),
        "tailFrameChaining": bool(data.get("tailFrameChaining")),
    }


def default_concurrent_generation_enabled() -> bool:
    data = _read_settings()
    return bool(data.get("concurrentGeneration"))


def default_smart_split_enabled() -> bool:
    return bool(_read_settings().get("smartSplit"))


def default_smart_split_confirmation_enabled() -> bool:
    return bool(_read_settings().get("confirmSmartSplit"))


def default_tail_frame_chaining_enabled() -> bool:
    return bool(_read_settings().get("tailFrameChaining"))


def update_generation_mode(
    *,
    concurrent_generation: bool,
    smart_split: bool = False,
    confirm_smart_split: bool = False,
    tail_frame_chaining: bool = False,
) -> dict[str, Any]:
    chained = bool(smart_split and tail_frame_chaining)
    _write_settings(
        {
            "concurrentGeneration": bool(concurrent_generation and not chained),
            "smartSplit": bool(smart_split),
            "confirmSmartSplit": bool(smart_split and confirm_smart_split),
            "tailFrameChaining": chained,
        }
    )
    return generation_mode_status()


def _read_settings() -> dict[str, Any]:
    try:
        data = json.loads(GENERATION_MODE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(data: dict[str, Any]) -> None:
    ensure_user_file_root()
    GENERATION_MODE_DIR.mkdir(parents=True, exist_ok=True)
    GENERATION_MODE_SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
