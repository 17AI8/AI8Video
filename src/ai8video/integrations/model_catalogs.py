from __future__ import annotations

import json
from typing import Any

from ai8video.core.identity import normalize_product_env_key
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


MODEL_CATALOG_DIR = (USER_FILE_ROOT / "模型设置").resolve()
MODEL_CATALOG_PATH = MODEL_CATALOG_DIR / "model_catalogs.json"

SUPPORTED_MODEL_CATALOG_KEYS = {
    "mykey.py model",
    "AI8VIDEO_LLM_MODEL",
    "AI8VIDEO_MULTIMODAL_MODEL",
    "AI8VIDEO_IMAGE_MODEL",
    "AI8VIDEO_VIDEO_MODEL",
}


def load_model_catalogs() -> dict[str, list[dict[str, Any]]]:
    try:
        data = json.loads(MODEL_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    raw_catalogs = data.get("catalogs")
    if not isinstance(raw_catalogs, dict):
        return {}
    catalogs: dict[str, list[dict[str, Any]]] = {}
    for key, value in raw_catalogs.items():
        env_name = normalize_product_env_key(str(key or "").strip())
        if env_name not in SUPPORTED_MODEL_CATALOG_KEYS or not isinstance(value, list):
            continue
        catalogs[env_name] = _clean_catalog(value)
    return catalogs


def load_model_catalog(env_name: str) -> list[dict[str, Any]]:
    key = normalize_product_env_key(str(env_name or "").strip())
    return load_model_catalogs().get(key, [])


def save_model_catalog(env_name: str, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key = normalize_product_env_key(str(env_name or "").strip())
    if key not in SUPPORTED_MODEL_CATALOG_KEYS:
        raise ValueError("unsupported model catalog")
    clean = _clean_catalog(models)
    catalogs = load_model_catalogs()
    catalogs[key] = clean
    ensure_user_file_root()
    MODEL_CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_CATALOG_PATH.write_text(
        json.dumps({"catalogs": catalogs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return clean


def _clean_catalog(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("modelId") or item.get("model") or item.get("name") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        clean.append({
            "modelId": model_id,
            "modelKey": str(item.get("modelKey") or "").strip(),
            "name": str(item.get("name") or model_id).strip(),
            "type": str(item.get("type") or "model").strip(),
            "provider": str(item.get("provider") or "").strip(),
            "price": item.get("price") if isinstance(item.get("price"), (int, float)) else 0,
        })
    return clean
