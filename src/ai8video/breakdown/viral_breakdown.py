from __future__ import annotations

import json
import math
import base64
import mimetypes
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from PIL import Image, ImageOps

from ai8video.media.ffmpeg_utils import resolve_ffmpeg_bin
from ai8video.core.config import AI8VideoConfig
from ai8video.integrations.http_client import api_request
from ai8video.integrations.llm_provider import normalize_chat_completions_url
from ai8video.assets.user_files import USER_FILE_ROOT


VIRAL_BREAKDOWN_ROOT = (USER_FILE_ROOT / "爆款拆解").resolve()
VIRAL_BREAKDOWN_SOURCE_VIDEO_DIR = (VIRAL_BREAKDOWN_ROOT / "原视频").resolve()
VIRAL_BREAKDOWN_FRAME_DIR = (VIRAL_BREAKDOWN_ROOT / "截图").resolve()
VIRAL_BREAKDOWN_GRID_DIR = (VIRAL_BREAKDOWN_ROOT / "宫格图").resolve()
VIRAL_BREAKDOWN_TRANSCRIPT_DIR = (VIRAL_BREAKDOWN_ROOT / "台词").resolve()
VIRAL_BREAKDOWN_GENERATED_VIDEO_DIR = (VIRAL_BREAKDOWN_ROOT / "用户生成视频").resolve()
VIRAL_BREAKDOWN_WHISPER_CACHE_DIR = (VIRAL_BREAKDOWN_ROOT / ".model-cache" / "faster-whisper").resolve()
DEFAULT_WHISPER_MODEL_DOWNLOAD_ENDPOINT = "https://hf-mirror.com"

SUPPORTED_VIRAL_BREAKDOWN_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".mkv",
    ".avi",
}
SUPPORTED_GRID_IMAGE_EXTENSION = ".jpg"
SUPPORTED_TARGET_RATIOS = {
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "1:1": 1.0,
}


