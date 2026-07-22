from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai8video.core.identity import normalize_product_env_key
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


MODEL_OVERRIDES_DIR = (USER_FILE_ROOT / "模型设置").resolve()
MODEL_OVERRIDES_PATH = MODEL_OVERRIDES_DIR / "model_overrides.json"

SUPPORTED_MODEL_OVERRIDE_KEYS = {
    "mykey.py model",
    "AI8VIDEO_LLM_MODEL",
    "AI8VIDEO_MULTIMODAL_MODEL",
    "AI8VIDEO_IMAGE_MODEL",
}


def load_model_overrides() -> dict[str, str]:
    try:
        data = json.loads(MODEL_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    models = data.get("models")
    if not isinstance(models, dict):
        return {}
    clean: dict[str, str] = {}
    for key, value in models.items():
        env_name = normalize_product_env_key(str(key or "").strip())
        model = str(value or "").strip()
        if env_name in SUPPORTED_MODEL_OVERRIDE_KEYS and model:
            clean[env_name] = model
    return clean


def save_model_override(env_name: str, model: str) -> dict[str, str]:
    key = normalize_product_env_key(str(env_name or "").strip())
    value = str(model or "").strip()
    if key not in SUPPORTED_MODEL_OVERRIDE_KEYS:
        raise ValueError("unsupported model setting")
    if not value:
        raise ValueError("model is required")
    overrides = load_model_overrides()
    overrides[key] = value
    ensure_user_file_root()
    MODEL_OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_OVERRIDES_PATH.write_text(
        json.dumps({"models": overrides}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return overrides
