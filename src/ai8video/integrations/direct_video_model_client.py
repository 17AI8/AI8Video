from __future__ import annotations

import base64
import json
import mimetypes
import re
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

from ai8video.core.config import AI8VideoConfig
from ai8video.integrations.http_client import api_request
from ai8video.core.models import FirstFrameAsset, QuickVideoJob
from ai8video.generation.real_generation_guard import RealGenerationGuard
from ai8video.integrations.video_model_settings import VideoModelSettings, load_video_model_settings


class DirectVideoModelError(RuntimeError):
    pass


ProgressCallback = Callable[[QuickVideoJob], None]
_DOUBAO_CREATE_LOCK = threading.Lock()
DOUBAO_CREATE_TIMEOUT_SECONDS = 420


class AI8VideoModelClient:
    def __init__(self, config: AI8VideoConfig | None = None, settings: VideoModelSettings | None = None):
        self.config = config or AI8VideoConfig.from_env()
        self.settings = settings or load_video_model_settings(
            llm_base_url=self.config.llm_base_url,
            llm_api_key=self.config.llm_api_key,
        )
        self._settings_by_job_id: dict[str, VideoModelSettings] = {}
        self.guard = RealGenerationGuard(
            path=self.config.real_job_audit_path,
            max_jobs_per_window=self.config.real_job_max_count,
            window_seconds=self.config.real_job_window_seconds,
            forced_duration_seconds=self.config.real_job_force_duration_seconds,
        )

    def create_job(
        self,
        text: str,
        video_index: int = 1,
        first_frame: FirstFrameAsset | None = None,
        duration_seconds: int | None = None,
        ratio: str = "9:16",
        resolution: str = "480p",
        preset: str = "custom",
        panel_count: int | None = None,
        platform: str = "ai8video-agent",
        device_id: str | None = None,
    ) -> QuickVideoJob:
        del panel_count, platform, device_id
        settings = self._load_current_settings()
        seconds = settings.seconds if duration_seconds is None else int(duration_seconds)
        if not self.config.dry_run and self.guard.forced_duration_seconds > 0:
            seconds = self.guard.forced_duration_seconds
        ratio = settings.ratio if ratio in ("", "9:16", None) else ratio
        preset = settings.preset if preset in ("", "custom", None) else preset
        resolution = settings.resolution if resolution in ("", "480p", None) else resolution

        if self.config.dry_run:
            job_id = f"dry-model-{video_index}-{uuid.uuid4().hex[:8]}"
            return QuickVideoJob(
                video_index=video_index,
                job_id=job_id,
                status="succeeded",
                prompt=text,
                video_url=f"https://example.invalid/{job_id}.mp4",
                storage_key=f"direct-video-model/{job_id}.mp4",
                usage={"dryRun": True, "provider": "direct-video-model", "settings": settings.public_dict()},
            )

        if not settings.configured():
            raise DirectVideoModelError("未配置视频模型。请在设置的视频模型页补齐地址、密钥、模型和模板。")

        self.guard.assert_can_create()
        reference_image = _resolve_reference_image(first_frame)
        template = _template_for(settings.template)
        create_payload = _build_create_payload(
            template=settings.template,
            model=settings.model,
            prompt=text,
            image=reference_image,
            seconds=seconds,
            ratio=ratio,
            resolution=resolution,
            preset=preset,
            enhance_prompt=settings.enhance_prompt,
            return_last_frame=settings.return_last_frame,
            watermark=settings.watermark,
            generate_audio=settings.generate_audio,
            service_tier=settings.service_tier,
            execution_expires_after=settings.execution_expires_after,
            draft=settings.draft,
            camera_fixed=settings.camera_fixed,
            seed=settings.seed,
            prompt_extend=settings.prompt_extend,
            shot_type=settings.shot_type,
            audio=settings.audio,
            audio_url=settings.audio_url,
            video_count=settings.video_count,
            resolution_mode=settings.resolution_mode,
        )
        create_url = _resolve_endpoint(settings.base_url, template["create_path"], model=settings.model)
        create_lock = _DOUBAO_CREATE_LOCK if settings.template == "doubao-seedance" else _NullLock()
        with create_lock:
            try:
                response = api_request(
                    template["create_method"],
                    create_url,
                    headers=self._headers(settings),
                    json=create_payload,
                    timeout=_create_timeout_seconds(settings.template, self.config.timeout_seconds),
                )
            except requests.Timeout as exc:
                raise DirectVideoModelError(_format_create_timeout_error(settings.template, create_url, exc)) from exc
        _raise_for_response(response, "创建视频任务")
        data = response.json()
        task_id = _read_first_path(data, template["task_id_paths"])
        video_url = _normalize_output_url(_read_first_path(data, template["output_url_paths"]))
        if not task_id and not video_url:
            raise DirectVideoModelError(f"视频模型创建任务响应缺少任务 ID：{data}")
        job_id = task_id or f"direct-{uuid.uuid4().hex[:12]}"
        self._settings_by_job_id[job_id] = settings
        self.guard.record_job(job_id=job_id, video_index=video_index, prompt=text)
        return QuickVideoJob(
            video_index=video_index,
            job_id=job_id,
            status="succeeded" if video_url else "pending",
            prompt=text,
            video_url=video_url or None,
            storage_key=f"direct-video-model/{job_id}.mp4",
            usage={
                "provider": "direct-video-model",
                "template": settings.template,
                "model": settings.model,
                "create": _redact_payload_for_usage(data),
            },
        )

    def get_job(self, job_id: str, video_index: int = 1, prompt: str = "") -> QuickVideoJob:
        settings = self._settings_for_job(job_id)
        if self.config.dry_run:
            return QuickVideoJob(
                video_index=video_index,
                job_id=job_id,
                status="succeeded",
                prompt=prompt,
                video_url=f"https://example.invalid/{job_id}.mp4",
                storage_key=f"direct-video-model/{job_id}.mp4",
                usage={"dryRun": True, "provider": "direct-video-model"},
            )

        template = _template_for(settings.template)
        status_url = _resolve_endpoint(
            settings.base_url,
            template["status_path"],
            model=settings.model,
            task_id=job_id,
        )
        response = api_request("GET", status_url, headers=self._headers(settings), timeout=self.config.timeout_seconds)
        _raise_for_response(response, "查询视频任务")
        data = response.json()
        provider_status = (_read_first_path(data, template["status_paths"]) or "").strip().lower()
        raw_provider_status = (_read_first_path(data, template["status_paths"]) or "").strip()
        provider_progress = _read_progress(data, template.get("progress_paths") or [])
        stage_label = _read_first_path(data, template.get("stage_label_paths") or [])
        video_url = _normalize_output_url(_read_first_path(data, template["output_url_paths"]))
        error = _read_first_path(data, template["error_paths"])
        if provider_status in template["fail_states"]:
            status = "failed"
        elif video_url:
            status = "succeeded"
        elif provider_status in template["done_states"]:
            status = "succeeded"
        else:
            status = "pending"
        return QuickVideoJob(
            video_index=video_index,
            job_id=job_id,
            status=status,
            prompt=prompt,
            video_url=video_url or None,
            storage_key=f"direct-video-model/{job_id}.mp4",
            usage={
                "provider": "direct-video-model",
                "template": settings.template,
                "model": settings.model,
                "status": _redact_payload_for_usage(data),
            },
            error=error or None,
            provider_status=raw_provider_status or provider_status or None,
            provider_progress=provider_progress,
            stage_label=stage_label or None,
        )

    def poll_job(self, job: QuickVideoJob, progress_callback: ProgressCallback | None = None) -> QuickVideoJob:
        if self.config.dry_run or job.video_url:
            if progress_callback:
                progress_callback(job)
            return job
        last_request_error: requests.RequestException | None = None
        for _ in range(self.config.max_poll_attempts):
            try:
                latest = self.get_job(job.job_id, job.video_index, job.prompt)
            except requests.RequestException as exc:
                last_request_error = exc
                time.sleep(self.config.poll_interval_seconds)
                continue
            latest.segment_index = getattr(job, "segment_index", None)
            latest.segment_label = getattr(job, "segment_label", None)
            if progress_callback:
                progress_callback(latest)
            if latest.status in {"succeeded", "failed"}:
                return latest
            time.sleep(self.config.poll_interval_seconds)
        if last_request_error is not None:
            raise last_request_error
        raise TimeoutError(f"Polling timed out for direct video model job {job.job_id}")

    def _load_current_settings(self) -> VideoModelSettings:
        self.settings = load_video_model_settings(
            llm_base_url=self.config.llm_base_url,
            llm_api_key=self.config.llm_api_key,
        )
        return self.settings

    def _settings_for_job(self, job_id: str) -> VideoModelSettings:
        return self._settings_by_job_id.get(job_id) or self._load_current_settings()

    def _headers(self, settings: VideoModelSettings) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        }