def ensure_viral_breakdown_dirs() -> Path:
    for path in (
        VIRAL_BREAKDOWN_ROOT,
        VIRAL_BREAKDOWN_SOURCE_VIDEO_DIR,
        VIRAL_BREAKDOWN_FRAME_DIR,
        VIRAL_BREAKDOWN_GRID_DIR,
        VIRAL_BREAKDOWN_TRANSCRIPT_DIR,
        VIRAL_BREAKDOWN_GENERATED_VIDEO_DIR,
        VIRAL_BREAKDOWN_WHISPER_CACHE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return VIRAL_BREAKDOWN_ROOT


def _normalize_viral_breakdown_relative_key(raw_key: object, *, field_name: str) -> str:
    decoded_key = unquote(str(raw_key or "")).strip().lstrip("/")
    if not decoded_key:
        raise ValueError(f"{field_name} is required")
    if Path(decoded_key).is_absolute():
        raise ValueError(f"{field_name} must be relative")
    return decoded_key


def resolve_viral_breakdown_video_path(video_key: object) -> tuple[Path, str]:
    ensure_viral_breakdown_dirs()
    normalized_key = _normalize_viral_breakdown_relative_key(video_key, field_name="videoKey")
    target = (VIRAL_BREAKDOWN_ROOT / normalized_key).resolve()
    if not _is_within(VIRAL_BREAKDOWN_ROOT, target):
        raise ValueError("videoKey is outside viral breakdown root")
    if target.suffix.lower() not in SUPPORTED_VIRAL_BREAKDOWN_VIDEO_EXTENSIONS:
        raise ValueError("videoKey must point to a supported video")
    if not target.is_file():
        raise FileNotFoundError("video not found")
    return target, target.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix()


def resolve_viral_breakdown_asset_path(asset_key: object) -> tuple[Path, str]:
    ensure_viral_breakdown_dirs()
    normalized_key = _normalize_viral_breakdown_relative_key(asset_key, field_name="asset key")
    target = (VIRAL_BREAKDOWN_ROOT / normalized_key).resolve()
    if not _is_within(VIRAL_BREAKDOWN_ROOT, target):
        raise ValueError("asset key is outside viral breakdown root")
    if not target.is_file():
        raise FileNotFoundError("asset not found")
    return target, target.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix()


def list_viral_breakdown_items(limit: int = 200) -> dict[str, Any]:
    ensure_viral_breakdown_dirs()
    items: list[dict[str, Any]] = []
    for source_video_path in sorted(VIRAL_BREAKDOWN_SOURCE_VIDEO_DIR.glob("*"), key=lambda path: path.stat().st_mtime, reverse=True):
        if not source_video_path.is_file() or source_video_path.suffix.lower() not in SUPPORTED_VIRAL_BREAKDOWN_VIDEO_EXTENSIONS:
            continue
        items.append(_build_viral_breakdown_item(source_video_path))
        if len(items) >= max(1, min(200, int(limit or 200))):
            break
    summary = _describe_directory(VIRAL_BREAKDOWN_ROOT)
    return {
        "root": str(VIRAL_BREAKDOWN_ROOT),
        "itemCount": len(items),
        "sizeBytes": summary["sizeBytes"],
        "sizeLabel": summary["sizeLabel"],
        "archiveDisplay": f"{len(items)} 个视频 · {summary['sizeLabel']}",
        "items": items,
    }


def process_viral_breakdown_video_frames(
    video_key: object,
    *,
    interval_seconds: float = 1.0,
    target_ratio: str = "16:9",
) -> dict[str, Any]:
    video_path, relative_video_key = resolve_viral_breakdown_video_path(video_key)
    safe_interval_seconds = max(0.2, min(60.0, float(interval_seconds or 1.0)))
    ratio_key = target_ratio if str(target_ratio or "") in SUPPORTED_TARGET_RATIOS else "16:9"
    video_stem = video_path.stem
    frame_output_dir = VIRAL_BREAKDOWN_FRAME_DIR / video_stem
    grid_output_path = VIRAL_BREAKDOWN_GRID_DIR / f"{video_stem}-{ratio_key.replace(':', 'x')}{SUPPORTED_GRID_IMAGE_EXTENSION}"
    _reset_directory(frame_output_dir)
    _extract_video_frames(video_path, frame_output_dir, interval_seconds=safe_interval_seconds)
    frame_paths = sorted(frame_output_dir.glob("frame-*.jpg"))
    if not frame_paths:
        raise RuntimeError("没有截到任何画面，请检查视频是否可读")
    grid_columns, grid_rows = _pick_grid_dimensions(len(frame_paths), SUPPORTED_TARGET_RATIOS[ratio_key])
    _compose_grid_image(
        frame_paths,
        grid_output_path,
        grid_columns=grid_columns,
        grid_rows=grid_rows,
    )
    payload = {
        "ok": True,
        "videoKey": relative_video_key,
        "frameDirKey": frame_output_dir.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix(),
        "frameCount": len(frame_paths),
        "intervalSeconds": safe_interval_seconds,
        "targetRatio": ratio_key,
        "gridColumns": grid_columns,
        "gridRows": grid_rows,
        "gridImageKey": grid_output_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix(),
        "gridImageUrl": f"/api/viral-breakdown/file?key={grid_output_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix()}",
    }
    _write_json(frame_output_dir / "meta.json", payload)
    return payload


def transcribe_viral_breakdown_video(
    video_key: object,
    *,
    model_name: str = "base",
) -> dict[str, Any]:
    video_path, relative_video_key = resolve_viral_breakdown_video_path(video_key)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("本机还没有安装 faster-whisper，暂时无法分析台词") from exc
    transcript_json_path = VIRAL_BREAKDOWN_TRANSCRIPT_DIR / f"{video_path.stem}.json"
    transcript_text_path = VIRAL_BREAKDOWN_TRANSCRIPT_DIR / f"{video_path.stem}.txt"
    resolved_model_name = str(model_name or "base")
    whisper_model = _load_faster_whisper_model(WhisperModel, resolved_model_name)
    try:
        segments, info = whisper_model.transcribe(str(video_path), vad_filter=True, beam_size=5)
    except Exception as exc:
        raise RuntimeError(f"Whisper 台词识别失败：{_normalize_runtime_error_message(exc)}") from exc
    normalized_segments: list[dict[str, Any]] = []
    transcript_lines: list[str] = []
    for segment in segments:
        text = str(segment.text or "").strip()
        if not text:
            continue
        normalized_segments.append(
            {
                "start": round(float(segment.start or 0.0), 3),
                "end": round(float(segment.end or 0.0), 3),
                "text": text,
            }
        )
        transcript_lines.append(text)
    transcript_text = "\n".join(transcript_lines).strip()
    payload = {
        "ok": True,
        "videoKey": relative_video_key,
        "language": str(getattr(info, "language", "") or ""),
        "durationSeconds": float(getattr(info, "duration", 0.0) or 0.0),
        "text": transcript_text,
        "segments": normalized_segments,
        "model": resolved_model_name,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(transcript_json_path, payload)
    transcript_text_path.write_text(transcript_text, encoding="utf-8")
    payload["transcriptJsonKey"] = transcript_json_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix()
    payload["transcriptTextKey"] = transcript_text_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix()
    return payload


def save_viral_breakdown_transcript(
    video_key: object,
    *,
    transcript_text: object,
) -> dict[str, Any]:
    video_path, relative_video_key = resolve_viral_breakdown_video_path(video_key)
    normalized_transcript_text = str(transcript_text if transcript_text is not None else "").replace("\r\n", "\n")
    transcript_json_path = VIRAL_BREAKDOWN_TRANSCRIPT_DIR / f"{video_path.stem}.json"
    transcript_text_path = VIRAL_BREAKDOWN_TRANSCRIPT_DIR / f"{video_path.stem}.txt"
    existing_payload = _read_json(transcript_json_path)
    payload = {
        "ok": True,
        "videoKey": relative_video_key,
        "language": str(existing_payload.get("language") or ""),
        "durationSeconds": float(existing_payload.get("durationSeconds", 0.0) or 0.0),
        "text": normalized_transcript_text,
        "segments": existing_payload.get("segments") if isinstance(existing_payload.get("segments"), list) else [],
        "model": str(existing_payload.get("model") or ""),
        "generatedAt": str(existing_payload.get("generatedAt") or datetime.now(timezone.utc).isoformat()),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "manuallyEdited": True,
    }
    _write_json(transcript_json_path, payload)
    transcript_text_path.write_text(normalized_transcript_text, encoding="utf-8")
    payload["transcriptJsonKey"] = transcript_json_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix()
    payload["transcriptTextKey"] = transcript_text_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix()
    return payload


def guess_viral_breakdown_script(
    video_key: object,
    *,
    transcript_text: object,
    config: AI8VideoConfig,
) -> dict[str, Any]:
    video_path, relative_video_key = resolve_viral_breakdown_video_path(video_key)
    if not (config.multimodal_base_url and config.multimodal_api_key and config.multimodal_model):
        raise RuntimeError("多模态模型配置不完整，请先在设置里填写接口地址、API Key 和模型名")
    grid_image_path = _find_latest_grid_image_path(video_path.stem)
    if not grid_image_path or not grid_image_path.is_file():
        raise RuntimeError("还没有可用的拼接宫格图，请先点击“拆解画面”")
    normalized_transcript_text = str(transcript_text if transcript_text is not None else "").strip()
    if not normalized_transcript_text:
        raise RuntimeError("还没有可用台词，请先点击“分析台词”或手动填写台词")

    response_text = _request_multimodal_script_guess(
        config,
        grid_image_path=grid_image_path,
        transcript_text=normalized_transcript_text,
    )
    return {
        "ok": True,
        "videoKey": relative_video_key,
        "gridImageKey": grid_image_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix(),
        "text": response_text,
        "model": str(config.multimodal_model or ""),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }


def stream_viral_breakdown_script_guess(
    video_key: object,
    *,
    transcript_text: object,
    config: AI8VideoConfig,
):
    video_path, _relative_video_key = resolve_viral_breakdown_video_path(video_key)
    if not (config.multimodal_base_url and config.multimodal_api_key and config.multimodal_model):
        raise RuntimeError("多模态模型配置不完整，请先在设置里填写接口地址、API Key 和模型名")
    grid_image_path = _find_latest_grid_image_path(video_path.stem)
    if not grid_image_path or not grid_image_path.is_file():
        raise RuntimeError("还没有可用的拼接宫格图，请先点击“拆解画面”")
    normalized_transcript_text = str(transcript_text if transcript_text is not None else "").strip()
    if not normalized_transcript_text:
        raise RuntimeError("还没有可用台词，请先点击“分析台词”或手动填写台词")
    yield from _stream_multimodal_script_guess(
        config,
        grid_image_path=grid_image_path,
        transcript_text=normalized_transcript_text,
    )


def _load_faster_whisper_model(whisper_model_class: type, model_name: str):
    ensure_viral_breakdown_dirs()
    os.environ.setdefault("HF_ENDPOINT", DEFAULT_WHISPER_MODEL_DOWNLOAD_ENDPOINT)
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        return whisper_model_class(
            model_name,
            device="cpu",
            compute_type="int8",
            download_root=str(VIRAL_BREAKDOWN_WHISPER_CACHE_DIR),
        )
    except Exception as exc:
        error_message = _normalize_runtime_error_message(exc)
        lowered_message = error_message.lower()
        if (
            "localentrynotfounderror" in lowered_message
            or "snapshot folder" in lowered_message
            or "connecterror" in lowered_message
            or "huggingface" in lowered_message
            or "ssl:" in lowered_message
            or "unexpected_eof_while_reading" in lowered_message
        ):
            raise RuntimeError(
                "Whisper 模型还没缓存到本机，且当前直连 huggingface.co 失败。"
                f"系统已默认切到 {DEFAULT_WHISPER_MODEL_DOWNLOAD_ENDPOINT} 作为模型镜像，并会把模型缓存到本地。"
                "请再点一次“分析台词”重试下载；如果仍失败，再检查本机网络或代理。"
            ) from exc
        raise RuntimeError(f"Whisper 模型加载失败：{error_message}") from exc


def _request_multimodal_script_guess(
    config: AI8VideoConfig,
    *,
    grid_image_path: Path,
    transcript_text: str,
) -> str:
    image_data_url = _encode_image_file_as_data_url(grid_image_path)
    response = api_request(
        "POST",
        normalize_chat_completions_url(config.multimodal_base_url or ""),
        headers={
            "Authorization": f"Bearer {config.multimodal_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.multimodal_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是短剧编剧。只根据分镜宫格图和台词反推完整剧本，直接输出剧本正文，不要解释、不要寒暄、不要写分析过程。",
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请根据这张拼接后的分镜宫格图和下面识别到的台词，反推出可直接拍摄/生成的完整剧本。只输出剧本，不要废话。\n\n台词：\n" + transcript_text,
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url},
                        },
                    ],
                },
            ],
            "temperature": 0.2,
        },
        timeout=config.timeout_seconds,
    )
    if response.status_code >= 400:
        raise RuntimeError(_format_multimodal_http_error(response))
    data = response.json()
    choices = data.get("choices") if isinstance(data, dict) else []
    if not choices:
        raise RuntimeError(f"多模态模型响应缺少 choices：{data}")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    raise RuntimeError(f"多模态模型响应缺少文本内容：{data}")


