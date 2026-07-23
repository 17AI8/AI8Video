from __future__ import annotations

import base64
import functools
import io
import ipaddress
import json
import mimetypes
import os
import re
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable
from urllib.parse import urlsplit

import requests
import urllib3

from ai8video.core.config import AI8VideoConfig
from ai8video.generation.business_prompt import finalize_video_prompt_with_ai
from ai8video.assets.default_reference_image import reference_image_effect_definitions
from ai8video.core.models import VideoPrompt, FirstFrameAsset, ParsedRequest
from ai8video.generation.prompt_trace import append_prompt_trace
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


TRANSFORMED_REFERENCE_DIR = (USER_FILE_ROOT / "参考图" / "图生图结果").resolve()
_IMAGE_GENERATION_SEMAPHORES: dict[int, threading.BoundedSemaphore] = {}
_IMAGE_GENERATION_SEMAPHORES_LOCK = threading.Lock()
_FAKE_DNS_NETWORKS = (
    ipaddress.ip_network("198.18.0.0/15"),
)
_PUBLIC_DNS_A_ENDPOINTS: tuple[tuple[str, dict[str, str]], ...] = (
    ("https://dns.google/resolve?name={host}&type=A", {}),
    ("https://1.1.1.1/dns-query?name={host}&type=A", {"accept": "application/dns-json"}),
)
_PUBLIC_DNS_TIMEOUT_SECONDS = 5


class ReferenceImagePreprocessError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageInputPayload:
    payload: str
    mime: str
    byte_count: int
    encoded_char_count: int
    input_format: str
    source_kind: str