def _template_for(name: str) -> dict[str, Any]:
    common_done = {"completed", "succeeded", "success", "done", "finished", "video_generation_completed", "video_upsampling_completed"}
    common_fail = {"failed", "error", "canceled", "cancelled", "expired", "timeout", "video_generation_failed", "video_upsampling_failed"}
    templates: dict[str, dict[str, Any]] = {
        "doubao-seedance": {
            "create_method": "POST",
            "create_path": "/v1/videos?model={model}",
            "status_path": "/v1/videos/{task_id}",
            "task_id_paths": ["id", "task_id", "data.id", "data.task_id"],
            "status_paths": ["status", "data.status"],
            "progress_paths": ["progress", "data.progress", "metadata.progress", "payload.providerProgress"],
            "stage_label_paths": ["stageLabel", "stage_label", "data.stageLabel", "data.stage_label"],
            "output_url_paths": ["content.video_url", "video_url", "data.video_url", "metadata.url", "url"],
            "error_paths": ["error.message", "message", "data.message"],
            "done_states": common_done | {"succeeded"},
            "fail_states": common_fail,
        },
        "yunwu-grok": {
            "create_method": "POST",
            "create_path": "/v1/videos",
            "status_path": "/v1/videos/{task_id}?model={model}",
            "task_id_paths": ["id", "task_id", "data.id", "data.task_id"],
            "status_paths": ["status", "data.status"],
            "progress_paths": ["progress", "data.progress", "metadata.progress", "payload.providerProgress"],
            "stage_label_paths": ["stageLabel", "stage_label", "data.stageLabel", "data.stage_label"],
            "output_url_paths": ["video_url", "data.video_url", "output.video_url", "url"],
            "error_paths": ["error.message", "message", "data.message"],
            "done_states": common_done,
            "fail_states": common_fail,
        },
        "yunwu-omni": {
            "create_method": "POST",
            "create_path": "/v1/videos",
            "status_path": "/v1/videos/{task_id}?model={model}",
            "task_id_paths": ["id", "task_id", "data.id", "data.task_id"],
            "status_paths": ["status", "data.status"],
            "progress_paths": ["progress", "data.progress", "metadata.progress", "payload.providerProgress"],
            "stage_label_paths": ["stageLabel", "stage_label", "data.stageLabel", "data.stage_label"],
            "output_url_paths": ["video_url", "data.video_url", "output.video_url", "url"],
            "error_paths": ["error.message", "message", "data.message"],
            "done_states": common_done,
            "fail_states": common_fail,
        },
        "yunwu-veo": {
            "create_method": "POST",
            "create_path": "/v1/video/create",
            "status_path": "/v1/video/query?id={task_id}",
            "task_id_paths": ["id", "task_id", "data.id", "data.task_id"],
            "status_paths": ["status", "data.status"],
            "progress_paths": ["progress", "data.progress", "metadata.progress", "payload.providerProgress"],
            "stage_label_paths": ["stageLabel", "stage_label", "data.stageLabel", "data.stage_label"],
            "output_url_paths": ["video_url", "data.video_url", "output.video_url", "url"],
            "error_paths": ["error.message", "error_message", "message", "data.message"],
            "done_states": common_done,
            "fail_states": common_fail,
        },
        "bailian-wan": {
            "create_method": "POST",
            "create_path": "/v1/videos",
            "status_path": "/v1/videos/{task_id}",
            "task_id_paths": ["task_id", "id", "data.task_id", "data.id"],
            "status_paths": ["status", "data.status"],
            "progress_paths": ["progress", "data.progress", "metadata.progress", "payload.providerProgress"],
            "stage_label_paths": ["stageLabel", "stage_label", "data.stageLabel", "data.stage_label"],
            "output_url_paths": ["metadata.url", "video_url", "data.video_url", "url"],
            "error_paths": ["error.message", "message", "data.message"],
            "done_states": common_done | {"SUCCEEDED".lower()},
            "fail_states": common_fail | {"FAILED".lower()},
        },
        "openai-compatible": {
            "create_method": "POST",
            "create_path": "/v1/videos",
            "status_path": "/v1/videos/{task_id}",
            "task_id_paths": ["id", "task_id", "data.id", "data.task_id"],
            "status_paths": ["status", "data.status"],
            "progress_paths": ["progress", "data.progress", "metadata.progress", "payload.providerProgress"],
            "stage_label_paths": ["stageLabel", "stage_label", "data.stageLabel", "data.stage_label"],
            "output_url_paths": ["video_url", "data.video_url", "output.video_url", "url"],
            "error_paths": ["error.message", "message", "data.message"],
            "done_states": common_done,
            "fail_states": common_fail,
        },
    }
    return templates.get(name) or templates["doubao-seedance"]