def _stream_multimodal_script_guess(
    config: AI8VideoConfig,
    *,
    grid_image_path: Path,
    transcript_text: str,
):
    image_data_url = _encode_image_file_as_data_url(grid_image_path)
    response = api_request(
        "POST",
        normalize_chat_completions_url(config.multimodal_base_url or ""),
        headers={
            "Authorization": f"Bearer {config.multimodal_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.multimodal_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是短剧编剧。只根据分镜宫格图和台词反推完整剧本，直接输出剧本正文，不要解释、不要寒暄、不要写分析过程。",
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请根据这张拼接后的分镜宫格图和下面识别到的台词，反推出可直接拍摄/生成的完整剧本。只输出剧本，不要废话。\n\n台词：\n" + transcript_text,
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url},
                        },
                    ],
                },
            ],
            "stream": True,
            "temperature": 0.2,
        },
        stream=True,
        timeout=config.timeout_seconds,
    )
    if response.status_code >= 400:
        raise RuntimeError(_format_multimodal_http_error(response))
    content_type = str(response.headers.get("Content-Type") or "").lower()
    if "text/event-stream" not in content_type:
        data = response.json()
        choices = data.get("choices") if isinstance(data, dict) else []
        if not choices:
            return
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content:
            yield content
        return
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
        if content is None:
            message = choices[0].get("message") or {}
            content = message.get("content")
        if isinstance(content, str) and content:
            yield content


