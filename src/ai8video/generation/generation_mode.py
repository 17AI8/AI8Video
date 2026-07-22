from __future__ import annotations

import json
from typing import Any

from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


GENERATION_MODE_DIR = (USER_FILE_ROOT / "生成模式").resolve()
GENERATION_MODE_SETTINGS_PATH = GENERATION_MODE_DIR / "settings.json"


def generation_mode_status() -> dict[str, Any]:
    return {
        "ok": True,
        "concurrentGeneration": default_concurrent_generation_enabled(),
    }


def default_concurrent_generation_enabled() -> bool:
    data = _read_settings()
    return bool(data.get("concurrentGeneration"))


def update_generation_mode(*, concurrent_generation: bool) -> dict[str, Any]:
    _write_settings({"concurrentGeneration": bool(concurrent_generation)})
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