class _NullLock:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return False


def _create_timeout_seconds(template: str, default_timeout: int | float) -> int | float:
    if template == "doubao-seedance":
        return max(float(default_timeout or 0), DOUBAO_CREATE_TIMEOUT_SECONDS)
    return default_timeout


def _format_create_timeout_error(template: str, url: str, exc: requests.Timeout) -> str:
    provider_label = "豆包上游" if template == "doubao-seedance" else "上游"
    return (
        f"创建视频任务超时：{provider_label}可能已经接收请求并继续在后台生成，"
        "但本地尚未拿到任务 ID。请先查看上游后台或等待结果，不要立刻重复提交；"
        f"url={url}，错误：{str(exc).strip() or exc.__class__.__name__}"
    )


def _build_create_payload(
    *,
    template: str,
    model: str,
    prompt: str,
    image: str | None,
    seconds: int,
    ratio: str,
    resolution: str,
    preset: str,
    enhance_prompt: bool,
    return_last_frame: bool,
    watermark: bool,
    generate_audio: bool,
    service_tier: str,
    execution_expires_after: int,
    draft: bool,
    camera_fixed: bool,
    seed: int | None,
    prompt_extend: bool,
    shot_type: str,
    audio: bool,
    audio_url: str,
    video_count: int,
    resolution_mode: str = "ratio",
) -> dict[str, Any]:
    size = _size_from_ratio(ratio, resolution, resolution_mode=resolution_mode)
    if template == "doubao-seedance":
        return _drop_empty({
            "model": model,
            "prompt": prompt,
            "image": image,
            "size": size,
            "seconds": str(seconds),
            "duration": seconds,
            "generate_audio": generate_audio,
            "service_tier": service_tier,
            "execution_expires_after": execution_expires_after,
            "return_last_frame": return_last_frame,
            "draft": draft,
            "camera_fixed": camera_fixed,
            "watermark": watermark,
            "seed": seed,
            "metadata": {
                "resolution": resolution,
                "resolution_mode": resolution_mode,
                "ratio": ratio,
                "segment_seconds": seconds,
                "video_count": video_count,
                "return_last_frame": return_last_frame,
                "watermark": watermark,
            },
        })
    if template == "yunwu-veo":
        return _drop_empty({
            "model": model,
            "prompt": prompt,
            "aspect_ratio": _normalize_yunwu_ratio(ratio),
            "images": [image] if image else None,
            "enhance_prompt": enhance_prompt,
            "enable_upsample": False,
            "duration": seconds,
            "seed": seed,
            "watermark": watermark,
        })
    if template == "yunwu-omni":
        return _drop_empty({
            "model": _resolve_yunwu_omni_model(model, bool(image)),
            "prompt": prompt,
            "aspect_ratio": _normalize_yunwu_ratio(ratio),
            "enhance_prompt": enhance_prompt,
            "enable_upsample": False,
            "images": [image] if image else None,
            "input_reference": image,
            "duration": seconds,
            "seed": seed,
            "watermark": watermark,
        })
    if template == "bailian-wan":
        return _drop_empty({
            "model": model,
            "prompt": prompt,
            "input_reference": image,
            "seconds": str(seconds),
            "size": resolution,
            "resolution": resolution.upper(),
            "prompt_extend": prompt_extend,
            "shot_type": shot_type,
            "audio": audio,
            "audio_url": audio_url,
            "watermark": watermark,
            "seed": seed,
            "metadata": {
                "parameters": {
                    "resolution": resolution,
                    "duration": seconds,
                    "prompt_extend": prompt_extend,
                    "shot_type": shot_type,
                    "audio": audio,
                    "audio_url": audio_url,
                    "watermark": watermark,
                    "seed": seed,
                },
            },
        })
    if template == "openai-compatible":
        return _drop_empty({
            "model": model,
            "prompt": prompt,
            "image": image,
            "seconds": str(seconds),
            "duration": seconds,
            "size": size,
            "aspect_ratio": ratio,
            "preset": preset,
            "generationMode": preset,
            "videoGenerationMode": template,
            "enhance_prompt": enhance_prompt,
            "return_last_frame": return_last_frame,
            "watermark": watermark,
            "seed": seed,
        })
    return _drop_empty({
        "model": model,
        "prompt": prompt,
        "image": image,
        "seconds": str(seconds),
        "duration": seconds,
        "size": size,
        "aspect_ratio": ratio,
        "preset": preset,
        "generationMode": preset,
        "videoGenerationMode": template,
        "enhance_prompt": enhance_prompt,
        "return_last_frame": return_last_frame,
        "watermark": watermark,
        "generate_audio": generate_audio,
        "service_tier": service_tier,
        "execution_expires_after": execution_expires_after,
        "draft": draft,
        "camera_fixed": camera_fixed,
        "seed": seed,
        "metadata": {
            "resolution": resolution,
            "resolution_mode": resolution_mode,
            "ratio": ratio,
            "segment_seconds": seconds,
            "video_count": video_count,
            "return_last_frame": return_last_frame,
            "watermark": watermark,
        },
    })


