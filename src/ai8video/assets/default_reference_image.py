from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root
from ai8video.assets.user_materials import list_user_materials


DEFAULT_REFERENCE_IMAGE_DIR = (USER_FILE_ROOT / "参考图").resolve()
DEFAULT_REFERENCE_IMAGE_SETTINGS_PATH = DEFAULT_REFERENCE_IMAGE_DIR / "settings.json"
REFERENCE_IMAGE_EFFECT_DEFINITIONS = (
    {
        "key": "autoChangeClothes",
        "label": "自动换衣",
        "prompt": "衣服必须和原参考图完全不同，根据用户任务和剧情重新设计服装，同时保持人物身份、脸部特征和整体气质一致",
    },
    {
        "key": "autoChangeBackground",
        "label": "自动换背景",
        "prompt": "背景必须和原参考图完全不同，根据用户任务和剧情重新生成背景环境，保持画面真实、干净、适合短视频首帧",
    },
    {
        "key": "autoChangePose",
        "label": "自动换姿势",
        "prompt": "人物姿势必须和原参考图完全不同，根据用户任务和剧情重新调整姿势和动作，保持自然可信",
    },
)


def default_reference_image_status() -> dict[str, Any]:
    item = load_default_reference_image()
    data = _read_settings()
    return {
        "ok": True,
        "enabled": bool(item),
        "item": item,
        "options": _normalize_options(data.get("options")),
        "customPrompt": _normalize_custom_prompt(data.get("customPrompt")),
        "effectDefinitions": reference_image_effect_definitions(),
    }


def load_default_reference_image() -> dict[str, Any] | None:
    data = _read_settings()
    relative_path = str(data.get("relativePath") or "").strip()
    if not relative_path:
        return None
    return _find_image_material(relative_path)


def default_reference_image_path() -> str | None:
    item = load_default_reference_image()
    if not item:
        return None
    path = str(item.get("path") or "").strip()
    return path or None


def default_reference_image_instruction() -> str | None:
    data = _read_settings()
    return build_reference_image_instruction(data.get("options"), data.get("customPrompt"))


def default_reference_image_options() -> dict[str, bool]:
    data = _read_settings()
    return _normalize_options(data.get("options"))


def default_reference_image_custom_prompt() -> str | None:
    data = _read_settings()
    custom_prompt = _normalize_custom_prompt(data.get("customPrompt"))
    return custom_prompt or None


def enabled_default_reference_image_options() -> dict[str, bool] | None:
    options = default_reference_image_options()
    return options if any(options.values()) else None


def reference_image_effect_definitions() -> list[dict[str, str]]:
    return [dict(item) for item in REFERENCE_IMAGE_EFFECT_DEFINITIONS]


def build_reference_image_instruction(options: Any = None, custom_prompt: Any = None) -> str | None:
    normalized_options = _normalize_options(options)
    lines = [effect["prompt"] for effect in reference_image_effect_definitions() if normalized_options.get(effect["key"])]
    normalized_custom_prompt = _normalize_custom_prompt(custom_prompt)
    if normalized_custom_prompt:
        lines.append(f"补充要求：{normalized_custom_prompt}")
    if not lines:
        return None
    return "参考图设定：" + "；".join(lines) + "。"


def select_default_reference_image(relative_path: str) -> dict[str, Any]:
    item = _find_image_material(relative_path)
    if not item:
        raise ValueError("参考图不在图片素材库里")
    data = _read_settings()
    data["relativePath"] = item.get("relativePath") or item.get("name") or ""
    data["name"] = item.get("name") or ""
    data["options"] = _normalize_options(data.get("options"))
    _write_settings(data)
    return default_reference_image_status()


def clear_default_reference_image() -> dict[str, Any]:
    data = _read_settings()
    options = _normalize_options(data.get("options"))
    custom_prompt = _normalize_custom_prompt(data.get("customPrompt"))
    if any(options.values()) or custom_prompt:
        next_data: dict[str, Any] = {"options": options}
        if custom_prompt:
            next_data["customPrompt"] = custom_prompt
        _write_settings(next_data)
    else:
        DEFAULT_REFERENCE_IMAGE_SETTINGS_PATH.unlink(missing_ok=True)
    return default_reference_image_status()


def update_default_reference_image_options(options: dict[str, Any], custom_prompt: str | None = None) -> dict[str, Any]:
    data = _read_settings()
    data["options"] = _normalize_options(options)
    normalized_custom_prompt = _normalize_custom_prompt(
        data.get("customPrompt") if custom_prompt is None else custom_prompt
    )
    if normalized_custom_prompt:
        data["customPrompt"] = normalized_custom_prompt
    else:
        data.pop("customPrompt", None)
    if (
        not str(data.get("relativePath") or "").strip()
        and not any(data["options"].values())
        and not normalized_custom_prompt
    ):
        DEFAULT_REFERENCE_IMAGE_SETTINGS_PATH.unlink(missing_ok=True)
        return default_reference_image_status()
    _write_settings(data)
    return default_reference_image_status()


def _read_settings() -> dict[str, Any]:
    try:
        data = json.loads(DEFAULT_REFERENCE_IMAGE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(data: dict[str, Any]) -> None:
    ensure_user_file_root()
    DEFAULT_REFERENCE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_REFERENCE_IMAGE_SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalize_options(value: Any) -> dict[str, bool]:
    source = value if isinstance(value, dict) else {}
    return {effect["key"]: bool(source.get(effect["key"])) for effect in reference_image_effect_definitions()}


def _normalize_custom_prompt(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def _find_image_material(relative_path: str) -> dict[str, Any] | None:
    target = str(relative_path or "").strip()
    if not target:
        return None
    for item in list_user_materials().get("images") or []:
        if target in {
            str(item.get("relativePath") or ""),
            str(item.get("name") or ""),
            str(item.get("path") or ""),
        }:
            return item
    return None
