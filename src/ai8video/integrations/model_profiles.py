from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


MODEL_PROFILES_DIR = (USER_FILE_ROOT / "模型设置").resolve()
MODEL_PROFILES_PATH = MODEL_PROFILES_DIR / "model_profiles.json"
MODEL_PROFILE_CATEGORIES = ("llm", "multimodal", "image", "video")
REMOVED_VIDEO_TEMPLATES = {"yunwu-grok", "yunwu-omni", "yunwu-veo"}


def load_model_profiles() -> dict[str, Any]:
    try:
        data = json.loads(MODEL_PROFILES_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    categories = data.get("categories") if isinstance(data, dict) else None
    clean = {category: _normalize_category((categories or {}).get(category)) for category in MODEL_PROFILE_CATEGORIES}
    return {"version": 1, "categories": clean}


def ensure_model_profiles(defaults: dict[str, dict[str, Any]]) -> dict[str, Any]:
    store = load_model_profiles()
    changed = False
    for category in MODEL_PROFILE_CATEGORIES:
        bucket = store["categories"][category]
        if bucket["profiles"]:
            continue
        profile = _normalize_profile({
            "id": _new_profile_id(),
            "name": "默认配置",
            **(defaults.get(category) or {}),
        })
        bucket["profiles"] = [profile]
        bucket["activeId"] = profile["id"]
        changed = True
    if changed:
        _save_model_profiles(store)
    return store


def active_model_profile(category: str) -> dict[str, Any] | None:
    bucket = load_model_profiles()["categories"].get(category)
    if not bucket:
        return None
    return next((item for item in bucket["profiles"] if item["id"] == bucket["activeId"]), None)


def create_model_profile(category: str, payload: dict[str, Any]) -> dict[str, Any]:
    store, bucket = _store_bucket(category)
    profile = _normalize_profile({"id": _new_profile_id(), "name": "备选配置", **payload})
    bucket["profiles"].append(profile)
    if not bucket["activeId"]:
        bucket["activeId"] = profile["id"]
    _save_model_profiles(store)
    return store


def duplicate_model_profile(category: str, profile_id: str) -> dict[str, Any]:
    store, bucket = _store_bucket(category)
    source = next((item for item in bucket["profiles"] if item["id"] == profile_id), None)
    if source is None:
        raise ValueError("模型配置不存在")
    profile = _normalize_profile({
        **source,
        "id": _new_profile_id(),
        "name": _duplicate_profile_name(bucket["profiles"], source["name"]),
    })
    bucket["profiles"].append(profile)
    _save_model_profiles(store)
    return store


def update_model_profile(category: str, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    store, bucket = _store_bucket(category)
    for index, current in enumerate(bucket["profiles"]):
        if current["id"] != profile_id:
            continue
        merged = {**current, **payload, "id": profile_id}
        if not str(payload.get("apiKey") or "").strip():
            merged["apiKey"] = current.get("apiKey", "")
        bucket["profiles"][index] = _normalize_profile(merged)
        _save_model_profiles(store)
        return store
    raise ValueError("模型配置不存在")


def activate_model_profile(category: str, profile_id: str) -> dict[str, Any]:
    store, bucket = _store_bucket(category)
    if not any(item["id"] == profile_id for item in bucket["profiles"]):
        raise ValueError("模型配置不存在")
    bucket["activeId"] = profile_id
    _save_model_profiles(store)
    return store


def delete_model_profile(category: str, profile_id: str) -> dict[str, Any]:
    store, bucket = _store_bucket(category)
    if bucket["activeId"] == profile_id:
        raise ValueError("当前启用配置不能删除")
    remaining = [item for item in bucket["profiles"] if item["id"] != profile_id]
    if len(remaining) == len(bucket["profiles"]):
        raise ValueError("模型配置不存在")
    bucket["profiles"] = remaining
    _save_model_profiles(store)
    return store


def public_model_profiles(store: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for category, bucket in store.get("categories", {}).items():
        result[category] = {
            "activeId": bucket.get("activeId", ""),
            "profiles": [
                {**item, "apiKey": "", "hasApiKey": bool(item.get("apiKey"))}
                for item in bucket.get("profiles", [])
            ],
        }
    return result


def _store_bucket(category: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if category not in MODEL_PROFILE_CATEGORIES:
        raise ValueError("不支持这个模型分类")
    store = load_model_profiles()
    return store, store["categories"][category]


def _normalize_category(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    profiles = [_normalize_profile(item) for item in raw.get("profiles", []) if isinstance(item, dict)]
    active_id = str(raw.get("activeId") or "").strip()
    if profiles and not any(item["id"] == active_id for item in profiles):
        active_id = profiles[0]["id"]
    return {"activeId": active_id, "profiles": profiles}


def _normalize_profile(value: dict[str, Any]) -> dict[str, Any]:
    template = str(value.get("template") or "").strip()
    if template in REMOVED_VIDEO_TEMPLATES:
        template = "openai-compatible"
    return {
        "id": str(value.get("id") or _new_profile_id()).strip(),
        "name": str(value.get("name") or "未命名配置").strip()[:60],
        "baseUrl": str(value.get("baseUrl") or value.get("base_url") or "").strip().rstrip("/"),
        "apiKey": str(value.get("apiKey") or value.get("api_key") or "").strip(),
        "model": str(value.get("model") or "").strip(),
        "template": template,
    }


def _new_profile_id() -> str:
    return f"profile_{secrets.token_hex(6)}"


def _duplicate_profile_name(profiles: list[dict[str, Any]], source_name: str) -> str:
    base_name = f"{source_name} 副本"
    existing_names = {str(item.get("name") or "") for item in profiles}
    if base_name not in existing_names:
        return base_name
    index = 2
    while f"{base_name} {index}" in existing_names:
        index += 1
    return f"{base_name} {index}"


def _save_model_profiles(store: dict[str, Any]) -> None:
    ensure_user_file_root()
    MODEL_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = Path(f"{MODEL_PROFILES_PATH}.tmp")
    temp_path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(MODEL_PROFILES_PATH)