def _resolve_endpoint(base_url: str, path: str, **values: str) -> str:
    base = base_url.rstrip("/")
    rendered = path
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", quote(str(value), safe=""))
    if base.endswith("/v1") and rendered.startswith("/v1/"):
        rendered = rendered[3:]
    return f"{base}{rendered if rendered.startswith('/') else '/' + rendered}"


def _raise_for_response(response: requests.Response, action: str) -> None:
    if response.ok:
        return
    raise DirectVideoModelError(_format_response_error(response, action))


def _format_response_error(response: requests.Response, action: str) -> str:
    status = f"{response.status_code} {response.reason or ''}".strip()
    body = _response_excerpt(response)
    message = f"{action}失败：HTTP {status}，url={response.url}"
    if body:
        message += f"，上游返回：{body}"
    return message


def _response_excerpt(response: requests.Response, limit: int = 1200) -> str:
    text = ""
    try:
        payload = response.json()
        text = json.dumps(payload, ensure_ascii=False)
    except ValueError:
        text = response.text or ""
    text = re.sub(r"(?i)(authorization\\s*[:=]\\s*bearer\\s+)[^\\s,;]+", r"\\1***", text)
    text = re.sub(r"(?i)(api[_-]?key\\s*[:=]\\s*)[^\\s,;]+", r"\\1***", text)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _normalize_output_url(value: str | None) -> str:
    text = str(value or "").strip()
    if text.startswith(("http://", "https://", "data:video/")):
        return text
    return ""


