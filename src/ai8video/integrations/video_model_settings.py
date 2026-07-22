from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


VIDEO_MODEL_SETTINGS_DIR = (USER_FILE_ROOT / "视频模型").resolve()
VIDEO_MODEL_SETTINGS_PATH = VIDEO_MODEL_SETTINGS_DIR / "settings.json"


SUPPORTED_VIDEO_TEMPLATES = (
    "doubao-seedance",
    "yunwu-grok",
    "yunwu-omni",
    "yunwu-veo",
    "bailian-wan",
    "openai-compatible",
)

SUPPORTED_RATIOS = ("9:16", "16:9", "1:1")
SUPPORTED_RESOLUTION_MODES = ("ratio", "size")
SUPPORTED_PRESETS = ("custom", "fast", "quality")
SUPPORTED_SERVICE_TIERS = ("default", "flex")
SUPPORTED_SHOT_TYPES = ("single", "multi")
DOUBAO_DEFAULT_RESOLUTION_OPTIONS = ("480p", "720p", "1080p")
DOUBAO_FAST_RESOLUTION_OPTIONS = ("480p", "720p")
GENERIC_RESOLUTION_OPTIONS = ("480p", "720p", "1080p")
BAILIAN_WAN_DEFAULT_RESOLUTION_OPTIONS = ("480P", "720P", "1080P")

VIDEO_TEMPLATE_OPTIONS = (
    {"value": "doubao-seedance", "label": "豆包 Seedance", "description": "适合短视频批量生成，默认 480p。"},
    {"value": "yunwu-grok", "label": "云雾 Grok", "description": "Grok 视频兼容模板。"},
    {"value": "yunwu-omni", "label": "云雾 Omni", "description": "Omni 文生/图生视频兼容模板。"},
    {"value": "yunwu-veo", "label": "云雾 Veo", "description": "Veo 视频兼容模板。"},
    {"value": "bailian-wan", "label": "百炼 Wan", "description": "通义 Wan 视频兼容模板。"},
    {"value": "openai-compatible", "label": "OpenAI 兼容", "description": "通用 /v1/videos 兼容模板。"},
)


