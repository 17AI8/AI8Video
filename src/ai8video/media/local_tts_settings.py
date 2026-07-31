from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOCAL_TTS_DIR_NAME = "TTS"
LEGACY_LOCAL_TTS_DIR_NAME = "本地TTS"
LOCAL_TTS_OUTPUT_DIR_NAME = "输出"
LOCAL_TTS_SETTINGS_NAME = "settings.json"
DEFAULT_LOCAL_TTS_ENABLED = False
DEFAULT_LOCAL_TTS_ENGINE = "mimo-api"
DEFAULT_LOCAL_TTS_VOICE = ""
DEFAULT_LOCAL_TTS_RATE = 185
DEFAULT_LOCAL_TTS_VOLUME = 1.0
MAX_LOCAL_TTS_VOLUME = 4.0
DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME = 0.18
DEFAULT_MIMO_API_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MIMO_API_MODEL = "mimo-v2.5-tts"
DEFAULT_MIMO_API_CLONE_MODEL = "mimo-v2.5-tts-voiceclone"
DEFAULT_MIMO_API_VOICE = "冰糖"
LOCAL_TTS_DURATION_FIT_TOLERANCE_SECONDS = 0.35
LOCAL_TTS_END_GUARD_SECONDS = 1.0
LOCAL_TTS_LOUDNESS_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"
LOCAL_TTS_CLONE_LIBRARY_DIR_NAME = "音色克隆"
LEGACY_LOCAL_TTS_CLONE_LIBRARY_DIR_NAME = "音色复刻"
LOCAL_TTS_CLONE_AUDIO_EXTENSIONS = {".mp3", ".wav"}
LOCAL_TTS_CLONE_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
LOCAL_TTS_CLONE_STORAGE_EXTENSION = ".wav"
LOCAL_TTS_CLONE_MAX_SECONDS = 45
LOCAL_TTS_CLONE_DATA_URI_MAX_BYTES = 10 * 1024 * 1024
LOCAL_TTS_CLONE_AUDIO_FILTER = (
    "silenceremove=start_periods=1:start_duration=0.2:start_threshold=-45dB:"
    "stop_periods=-1:stop_duration=0.6:stop_threshold=-45dB,"
    "loudnorm=I=-18:TP=-2:LRA=11"
)
MIMO_API_PRESET_VOICE_OPTIONS = [
    {
        "value": "mimo_default",
        "label": "MiMo-默认（中国集群默认冰糖）",
        "language": "自适应",
        "gender": "默认",
    },
    {"value": "冰糖", "label": "冰糖", "language": "中文", "gender": "女性"},
    {"value": "茉莉", "label": "茉莉", "language": "中文", "gender": "女性"},
    {"value": "苏打", "label": "苏打", "language": "中文", "gender": "男性"},
    {"value": "白桦", "label": "白桦", "language": "中文", "gender": "男性"},
    {"value": "Mia", "label": "Mia", "language": "英文", "gender": "女性"},
    {"value": "Chloe", "label": "Chloe", "language": "英文", "gender": "女性"},
    {"value": "Milo", "label": "Milo", "language": "英文", "gender": "男性"},
    {"value": "Dean", "label": "Dean", "language": "英文", "gender": "男性"},
]


def migrate_legacy_local_tts_dir(target: Path, legacy: Path, *, configured: bool) -> None:
    if configured or target.exists() or not legacy.exists() or legacy == target:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(legacy), str(target))


def migrate_legacy_voice_clone_dir(root: Path) -> None:
    legacy = root / LEGACY_LOCAL_TTS_CLONE_LIBRARY_DIR_NAME
    target = root / LOCAL_TTS_CLONE_LIBRARY_DIR_NAME
    if not legacy.exists() or legacy == target:
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in legacy.iterdir():
        if not item.is_file():
            continue
        destination = target / item.name
        if destination.exists():
            destination = next_available_path(target, item.name)
        shutil.move(str(item), str(destination))
    try:
        legacy.rmdir()
    except OSError:
        pass