def _encode_image_file_as_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _format_multimodal_http_error(response) -> str:
    status_code = getattr(response, "status_code", "")
    body = ""
    try:
        body = str(response.text or "").strip()
    except Exception:
        body = ""
    if not body:
        return f"多模态模型请求失败（HTTP {status_code}）"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return f"多模态模型请求失败（HTTP {status_code}）：{body[:500]}"
    error_payload = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error_payload, dict):
        message = str(error_payload.get("message") or "").strip()
        if message:
            return f"多模态模型请求失败（HTTP {status_code}）：{message}"
    message = str(payload.get("message") or "").strip() if isinstance(payload, dict) else ""
    return f"多模态模型请求失败（HTTP {status_code}）：{message or body[:500]}"


def _normalize_runtime_error_message(error: Exception) -> str:
    return str(error or "").strip() or error.__class__.__name__


def _build_viral_breakdown_item(source_video_path: Path) -> dict[str, Any]:
    stat = source_video_path.stat()
    relative_video_key = source_video_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix()
    transcript_json_path = VIRAL_BREAKDOWN_TRANSCRIPT_DIR / f"{source_video_path.stem}.json"
    transcript_payload = _read_json(transcript_json_path)
    generated_video_path = _find_generated_video_path(source_video_path.stem)
    grid_image_path = _find_latest_grid_image_path(source_video_path.stem)
    frame_dir_path = VIRAL_BREAKDOWN_FRAME_DIR / source_video_path.stem
    frame_count = len(sorted(frame_dir_path.glob("frame-*.jpg"))) if frame_dir_path.is_dir() else 0
    related_size_bytes = stat.st_size
    if grid_image_path and grid_image_path.is_file():
        related_size_bytes += grid_image_path.stat().st_size
    if transcript_json_path.is_file():
        related_size_bytes += transcript_json_path.stat().st_size
    transcript_text_path = VIRAL_BREAKDOWN_TRANSCRIPT_DIR / f"{source_video_path.stem}.txt"
    if transcript_text_path.is_file():
        related_size_bytes += transcript_text_path.stat().st_size
    if frame_dir_path.is_dir():
        related_size_bytes += _directory_size_bytes(frame_dir_path)
    if generated_video_path and generated_video_path.is_file():
        related_size_bytes += generated_video_path.stat().st_size
    return {
        "name": source_video_path.name,
        "videoKey": relative_video_key,
        "videoUrl": f"/api/viral-breakdown/file?key={relative_video_key}",
        "videoLocalPath": str(source_video_path.resolve()),
        "sizeBytes": stat.st_size,
        "sizeLabel": _format_bytes(stat.st_size),
        "archiveSizeBytes": related_size_bytes,
        "archiveSizeLabel": _format_bytes(related_size_bytes),
        "updatedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "frameCount": frame_count,
        "gridImageKey": grid_image_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix() if grid_image_path else "",
        "gridImageUrl": f"/api/viral-breakdown/file?key={grid_image_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix()}" if grid_image_path else "",
        "transcriptText": str(transcript_payload.get("text") or "").strip(),
        "transcriptJsonKey": transcript_json_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix() if transcript_json_path.is_file() else "",
        "generatedVideoKey": generated_video_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix() if generated_video_path else "",
        "generatedVideoUrl": f"/api/viral-breakdown/file?key={generated_video_path.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix()}" if generated_video_path else "",
    }