@dataclass(frozen=True)
class VideoModelSettings:
    base_url: str = ""
    api_key: str = ""
    model: str = "doubao-seedance-1-5-pro-251215"
    template: str = "doubao-seedance"
    seconds: int = 10
    resolution: str = "480p"
    resolution_mode: str = "ratio"
    provider: str = "openai-compatible"
    ratio: str = "9:16"
    preset: str = "custom"
    enhance_prompt: bool = True
    return_last_frame: bool = True
    watermark: bool = False
    video_count: int = 1
    generate_audio: bool = False
    service_tier: str = "default"
    execution_expires_after: int = 172800
    draft: bool = False
    camera_fixed: bool = False
    seed: int | None = None
    prompt_extend: bool = True
    shot_type: str = "multi"
    audio: bool = False
    audio_url: str = ""
    source: str = "default"

    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model and self.template in SUPPORTED_VIDEO_TEMPLATES)

    def public_dict(self, *, include_api_key: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        if not include_api_key:
            payload["api_key"] = ""
        payload["configured"] = self.configured()
        payload["templates"] = list(SUPPORTED_VIDEO_TEMPLATES)
        payload["templateOptions"] = list(VIDEO_TEMPLATE_OPTIONS)
        payload["ratios"] = list(SUPPORTED_RATIOS)
        payload["resolutionModes"] = list(SUPPORTED_RESOLUTION_MODES)
        payload["presets"] = list(SUPPORTED_PRESETS)
        payload["serviceTiers"] = list(SUPPORTED_SERVICE_TIERS)
        payload["shotTypes"] = list(SUPPORTED_SHOT_TYPES)
        payload["resolutionOptions"] = list(get_video_resolution_options(self.template, self.model))
        return payload


def load_video_model_settings(*, llm_base_url: str | None = None, llm_api_key: str | None = None) -> VideoModelSettings:
    env_settings = _load_from_env()
    file_settings = _load_from_file()
    source = "default"
    data: dict[str, Any] = {}

    if llm_base_url:
        data["base_url"] = llm_base_url
        source = _merge_source(source, "llm")
    if llm_api_key:
        data["api_key"] = llm_api_key
        source = _merge_source(source, "llm")

    if env_settings:
        data.update(env_settings)
        source = _merge_source(source, "env")
    if file_settings:
        data.update(file_settings)
        source = _merge_source(source, "user_file")

    settings = normalize_video_model_settings(data, source=source)
    if not settings.api_key and llm_api_key:
        settings = VideoModelSettings(**{**asdict(settings), "api_key": llm_api_key})
    return settings


def save_video_model_settings(payload: dict[str, Any]) -> VideoModelSettings:
    current = load_video_model_settings()
    user_payload = {
        "model": payload.get("model"),
        "template": payload.get("template"),
        "seconds": payload.get("seconds"),
        "resolution": payload.get("resolution"),
        "resolution_mode": payload.get("resolution_mode", payload.get("resolutionMode")),
        "ratio": payload.get("ratio"),
        "preset": payload.get("preset"),
        "enhance_prompt": payload.get("enhance_prompt", payload.get("enhancePrompt")),
        "return_last_frame": payload.get("return_last_frame", payload.get("returnLastFrame")),
        "video_count": payload.get("video_count", payload.get("videoCount")),
        "generate_audio": payload.get("generate_audio", payload.get("generateAudio")),
        "service_tier": payload.get("service_tier", payload.get("serviceTier")),
        "execution_expires_after": payload.get("execution_expires_after", payload.get("executionExpiresAfter")),
        "draft": payload.get("draft"),
        "camera_fixed": payload.get("camera_fixed", payload.get("cameraFixed")),
        "seed": payload.get("seed"),
        "prompt_extend": payload.get("prompt_extend", payload.get("promptExtend")),
        "shot_type": payload.get("shot_type", payload.get("shotType")),
        "audio": payload.get("audio"),
        "audio_url": payload.get("audio_url", payload.get("audioUrl")),
    }
    merged = {**asdict(current), **user_payload}
    merged.pop("source", None)
    settings = normalize_video_model_settings(merged, source="user_file")
    ensure_user_file_root()
    VIDEO_MODEL_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_MODEL_SETTINGS_PATH.write_text(
        json.dumps(_settings_file_payload(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return load_video_model_settings()


def normalize_video_model_settings(payload: dict[str, Any], *, source: str = "default") -> VideoModelSettings:
    template = str(payload.get("template") or "doubao-seedance").strip()
    if template not in SUPPORTED_VIDEO_TEMPLATES:
        template = "doubao-seedance"
    model = str(payload.get("model") or "doubao-seedance-1-5-pro-251215").strip()
    seconds = _positive_int(payload.get("seconds"), 10)
    seconds = min(max(seconds, 1), 60)
    ratio = str(payload.get("ratio") or "9:16").strip()
    if ratio not in SUPPORTED_RATIOS:
        ratio = "9:16"
    preset = str(payload.get("preset") or "custom").strip()
    if preset not in SUPPORTED_PRESETS:
        preset = "custom"
    resolution_mode = str(payload.get("resolution_mode") or payload.get("resolutionMode") or "ratio").strip()
    if resolution_mode not in SUPPORTED_RESOLUTION_MODES:
        resolution_mode = "ratio"
    service_tier = str(payload.get("service_tier") or payload.get("serviceTier") or "default").strip()
    if service_tier not in SUPPORTED_SERVICE_TIERS:
        service_tier = "default"
    shot_type = str(payload.get("shot_type") or payload.get("shotType") or "multi").strip()
    if shot_type not in SUPPORTED_SHOT_TYPES:
        shot_type = "multi"
    return VideoModelSettings(
        base_url=str(payload.get("base_url") or payload.get("baseUrl") or "").strip().rstrip("/"),
        api_key=str(payload.get("api_key") or payload.get("apiKey") or "").strip(),
        model=model,
        template=template,
        seconds=seconds,
        resolution=_normalize_resolution(payload.get("resolution"), template, model, resolution_mode=resolution_mode, ratio=ratio),
        resolution_mode=resolution_mode,
        provider=str(payload.get("provider") or "openai-compatible").strip() or "openai-compatible",
        ratio=ratio,
        preset=preset,
        enhance_prompt=_bool_value(payload.get("enhance_prompt", payload.get("enhancePrompt")), True),
        return_last_frame=_bool_value(payload.get("return_last_frame", payload.get("returnLastFrame")), True),
        watermark=False,
        video_count=min(max(_positive_int(payload.get("video_count", payload.get("videoCount")), 1), 1), 20),
        generate_audio=_bool_value(payload.get("generate_audio", payload.get("generateAudio")), False),
        service_tier=service_tier,
        execution_expires_after=min(max(_positive_int(payload.get("execution_expires_after", payload.get("executionExpiresAfter")), 172800), 3600), 259200),
        draft=_bool_value(payload.get("draft"), False),
        camera_fixed=_bool_value(payload.get("camera_fixed", payload.get("cameraFixed")), False),
        seed=_normalize_seed(payload.get("seed")),
        prompt_extend=_bool_value(payload.get("prompt_extend", payload.get("promptExtend")), True),
        shot_type=shot_type,
        audio=_bool_value(payload.get("audio"), False),
        audio_url=str(payload.get("audio_url") or payload.get("audioUrl") or "").strip(),
        source=source,
    )


def get_video_resolution_options(template: str, model: str = "") -> tuple[str, ...]:
    normalized_template = template if template in SUPPORTED_VIDEO_TEMPLATES else "doubao-seedance"
    model_text = str(model or "").strip()
    if normalized_template == "doubao-seedance":
        if "doubao-seedance-2-0-fast-260128" in model_text:
            return DOUBAO_FAST_RESOLUTION_OPTIONS
        return DOUBAO_DEFAULT_RESOLUTION_OPTIONS
    if normalized_template == "bailian-wan":
        return _bailian_wan_resolution_options(model_text)
    return GENERIC_RESOLUTION_OPTIONS


def _bailian_wan_resolution_options(model: str) -> tuple[str, ...]:
    normalized_model = str(model or "").strip()
    if normalized_model in {"wan2.6-i2v", "wan2.6-i2v-flash"}:
        return ("720P", "1080P")
    if normalized_model == "wan2.5-i2v-preview":
        return ("480P", "720P", "1080P")
    if normalized_model == "wan2.2-i2v-plus":
        return ("480P", "1080P")
    if normalized_model in {"wan2.2-i2v-flash", "wanx2.1-i2v-turbo"}:
        return ("480P", "720P")
    if normalized_model == "wanx2.1-i2v-plus":
        return ("720P",)
    return BAILIAN_WAN_DEFAULT_RESOLUTION_OPTIONS


def _normalize_resolution(
    value: Any,
    template: str,
    model: str,
    *,
    resolution_mode: str = "ratio",
    ratio: str = "9:16",
) -> str:
    if resolution_mode == "size":
        raw_size = str(value or "").strip().lower()
        if re.match(r"^\d{3,4}x\d{3,4}$", raw_size):
            return raw_size
        return _default_size_for_ratio(ratio)
    options = get_video_resolution_options(template, model)
    if template == "bailian-wan":
        raw = str(value or "").strip().upper()
        default = "480P"
    else:
        raw = str(value or "").strip().lower()
        default = "480p"
    if raw in options:
        return raw
    if default in options:
        return default
    return options[0]


def _default_size_for_ratio(ratio: str) -> str:
    if ratio == "16:9":
        return "720x480"
    if ratio == "1:1":
        return "720x720"
    return "480x720"


def _settings_file_payload(settings: VideoModelSettings) -> dict[str, Any]:
    return {
        "model": settings.model,
        "template": settings.template,
        "seconds": settings.seconds,
        "resolution": settings.resolution,
        "resolution_mode": settings.resolution_mode,
        "ratio": settings.ratio,
        "preset": settings.preset,
        "enhance_prompt": settings.enhance_prompt,
        "return_last_frame": settings.return_last_frame,
        "video_count": settings.video_count,
        "generate_audio": settings.generate_audio,
        "service_tier": settings.service_tier,
        "execution_expires_after": settings.execution_expires_after,
        "draft": settings.draft,
        "camera_fixed": settings.camera_fixed,
        "seed": settings.seed,
        "prompt_extend": settings.prompt_extend,
        "shot_type": settings.shot_type,
        "audio": settings.audio,
        "audio_url": settings.audio_url,
    }


def _load_from_file() -> dict[str, Any] | None:
    try:
        data = json.loads(VIDEO_MODEL_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    clean: dict[str, Any] = {}
    for key in (
        "model",
        "template",
        "seconds",
        "resolution",
        "resolution_mode",
        "ratio",
        "preset",
        "enhance_prompt",
        "return_last_frame",
        "video_count",
        "generate_audio",
        "service_tier",
        "execution_expires_after",
        "draft",
        "camera_fixed",
        "seed",
        "prompt_extend",
        "shot_type",
        "audio",
        "audio_url",
    ):
        if key in data:
            clean[key] = data.get(key)
    return clean


def _load_from_env() -> dict[str, Any] | None:
    data = {
        "base_url": os.getenv("AI8VIDEO_VIDEO_BASE_URL"),
        "api_key": os.getenv("AI8VIDEO_VIDEO_API_KEY"),
        "model": os.getenv("AI8VIDEO_VIDEO_MODEL"),
        "template": os.getenv("AI8VIDEO_VIDEO_TEMPLATE"),
        "seconds": os.getenv("AI8VIDEO_VIDEO_SECONDS"),
        "resolution": os.getenv("AI8VIDEO_VIDEO_RESOLUTION"),
        "resolution_mode": os.getenv("AI8VIDEO_VIDEO_RESOLUTION_MODE"),
        "ratio": os.getenv("AI8VIDEO_VIDEO_RATIO"),
        "preset": os.getenv("AI8VIDEO_VIDEO_PRESET"),
        "enhance_prompt": os.getenv("AI8VIDEO_VIDEO_ENHANCE_PROMPT"),
        "return_last_frame": os.getenv("AI8VIDEO_VIDEO_RETURN_LAST_FRAME"),
        "video_count": os.getenv("AI8VIDEO_VIDEO_COUNT"),
        "generate_audio": os.getenv("AI8VIDEO_VIDEO_GENERATE_AUDIO"),
        "service_tier": os.getenv("AI8VIDEO_VIDEO_SERVICE_TIER"),
        "execution_expires_after": os.getenv("AI8VIDEO_VIDEO_EXECUTION_EXPIRES_AFTER"),
        "draft": os.getenv("AI8VIDEO_VIDEO_DRAFT"),
        "camera_fixed": os.getenv("AI8VIDEO_VIDEO_CAMERA_FIXED"),
        "seed": os.getenv("AI8VIDEO_VIDEO_SEED"),
        "prompt_extend": os.getenv("AI8VIDEO_VIDEO_PROMPT_EXTEND"),
        "shot_type": os.getenv("AI8VIDEO_VIDEO_SHOT_TYPE"),
        "audio": os.getenv("AI8VIDEO_VIDEO_AUDIO"),
        "audio_url": os.getenv("AI8VIDEO_VIDEO_AUDIO_URL"),
    }
    clean = {key: value for key, value in data.items() if value not in (None, "")}
    return clean or None


def pull_video_model_catalog(settings: VideoModelSettings, *, timeout_seconds: int = 15) -> dict[str, Any]:
    return pull_model_catalog(
        base_url=settings.base_url,
        api_key=settings.api_key,
        provider=settings.provider,
        timeout_seconds=timeout_seconds,
        allowed_types={"video", "lipsync"},
        exclude_high_cost_video=True,
    )


def pull_model_catalog(
    *,
    base_url: str | None,
    api_key: str | None,
    provider: str = "openai-compatible",
    timeout_seconds: int = 15,
    allowed_types: set[str] | None = None,
    exclude_high_cost_video: bool = False,
) -> dict[str, Any]:
    if not base_url or not api_key:
        return {
            "ok": False,
            "models": [],
            "attempts": [],
            "error": "未配置接口地址或 API Key。",
        }

    attempts: list[dict[str, Any]] = []
    best_models: list[dict[str, Any]] = []
    auth_profiles = _auth_header_profiles(provider, api_key)
    for url in _compatible_probe_urls(base_url):
        for profile_name, headers in auth_profiles:
            try:
                res = requests.get(
                    url,
                    headers={"Accept": "application/json", **headers},
                    timeout=timeout_seconds,
                )
                body_text = res.text or ""
            except Exception as exc:
                attempts.append({"url": url, "note": f"[h={profile_name}] network:{str(exc)[:160]}"})
                continue
            if not res.ok:
                attempts.append({
                    "url": url,
                    "status": res.status_code,
                    "note": f"[h={profile_name}] {body_text[:180] or 'request_failed'}",
                })
                continue
            payload = _parse_json_lenient(body_text)
            if payload is None:
                attempts.append({"url": url, "status": res.status_code, "note": f"[h={profile_name}] invalid_json"})
                continue
            raw_models = _collect_model_list(payload)
            parsed = _normalize_model_records(raw_models, provider)
            attempts.append({
                "url": url,
                "status": res.status_code,
                "note": f"[h={profile_name}] ok:{len(parsed)}",
            })
            if parsed:
                best_models = parsed
                break
        if best_models:
            break

    visible_models = [
        item
        for item in best_models
        if (not allowed_types or item.get("type") in allowed_types)
        and not (exclude_high_cost_video and _looks_like_high_cost_video_model(item.get("modelId", "")))
    ]
    return {
        "ok": bool(visible_models),
        "models": visible_models,
        "attempts": attempts,
        "error": "" if visible_models else "没有从当前接口拉到可用模型。",
    }


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        return fallback
    return parsed if parsed > 0 else fallback


def _normalize_seed(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return min(max(parsed, 0), 4294967295)


def _bool_value(value: Any, fallback: bool) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "开启", "是"}:
        return True
    if text in {"0", "false", "no", "n", "off", "关闭", "否"}:
        return False
    return fallback


def _auth_header_profiles(provider: str, api_key: str) -> list[tuple[str, dict[str, str]]]:
    if provider == "gemini-compatible":
        return [
            ("x-goog-api-key", {"x-goog-api-key": api_key}),
            ("api-key", {"api-key": api_key, "x-api-key": api_key}),
            ("bearer", {"Authorization": f"Bearer {api_key}"}),
        ]
    return [
        ("bearer", {"Authorization": f"Bearer {api_key}"}),
        ("api-key", {"api-key": api_key, "x-api-key": api_key}),
    ]


def _compatible_probe_urls(base_url: str) -> list[str]:
    normalized = str(base_url or "").strip().rstrip("/")
    bases = [normalized]
    if normalized.endswith("/v1"):
        bases.append(normalized[:-3].rstrip("/"))
    else:
        bases.append(f"{normalized}/v1")
    urls: list[str] = []
    for base in dict.fromkeys(item for item in bases if item):
        for path in ("/models", "/models/list", "/model/list"):
            urls.append(urljoin(base + "/", path.lstrip("/")))
    return list(dict.fromkeys(urls))


def _parse_json_lenient(body_text: str) -> Any | None:
    raw = str(body_text or "").strip().lstrip("\ufeff")
    if not raw:
        return None
    for candidate in (raw, _slice_between(raw, "{", "}"), _slice_between(raw, "[", "]")):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def _slice_between(text: str, left: str, right: str) -> str:
    start = text.find(left)
    end = text.rfind(right)
    if start < 0 or end <= start:
        return ""
    return text[start : end + 1]


def _collect_model_list(payload: Any) -> list[Any]:
    queue: list[Any] = [payload]
    best: list[Any] = []
    while queue:
        current = queue.pop(0)
        if isinstance(current, list):
            if _score_model_list(current) > _score_model_list(best):
                best = current
            queue.extend(item for item in current if isinstance(item, (dict, list)))
            continue
        if not isinstance(current, dict):
            continue
        mapped = _parse_model_map(current)
        if _score_model_list(mapped) > _score_model_list(best):
            best = mapped
        for key, value in current.items():
            if key in {"data", "models", "model_list", "modelList", "list", "result"}:
                mapped = _parse_model_map(value)
                if _score_model_list(mapped) > _score_model_list(best):
                    best = mapped
            if isinstance(value, str) and value.strip().startswith(("{", "[")):
                embedded = _parse_json_lenient(value)
                if embedded is not None:
                    queue.append(embedded)
            if isinstance(value, (dict, list)):
                queue.append(value)
    return best


def _parse_model_map(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []
    if len(value) > 2000:
        return []
    mapped: list[Any] = []
    for key, item in value.items():
        key_id = str(key).strip()
        if not key_id:
            continue
        if isinstance(item, dict):
            model_id = _first_string(item, ("id", "model", "modelId", "model_name", "slug", "key")) or key_id
            if not _looks_like_model_key(model_id) and not _looks_like_model_record(item):
                continue
            mapped.append({**item, "id": model_id})
        elif isinstance(item, str) and item.strip():
            if not _looks_like_model_key(key_id):
                continue
            mapped.append({"id": key_id, "name": item.strip()})
        else:
            if not _looks_like_model_key(key_id):
                continue
            mapped.append({"id": key_id, "name": key_id})
    return mapped if _score_model_list(mapped) > 0 else []


def _score_model_list(items: list[Any]) -> int:
    score = 0
    for item in items:
        if isinstance(item, str) and item.strip():
            score += 3
            continue
        if not isinstance(item, dict):
            continue
        if _first_string(item, ("id", "model", "modelId", "model_name", "slug", "key")):
            score += 3
        if _first_string(item, ("name", "display_name", "displayName", "label")):
            score += 1
    return score


def _normalize_model_records(raw_models: list[Any], provider: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_models:
        if isinstance(raw, str):
            model_id = raw.strip()
            name = model_id
            record: dict[str, Any] = {"id": model_id}
        elif isinstance(raw, dict):
            record = raw
            model_id = _first_string(record, ("id", "model", "modelId", "model_name", "slug", "key", "name"))
            name = _first_string(record, ("name", "display_name", "displayName", "label")) or model_id
        else:
            continue
        if not model_id:
            continue
        model_key = f"{provider}::{model_id}" if provider else model_id
        if model_key in seen:
            continue
        seen.add(model_key)
        model_type = _infer_model_type(record, model_id)
        normalized.append({
            "modelId": model_id,
            "modelKey": model_key,
            "name": name or model_id,
            "type": model_type,
            "provider": provider,
            "price": 0,
        })
    return normalized


def _first_string(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _looks_like_model_record(record: dict[str, Any]) -> bool:
    if _first_string(record, ("id", "model", "modelId", "model_name", "slug", "key")):
        return True
    model_type = _first_string(record, ("type", "model_type", "family", "category"))
    return bool(model_type and model_type.lower() in {"video", "image", "llm", "audio", "lipsync"})


def _looks_like_model_key(value: str) -> bool:
    text = str(value or "").strip().lower()
    if len(text) < 2 or len(text) > 160:
        return False
    if text in {"data", "list", "result", "models", "model", "id", "name", "object", "error", "message", "code", "success", "status"}:
        return False
    if any(token in text for token in ("video", "image", "img", "banana", "veo", "wan", "seedance", "seedream", "kling", "vidu", "hailuo", "sora", "grok", "omni")):
        return True
    return any(char in text for char in (".", "_", "-", ":", "/")) or any(char.isdigit() for char in text)


def _infer_model_type(record: dict[str, Any], model_id: str) -> str:
    values = [model_id.lower()]
    for key in ("type", "object", "model_type", "family", "category"):
        value = record.get(key)
        if isinstance(value, str):
            values.append(value.lower())
    for key in ("modalities", "input_modalities", "output_modalities"):
        value = record.get(key)
        if isinstance(value, list):
            values.extend(str(item).lower() for item in value if isinstance(item, str))
    signal = " ".join(values)
    if any(token in signal for token in ("lipsync", "lip-sync", "retalk", "唇形", "对口型")):
        return "lipsync"
    if any(token in signal for token in ("video", "视频", "i2v", "t2v", "kling", "veo", "hailuo", "vidu", "seedance", "sora", "wan")):
        return "video"
    if any(token in signal for token in ("audio", "音频", "voice", "tts", "asr", "whisper", "music")):
        return "audio"
    if any(token in signal for token in ("image", "img", "图像", "图片", "vision", "flux", "seedream", "banana")):
        return "image"
    return "llm"


def _looks_like_high_cost_video_model(model_id: str) -> bool:
    text = str(model_id or "").lower()
    return any(token in text for token in ("4k", "1080", "720p", "1080p"))


def _merge_source(current: str, addition: str) -> str:
    if current in {"", "default", "missing"}:
        return addition
    if current == addition or addition in current.split("+"):
        return current
    return f"{current}+{addition}"
