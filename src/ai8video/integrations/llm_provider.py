from __future__ import annotations

import json
import re
from collections.abc import Callable

import requests

from ai8video.core.config import AI8VideoConfig
from ai8video.integrations.http_client import api_request


def build_openai_compat_llm(
    config: AI8VideoConfig,
    *,
    timeout_seconds: int | None = None,
    system_prompt: str = "你是严格遵守 JSON 输出要求的短视频规划模型。",
    stream: bool = True,
    transport_retry_count: int = 0,
    on_delta: Callable[[str], None] | None = None,
):
    if not config.has_llm():
        return None

    def _call(prompt: str) -> str:
        for attempt in range(max(0, transport_retry_count) + 1):
            try:
                return _request_completion(config, prompt, system_prompt, stream, timeout_seconds, on_delta)
            except requests.RequestException as exc:
                if attempt >= transport_retry_count or not _should_retry_transport_error(exc):
                    raise RuntimeError(_transport_error_message(exc, attempt)) from exc
        raise RuntimeError("文本模型请求未执行")

    return _call


def _request_completion(
    config: AI8VideoConfig,
    prompt: str,
    system_prompt: str,
    stream: bool,
    timeout_seconds: int | None,
    on_delta: Callable[[str], None] | None,
) -> str:
    response = api_request(
        "POST",
        normalize_chat_completions_url(config.llm_base_url or ""),
        headers={
            "Authorization": f"Bearer {config.llm_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": stream,
            "temperature": 0.2,
        },
        stream=stream,
        timeout=timeout_seconds or config.timeout_seconds,
    )
    _raise_for_llm_http_error(response)
    if stream and _is_event_stream_response(response):
        return _read_openai_chat_stream(response, on_delta=on_delta)
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM response missing choices: {data}")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    raise RuntimeError(f"LLM response missing text content: {data}")


def _transport_error_message(exc: Exception, retries_completed: int) -> str:
    attempts = retries_completed + 1
    prefix = "文本模型连接中断"
    if retries_completed:
        prefix += f"，已自动重试 {retries_completed} 次"
    return f"{prefix}（共 {attempts} 次请求）：{str(exc).strip() or exc.__class__.__name__}"


def _should_retry_transport_error(exc: requests.RequestException) -> bool:
    return not isinstance(exc, requests.exceptions.Timeout)


def _raise_for_llm_http_error(response) -> None:
    if response.status_code < 400:
        return
    detail = _extract_llm_error_detail(response)
    if detail:
        raise RuntimeError(detail)
    response.raise_for_status()


def _extract_llm_error_detail(response) -> str:
    status_code = getattr(response, "status_code", None)
    body = _safe_response_text(response)
    if not body:
        return ""
    payload = _parse_json_error_payload(body)
    message = str(payload.get("message") or "").strip()
    code = str(payload.get("code") or "").strip()
    if code == "pre_consume_token_quota_failed" or "quota is not enough" in message.lower():
        readable = _format_quota_error_message(message)
        if readable:
            return readable
        return "文本模型额度不足，请充值或切换到有余额的文本模型密钥后重试。"
    if message:
        return f"文本模型请求失败（HTTP {status_code}）：{message}"
    preview = body.strip().replace("\n", " ")[:500]
    return f"文本模型请求失败（HTTP {status_code}）：{preview}"


def _safe_response_text(response) -> str:
    try:
        return str(response.text or "")
    except Exception:
        return ""


def _parse_json_error_payload(body: str) -> dict[str, object]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    error = data.get("error")
    if isinstance(error, dict):
        return error
    return data


def _format_quota_error_message(message: str) -> str:
    remain = _match_quota_amount(message, r"remain quota:\s*([^,，]+)")
    need = _match_quota_amount(message, r"need quota:\s*([^()，,]+)")
    request_id = _match_quota_amount(message, r"request id:\s*([^()，,\s]+)")
    parts = ["文本模型额度不足"]
    if remain:
        parts.append(f"剩余额度 {remain}")
    if need:
        parts.append(f"本次预扣需要 {need}")
    text = "，".join(parts) + "。请充值或切换到有余额的文本模型密钥后重试。"
    if request_id:
        text += f" request id: {request_id}"
    return text


def _match_quota_amount(message: str, pattern: str) -> str:
    match = re.search(pattern, message, flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def _is_event_stream_response(response) -> bool:
    content_type = str(response.headers.get("Content-Type") or "").lower()
    return "text/event-stream" in content_type


def _read_openai_chat_stream(response, *, on_delta: Callable[[str], None] | None = None) -> str:
    chunks: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=False):
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace").strip()
        else:
            line = str(raw_line or "").strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = data.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            chunks.append(content)
            if on_delta is not None:
                on_delta(content)
    text = "".join(chunks).strip()
    if text:
        return text
    raise RuntimeError("LLM stream response missing text content")


def normalize_chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"