def _resolve_reference_image(first_frame: FirstFrameAsset | None) -> str | None:
    if not first_frame:
        return None
    source = first_frame.source or first_frame.first_frame_image_url or first_frame.first_frame_storage_key
    if not source:
        return None
    text = str(source).strip()
    if text.startswith(("http://", "https://", "data:")):
        return text
    path = Path(text).expanduser()
    if not path.exists() or not path.is_file():
        return None
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _read_first_path(payload: Any, paths: list[str]) -> str:
    for path in paths:
        current = payload
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = None
                break
        if isinstance(current, str) and current.strip():
            return current.strip()
        if isinstance(current, (int, float)) and current:
            return str(current)
    return ""


def _read_progress(payload: Any, paths: list[str]) -> int | None:
    for path in paths:
        current = payload
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = None
                break
        value = _coerce_progress(current)
        if value is not None:
            return value
    return _find_progress_by_key(payload)


def _coerce_progress(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        text = value.strip().rstrip("%")
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if not (number == number) or number in (float("inf"), float("-inf")):
        return None
    if 0 <= number <= 1:
        number *= 100
    return max(0, min(100, int(number)))


def _find_progress_by_key(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = str(key).replace("_", "").replace("-", "").lower()
            if normalized_key in {"progress", "providerprogress"}:
                progress = _coerce_progress(value)
                if progress is not None:
                    return progress
            found = _find_progress_by_key(value)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_progress_by_key(item)
            if found is not None:
                return found
    return None


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            cleaned = _drop_empty(item)
            if cleaned in (None, "", [], {}):
                continue
            output[key] = cleaned
        return output
    if isinstance(value, list):
        return [_drop_empty(item) for item in value if _drop_empty(item) not in (None, "", [], {})]
    return value


def _size_from_ratio(ratio: str, resolution: str, *, resolution_mode: str = "ratio") -> str:
    raw = str(resolution or "").strip().lower()
    if resolution_mode == "size" and re.match(r"^\d{3,4}x\d{3,4}$", raw):
        return raw
    text = str(resolution).lower()
    if "1080" in text:
        if ratio == "1:1":
            return "1080x1080"
        if ratio == "16:9":
            return "1920x1080"
        return "1080x1920"
    if "720" in text:
        if ratio == "1:1":
            return "720x720"
        if ratio == "16:9":
            return "1280x720"
        return "720x1280"
    if ratio == "1:1":
        return "480x480"
    if ratio == "16:9":
        return "854x480"
    return "480x854"


def _normalize_yunwu_ratio(ratio: str) -> str:
    return "9:16" if ratio in {"9:16", "3:4", "2:3"} else "16:9"


def _resolve_yunwu_omni_model(model: str, has_image: bool) -> str:
    if has_image and model == "omni-flash":
        return "omni-flash-components"
    if not has_image and model == "omni-flash-components":
        return "omni-flash"
    return model


def _redact_payload_for_usage(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {key: _redact_payload_for_usage(value) for key, value in payload.items() if key.lower() not in {"api_key", "apikey", "authorization"}}
    if isinstance(payload, list):
        return [_redact_payload_for_usage(item) for item in payload]
    return payload
