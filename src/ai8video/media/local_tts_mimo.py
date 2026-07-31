from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from ai8video.media.local_tts_settings import (
    DEFAULT_LOCAL_TTS_RATE,
    LOCAL_TTS_CLONE_DATA_URI_MAX_BYTES,
    clean_int,
    clean_mimo_api_base_url,
    clean_mimo_clone_model,
    clean_mimo_model,
    clean_secret_text,
    clean_tts_duration_seconds,
    is_voice_clone_selection,
)


VoiceSampleResolver = Callable[[Any], Path | None]


def synthesize_with_mimo_api(
    text: str,
    output_path: Path,
    settings: dict[str, Any],
    *,
    voice_sample_resolver: VoiceSampleResolver,
) -> None:
    api_key = clean_secret_text(settings.get("apiKey"))
    if not api_key:
        raise RuntimeError("未配置 MiMo API Key")
    model, voice_payload = _resolve_voice_payload(settings, voice_sample_resolver)
    payload = _build_request_payload(text, settings, model, voice_payload)
    response = _post_mimo_request(clean_mimo_api_base_url(settings.get("apiBaseUrl")), api_key, payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_decode_audio_response(response))


def _resolve_voice_payload(
    settings: dict[str, Any],
    voice_sample_resolver: VoiceSampleResolver,
) -> tuple[str, str]:
    voice = str(settings.get("voice") or "").strip()
    if not is_voice_clone_selection(voice):
        return clean_mimo_model(settings.get("model")), voice
    sample_path = voice_sample_resolver(voice)
    if sample_path is None:
        raise RuntimeError("所选克隆音色样本不存在")
    mime_type = "audio/wav" if sample_path.suffix.lower() == ".wav" else "audio/mpeg"
    sample_b64 = base64.b64encode(sample_path.read_bytes()).decode("ascii")
    voice_payload = f"data:{mime_type};base64,{sample_b64}"
    if len(voice_payload.encode("utf-8")) > LOCAL_TTS_CLONE_DATA_URI_MAX_BYTES:
        raise RuntimeError("音色克隆样本过大，请缩短到更短的人声片段后重试")
    return clean_mimo_clone_model(settings.get("cloneModel")), voice_payload


def _build_request_payload(
    text: str,
    settings: dict[str, Any],
    model: str,
    voice_payload: str,
) -> dict[str, Any]:
    return {
        "model": model,
        "audio": {"voice": voice_payload, "format": "wav"},
        "messages": [
            {"role": "user", "content": mimo_tts_user_instruction(settings)},
            {"role": "assistant", "content": str(text or "").strip()},
        ],
    }


def _post_mimo_request(base_url: str, api_key: str, payload: dict[str, Any]) -> requests.Response:
    try:
        response = requests.post(
            resolve_mimo_chat_completions_url(base_url),
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=180,
        )
    except requests.Timeout as exc:
        raise RuntimeError("MiMo TTS 请求超时，请稍后重试") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"MiMo TTS 请求失败：{exc}") from exc
    if not response.ok:
        raise RuntimeError(format_http_response_error(response, "MiMo TTS"))
    return response


def _decode_audio_response(response: requests.Response) -> bytes:
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError("MiMo TTS 返回了无法解析的 JSON") from exc
    audio_b64 = extract_mimo_audio_data(body)
    if not audio_b64:
        body_text = truncate_text(json.dumps(body, ensure_ascii=False), 400)
        raise RuntimeError(f"MiMo TTS 响应缺少音频数据：{body_text}")
    try:
        return base64.b64decode(audio_b64)
    except Exception as exc:
        raise RuntimeError("MiMo TTS 音频解码失败") from exc


def mimo_tts_user_instruction(settings: dict[str, Any]) -> str:
    rate = clean_int(settings.get("rate"), DEFAULT_LOCAL_TTS_RATE, 80, 360)
    target_duration = clean_tts_duration_seconds(settings.get("targetDurationSeconds"))
    video_duration = clean_tts_duration_seconds(settings.get("videoDurationSeconds"))
    lines = [
        "请只朗读 assistant 消息中的文本，不要朗读本条指令。",
        "不要省略、改写或新增 assistant 文本。",
        f"语速设置基准为 {DEFAULT_LOCAL_TTS_RATE}，当前为 {rate}，请按这个相对快慢处理。",
    ]
    if settings.get("durationAutoSpeed") and target_duration:
        duration_instruction = (
            f"请根据目标时长自动调整语速，整段音频必须在 {target_duration:.2f} 秒内自然读完，"
            "收尾留一点余量。"
        )
        lines.insert(1, duration_instruction)
        if video_duration:
            lines.insert(2, f"对应视频实际时长约 {video_duration:.2f} 秒。")
    else:
        lines.insert(1, "请用自然清晰的短视频口播语气朗读。")
    return "\n".join(lines)


def resolve_mimo_chat_completions_url(base_url: str) -> str:
    base = clean_mimo_api_base_url(base_url)
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def extract_mimo_audio_data(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        data = _audio_data_from_choice(choices[0])
        if data:
            return data
    return _audio_data_from_container(payload)


def _audio_data_from_choice(choice: Any) -> str:
    if not isinstance(choice, dict):
        return ""
    message = choice.get("message")
    if isinstance(message, dict):
        data = _audio_data_from_container(message)
        if data:
            return data
    return _audio_data_from_container(choice)


def _audio_data_from_container(container: dict[str, Any]) -> str:
    audio = container.get("audio")
    if not isinstance(audio, dict):
        return ""
    data = audio.get("data")
    return data.strip() if isinstance(data, str) and data.strip() else ""


def format_http_response_error(response: requests.Response, action: str) -> str:
    status = f"{response.status_code} {response.reason or ''}".strip()
    body = http_response_excerpt(response)
    message = f"{action}失败：HTTP {status}"
    return f"{message}，上游返回：{body}" if body else message


def http_response_excerpt(response: requests.Response, limit: int = 500) -> str:
    try:
        text = json.dumps(response.json(), ensure_ascii=False)
    except ValueError:
        text = response.text or ""
    text = re.sub(r"(?i)(api[_-]?key\\s*[:=]\\s*)[^\\s,;]+", r"\\1***", text)
    return truncate_text(" ".join(text.split()), limit)


def truncate_text(text: str, limit: int) -> str:
    raw = str(text or "").strip()
    return raw if len(raw) <= limit else raw[:limit] + "..."