def _find_generated_video_path(video_stem: str) -> Path | None:
    for suffix in sorted(SUPPORTED_VIRAL_BREAKDOWN_VIDEO_EXTENSIONS):
        candidate = VIRAL_BREAKDOWN_GENERATED_VIDEO_DIR / f"{video_stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _find_latest_grid_image_path(video_stem: str) -> Path | None:
    candidates = sorted(VIRAL_BREAKDOWN_GRID_DIR.glob(f"{video_stem}-*{SUPPORTED_GRID_IMAGE_EXTENSION}"))
    return candidates[-1] if candidates else None


def _extract_video_frames(video_path: Path, frame_output_dir: Path, *, interval_seconds: float) -> None:
    ffmpeg_bin = resolve_ffmpeg_bin()
    output_pattern = frame_output_dir / "frame-%04d.jpg"
    command = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps=1/{interval_seconds}",
        "-vsync",
        "vfr",
        str(output_pattern),
    ]
    process = subprocess.run(command, check=False, capture_output=True, text=True)
    if process.returncode != 0:
        error_text = (process.stderr or process.stdout or "ffmpeg failed").strip()
        raise RuntimeError(f"视频截图失败：{error_text}")


def _compose_grid_image(
    frame_paths: list[Path],
    output_path: Path,
    *,
    grid_columns: int,
    grid_rows: int,
) -> None:
    if not frame_paths:
        raise RuntimeError("没有可用截图")
    with Image.open(frame_paths[0]) as first_image:
        source_width, source_height = first_image.size
    max_canvas_long_edge = 1920
    base_canvas_width = source_width * grid_columns
    base_canvas_height = source_height * grid_rows
    scale = min(1.0, max_canvas_long_edge / max(base_canvas_width, base_canvas_height, 1))
    cell_width = max(80, int(source_width * scale))
    cell_height = max(80, int(source_height * scale))
    canvas = Image.new("RGB", (cell_width * grid_columns, cell_height * grid_rows), color=(12, 16, 24))
    for index, frame_path in enumerate(frame_paths):
        row_index = index // grid_columns
        column_index = index % grid_columns
        if row_index >= grid_rows:
            break
        with Image.open(frame_path) as raw_image:
            normalized_image = ImageOps.contain(raw_image.convert("RGB"), (cell_width, cell_height))
        x_offset = column_index * cell_width + max(0, (cell_width - normalized_image.width) // 2)
        y_offset = row_index * cell_height + max(0, (cell_height - normalized_image.height) // 2)
        canvas.paste(normalized_image, (x_offset, y_offset))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=88)


def _pick_grid_dimensions(frame_count: int, target_ratio_value: float) -> tuple[int, int]:
    safe_frame_count = max(1, int(frame_count or 1))
    best_columns = safe_frame_count
    best_rows = 1
    best_score = float("inf")
    for row_count in range(1, safe_frame_count + 1):
        column_count = math.ceil(safe_frame_count / row_count)
        ratio_value = column_count / row_count
        empty_slots = column_count * row_count - safe_frame_count
        score = abs(math.log(max(ratio_value, 1e-6) / max(target_ratio_value, 1e-6))) + empty_slots * 0.08
        if score < best_score:
            best_score = score
            best_columns = column_count
            best_rows = row_count
    return best_columns, best_rows


def _describe_directory(path: Path) -> dict[str, Any]:
    ensure_viral_breakdown_dirs()
    file_count = 0
    total_bytes = 0
    for source in path.rglob("*"):
        if not source.is_file():
            continue
        file_count += 1
        total_bytes += source.stat().st_size
    return {
        "path": str(path),
        "fileCount": file_count,
        "sizeBytes": total_bytes,
        "sizeLabel": _format_bytes(total_bytes),
    }


def _format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(0, int(size or 0)))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


def _directory_size_bytes(path: Path) -> int:
    total_bytes = 0
    for source in path.rglob("*"):
        if source.is_file():
            total_bytes += source.stat().st_size
    return total_bytes


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _reset_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False