def read_local_tts_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_local_tts_settings(path: Path, payload: dict[str, Any], clone_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = normalize_settings(payload, clone_dir)
    data["updatedAt"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_settings(payload: dict[str, Any], clone_dir: Path) -> dict[str, Any]:
    data = dict(payload)
    data["engine"] = clean_engine(data.get("engine"))
    data["modelDir"] = ""
    data["enabled"] = clean_bool(data.get("enabled"), DEFAULT_LOCAL_TTS_ENABLED)
    data["apiBaseUrl"] = clean_mimo_api_base_url(data.get("apiBaseUrl"))
    data["apiKey"] = clean_secret_text(data.get("apiKey"))
    data["model"] = clean_mimo_model(data.get("model"))
    data["cloneModel"] = clean_mimo_clone_model(data.get("cloneModel"))
    data.pop("stylePrompt", None)
    data.pop("audioTag", None)
    data["voice"] = clean_voice_selection(data.get("voice"), clone_dir)
    data["rate"] = clean_int(data.get("rate"), DEFAULT_LOCAL_TTS_RATE, 80, 360)
    data["volume"] = clean_float(data.get("volume"), DEFAULT_LOCAL_TTS_VOLUME, 0.0, MAX_LOCAL_TTS_VOLUME)
    data["originalAudioVolume"] = clean_float(
        data.get("originalAudioVolume"),
        DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME,
        0.0,
        1.0,
    )
    return data


def update_settings_values(current: dict[str, Any], payload: dict[str, Any], clone_dir: Path) -> dict[str, Any]:
    updated = dict(current)
    cleaners = {
        "enabled": lambda value: clean_bool(value, DEFAULT_LOCAL_TTS_ENABLED),
        "engine": clean_engine,
        "apiBaseUrl": clean_mimo_api_base_url,
        "apiKey": clean_secret_text,
        "model": clean_mimo_model,
        "cloneModel": clean_mimo_clone_model,
        "rate": lambda value: clean_int(value, DEFAULT_LOCAL_TTS_RATE, 80, 360),
        "volume": lambda value: clean_float(value, DEFAULT_LOCAL_TTS_VOLUME, 0.0, MAX_LOCAL_TTS_VOLUME),
        "originalAudioVolume": lambda value: clean_float(
            value,
            DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME,
            0.0,
            1.0,
        ),
    }
    for key, cleaner in cleaners.items():
        if key in payload:
            updated[key] = cleaner(payload.get(key))
    if "modelDir" in payload:
        updated["modelDir"] = ""
    if any(key in payload for key in ("voice", "engine")):
        updated["voice"] = clean_voice_selection(payload.get("voice", updated.get("voice")), clone_dir)
    updated.pop("stylePrompt", None)
    updated.pop("audioTag", None)
    return updated


def build_local_tts_status(settings: dict[str, Any], output_dir: Path, clone_dir: Path) -> dict[str, Any]:
    clone_items = voice_clone_items(clone_dir)
    voice_options = mimo_voice_options(clone_items)
    voice = clean_voice_selection(settings.get("voice"), clone_dir)
    available = engine_available(settings, clone_dir)
    output_stats = folder_stats(output_dir)
    status = _base_status_values(settings, voice, voice_options)
    status.update({
        "voiceCloneCount": len(clone_items),
        "voiceCloneItems": clone_items,
        "voiceCloneDir": str(clone_dir),
        "available": available["available"],
        "availabilityReason": available["reason"],
        "modelDir": "",
        "modelAvailable": True,
        "modelReason": "MiMo API 不需要本地模型",
        "outputDir": str(output_dir),
        "outputFileCount": output_stats["fileCount"],
        "outputSizeBytes": output_stats["sizeBytes"],
        "outputSizeDisplay": output_stats["display"],
    })
    return status


def _base_status_values(
    settings: dict[str, Any],
    voice: str,
    voice_options: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "ok": True,
        "enabled": clean_bool(settings.get("enabled"), DEFAULT_LOCAL_TTS_ENABLED),
        "engine": clean_engine(settings.get("engine")),
        "apiBaseUrl": clean_mimo_api_base_url(settings.get("apiBaseUrl")),
        "apiKey": clean_secret_text(settings.get("apiKey")),
        "model": clean_mimo_model(settings.get("model")),
        "cloneModel": clean_mimo_clone_model(settings.get("cloneModel")),
        "voice": voice,
        "voiceLabel": voice_label(voice, voice_options),
        "voiceCount": len(voice_options),
        "voiceOptions": voice_options,
        "rate": clean_int(settings.get("rate"), DEFAULT_LOCAL_TTS_RATE, 80, 360),
        "volume": clean_float(settings.get("volume"), DEFAULT_LOCAL_TTS_VOLUME, 0.0, MAX_LOCAL_TTS_VOLUME),
        "originalAudioVolume": clean_float(
            settings.get("originalAudioVolume"),
            DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME,
            0.0,
            1.0,
        ),
    }


def engine_available(settings: dict[str, Any], clone_dir: Path) -> dict[str, Any]:
    api_key = clean_secret_text(settings.get("apiKey"))
    voice = clean_voice_selection(settings.get("voice"), clone_dir)
    if not api_key:
        return {"available": False, "reason": "MiMo API Key 未配置"}
    if is_voice_clone_selection(voice):
        if voice_clone_sample_path(voice, clone_dir) is None:
            return {"available": False, "reason": "已选择克隆音色，但样本文件不存在"}
        model = clean_mimo_clone_model(settings.get("cloneModel"))
        return {"available": True, "reason": f"MiMo API · {model}"}
    model = clean_mimo_model(settings.get("model"))
    return {"available": True, "reason": f"MiMo API · {model}"}


def voice_clone_items(directory: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not directory.is_dir():
        return items
    for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() not in LOCAL_TTS_CLONE_AUDIO_EXTENSIONS:
            continue
        stat = path.stat()
        items.append({
            "id": path.name,
            "name": path.name,
            "label": f"克隆 · {path.stem}",
            "path": str(path),
            "sizeBytes": stat.st_size,
            "updatedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    return items


def voice_clone_value(item_id: str) -> str:
    return f"clone:{item_id}"


def is_voice_clone_selection(value: Any) -> bool:
    return str(value or "").strip().startswith("clone:")


def voice_clone_item_id(value: Any) -> str:
    text = str(value or "").strip()
    return text.split(":", 1)[1].strip() if text.startswith("clone:") else ""


def voice_clone_sample_path(value: Any, directory: Path) -> Path | None:
    item_id = voice_clone_item_id(value)
    if not item_id:
        return None
    target = (directory / Path(item_id).name).resolve()
    try:
        target.relative_to(directory.resolve())
    except ValueError:
        return None
    if not target.is_file() or target.suffix.lower() not in LOCAL_TTS_CLONE_AUDIO_EXTENSIONS:
        return None
    return target


def voice_clone_cache_signature(value: Any, directory: Path) -> str:
    sample_path = voice_clone_sample_path(value, directory)
    if sample_path is None:
        return ""
    try:
        stat = sample_path.stat()
    except OSError:
        return ""
    return f"{sample_path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def next_available_path(directory: Path, filename: str) -> Path:
    safe_name = Path(str(filename or "")).name
    candidate = directory / safe_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 1000):
        numbered = directory / f"{stem}-{index}{suffix}"
        if not numbered.exists():
            return numbered
    raise RuntimeError("音色克隆文件名重复过多，请先清理文件夹")


def mimo_voice_options(clone_items: list[dict[str, Any]]) -> list[dict[str, str]]:
    clone_options = [
        {
            "value": voice_clone_value(str(item.get("id") or "")),
            "label": str(item.get("label") or item.get("name") or "克隆音色"),
        }
        for item in clone_items
        if str(item.get("id") or "").strip()
    ]
    return [dict(option) for option in MIMO_API_PRESET_VOICE_OPTIONS] + clone_options


def clean_voice_selection(value: Any, clone_dir: Path) -> str:
    text = str(value or "").strip()
    if is_voice_clone_selection(text):
        return text if voice_clone_sample_path(text, clone_dir) is not None else DEFAULT_MIMO_API_VOICE
    return text or DEFAULT_MIMO_API_VOICE


def voice_label(voice: str, options: list[dict[str, str]]) -> str:
    selected = str(voice or "").strip()
    for option in options:
        if str(option.get("value") or "").strip() == selected:
            return str(option.get("label") or selected)
    return selected or DEFAULT_LOCAL_TTS_VOICE


def clean_engine(value: Any) -> str:
    text = str(value or DEFAULT_LOCAL_TTS_ENGINE).strip().lower()
    aliases = {
        "mimo": "mimo-api",
        "mimo-api": "mimo-api",
        "mimo-v2.5-tts": "mimo-api",
        "sherpa-onnx": "mimo-api",
        "system": "mimo-api",
    }
    return aliases.get(text, DEFAULT_LOCAL_TTS_ENGINE)


def clean_mimo_clone_model(value: Any) -> str:
    return str(value or "").strip() or DEFAULT_MIMO_API_CLONE_MODEL


def clean_mimo_api_base_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return DEFAULT_MIMO_API_BASE_URL
    if text.endswith("/chat/completions"):
        text = text[: -len("/chat/completions")]
    return text or DEFAULT_MIMO_API_BASE_URL


def clean_mimo_model(value: Any) -> str:
    return str(value or "").strip() or DEFAULT_MIMO_API_MODEL


def clean_secret_text(value: Any) -> str:
    return str(value or "").strip()


def clean_tts_duration_seconds(value: Any) -> float | None:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if duration > 0 else None


def clean_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "开启"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "关闭"}:
        return False
    return default


def clean_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(float(str(value)))
    except Exception:
        return default
    return max(minimum, min(maximum, number))


def clean_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(str(value))
    except Exception:
        return default
    return round(max(minimum, min(maximum, number)), 2)


def format_volume(value: Any, default: float) -> str:
    return f"{clean_float(value, default, 0.0, MAX_LOCAL_TTS_VOLUME):.2f}".rstrip("0").rstrip(".")


def safe_file_part(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z._-]+", "-", str(value or "").strip())
    return (text or "local")[:60].strip("-") or "local"


def folder_stats(path: Path) -> dict[str, int | str]:
    path.mkdir(parents=True, exist_ok=True)
    files = [item for item in path.iterdir() if item.is_file()]
    size = 0
    for item in files:
        try:
            size += item.stat().st_size
        except OSError:
            pass
    return {"fileCount": len(files), "sizeBytes": size, "display": format_bytes(size)}


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(0, size))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