class ReferenceImagePreprocessor:
    def __init__(self, config: AI8VideoConfig | None = None, llm: Callable[[str], str] | None = None):
        self.config = config or AI8VideoConfig.from_env()
        self.llm = llm

    def prepare_first_frame(
        self,
        request: ParsedRequest,
        video: VideoPrompt | None = None,
        trace_session_id: str | None = None,
    ) -> FirstFrameAsset | None:
        if not request.reference_image:
            append_prompt_trace(
                "first_frame_skipped",
                session_id=trace_session_id,
                payload={
                    "videoIndex": video.index if video else None,
                    "reason": "no_reference_image",
                },
            )
            return None
        options = _normalize_options(request.reference_image_transform_options)
        custom_prompt = _normalize_custom_prompt(request.reference_image_custom_prompt)
        if not any(options.values()) and not custom_prompt:
            append_prompt_trace(
                "first_frame_passthrough",
                session_id=trace_session_id,
                payload={
                    "videoIndex": video.index if video else None,
                    "source": request.reference_image,
                    "transformOptions": options,
                    "customPrompt": custom_prompt,
                },
            )
            return FirstFrameAsset(source=request.reference_image)
        if self.config.dry_run:
            append_prompt_trace(
                "first_frame_dry_run",
                session_id=trace_session_id,
                payload={
                    "videoIndex": video.index if video else None,
                    "source": request.reference_image,
                    "transformOptions": options,
                    "customPrompt": custom_prompt,
                },
            )
            return FirstFrameAsset(source=request.reference_image)
        if not self.config.has_image_model():
            raise ReferenceImagePreprocessError("请设置图片模型。请在设置的图片模型页补齐地址、API Key 和模型名。")

        prompt = build_reference_image_transform_prompt(
            _build_context_text(request, video),
            options,
            custom_prompt=custom_prompt,
            llm=self.llm,
            trace_session_id=trace_session_id,
            video_index=video.index if video else None,
        )
        request_id = _image_generation_request_id()
        append_prompt_trace(
            "first_frame_image_prompt",
            session_id=trace_session_id,
            payload={
                "videoIndex": video.index if video else None,
                "source": request.reference_image,
                "transformOptions": options,
                "customPrompt": custom_prompt,
                "prompt": prompt,
                "requestId": request_id,
            },
        )
        try:
            output_source, image_trace = self._generate_image_to_image(
                request.reference_image,
                prompt,
                request_id=request_id,
                trace_session_id=trace_session_id,
                video_index=video.index if video else None,
            )
        except Exception as exc:
            append_prompt_trace(
                "first_frame_image_error",
                session_id=trace_session_id,
                payload={
                    "videoIndex": video.index if video else None,
                    "requestId": request_id,
                    "errorType": exc.__class__.__name__,
                    "error": str(exc),
                    "request": _exception_image_request_meta(exc),
                },
            )
            raise
        append_prompt_trace(
            "first_frame_image_output",
            session_id=trace_session_id,
            payload={
                "videoIndex": video.index if video else None,
                "outputSource": output_source,
                "requestId": request_id,
                **image_trace,
            },
        )
        return FirstFrameAsset(source=output_source)

    def _generate_image_to_image(
        self,
        source: str,
        prompt: str,
        *,
        request_id: str | None = None,
        trace_session_id: str | None = None,
        video_index: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        return self._generate_images_to_image(
            [source],
            prompt,
            request_id=request_id,
            trace_session_id=trace_session_id,
            video_index=video_index,
        )

    def repair_frame_with_references(
        self,
        frame_source: str,
        reference_sources: list[str],
        *,
        max_concurrency: int | None = None,
        custom_prompt: str = "",
    ) -> str:
        if self.config.dry_run:
            raise ReferenceImagePreprocessError("当前为演练模式，无法调用图片模型修图。")
        if not self.config.has_image_model():
            raise ReferenceImagePreprocessError("请先在设置中补齐图片模型后再修图。")
        sources = [str(frame_source or "").strip(), *[str(item or "").strip() for item in reference_sources]]
        sources = [item for item in sources if item]
        if len(sources) < 2:
            raise ReferenceImagePreprocessError("请至少选择一张参考图后再修图。")
        user_requirement = custom_prompt.strip()[:2000]
        if user_requirement:
            prompt = (
                "第一张输入图是视频截图，始终是唯一的主修图对象与输出画面基底。"
                "后续输入图是用户选定的参考图；在执行用户补充要求时，"
                "参考图可与截图一起参与人物、服装、场景、色彩和细节的取舍。"
                "严格根据用户补充要求，在这张截图上完成一张自然、连贯的修图结果，"
                "不得将输出变成参考图的独立复刻。"
                "可以按要求调整人物、服装、场景、动作或构图，但要保留视频画面比例。"
                "只输出一张完整、无文字、无水印的单帧画面，不要生成拼图、分屏或对比图。"
                f"\n用户补充要求：{user_requirement}"
            )
        else:
            prompt = (
                "第一张输入图是视频截图，始终是唯一的主修图对象与输出画面基底；"
                "后续输入图是用户选定的参考图。"
                "在没有额外修改要求时，以截图为略高优先级的画面基础，"
                "并自然融合参考图的人物、服装、场景、色彩与细节。"
                "保留视频画面比例，只输出一张自然、完整、无文字、无水印的单帧画面，"
                "不要生成拼图、分屏或对比图。"
            )
        output_source, _ = self._generate_images_to_image(
            sources[:5], prompt, max_concurrency=max_concurrency,
        )
        return output_source

    def _generate_images_to_image(
        self,
        sources: list[str],
        prompt: str,
        *,
        request_id: str | None = None,
        trace_session_id: str | None = None,
        video_index: int | None = None,
        max_concurrency: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        image_inputs = [_reference_image_input_payload(source) for source in sources]
        if not image_inputs:
            raise ReferenceImagePreprocessError("没有可提交给图片模型的图片。")
        image_input = image_inputs[0]
        url = _normalize_image_generations_url(self.config.image_base_url or "")
        timeout_seconds = _image_generation_timeout_seconds(self.config)
        max_concurrency = max(1, int(max_concurrency or _image_generation_max_concurrency()))
        direct_target = _image_generation_direct_connect_target(url)
        request_meta = {
            "endpoint": _redact_url(url),
            "model": self.config.image_model,
            "responseFormat": _image_response_format(),
            "size": _image_generation_size(self.config.image_model),
            "timeoutSeconds": timeout_seconds,
            "maxConcurrency": max_concurrency,
            "imageInputFormat": image_input.input_format,
            "imageMime": image_input.mime,
            "imageBytes": image_input.byte_count,
            "imageChars": image_input.encoded_char_count,
            "sourceKind": image_input.source_kind,
            "inputImageCount": len(image_inputs),
            **_image_generation_direct_connect_trace(direct_target),
        }
        append_prompt_trace(
            "first_frame_image_waiting",
            session_id=trace_session_id,
            payload={
                "videoIndex": video_index,
                "requestId": request_id,
                **request_meta,
            },
        )
        response: requests.Response | None = None
        slot = _image_generation_semaphore(max_concurrency)
        slot.acquire()
        try:
            append_prompt_trace(
                "first_frame_image_request",
                session_id=trace_session_id,
                payload={
                    "videoIndex": video_index,
                    "requestId": request_id,
                    **request_meta,
                },
            )
            with _image_generation_request_session() as session:
                try:
                    response = _post_image_generation_with_lost_response_retry(
                        session,
                        url,
                        headers={
                            "Authorization": f"Bearer {self.config.image_api_key}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            **(_image_idempotency_headers(request_id) if request_id else {}),
                        },
                        payload={
                            "model": self.config.image_model,
                            "prompt": prompt,
                            "n": 1,
                            "response_format": request_meta["responseFormat"],
                            "size": request_meta["size"],
                            "image": [item.payload for item in image_inputs],
                        },
                        timeout=timeout_seconds,
                        request_meta=request_meta,
                        trace_session_id=trace_session_id,
                        video_index=video_index,
                        request_id=request_id,
                    )
                except requests.RequestException as exc:
                    _attach_image_request_meta(exc, request_meta)
                    raise
        finally:
            slot.release()
        response_meta = {
            **request_meta,
            "statusCode": getattr(response, "status_code", None),
            "responseHeaders": _safe_response_headers(getattr(response, "headers", {})),
        }
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            _attach_image_request_meta(
                exc,
                {
                    **response_meta,
                    "responseText": _response_text_snippet(response),
                },
            )
            raise
        payload = response.json()
        image = _extract_output_image(payload)
        if not image:
            exc = ReferenceImagePreprocessError(f"图片模型响应缺少输出图：{_payload_summary(payload)}")
            _attach_image_request_meta(exc, response_meta)
            raise exc
        if image.startswith(("http://", "https://")):
            return _download_output_image(image), {
                **response_meta,
                "outputKind": "url",
                "payloadSummary": _payload_summary(payload),
            }
        return _save_output_image(image), {
            **response_meta,
            "outputKind": "base64",
            "payloadSummary": _payload_summary(payload),
        }


def build_reference_image_transform_prompt(
    context_text: str,
    options: dict[str, bool] | None,
    *,
    custom_prompt: str | None = None,
    llm: Callable[[str], str] | None = None,
    trace_session_id: str | None = None,
    video_index: int | None = None,
) -> str:
    normalized = _normalize_options(options)
    effects = [effect["prompt"] for effect in reference_image_effect_definitions() if normalized.get(effect["key"])]
    extra_prompt = _normalize_custom_prompt(custom_prompt)
    effect_text = "；".join(effects) if effects else "保持参考图主体一致"
    context = str(context_text or "").strip()
    if len(context) > 1200:
        context = context[:1200].rstrip() + "..."
    extra_requirement = f"用户补充要求：{extra_prompt}。" if extra_prompt else ""
    prompt = (
        "基于输入参考图做图片图生图，生成一张可作为视频默认首帧图 / 参考图的竖屏写实图片。"
        "必须保留参考图中人物的身份、脸部特征、年龄感和真实质感。"
        f"{effect_text}。"
        f"{extra_requirement}"
        "如果用户在任务描述里没有明确说明构图、镜头角度或姿态，默认生成正对着镜头的全身人物，人物全身入镜，站姿自然，视线看向镜头。"
        "只生成一张单一时刻、单一场景、单一构图的完整画面，禁止分镜板、四宫格、多宫格、拼贴、分屏或前后对比图。"
        "不要添加文字、字幕、Logo、水印、边框或界面元素。"
        f"用户任务描述：{context or '未提供额外描述'}"
    )
    return finalize_video_prompt_with_ai(
        prompt,
        llm=llm,
        trace_session_id=trace_session_id,
        video_index=video_index,
        prompt_kind="image",
    )


def _build_context_text(request: ParsedRequest, video: VideoPrompt | None) -> str:
    parts: list[str] = []
    if video is not None:
        title = str(video.title or "").strip()
        prompt = str(video.prompt or "").strip()
        if title:
            parts.append(f"当前视频标题：{title}")
        if prompt:
            parts.append(f"当前视频首镜头：{_first_shot_prompt(prompt)}")
    raw_text = str(request.raw_text or "").strip()
    if raw_text:
        parts.append(f"用户原始任务：{raw_text}")
    return "\n".join(parts)


def _first_shot_prompt(prompt: str) -> str:
    text = str(prompt or "").strip()
    if not text:
        return ""
    next_shot = re.search(
        r"(?m)^\s*(?:【\s*)?(?:镜头\s*(?:二|2)|第二镜头)(?:\s*】)?\s*(?:[（(][^\n)]*[）)])?\s*[：:]?",
        text,
    )
    if next_shot:
        return text[:next_shot.start()].strip()
    next_time_block = re.search(
        r"(?m)^\s*(?:[-*]\s*)?(?:【\s*)?(?:[（(]\s*)?[1-9]\d*\s*[-—~至到]\s*\d+\s*(?:秒|s)(?:\s*[）)])?",
        text,
    )
    if next_time_block:
        return text[:next_time_block.start()].strip()
    return text


def _normalize_options(options: dict[str, Any] | None) -> dict[str, bool]:
    source = options if isinstance(options, dict) else {}
    return {effect["key"]: bool(source.get(effect["key"])) for effect in reference_image_effect_definitions()}


def _normalize_custom_prompt(value: str | None) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def _reference_image_input(source: str) -> str:
    return _reference_image_input_payload(source).payload


def _reference_image_input_payload(source: str) -> ImageInputPayload:
    text = str(source or "").strip()
    if not text:
        raise ReferenceImagePreprocessError("参考图为空，无法做图生图。")
    if text.startswith("data:image/"):
        return _data_url_to_supported_image_payload(text)
    if text.startswith(("http://", "https://")):
        return ImageInputPayload(
            payload=text,
            mime="",
            byte_count=0,
            encoded_char_count=len(text),
            input_format="url",
            source_kind="url",
        )
    path = Path(text).expanduser()
    if not path.exists() or not path.is_file():
        raise ReferenceImagePreprocessError(f"参考图文件不存在：{text}")
    return _file_to_supported_image_payload(path)


def _data_url_to_supported_image_base64(data_url: str) -> str:
    return _data_url_to_supported_image_payload(data_url).payload


def _data_url_to_supported_image_payload(data_url: str) -> ImageInputPayload:
    try:
        header, raw = data_url.split(",", 1)
    except ValueError as exc:
        raise ReferenceImagePreprocessError("参考图 data URL 格式错误，无法做图生图。") from exc
    mime = header.split(";", 1)[0].replace("data:", "").strip().lower()
    try:
        image_bytes = base64.b64decode(raw.strip(), validate=True)
    except Exception as exc:
        raise ReferenceImagePreprocessError("参考图 data URL 的 base64 数据无效，无法做图生图。") from exc
    return _supported_image_payload(image_bytes, mime=mime, label="data URL", source_kind="data_url")


def _file_to_supported_image_base64(path: Path) -> str:
    return _file_to_supported_image_payload(path).payload


def _file_to_supported_image_payload(path: Path) -> ImageInputPayload:
    try:
        image_bytes = path.read_bytes()
    except OSError as exc:
        raise ReferenceImagePreprocessError(f"参考图文件读取失败：{path}") from exc
    mime = mimetypes.guess_type(str(path))[0] or ""
    return _supported_image_payload(image_bytes, mime=mime, label=str(path), source_kind="file")


def _supported_image_base64(image_bytes: bytes, *, mime: str, label: str) -> str:
    return _supported_image_payload(image_bytes, mime=mime, label=label, source_kind="bytes").payload


def _supported_image_payload(
    image_bytes: bytes,
    *,
    mime: str,
    label: str,
    source_kind: str,
) -> ImageInputPayload:
    normalized_mime = str(mime or "").split(";", 1)[0].strip().lower()
    if normalized_mime == "image/jpg":
        normalized_mime = "image/jpeg"
    if normalized_mime in {"image/png", "image/jpeg", "image/jpg"}:
        _verify_image_bytes(image_bytes, label)
        return _format_image_input_payload(image_bytes, normalized_mime, source_kind)
    converted_bytes, converted_mime = _convert_image_bytes_to_compatible_bytes(image_bytes, label)
    return _format_image_input_payload(converted_bytes, converted_mime, source_kind)


def _verify_image_bytes(image_bytes: bytes, label: str) -> None:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
    except Exception as exc:
        raise ReferenceImagePreprocessError(f"参考图不是有效图片，无法做图生图：{label}") from exc


def _convert_image_bytes_to_compatible_base64(image_bytes: bytes, label: str) -> str:
    converted_bytes, _mime = _convert_image_bytes_to_compatible_bytes(image_bytes, label)
    return base64.b64encode(converted_bytes).decode("ascii")


def _convert_image_bytes_to_compatible_bytes(image_bytes: bytes, label: str) -> tuple[bytes, str]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            has_alpha = image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in getattr(image, "info", {})
            )
            output = io.BytesIO()
            if has_alpha:
                if image.mode != "RGBA":
                    image = image.convert("RGBA")
                image.save(output, format="PNG", optimize=True)
                mime = "image/png"
            else:
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(output, format="JPEG", quality=92, optimize=True)
                mime = "image/jpeg"
            return output.getvalue(), mime
    except Exception as exc:
        raise ReferenceImagePreprocessError(f"参考图格式无法转为兼容图片，无法做图生图：{label}") from exc


def _format_image_input_payload(image_bytes: bytes, mime: str, source_kind: str) -> ImageInputPayload:
    normalized_mime = str(mime or "").strip().lower() or "image/png"
    raw = base64.b64encode(image_bytes).decode("ascii")
    input_format = _image_input_format()
    payload = f"data:{normalized_mime};base64,{raw}" if input_format == "data_url" else raw
    return ImageInputPayload(
        payload=payload,
        mime=normalized_mime,
        byte_count=len(image_bytes),
        encoded_char_count=len(raw),
        input_format=input_format,
        source_kind=source_kind,
    )


def _extract_output_image(payload: Any) -> str:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                for key in ("url", "image_url", "b64_json"):
                    value = first.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        for key in ("url", "image_url", "output_url", "b64_json"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        images = payload.get("images") or payload.get("output") or payload.get("result")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
            if isinstance(first, dict):
                for key in ("url", "image_url", "b64_json"):
                    value = first.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    return ""


def _save_output_image(image_payload: str) -> str:
    text = str(image_payload or "").strip()
    if text.startswith("data:image/"):
        header, raw = text.split(",", 1)
        mime = header.split(";", 1)[0].replace("data:", "")
        suffix = mimetypes.guess_extension(mime) or ".png"
    else:
        raw = text
        suffix = ".png"
    ensure_user_file_root()
    TRANSFORMED_REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    output = TRANSFORMED_REFERENCE_DIR / f"reference-i2i-{int(time.time())}-{uuid.uuid4().hex[:8]}{suffix}"
    output.write_bytes(base64.b64decode(raw))
    return str(output)


def _download_output_image(url: str) -> str:
    try:
        with _image_generation_request_session() as session:
            direct_target = _image_generation_direct_connect_target(url)
            response = (
                _get_image_output_direct(url, direct_target, timeout=20)
                if direct_target
                else session.get(url, timeout=20)
            )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ReferenceImagePreprocessError(f"图片模型输出图无法下载：{exc}") from exc
    content_type = response.headers.get("Content-Type") or ""
    suffix = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or Path(url.split("?", 1)[0]).suffix or ".png"
    ensure_user_file_root()
    TRANSFORMED_REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    output = TRANSFORMED_REFERENCE_DIR / f"reference-i2i-{int(time.time())}-{uuid.uuid4().hex[:8]}{suffix}"
    output.write_bytes(response.content)
    return str(output)


def remove_transformed_reference_asset(source: str | None) -> bool:
    text = str(source or "").strip()
    if not text:
        return False
    if text.startswith(("http://", "https://", "data:")):
        return False
    path = Path(text).expanduser()
    try:
        resolved = path.resolve()
        resolved.relative_to(TRANSFORMED_REFERENCE_DIR.resolve())
    except (OSError, ValueError):
        return False
    if not resolved.name.startswith("reference-i2i-") or not resolved.is_file():
        return False
    resolved.unlink()
    return True


def _normalize_image_generations_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if base.endswith("/images/generations"):
        return base
    if base.endswith("/v1"):
        return base + "/images/generations"
    return base + "/v1/images/generations"


def _image_response_format() -> str:
    value = str(os.getenv("AI8VIDEO_IMAGE_RESPONSE_FORMAT") or "url").strip().lower()
    return value if value in {"url", "b64_json"} else "url"


def _image_input_format() -> str:
    value = str(os.getenv("AI8VIDEO_IMAGE_INPUT_FORMAT") or "data_url").strip().lower()
    return value if value in {"data_url", "base64"} else "data_url"


def _image_generation_size(model: str | None = None) -> str:
    override = str(os.getenv("AI8VIDEO_IMAGE_SIZE") or "").strip().lower()
    if re.match(r"^\d+x\d+$", override):
        return override
    normalized_model = str(model or "").strip().lower()
    if "seedream" in normalized_model:
        return "1440x2560"
    return "1024x1792"


def _image_generation_timeout_seconds(config: AI8VideoConfig) -> int:
    raw = str(os.getenv("AI8VIDEO_IMAGE_TIMEOUT_SECONDS") or "").strip()
    try:
        configured = int(raw) if raw else 300
    except ValueError:
        configured = 300
    base_timeout = int(getattr(config, "timeout_seconds", 0) or 0)
    return max(240, configured, base_timeout)


def _image_generation_max_concurrency() -> int:
    raw = str(os.getenv("AI8VIDEO_IMAGE_MAX_CONCURRENCY") or "").strip()
    try:
        value = int(raw) if raw else 1
    except ValueError:
        value = 1
    return max(1, min(8, value))


def _image_generation_semaphore(max_concurrency: int | None = None) -> threading.BoundedSemaphore:
    limit = max(1, int(max_concurrency or _image_generation_max_concurrency()))
    with _IMAGE_GENERATION_SEMAPHORES_LOCK:
        semaphore = _IMAGE_GENERATION_SEMAPHORES.get(limit)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(limit)
            _IMAGE_GENERATION_SEMAPHORES[limit] = semaphore
        return semaphore


def _image_generation_use_system_proxy() -> bool:
    use_system_proxy = str(os.getenv("AI8VIDEO_IMAGE_USE_SYSTEM_PROXY") or "").strip().lower()
    return use_system_proxy in {"1", "true", "yes", "on"}


def _image_generation_request_session() -> requests.Session:
    session = requests.Session()
    if not _image_generation_use_system_proxy():
        session.trust_env = False
        session.proxies.clear()
    return session


def _post_image_generation_with_lost_response_retry(
    session: requests.Session,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
    request_meta: dict[str, Any],
    trace_session_id: str | None,
    video_index: int | None,
    request_id: str | None,
) -> requests.Response:
    lost_response_retries = _image_lost_response_retries()
    transient_http_retries = _image_transient_http_retries()
    attempt = 0
    while True:
        try:
            direct_target = _image_generation_direct_connect_target(url)
            if direct_target:
                response = _post_image_generation_direct(
                    url,
                    direct_target,
                    headers=headers,
                    payload=payload,
                    timeout=timeout,
                )
            else:
                response = session.post(url, headers=headers, json=payload, timeout=timeout)
            status_code = int(getattr(response, "status_code", 0) or 0)
            if attempt < transient_http_retries and _is_transient_image_http_status(status_code):
                attempt += 1
                append_prompt_trace(
                    "first_frame_image_retry",
                    session_id=trace_session_id,
                    payload={
                        "videoIndex": video_index,
                        "requestId": request_id,
                        "attempt": attempt + 1,
                        "statusCode": status_code,
                        "reason": f"HTTP {status_code}",
                        **request_meta,
                    },
                )
                time.sleep(_image_transient_http_retry_delay_seconds(attempt, response=response))
                continue
            return response
        except requests.RequestException as exc:
            if attempt >= lost_response_retries or not _is_lost_image_response_exception(exc):
                raise
            attempt += 1
            append_prompt_trace(
                "first_frame_image_retry",
                session_id=trace_session_id,
                payload={
                    "videoIndex": video_index,
                    "requestId": request_id,
                    "attempt": attempt + 1,
                    "reason": str(exc),
                    **request_meta,
                },
            )
            time.sleep(_image_lost_response_retry_delay_seconds(attempt))


def _image_idempotency_headers(request_id: str) -> dict[str, str]:
    return {
        "Idempotency-Key": request_id,
        "X-Request-Id": request_id,
        "X-Request-ID": request_id,
    }


def _image_lost_response_retries() -> int:
    raw = str(os.getenv("AI8VIDEO_IMAGE_LOST_RESPONSE_RETRIES") or "").strip()
    try:
        value = int(raw) if raw else 0
    except ValueError:
        value = 0
    return max(0, min(2, value))


def _image_lost_response_retry_delay_seconds(attempt: int) -> float:
    raw = str(os.getenv("AI8VIDEO_IMAGE_LOST_RESPONSE_RETRY_DELAY_SECONDS") or "").strip()
    try:
        value = float(raw) if raw else 3.0
    except ValueError:
        value = 3.0
    return max(0.0, min(30.0, value * max(1, attempt)))


def _image_transient_http_retries() -> int:
    raw = str(os.getenv("AI8VIDEO_IMAGE_TRANSIENT_HTTP_RETRIES") or "").strip()
    try:
        value = int(raw) if raw else 5
    except ValueError:
        value = 5
    return max(0, min(8, value))


def _image_transient_http_retry_delay_seconds(attempt: int, *, response: requests.Response | None = None) -> float:
    retry_after = _response_retry_after_seconds(response)
    if retry_after is not None:
        return retry_after
    raw = str(os.getenv("AI8VIDEO_IMAGE_TRANSIENT_HTTP_RETRY_DELAY_SECONDS") or "").strip()
    try:
        value = float(raw) if raw else 8.0
    except ValueError:
        value = 8.0
    return max(0.0, min(60.0, value * max(1, attempt)))


def _response_retry_after_seconds(response: requests.Response | None) -> float | None:
    if response is None:
        return None
    retry_after = str(getattr(response, "headers", {}).get("Retry-After") or "").strip()
    if not retry_after:
        return None
    try:
        seconds = float(retry_after)
    except ValueError:
        return None
    return max(0.0, min(180.0, seconds))


def _image_generation_direct_connect_target(url: str) -> tuple[str, str] | None:
    if _image_generation_use_system_proxy():
        return None
    parsed = urlsplit(str(url or ""))
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    forced = str(os.getenv("AI8VIDEO_IMAGE_DIRECT_CONNECT_IP") or "").strip()
    if forced:
        return host, forced
    try:
        resolved = socket.gethostbyname(host)
    except OSError:
        return None
    if not _is_fake_dns_address(resolved):
        return None
    origin_ip = str(os.getenv(f"AI8VIDEO_IMAGE_DIRECT_CONNECT_IP_{_env_key_host(host)}") or "").strip()
    if origin_ip:
        return host, origin_ip
    public_ip = _lookup_public_dns_ipv4(host)
    if public_ip:
        return host, public_ip
    return None


@functools.lru_cache(maxsize=32)
def _lookup_public_dns_ipv4(host: str) -> str | None:
    normalized = str(host or "").strip().lower()
    if not normalized:
        return None
    for endpoint, headers in _PUBLIC_DNS_A_ENDPOINTS:
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.proxies.clear()
                response = session.get(
                    endpoint.format(host=normalized),
                    headers=headers,
                    timeout=_PUBLIC_DNS_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                resolved = _extract_public_dns_ipv4(response.json())
        except (requests.RequestException, ValueError, TypeError):
            continue
        if resolved and not _is_fake_dns_address(resolved):
            return resolved
    return None


def _extract_public_dns_ipv4(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    try:
        answers = payload.get("Answer")
    except Exception:
        return None
    if not isinstance(answers, list):
        return None
    for item in answers:
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("data") or "").strip()
        if not candidate:
            continue
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if ip.version == 4:
            return candidate
    return None


def _image_generation_direct_connect_trace(target: tuple[str, str] | None) -> dict[str, Any]:
    if not target:
        return {"directConnect": False}
    host, origin = target
    origin_host, origin_port = _split_direct_origin(origin)
    return {
        "directConnect": True,
        "directConnectHost": host,
        "directConnectOrigin": origin_host,
        "directConnectPort": origin_port,
    }


def _split_direct_origin(origin: str) -> tuple[str, int | None]:
    text = str(origin or "").strip()
    if not text:
        return "", None
    if text.startswith("[") and "]" in text:
        host, _, tail = text[1:].partition("]")
        port = tail[1:] if tail.startswith(":") else ""
        return host, _coerce_port(port)
    if ":" in text and text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        return host, _coerce_port(port)
    return text, None


def _coerce_port(value: str) -> int | None:
    try:
        port = int(str(value or "").strip())
    except ValueError:
        return None
    return port if 0 < port < 65536 else None


def _is_fake_dns_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(address or "").strip())
    except ValueError:
        return False
    return any(ip in network for network in _FAKE_DNS_NETWORKS)


def _env_key_host(host: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", host.upper()).strip("_")


def _post_image_generation_direct(
    url: str,
    target: tuple[str, str],
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
) -> requests.Response:
    host, origin_ip = target
    origin_host, origin_port = _split_direct_origin(origin_ip)
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    request_headers = dict(headers)
    request_headers["Host"] = host
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers["Content-Length"] = str(len(body))
    pool = urllib3.HTTPSConnectionPool(
        origin_host,
        port=origin_port or parsed.port or 443,
        server_hostname=host,
        assert_hostname=host,
        cert_reqs="CERT_REQUIRED",
        ca_certs=requests.certs.where(),
        timeout=urllib3.Timeout(connect=min(30, timeout), read=timeout),
        retries=False,
    )
    try:
        response = pool.request(
            "POST",
            path,
            body=body,
            headers=request_headers,
            assert_same_host=False,
            preload_content=True,
        )
        return _Urllib3ImageResponseAdapter(response, url)
    except urllib3.exceptions.HTTPError as exc:
        raise requests.ConnectionError(str(exc)) from exc
    finally:
        pool.close()


def _get_image_output_direct(url: str, target: tuple[str, str], *, timeout: int) -> requests.Response:
    host, origin_ip = target
    origin_host, origin_port = _split_direct_origin(origin_ip)
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    pool = urllib3.HTTPSConnectionPool(
        origin_host,
        port=origin_port or parsed.port or 443,
        server_hostname=host,
        assert_hostname=host,
        cert_reqs="CERT_REQUIRED",
        ca_certs=requests.certs.where(),
        timeout=urllib3.Timeout(connect=min(20, timeout), read=timeout),
        retries=False,
    )
    try:
        response = pool.request(
            "GET",
            path,
            headers={"Host": host},
            assert_same_host=False,
            preload_content=True,
        )
        return _Urllib3ImageResponseAdapter(response, url)
    except urllib3.exceptions.HTTPError as exc:
        raise requests.ConnectionError(str(exc)) from exc
    finally:
        pool.close()


class _Urllib3ImageResponseAdapter(requests.Response):
    def __init__(self, response: urllib3.HTTPResponse, url: str):
        super().__init__()
        self.status_code = int(response.status)
        self.headers.update(dict(response.headers))
        self._content = bytes(response.data or b"")
        self.url = url
        self.reason = getattr(response, "reason", None) or ""


def _is_lost_image_response_exception(exc: BaseException) -> bool:
    lowered = str(exc or "").lower()
    return any(marker in lowered for marker in (
        "remotedisconnected",
        "remote end closed connection",
        "connection aborted",
        "read timed out",
        "gateway timeout",
    ))


def _is_transient_image_http_status(status_code: int) -> bool:
    return int(status_code or 0) in {429, 500, 502, 503, 504}


def _image_generation_request_id() -> str:
    return f"ai8video-first_frame_{int(time.time())}_{uuid.uuid4().hex[:12]}"


def _attach_image_request_meta(exc: BaseException, meta: dict[str, Any]) -> None:
    try:
        setattr(exc, "image_request_meta", dict(meta))
    except Exception:
        return


def _exception_image_request_meta(exc: BaseException) -> dict[str, Any]:
    meta = getattr(exc, "image_request_meta", None)
    return dict(meta) if isinstance(meta, dict) else {}


def _safe_response_headers(headers: Any) -> dict[str, str]:
    allow = {
        "content-type",
        "x-request-id",
        "x-newapi-request-id",
        "x-oneapi-request-id",
        "x-ratelimit-remaining",
        "x-ratelimit-limit",
        "retry-after",
    }
    result: dict[str, str] = {}
    try:
        items = headers.items()
    except Exception:
        return result
    for key, value in items:
        lowered = str(key or "").strip().lower()
        if lowered in allow:
            result[str(key)] = str(value)
    return result


def _response_text_snippet(response: requests.Response) -> str:
    try:
        text = response.text
    except Exception:
        return ""
    text = str(text or "").strip()
    return text[:800]


def _payload_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        keys = sorted(str(key) for key in payload.keys())
        data = payload.get("data")
        data_len = len(data) if isinstance(data, list) else None
        first_keys: list[str] = []
        if isinstance(data, list) and data and isinstance(data[0], dict):
            first_keys = sorted(str(key) for key in data[0].keys())
        return {
            "type": "dict",
            "keys": keys,
            "dataLength": data_len,
            "firstDataKeys": first_keys,
        }
    if isinstance(payload, list):
        return {"type": "list", "length": len(payload)}
    return {"type": type(payload).__name__}


def _redact_url(url: str) -> str:
    text = str(url or "")
    if "?" not in text:
        return text
    return text.split("?", 1)[0] + "?..."
