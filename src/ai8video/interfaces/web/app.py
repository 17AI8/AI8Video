from __future__ import annotations

import logging
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from bottle import Bottle, HTTPResponse, request, response, run, static_file

from ai8video.application.facade import (
    CHAT_BACKEND,
    build_batch_seed_file_payload,
    cancel_chat as cancel_chat_via_ai8video,
    get_assets_payload,
    get_batch_alerts_payload,
    get_batch_reports_payload,
    get_chat_status as get_chat_status_via_ai8video,
    get_health_payload,
    get_supervisor_admin_result_path,
    handle_chat as handle_chat_via_ai8video,
    run_batch_payload,
    write_supervisor_admin_result_payload,
)
from ai8video.core.config import AI8VideoConfig, load_ai8video_core_model_settings
from ai8video.core.legacy_payload import normalize_legacy_video_payload
from ai8video.core.paths import PROJECT_ROOT
from ai8video.interfaces.web.static_bundle import read_workbench_script
from ai8video.interfaces.web.transport import (
    ThreadingWSGIRefServer,
    allowed_cors_origin as _allowed_cors_origin,
    install_transport_hooks,
    read_query_string_value as _read_query_string_value,
    should_reject_untrusted_browser_write as _should_reject_untrusted_browser_write,
)
from ai8video.integrations.direct_video_model_client import AI8VideoModelClient
from ai8video.generation.business_prompt import (
    business_prompt_meta,
    read_business_prompt,
    sanitize_internal_fidelity_notes,
    write_business_prompt,
)
from ai8video.media.background_music import (
    background_music_status,
    clear_background_music_selection,
    ensure_background_music_dir,
    mix_background_music,
    save_background_music_upload,
    select_background_music,
    update_background_music_volume,
    update_preserve_original_audio,
)
from ai8video.assets.default_reference_image import (
    clear_default_reference_image,
    default_reference_image_status,
    select_default_reference_image,
    update_default_reference_image_options,
)
from ai8video.knowledge.default_script_reference import (
    apply_default_script_reference,
    clear_default_script_reference,
    default_script_reference_status,
    load_default_script_reference,
    retrieve_script_reference_context,
    select_default_script_reference,
)
from ai8video.generation.video_prompt_planner import plan_video_prompts_with_ai
from ai8video.knowledge.script_knowledge_query import build_script_query_llm
from ai8video.knowledge.script_knowledge_rerank import build_script_rerank_llm
from ai8video.generation.generation_mode import (
    generation_mode_status,
    update_generation_mode,
)
from ai8video.media.motion.html_motion_overlay import (
    HTML_MOTION_DIR,
    apply_html_motion_overlay,
    build_html_motion_llm,
    html_motion_overlay_status,
    html_motion_safe_zone_status,
    update_html_motion_overlay,
    update_html_motion_beat_interval_seconds,
    update_html_motion_smart_beat_interval,
    update_html_motion_quality_retry_count,
    update_html_motion_safe_zone,
)
from ai8video.media.motion.html_motion_review import (
    HTML_MOTION_REVIEW_ROOT,
    confirm_html_motion_review,
    html_motion_review_status,
    prepare_html_motion_review,
    resolve_html_motion_review_video,
    sync_html_motion_review_audio,
)
from ai8video.media.motion.html_motion_tasks import html_motion_task_service
from ai8video.media.narration_review import (
    narration_review_status,
    update_narration_review_count,
)
from ai8video.core.models import VideoPrompt, FirstFrameAsset, ParsedRequest, QuickVideoJob
from ai8video.generation.pipeline import AI8VideoPipeline
from ai8video.generation.reference_image_preprocessor import ReferenceImagePreprocessor
from ai8video.generation.prompt_trace import append_prompt_trace
from ai8video.media.local_tts import (
    attach_local_tts_to_video,
    ensure_local_tts_dir,
    local_tts_output_dir,
    local_tts_status,
    local_tts_voice_clone_cache_signature,
    prepare_narration_text,
    local_tts_voice_clone_dir,
    save_local_tts_voice_clone_upload,
    synthesize_local_tts,
    update_local_tts_settings,
)
from ai8video.media.video_merge_mode import (
    load_video_merge_mode,
    save_video_merge_mode,
    video_merge_mode_status,
)
from ai8video.media.video_text_overlay import (
    SUPPORTED_FONT_EXTENSIONS,
    USER_FONT_DIR,
    USER_FONT_PREVIEW_DIR,
    VIDEO_TEXT_PREVIEW_BACKGROUND_DIR,
    clear_video_text_preview_background_image,
    render_video_text_overlay_preview,
    save_video_text_preview_background_upload,
    update_video_text_overlay,
    video_text_preview_background_url,
    video_text_overlay_status,
)
from ai8video.batch.batch_seed_file import resolve_batch_seed_file_path
from ai8video.batch.live_preflight import SAFE_PREFLIGHT_CHECKS, run_preflight_checks
from ai8video.batch.specialist_agent_observer import (
    shutdown_specialist_agent_scheduler,
    start_specialist_agent_scheduler,
)
from ai8video.assets.asset_maintenance import AssetMaintenanceService
from ai8video.assets.asset_store import JsonlAssetStore
from ai8video.integrations.llm_provider import build_openai_compat_llm
from ai8video.batch.supervisor_launchd import (
    build_launchd_plist,
    default_launchd_plist_path,
    inspect_launchd_deployment,
    install_launchd_service,
    uninstall_launchd_service,
    write_launchd_plist,
)
from ai8video.assets.user_materials import (
    IMAGE_MATERIAL_EXTENSIONS,
    SCRIPT_MATERIAL_EXTENSIONS,
    USER_FLOWER_WATERMARK_DIR,
    USER_IMAGE_MATERIAL_DIR,
    delete_user_material,
    ensure_user_material_dirs,
    list_user_materials,
    material_dir,
)
from ai8video.knowledge.script_knowledge import (
    ScriptKnowledgeUnavailable,
    get_script_knowledge_store,
    index_script_path,
    remove_script_knowledge_document,
    script_knowledge_payload,
)
from ai8video.knowledge.script_knowledge_ingestion import (
    script_knowledge_ingestion_status,
    start_script_knowledge_ingestion,
)
from ai8video.assets.upload_utils import resolve_upload_filename
from ai8video.assets.user_generated_results import (
    USER_GENERATED_RESULT_ROOT,
    ensure_user_generated_result_dir,
    is_simulated_user_generated_result_path,
    migrate_legacy_result_layout,
)
from ai8video.assets.user_files import USER_FILE_ROOT
from ai8video.assets.user_recycle_bin import (
    RESTORED_RESULT_METADATA_DIR,
    USER_RECYCLE_BIN_ROOT,
    delete_restored_result_metadata,
    delete_failed_video_tasks,
    ensure_user_recycle_bin_dir,
    humanize_failed_video_reason,
    load_restored_result_metadata,
    list_failed_video_tasks,
    restored_result_metadata_path,
    restore_failed_video_task,
    save_restored_result_html_motion_overlay,
    save_restored_result_narration_text,
)
from ai8video.generation.prompt_trace import TRACE_PATH as PROMPT_TRACE_PATH
from ai8video.generation.generation_progress import (
    clear_generation_progress,
    settle_stale_first_frame_progress,
    stop_unsubmitted_generation_progress,
)
from ai8video.assets.user_generated_previews import (
    PREVIEW_DIR_NAME,
    delete_preview_for_video,
    find_preview_key,
    generate_preview_for_video,
    regenerate_previews_for_videos,
)
from ai8video.breakdown.viral_breakdown import (
    SUPPORTED_VIRAL_BREAKDOWN_VIDEO_EXTENSIONS,
    VIRAL_BREAKDOWN_ROOT,
    VIRAL_BREAKDOWN_SOURCE_VIDEO_DIR,
    ensure_viral_breakdown_dirs,
    list_viral_breakdown_items,
    process_viral_breakdown_video_frames,
    guess_viral_breakdown_script,
    resolve_viral_breakdown_asset_path,
    save_viral_breakdown_transcript,
    stream_viral_breakdown_script_guess,
    transcribe_viral_breakdown_video,
)
from ai8video.interfaces.web.routes.hot_topics import (
    api_hot_topic_sources,
    api_hot_topic_summary,
    api_hot_topic_to_prompt,
    api_hot_topics,
    register_hot_topic_routes,
)
from ai8video.integrations.video_model_settings import (
    load_video_model_settings,
    pull_model_catalog,
    pull_video_model_catalog,
    save_video_model_settings,
)
from ai8video.integrations.model_catalogs import (
    load_model_catalog,
    load_model_catalogs,
    save_model_catalog,
)
from ai8video.integrations.model_overrides import save_model_override
from ai8video.media.video_segment_postprocess import concat_videos, extract_frame_at_time, trim_video_end


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

app = Bottle()
logger = logging.getLogger(__name__)
LEGACY_ARCHIVE_DIR = (PROJECT_ROOT / "temp" / "ai8video" / "archive").resolve()
DEFAULT_WEB_CHAT_TIMEOUT_SECONDS = 600
USER_GENERATED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
USER_GENERATED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
LOCAL_TTS_PREVIEW_TEXT = "今天天气真好，你下载AI8video 了吗"
ARCHIVE_ARTIFACT_IMAGE_EXTENSIONS = USER_GENERATED_IMAGE_EXTENSIONS
ARCHIVE_ARTIFACT_AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg"}
ARCHIVE_ARTIFACT_JSON_EXTENSIONS = {".json", ".jsonl"}
ARCHIVE_ARTIFACT_ALL_EXTENSIONS = None
MERGE_TEMP_MEDIA_DIR = (USER_FILE_ROOT / "临时媒体" / "视频合并").resolve()
TRANSFORMED_REFERENCE_DIR = (USER_FILE_ROOT / "参考图" / "图生图结果").resolve()
def read_query_string_value(field_name: str) -> str:
    return _read_query_string_value(request, field_name)


_reject_untrusted_browser_writes, _cors_headers = install_transport_hooks(app)


@app.route("/", method=["GET"])
def index():
    return static_file("index.html", root=str(STATIC_DIR))


@app.route("/static/<relative_path:path>", method=["GET"])
def static_asset(relative_path: str):
    clean_path = str(relative_path or "").strip().lstrip("/")
    if clean_path == "workbench.js":
        response.content_type = "application/javascript; charset=UTF-8"
        return read_workbench_script(STATIC_DIR)
    return static_file(clean_path, root=str(STATIC_DIR))
@app.route("/api/user-materials", method=["GET", "OPTIONS"])
def api_user_materials():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    return list_user_materials()


@app.route("/api/script-knowledge", method=["GET", "OPTIONS"])
def api_script_knowledge():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    query = read_query_string_value("q").strip()
    try:
        limit = int(read_query_string_value("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    try:
        return script_knowledge_payload(query, limit=limit)
    except Exception as exc:
        response.status = 503
        return {"ok": False, "error": _script_knowledge_error(exc), "items": []}


@app.route("/api/script-knowledge/<document_id:int>", method=["GET", "POST", "OPTIONS"])
def api_script_knowledge_document(document_id: int):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    store = get_script_knowledge_store()
    try:
        if request.method == "GET":
            return {"ok": True, "document": store.get_document(document_id)}
        payload = request.json or {}
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        tags = payload.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError("tags must be an array")
        document = store.update_document(
            document_id,
            title=str(payload.get("title") or ""),
            summary=str(payload.get("summary") or ""),
            tags=[str(tag) for tag in tags],
        )
        return {"ok": True, "document": document}
    except KeyError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc).strip("'")}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        response.status = 503
        return {"ok": False, "error": _script_knowledge_error(exc)}


@app.route("/api/script-knowledge/<document_id:int>/ingest", method=["GET", "POST", "OPTIONS"])
def api_script_knowledge_ingest(document_id: int):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        if request.method == "GET":
            return {"ok": True, "job": script_knowledge_ingestion_status(document_id)}
        return {"ok": True, "job": start_script_knowledge_ingestion(document_id, AI8VideoConfig.from_env())}
    except Exception as exc:
        response.status = 503
        return {"ok": False, "error": _script_knowledge_error(exc)}


@app.route("/api/open-user-material-folder", method=["POST", "OPTIONS"])
def api_open_user_material_folder():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    target = material_dir(str(payload.get("kind") or "root"))
    ensure_user_material_dirs()
    if not target.exists():
        response.status = 404
        return {"error": "material folder not found"}
    _open_in_file_manager(target)
    return {"ok": True, "path": str(target)}


@app.route("/api/open-background-music-folder", method=["POST", "OPTIONS"])
def api_open_background_music_folder():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = ensure_background_music_dir()
    _open_in_file_manager(target)
    return {"ok": True, "path": str(target)}


@app.route("/api/open-viral-breakdown-folder", method=["POST", "OPTIONS"])
def api_open_viral_breakdown_folder():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = ensure_viral_breakdown_dirs()
    _open_in_file_manager(target)
    return {"ok": True, "path": str(target)}


@app.route("/api/upload-user-material", method=["POST", "OPTIONS"])
def api_upload_user_material():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    kind = str(request.forms.get("kind") or "image")
    normalized_kind = kind.strip().lower()
    target_dir = material_dir(kind)
    allowed_extensions = SCRIPT_MATERIAL_EXTENSIONS if target_dir == material_dir("script") else IMAGE_MATERIAL_EXTENSIONS
    ensure_user_material_dirs()
    uploads = request.files.getall("files")
    saved: list[dict[str, str | int]] = []
    skipped: list[dict[str, str]] = []
    knowledge_index: list[dict[str, Any]] = []
    # 花字水印库需要允许多张并存；前端会分别引用不同文件作为水印 1 / 水印 2。
    replace_existing = False
    did_clear_target = False
    for upload in uploads:
        if replace_existing and saved:
            skipped.append({"name": resolve_upload_filename(upload), "reason": "flower watermark keeps only the latest image"})
            continue
        source_name = resolve_upload_filename(upload)
        suffix = Path(source_name).suffix.lower()
        if not source_name or suffix not in allowed_extensions:
            skipped.append({"name": source_name, "reason": "unsupported extension"})
            continue
        if replace_existing and not did_clear_target:
            _clear_directory_files(target_dir)
            did_clear_target = True
        target = _next_available_path(target_dir, source_name)
        upload.save(str(target), overwrite=False)
        saved.append({
            "name": target.name,
            "relativePath": target.relative_to(target_dir).as_posix(),
            "path": str(target),
            "sizeBytes": target.stat().st_size,
        })
        if target_dir == material_dir("script"):
            knowledge_index.append(_index_uploaded_script(target, target_dir))
    if target_dir == material_dir("script"):
        response_kind = "script"
    elif normalized_kind in {"flower-watermark", "flower_watermark", "watermark", "watermarks", "花字水印", "花字水印库"}:
        response_kind = "flower-watermark"
    else:
        response_kind = "image"
    return {
        "ok": True,
        "kind": response_kind,
        "saved": saved,
        "skipped": skipped,
        "knowledgeIndex": knowledge_index,
    }


@app.route("/api/viral-breakdown/upload", method=["POST", "OPTIONS"])
def api_upload_viral_breakdown_video():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    ensure_viral_breakdown_dirs()
    uploads = request.files.getall("files")
    saved: list[dict[str, str | int]] = []
    skipped: list[dict[str, str]] = []
    for upload in uploads:
        source_name = resolve_upload_filename(upload)
        suffix = Path(source_name).suffix.lower()
        if not source_name or suffix not in SUPPORTED_VIRAL_BREAKDOWN_VIDEO_EXTENSIONS:
            skipped.append({"name": source_name, "reason": "unsupported extension"})
            continue
        target = _next_available_path(VIRAL_BREAKDOWN_SOURCE_VIDEO_DIR, source_name)
        upload.save(str(target), overwrite=False)
        saved.append(
            {
                "name": target.name,
                "videoKey": target.relative_to(VIRAL_BREAKDOWN_ROOT).as_posix(),
                "path": str(target),
                "sizeBytes": target.stat().st_size,
            }
        )
    return {
        "ok": True,
        "saved": saved,
        "skipped": skipped,
        "summary": list_viral_breakdown_items(limit=200),
    }


@app.route("/api/viral-breakdown", method=["GET", "OPTIONS"])
def api_viral_breakdown_status():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    limit = max(1, min(200, int(request.query.get("limit", "200"))))
    return list_viral_breakdown_items(limit=limit)


register_hot_topic_routes(app)


@app.route("/api/viral-breakdown/file", method=["GET", "OPTIONS"])
def api_viral_breakdown_file():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        target, relative_key = resolve_viral_breakdown_asset_path(read_query_string_value("key"))
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    return static_file(relative_key, root=str(VIRAL_BREAKDOWN_ROOT))


@app.route("/api/viral-breakdown/process-frames", method=["POST", "OPTIONS"])
def api_viral_breakdown_process_frames():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return process_viral_breakdown_video_frames(
            payload.get("videoKey"),
            interval_seconds=float(payload.get("intervalSeconds") or 1.0),
            target_ratio=str(payload.get("targetRatio") or "16:9"),
        )
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/viral-breakdown/transcribe", method=["POST", "OPTIONS"])
def api_viral_breakdown_transcribe():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return transcribe_viral_breakdown_video(
            payload.get("videoKey"),
            model_name=str(payload.get("model") or "base"),
        )
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/viral-breakdown/save-transcript", method=["POST", "OPTIONS"])
def api_viral_breakdown_save_transcript():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return save_viral_breakdown_transcript(
            payload.get("videoKey"),
            transcript_text=payload.get("text"),
        )
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/viral-breakdown/guess-script", method=["POST", "OPTIONS"])
def api_viral_breakdown_guess_script():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        if str(request.query.get("stream") or "").strip() == "1":
            response.content_type = "text/plain; charset=utf-8"
            response.headers["X-Accel-Buffering"] = "no"
            return stream_viral_breakdown_script_guess(
                payload.get("videoKey"),
                transcript_text=payload.get("text"),
                config=AI8VideoConfig.from_env(),
            )
        return guess_viral_breakdown_script(
            payload.get("videoKey"),
            transcript_text=payload.get("text"),
            config=AI8VideoConfig.from_env(),
        )
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/delete-user-material", method=["POST", "OPTIONS"])
def api_delete_user_material():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        result = delete_user_material(
            str(payload.get("kind") or "image"),
            str(payload.get("relativePath") or payload.get("name") or ""),
        )
        if result.get("kind") == "script":
            relative_path = str(result.get("deleted", {}).get("relativePath") or "")
            result["knowledgeIndex"] = remove_script_knowledge_document(relative_path)
        return result
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/background-music", method=["GET", "POST", "OPTIONS"])
def api_background_music():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        return background_music_status()
    upload = request.files.get("file")
    if upload is None:
        response.status = 400
        return {"ok": False, "error": "请选择 MP3 或视频文件"}
    try:
        return save_background_music_upload(upload)
    except (RuntimeError, ValueError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/background-music/select", method=["POST", "OPTIONS"])
def api_background_music_select():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    item_id = str(payload.get("id") or "").strip() if isinstance(payload, dict) else ""
    try:
        return select_background_music(item_id)
    except (RuntimeError, ValueError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/background-music/clear", method=["POST", "OPTIONS"])
def api_background_music_clear():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    return clear_background_music_selection()


@app.route("/api/background-music/volume", method=["POST", "OPTIONS"])
def api_background_music_volume():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    return update_background_music_volume(payload.get("volume"))


@app.route("/api/background-music/original-audio", method=["POST", "OPTIONS"])
def api_background_music_original_audio():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    return update_preserve_original_audio(payload.get("preserveOriginalAudio"))


@app.route("/api/open-local-tts-folder", method=["POST", "OPTIONS"])
def api_open_local_tts_folder():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = ensure_local_tts_dir()
    _open_in_file_manager(target)
    return {"ok": True, "path": str(target)}


@app.route("/api/open-local-tts-voice-clone-folder", method=["POST", "OPTIONS"])
def api_open_local_tts_voice_clone_folder():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = local_tts_voice_clone_dir()
    target.mkdir(parents=True, exist_ok=True)
    _open_in_file_manager(target)
    return {"ok": True, "path": str(target)}


@app.route("/api/local-tts", method=["GET", "POST", "OPTIONS"])
def api_local_tts():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        return local_tts_status()
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return update_local_tts_settings(payload)
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/local-tts/voice-clone", method=["POST", "OPTIONS"])
def api_local_tts_voice_clone():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    upload = request.files.get("file")
    if upload is None:
        response.status = 400
        return {"ok": False, "error": "请选择 MP3、WAV 或视频文件"}
    try:
        return save_local_tts_voice_clone_upload(upload)
    except (RuntimeError, ValueError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


def _local_tts_preview_cache_info(text: str, settings: dict[str, object]) -> tuple[str, str]:
    payload = {
        "text": str(text or "").strip(),
        "apiBaseUrl": str(settings.get("apiBaseUrl") or "").strip(),
        "model": str(settings.get("model") or "").strip(),
        "cloneModel": str(settings.get("cloneModel") or "").strip(),
        "voice": str(settings.get("voice") or "").strip(),
        "voiceCloneSample": local_tts_voice_clone_cache_signature(settings.get("voice")),
        "volume": str(settings.get("volume") or "").strip(),
    }
    digest = hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return digest, f"preview-cache-{digest}.m4a"


@app.route("/api/local-tts/preview", method=["POST", "OPTIONS"])
def api_local_tts_preview():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    text = LOCAL_TTS_PREVIEW_TEXT
    settings = local_tts_status()
    for key in (
        "voice",
        "volume",
        "apiBaseUrl",
        "apiKey",
        "model",
        "cloneModel",
    ):
        if key in payload:
            settings[key] = payload.get(key)
    output_dir = local_tts_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_key, cache_name = _local_tts_preview_cache_info(text, settings)
    audio_path = output_dir / cache_name
    cached = audio_path.is_file() and audio_path.stat().st_size > 0
    if not cached:
        if audio_path.exists():
            audio_path.unlink()
        result = synthesize_local_tts(
            text,
            audio_path,
            settings=settings,
            output_volume=float(settings.get("volume") or 1),
        )
        if result.get("status") != "generated":
            response.status = 500
            return {"ok": False, "error": result.get("reason") or "试听生成失败"}
    return {
        "ok": True,
        "text": text,
        "audioUrl": f"/api/local-tts/preview-audio/{audio_path.name}",
        "audioPath": str(audio_path),
        "cacheKey": cache_key,
        "cached": cached,
        "voice": str(settings.get("voice") or ""),
        "voiceLabel": str(settings.get("voice") or ""),
    }


@app.route("/api/local-tts/preview-audio/<filename:path>", method=["GET", "OPTIONS"])
def api_local_tts_preview_audio(filename: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    clean_name = Path(str(filename or "")).name
    if clean_name != filename or not clean_name.startswith("preview-") or not clean_name.endswith(".m4a"):
        response.status = 404
        return {"ok": False, "error": "audio not found"}
    file_response = static_file(clean_name, root=str(local_tts_output_dir()))
    file_response.set_header("Cache-Control", "public, max-age=31536000, immutable")
    return file_response


@app.route("/api/default-reference-image", method=["GET", "POST", "OPTIONS"])
def api_default_reference_image():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        return default_reference_image_status()
    payload = request.json or {}
    relative_path = str(payload.get("relativePath") or payload.get("name") or "").strip() if isinstance(payload, dict) else ""
    try:
        return select_default_reference_image(relative_path)
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/default-reference-image/clear", method=["POST", "OPTIONS"])
def api_default_reference_image_clear():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    return clear_default_reference_image()


@app.route("/api/default-reference-image/options", method=["POST", "OPTIONS"])
def api_default_reference_image_options():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    options = payload.get("options") if isinstance(payload, dict) else {}
    custom_prompt = payload.get("customPrompt") if isinstance(payload, dict) else ""
    if not isinstance(options, dict):
        response.status = 400
        return {"ok": False, "error": "options must be an object"}
    return update_default_reference_image_options(options, custom_prompt=str(custom_prompt or ""))


@app.route("/api/default-script-reference", method=["GET", "POST", "OPTIONS"])
def api_default_script_reference():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        return default_script_reference_status()
    payload = request.json or {}
    relative_path = str(payload.get("relativePath") or payload.get("name") or "").strip() if isinstance(payload, dict) else ""
    try:
        return select_default_script_reference(relative_path)
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/default-script-reference/clear", method=["POST", "OPTIONS"])
def api_default_script_reference_clear():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    return clear_default_script_reference()


@app.route("/api/generation-mode", method=["GET", "POST", "OPTIONS"])
def api_generation_mode():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        return generation_mode_status()
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    return update_generation_mode(concurrent_generation=bool(payload.get("concurrentGeneration")))


@app.route("/api/html-motion-overlay", method=["GET", "POST", "OPTIONS"])
def api_html_motion_overlay():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        return html_motion_overlay_status()
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    if "qualityRetryCount" in payload:
        return update_html_motion_quality_retry_count(payload.get("qualityRetryCount"))
    if "beatIntervalSeconds" in payload:
        return update_html_motion_beat_interval_seconds(payload.get("beatIntervalSeconds"))
    if "smartBeatInterval" in payload:
        return update_html_motion_smart_beat_interval(payload.get("smartBeatInterval"))
    return update_html_motion_overlay(enabled=bool(payload.get("enabled")))


@app.route("/api/narration-review", method=["GET", "POST", "OPTIONS"])
def api_narration_review():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        return narration_review_status()
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    return update_narration_review_count(payload.get("reviewCount"))


@app.route("/api/html-motion-safe-zone", method=["GET", "POST", "OPTIONS"])
def api_html_motion_safe_zone():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        return html_motion_safe_zone_status(request.query.get("aspectRatio", "9:16"))
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return update_html_motion_safe_zone(
            str(payload.get("aspectRatio") or "9:16"),
            payload.get("safeZone"),
        )
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}














































@app.route("/api/video-text-overlay", method=["GET", "POST", "OPTIONS"])
def api_video_text_overlay():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        return video_text_overlay_status()
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    return update_video_text_overlay(
        enabled=bool(payload.get("enabled")) if "enabled" in payload else None,
        text=str(payload.get("text") or "") if "text" in payload else None,
        canvas_width=payload.get("canvasWidth") if "canvasWidth" in payload else None,
        canvas_height=payload.get("canvasHeight") if "canvasHeight" in payload else None,
        text_color=str(payload.get("textColor") or "") if "textColor" in payload else None,
        stroke_color=str(payload.get("strokeColor") or "") if "strokeColor" in payload else None,
        font_family=str(payload.get("fontFamily") or "") if "fontFamily" in payload else None,
        font_size=payload.get("fontSize") if "fontSize" in payload else None,
        font_weight=payload.get("fontWeight") if "fontWeight" in payload else None,
        stroke_width=payload.get("strokeWidth") if "strokeWidth" in payload else None,
        position=str(payload.get("position") or "") if "position" in payload else None,
        text_x=payload.get("textX") if "textX" in payload else None,
        text_y=payload.get("textY") if "textY" in payload else None,
        animation_delay_seconds=payload.get("animationDelaySeconds") if "animationDelaySeconds" in payload else None,
        animation_type=str(payload.get("animationType") or "") if "animationType" in payload else None,
        watermark_enabled=bool(payload.get("watermarkEnabled")) if "watermarkEnabled" in payload else None,
        watermark_image=str(payload.get("watermarkImage") or "") if "watermarkImage" in payload else None,
        watermark_size=payload.get("watermarkSize") if "watermarkSize" in payload else None,
        watermark_opacity=payload.get("watermarkOpacity") if "watermarkOpacity" in payload else None,
        watermark_animation_delay_seconds=payload.get("watermarkAnimationDelaySeconds") if "watermarkAnimationDelaySeconds" in payload else None,
        watermark_animation_type=str(payload.get("watermarkAnimationType") or "") if "watermarkAnimationType" in payload else None,
        watermark_position=str(payload.get("watermarkPosition") or "") if "watermarkPosition" in payload else None,
        watermark_x=payload.get("watermarkX") if "watermarkX" in payload else None,
        watermark_y=payload.get("watermarkY") if "watermarkY" in payload else None,
        watermark2_enabled=bool(payload.get("watermark2Enabled")) if "watermark2Enabled" in payload else None,
        watermark2_image=str(payload.get("watermark2Image") or "") if "watermark2Image" in payload else None,
        watermark2_size=payload.get("watermark2Size") if "watermark2Size" in payload else None,
        watermark2_opacity=payload.get("watermark2Opacity") if "watermark2Opacity" in payload else None,
        watermark2_animation_delay_seconds=payload.get("watermark2AnimationDelaySeconds") if "watermark2AnimationDelaySeconds" in payload else None,
        watermark2_animation_type=str(payload.get("watermark2AnimationType") or "") if "watermark2AnimationType" in payload else None,
        watermark2_position=str(payload.get("watermark2Position") or "") if "watermark2Position" in payload else None,
        watermark2_x=payload.get("watermark2X") if "watermark2X" in payload else None,
        watermark2_y=payload.get("watermark2Y") if "watermark2Y" in payload else None,
        preview_background_color=str(payload.get("previewBackgroundColor") or "") if "previewBackgroundColor" in payload else None,
        preview_background_image=str(payload.get("previewBackgroundImage") or "") if "previewBackgroundImage" in payload else None,
    )


@app.route("/api/video-text-overlay/preview-background-color", method=["POST", "OPTIONS"])
def api_video_text_overlay_preview_background_color():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    clear_video_text_preview_background_image()
    return update_video_text_overlay(
        preview_background_color=str(payload.get("previewBackgroundColor") or ""),
        preview_background_image="",
    )


@app.route("/api/video-text-overlay/preview-background", method=["POST", "OPTIONS"])
def api_video_text_overlay_preview_background_upload():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    upload = request.files.get("file")
    if not upload:
        response.status = 400
        return {"ok": False, "error": "file is required"}
    source_name = resolve_upload_filename(upload)
    try:
        payload = upload.file.read()
        return save_video_text_preview_background_upload(source_name, payload)
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        response.status = 500
        return {"ok": False, "error": str(exc)}


@app.route("/api/video-text-overlay/preview", method=["POST", "OPTIONS"])
def api_video_text_overlay_preview():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    image = render_video_text_overlay_preview(
        payload,
        target_width=payload.get("targetWidth"),
        target_height=payload.get("targetHeight"),
    )
    return HTTPResponse(
        body=image,
        headers={
            "Content-Type": "image/png",
            "Cache-Control": "no-store",
        },
    )


@app.route("/video-text-overlay-preview-background/<relative_path:path>", method=["GET", "OPTIONS"])
def video_text_overlay_preview_background(relative_path: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    clean_path = str(relative_path or "").strip().lstrip("/")
    if video_text_preview_background_url(clean_path) == "":
        response.status = 404
        return {"ok": False, "error": "not found"}
    target = (VIDEO_TEXT_PREVIEW_BACKGROUND_DIR / clean_path).resolve()
    if not _is_within(VIDEO_TEXT_PREVIEW_BACKGROUND_DIR, target) or not target.is_file():
        response.status = 404
        return {"ok": False, "error": "not found"}
    return static_file(clean_path, root=str(VIDEO_TEXT_PREVIEW_BACKGROUND_DIR))


@app.route("/user-font-previews/<relative_path:path>", method=["GET", "OPTIONS"])
def user_font_preview(relative_path: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    clean_path = str(relative_path or "").strip().lstrip("/")
    target = (USER_FONT_PREVIEW_DIR / clean_path).resolve()
    try:
        target.relative_to(USER_FONT_PREVIEW_DIR.resolve())
    except ValueError:
        response.status = 403
        return {"ok": False, "error": "invalid path"}
    if not target.is_file():
        response.status = 404
        return {"ok": False, "error": "not found"}
    return static_file(clean_path, root=str(USER_FONT_PREVIEW_DIR))


@app.route("/user-fonts/<relative_path:path>", method=["GET", "OPTIONS"])
def user_font_file(relative_path: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    clean_path = str(relative_path or "").strip().lstrip("/")
    target = (USER_FONT_DIR / clean_path).resolve()
    try:
        target.relative_to(USER_FONT_DIR.resolve())
    except ValueError:
        response.status = 403
        return {"ok": False, "error": "invalid path"}
    if target.suffix.lower() not in SUPPORTED_FONT_EXTENSIONS:
        response.status = 403
        return {"ok": False, "error": "unsupported font"}
    if not target.is_file():
        response.status = 404
        return {"ok": False, "error": "not found"}
    return static_file(clean_path, root=str(USER_FONT_DIR))


@app.route("/user-materials/images/<relative_path:path>", method=["GET", "OPTIONS"])
def user_material_image(relative_path: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    ensure_user_material_dirs()
    clean_path = str(relative_path or "").strip().lstrip("/")
    target = (USER_IMAGE_MATERIAL_DIR / clean_path).resolve()
    if not _is_within(USER_IMAGE_MATERIAL_DIR, target) or not target.is_file():
        response.status = 404
        return {"error": "image material not found"}
    return static_file(clean_path, root=str(USER_IMAGE_MATERIAL_DIR))


@app.route("/user-materials/flower-watermarks/<relative_path:path>", method=["GET", "OPTIONS"])
def user_material_flower_watermark(relative_path: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    ensure_user_material_dirs()
    clean_path = str(relative_path or "").strip().lstrip("/")
    target = (USER_FLOWER_WATERMARK_DIR / clean_path).resolve()
    if not _is_within(USER_FLOWER_WATERMARK_DIR, target) or not target.is_file():
        response.status = 404
        return {"error": "flower watermark material not found"}
    return static_file(clean_path, root=str(USER_FLOWER_WATERMARK_DIR))


@app.route("/user-generated-results/<relative_path:path>", method=["GET", "OPTIONS"])
def user_generated_result_media(relative_path: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    root = ensure_user_generated_result_dir().resolve()
    clean_path = str(relative_path or "").strip().lstrip("/")
    target = (root / clean_path).resolve()
    if not target.is_file() and Path(clean_path).suffix.lower() in USER_GENERATED_VIDEO_EXTENSIONS:
        alias_target = _user_generated_video_alias_target(root, clean_path)
        if alias_target is not None:
            target = alias_target
            clean_path = alias_target.relative_to(root).as_posix()
    if not _is_within(root, target) or not target.is_file():
        response.status = 404
        return {"error": "generated result media not found"}
    return static_file(clean_path, root=str(root))


@app.route("/api/user-generated-results/html-motion-preview/<review_id>", method=["GET", "OPTIONS"])
def user_generated_html_motion_preview(review_id: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        target = resolve_html_motion_review_video(review_id)
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    except FileNotFoundError:
        response.status = 404
        return {"ok": False, "error": "HTML 动效预览不存在"}
    response.set_header("Cache-Control", "no-store")
    return static_file(target.name, root=str(target.parent))


@app.route("/user-recycle-bin/<relative_path:path>", method=["GET", "OPTIONS"])
def user_recycle_bin_media(relative_path: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    root = ensure_user_recycle_bin_dir()
    clean_path = str(relative_path or "").replace("\\", "/").lstrip("/")
    target = (root / clean_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        response.status = 404
        return {"error": "recycle media not found"}
    if not target.is_file():
        response.status = 404
        return {"error": "recycle media not found"}
    return static_file(clean_path, root=str(root))


def _archive_roots() -> list[Path]:
    configured = Path(AI8VideoConfig.from_env().archive_local_dir)
    if not configured.is_absolute():
        configured = (PROJECT_ROOT / configured).resolve()
    roots = [configured]
    if LEGACY_ARCHIVE_DIR not in roots:
        roots.append(LEGACY_ARCHIVE_DIR)
    return roots


def _move_user_generated_result_file(root: Path, source_key: str, target_key: str) -> bool:
    source = (root / source_key).resolve()
    target = (root / target_key).resolve()
    if not _is_within(root, source) or not _is_within(root, target) or not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return False
    shutil.move(str(source), str(target))
    return True


def _migrate_legacy_extension_results(root: Path, asset_store: JsonlAssetStore) -> None:
    legacy_records = asset_store.read_all()
    legacy_video_keys = [
        path.relative_to(root).as_posix()
        for path in (root / "video").glob("*-延长-task_*")
        if path.is_file() and path.suffix.lower() in USER_GENERATED_VIDEO_EXTENSIONS
    ]
    has_legacy_record = any(
        str(record.get("archiveKey") or "").startswith("video/")
        and str(record.get("videoTitle") or "").endswith("-延长")
        and (root / str(record.get("archiveKey"))).is_file()
        for record in legacy_records
    )
    if not has_legacy_record and not legacy_video_keys:
        return

    def migrate(records: list[dict]) -> None:
        for record in records:
            archive_key = str(record.get("archiveKey") or "").strip()
            title = str(record.get("videoTitle") or "").strip()
            if not archive_key.startswith("video/") or not title.endswith("-延长"):
                continue
            extension_key = f"extensions/{archive_key}"
            if not _move_user_generated_result_file(root, archive_key, extension_key):
                continue
            preview_key = find_preview_key(root, archive_key)
            cover_key = str(record.get("archiveCoverKey") or _find_user_generated_cover_key(root, archive_key)).strip()
            if preview_key:
                _move_user_generated_result_file(root, preview_key, f"extensions/{preview_key}")
            if cover_key:
                _move_user_generated_result_file(root, cover_key, f"extensions/{cover_key}")
            record["archiveKey"] = extension_key
            record["archiveUrl"] = extension_key
            record["archiveLocalPath"] = str((root / extension_key).resolve())
            if preview_key:
                record["userGeneratedPreviewKey"] = f"extensions/{preview_key}"
            if cover_key:
                record["archiveCoverKey"] = f"extensions/{cover_key}"
                record["archiveCoverUrl"] = f"extensions/{cover_key}"
                record["archiveLocalCoverPath"] = str((root / "extensions" / cover_key).resolve())
            archive_meta = record.get("archiveMeta") if isinstance(record.get("archiveMeta"), dict) else {}
            record["archiveMeta"] = {**archive_meta, "artifactKind": "extension"}

    asset_store.mutate_records(migrate)
    for archive_key in legacy_video_keys:
        extension_key = f"extensions/{archive_key}"
        if not _move_user_generated_result_file(root, archive_key, extension_key):
            continue
        preview_key = find_preview_key(root, archive_key)
        cover_key = _find_user_generated_cover_key(root, archive_key)
        if preview_key:
            _move_user_generated_result_file(root, preview_key, f"extensions/{preview_key}")
        if cover_key:
            _move_user_generated_result_file(root, cover_key, f"extensions/{cover_key}")


def _user_generated_result_items(limit: int = 50) -> list[dict]:
    config = AI8VideoConfig.from_env()
    asset_store = JsonlAssetStore(config.asset_store_path)
    root = ensure_user_generated_result_dir().resolve()
    _migrate_legacy_extension_results(root, asset_store)
    asset_records = asset_store.read_all()
    asset_by_archive_key = {
        str(item.get("archiveKey") or "").strip(): item
        for item in asset_records
        if str(item.get("archiveKey") or "").strip()
    }
    asset_by_archive_name = {
        Path(str(item.get("archiveKey") or "")).name: item
        for item in asset_records
        if Path(str(item.get("archiveKey") or "")).name
    }
    items: list[dict] = []
    for source in root.rglob("*"):
        if not source.is_file() or source.suffix.lower() not in USER_GENERATED_VIDEO_EXTENSIONS:
            continue
        relative_key = source.relative_to(root).as_posix()
        if relative_key.startswith("extensions/video/"):
            continue
        restored_record = load_restored_result_metadata(root, relative_key)
        asset_record = asset_by_archive_key.get(relative_key) or asset_by_archive_name.get(source.name) or {}
        record = _merge_user_generated_records(restored_record, asset_record)
        if _is_simulated_user_generated_result(source, record):
            continue
        stat = source.stat()
        item = {
            **record,
            "archiveKey": relative_key,
            "archiveBackend": "local",
            "archiveStatus": record.get("archiveStatus") or "archived",
            "archiveUrl": relative_key,
            "archiveLocalPath": str(source.resolve()),
            "archiveManifestPath": record.get("archiveManifestPath"),
            "archiveMeta": record.get("archiveMeta"),
            "createdAt": record.get("createdAt") or datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "videoTitle": sanitize_internal_fidelity_notes(record.get("videoTitle") or source.stem),
            "prompt": sanitize_internal_fidelity_notes(record.get("prompt") or ""),
            "userGeneratedKey": relative_key,
            "userGeneratedLocalPath": str(source.resolve()),
            "userGeneratedSizeBytes": stat.st_size,
            "userGeneratedUpdatedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }
        cover_key = _find_user_generated_cover_key(root, relative_key)
        preview_key = find_preview_key(root, relative_key)
        if preview_key:
            item["userGeneratedPreviewKey"] = preview_key
            item["userGeneratedPreviewLocalPath"] = str((root / preview_key).resolve())
        if cover_key:
            item["userGeneratedCoverKey"] = cover_key
            item["userGeneratedCoverLocalPath"] = str((root / cover_key).resolve())
            item["archiveCoverKey"] = cover_key
            item["archiveCoverUrl"] = cover_key
            item["archiveLocalCoverPath"] = str((root / cover_key).resolve())
        items.append(item)
    items.sort(
        key=lambda item: (
            str(item.get("createdAt") or ""),
            str(item.get("userGeneratedKey") or ""),
        ),
        reverse=True,
    )
    bounded_limit = max(1, min(200, int(limit or 50)))
    return items[:bounded_limit]


def _is_simulated_user_generated_result(source: Path, record: dict) -> bool:
    if is_simulated_user_generated_result_path(source):
        return True
    if (
        record.get("dryRun") is True
        or str(record.get("archiveStatus") or "").strip().lower() == "simulated"
    ):
        return True
    for key in ("usage", "generationMeta", "archiveMeta"):
        metadata = record.get(key)
        if not isinstance(metadata, dict):
            continue
        if (
            metadata.get("dryRun") is True
            or str(metadata.get("mode") or "").strip().lower() == "simulated"
        ):
            return True
    return False


def _find_user_generated_cover_key(root: Path, video_relative_key: str) -> str:
    rel_path = Path(video_relative_key)
    flat_cover_dir = Path("cover")
    for suffix in sorted(USER_GENERATED_IMAGE_EXTENSIONS):
        candidate = flat_cover_dir / f"{rel_path.stem}{suffix}"
        if (root / candidate).is_file():
            return candidate.as_posix()
    parts = list(rel_path.parts)
    if "video" not in parts:
        return ""
    video_index = parts.index("video")
    cover_dir = Path(*parts[:video_index], "cover")
    stem = rel_path.stem
    for suffix in sorted(USER_GENERATED_IMAGE_EXTENSIONS):
        candidate = cover_dir / f"{stem}{suffix}"
        if (root / candidate).is_file():
            return candidate.as_posix()
    return ""


def _resolve_user_generated_video_key(raw_key: object) -> tuple[Path, str]:
    key = unquote(str(raw_key or "").strip())
    if not key:
        raise ValueError("userGeneratedKey is required")
    if Path(key).is_absolute():
        raise ValueError("userGeneratedKey must be relative")
    clean_key = key.lstrip("/")
    root = ensure_user_generated_result_dir().resolve()
    target = (root / clean_key).resolve()
    if not _is_within(root, target):
        raise ValueError("userGeneratedKey is outside generated results")
    if target.suffix.lower() not in USER_GENERATED_VIDEO_EXTENSIONS:
        raise ValueError("userGeneratedKey must point to a video")
    if not target.is_file():
        target = _user_generated_video_alias_target(root, clean_key)
        if target is None:
            raise FileNotFoundError("video not found")
    return target, target.relative_to(root).as_posix()


def _user_generated_video_alias_target(root: Path, clean_key: str) -> Path | None:
    filename = Path(clean_key).name
    candidates = [root / "video" / filename, root / filename]
    for candidate in candidates:
        resolved = candidate.resolve()
        if _is_within(root, resolved) and resolved.is_file():
            return resolved
    return None


def _delete_user_generated_video(raw_key: object) -> dict:
    root = ensure_user_generated_result_dir().resolve()
    target, relative_key = _resolve_user_generated_video_key(raw_key)
    cover_key = _find_user_generated_cover_key(root, relative_key)
    preview_key = find_preview_key(root, relative_key)
    related = _find_related_user_generated_asset_identity(relative_key)
    deleted: list[str] = []
    target.unlink()
    deleted.append(relative_key)
    deleted_preview_key = delete_preview_for_video(root, relative_key)
    preview_key = preview_key or deleted_preview_key
    if preview_key:
        deleted.append(preview_key)
    if cover_key:
        cover_target = (root / cover_key).resolve()
        if _is_within(root, cover_target) and cover_target.is_file():
            cover_target.unlink()
            deleted.append(cover_key)
    restored_metadata_key = delete_restored_result_metadata(root, relative_key)
    if restored_metadata_key:
        deleted.append(restored_metadata_key)
    return {
        "ok": True,
        "deleted": deleted,
        "userGeneratedKey": relative_key,
        "userGeneratedCoverKey": cover_key,
        "userGeneratedPreviewKey": preview_key,
        "relatedJobIds": sorted(related["jobIds"]),
        "relatedKeys": sorted(related["keys"]),
    }


def _load_json_file(path: Path | str | None) -> dict:
    if not path:
        return {}
    candidate = Path(str(path))
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()
    if not candidate.is_file():
        return {}
    try:
        data = normalize_legacy_video_payload(json.loads(candidate.read_text(encoding="utf-8")))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _asset_record_for_user_generated_key(relative_key: str, video_path: Path) -> dict:
    return _asset_maintenance_service().find_user_generated_record(relative_key, video_path)


def _merge_user_generated_records(restored_record: dict, asset_record: dict) -> dict:
    record = {**restored_record, **asset_record}
    restored_meta = restored_record.get("generationMeta")
    asset_meta = asset_record.get("generationMeta")
    if isinstance(restored_meta, dict) or isinstance(asset_meta, dict):
        record["generationMeta"] = {
            **(asset_meta if isinstance(asset_meta, dict) else {}),
            **(restored_meta if isinstance(restored_meta, dict) else {}),
        }
    return record


def _asset_maintenance_service() -> AssetMaintenanceService:
    return AssetMaintenanceService(JsonlAssetStore(_asset_store_path()), PROJECT_ROOT)


def _register_extension_frame_archive(
    video_path: Path,
    relative_video_key: str,
    frame_path: Path,
    frame_key: str,
    frame_time: float,
) -> dict:
    updated_at = datetime.now(timezone.utc).isoformat()
    metadata = {
        "status": "archived",
        "sourceVideoKey": relative_video_key,
        "frameKey": frame_key,
        "frameLocalPath": str(frame_path),
        "frameTime": round(frame_time, 3),
        "updatedAt": updated_at,
    }
    manifest_path = frame_path.with_suffix(".json")
    temporary = manifest_path.with_name(f".{manifest_path.name}.tmp")
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
    metadata["manifestPath"] = str(manifest_path)
    try:
        record = _asset_maintenance_service().save_extension_frame_result(
            relative_video_key,
            video_path,
            metadata,
        )
        archive_manifest_path = record.get("archiveManifestPath")
        archive_manifest = _load_json_file(archive_manifest_path)
        if archive_manifest and archive_manifest_path:
            archive_manifest["extensionFrame"] = metadata
            path = Path(str(archive_manifest_path))
            if not path.is_absolute():
                path = (PROJECT_ROOT / path).resolve()
            archive_temp = path.with_name(f".{path.name}.tmp")
            archive_temp.write_text(json.dumps(archive_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            archive_temp.replace(path)
    except LookupError:
        metadata["assetRecordStatus"] = "missing"
    return metadata


def _resolve_extension_frame_path(frame_key: object) -> Path:
    root = ensure_user_generated_result_dir().resolve()
    clean_key = str(frame_key or "").strip().lstrip("/")
    target = (root / clean_key).resolve()
    if not clean_key or not _is_within(root, target):
        raise ValueError("修图截图路径无效")
    if target.suffix.lower() not in USER_GENERATED_IMAGE_EXTENSIONS or not target.is_file():
        raise FileNotFoundError("修图截图不存在，请重新截取")
    return target


def _resolve_frame_repair_references(raw_paths: object) -> list[str]:
    selected_paths = raw_paths if isinstance(raw_paths, list) else []
    materials = list_user_materials().get("images") or []
    by_relative_path = {
        str(item.get("relativePath") or ""): str(item.get("path") or "")
        for item in materials
        if item.get("relativePath") and item.get("path")
    }
    resolved = [by_relative_path.get(str(item or "").strip(), "") for item in selected_paths[:4]]
    return [item for item in resolved if item]


def _delete_extension_state_assets(left_key: object, right_key: object) -> dict:
    left_path, relative_left_key = _resolve_user_generated_video_key(left_key)
    root = ensure_user_generated_result_dir().resolve()
    frame_name = hashlib.sha256(relative_left_key.encode("utf-8")).hexdigest()[:24]
    deleted: list[str] = []
    frame_root = (root / "extension-frame").resolve()
    for target in frame_root.glob(f"{frame_name}*"):
        resolved = target.resolve()
        if not _is_within(frame_root, resolved) or not resolved.is_file():
            continue
        if resolved.suffix not in {".png", ".json"} and not resolved.name.endswith(".state.json"):
            continue
        resolved.unlink()
        deleted.append(resolved.relative_to(root).as_posix())
    try:
        left_record = _asset_maintenance_service().clear_extension_frame_result(relative_left_key, left_path)
        archive_manifest_path = left_record.get("archiveManifestPath")
        archive_manifest = _load_json_file(archive_manifest_path)
        if archive_manifest and archive_manifest_path:
            archive_manifest.pop("extensionFrame", None)
            archive_manifest.pop("extensionFrameVariants", None)
            path = Path(str(archive_manifest_path))
            if not path.is_absolute():
                path = (PROJECT_ROOT / path).resolve()
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_text(json.dumps(archive_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
    except LookupError:
        pass
    clean_right_key = str(right_key or "").strip()
    if clean_right_key and clean_right_key != relative_left_key:
        right_path, relative_right_key = _resolve_user_generated_video_key(clean_right_key)
        record = _asset_record_for_user_generated_key(relative_right_key, right_path)
        audio_path = Path(str(((record.get("archiveMeta") or {}).get("localTts") or {}).get("audioPath") or ""))
        tts_root = local_tts_output_dir().resolve()
        if audio_path.is_file() and _is_within(tts_root, audio_path.resolve()):
            audio_path.unlink()
            deleted.append(str(audio_path))
        manifest_path = Path(str(record.get("archiveManifestPath") or ""))
        if manifest_path.is_file():
            manifest_path.unlink()
            deleted.append(str(manifest_path))
        delete_result = _delete_user_generated_video(relative_right_key)
        deleted.extend(delete_result.get("deleted") or [])
        related_job_ids = set(delete_result.get("relatedJobIds") or [])
        related_keys = set(delete_result.get("relatedKeys") or [])
        _asset_maintenance_service().remove_records(
            lambda item: str(item.get("jobId") or "") in related_job_ids
            or str(item.get("archiveKey") or "") in related_keys
            or str(item.get("archiveKey") or "") == relative_right_key
        )
    return {"ok": True, "deleted": deleted, "sourceVideoKey": relative_left_key, "sourceVideoPath": str(left_path)}


def _collect_tts_narration_candidates(value: Any) -> list[str]:
    candidates: list[str] = []

    def add(text: Any) -> None:
        clean = str(text or "").strip()
        if clean:
            candidates.append(clean)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in (
                "userTtsNarrationText",
                "localTtsNarrationText",
                "localTtsNarrationRawText",
                "narrationText",
                "sourceSummary",
                "source_summary",
            ):
                add(node.get(key))
            for key in ("segmentRecords", "segments"):
                segment_texts = []
                for segment in node.get(key) or []:
                    if isinstance(segment, dict):
                        text = str(segment.get("narrationText") or "").strip()
                        if text:
                            segment_texts.append(text)
                if segment_texts:
                    add(" ".join(segment_texts))
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                if isinstance(child, (dict, list)):
                    walk(child)

    walk(value)
    return candidates


def _tts_narration_text_for_user_generated_video(relative_key: str, video_path: Path) -> tuple[str, dict]:
    restored_record = load_restored_result_metadata(
        ensure_user_generated_result_dir(),
        relative_key,
    )
    asset_record = _asset_record_for_user_generated_key(relative_key, video_path)
    record = _merge_user_generated_records(restored_record, asset_record)
    generation_meta = record.get("generationMeta") if isinstance(record, dict) else None
    if isinstance(generation_meta, dict) and "userTtsNarrationText" in generation_meta:
        return prepare_narration_text(generation_meta.get("userTtsNarrationText")), record
    manifest = _load_json_file(record.get("archiveManifestPath") if record else None)
    sources = [record, manifest]
    for source in sources:
        if not source:
            continue
        for candidate in _collect_tts_narration_candidates(source):
            prepared = prepare_narration_text(candidate)
            if prepared:
                return prepared, record
    return "", record


def _tts_narration_text_payload_for_user_generated_video(raw_key: object) -> dict:
    video_path, relative_key = _resolve_user_generated_video_key(raw_key)
    narration_text, record = _tts_narration_text_for_user_generated_video(relative_key, video_path)
    if not narration_text:
        return {
            "ok": True,
            "deleted": True,
            "userGeneratedKey": relative_key,
            "text": "",
            "textChars": 0,
            "manual": bool(
                isinstance(record.get("generationMeta") if record else None, dict)
                and "userTtsNarrationText" in record.get("generationMeta", {})
            ),
        }
    return {
        "ok": True,
        "userGeneratedKey": relative_key,
        "text": narration_text,
        "textChars": len(narration_text),
        "manual": bool(
            isinstance(record.get("generationMeta") if record else None, dict)
            and "userTtsNarrationText" in record.get("generationMeta", {})
        ),
    }


def _save_tts_narration_text_for_user_generated_video(raw_key: object, raw_text: object) -> dict:
    video_path, relative_key = _resolve_user_generated_video_key(raw_key)
    text = str(raw_text or "").strip()
    result_root = ensure_user_generated_result_dir()
    restored = save_restored_result_narration_text(result_root, relative_key, text)
    if not restored:
        _asset_maintenance_service().save_tts_narration_text(relative_key, video_path, text)
    prepared = prepare_narration_text(text)
    return {
        "ok": True,
        "userGeneratedKey": relative_key,
        "text": prepared,
        "textChars": len(prepared),
        "deleted": not bool(prepared),
    }


def _save_merged_narration_metadata(relative_key: str, source_key: str, text: str) -> None:
    if not text:
        return
    metadata_path = restored_result_metadata_path(ensure_user_generated_result_dir(), relative_key)
    payload = {
        "schema": "merged-result-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "userGeneratedKey": relative_key,
        "sourceVideoKey": source_key,
        "generationMeta": {"userTtsNarrationText": text},
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = metadata_path.with_name(f".{metadata_path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(metadata_path)


def _clean_polished_tts_narration(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        for key in ("text", "narration_text", "polished_text", "台词"):
            value = str(parsed.get(key) or "").strip()
            if value:
                return prepare_narration_text(value)
    return prepare_narration_text(text.strip("“”\"'` \n\r\t"))


def _polish_tts_narration_text(raw_text: object, duration_seconds: object = None) -> dict:
    text = prepare_narration_text(str(raw_text or ""))
    if not text:
        raise LookupError("台词已删除")
    config = AI8VideoConfig.from_env()
    knowledge = _tts_script_knowledge(text, config)
    llm = build_openai_compat_llm(
        config,
        timeout_seconds=45,
        system_prompt="你是短视频 TTS 口播台词润色助手，只输出润色后的中文台词。",
    )
    if llm is None:
        raise RuntimeError("文本/视频规划模型没有配置完整，不能 AI 润色")
    duration = max(0.0, float(duration_seconds or 0))
    prompt = f"""请润色下面这段短视频 TTS 口播台词。

当前视频时长：{duration:.2f} 秒。润色后的台词必须适合在该时长内自然说完。

要求：
1. 只输出润色后的台词正文，不要解释，不要标题，不要 Markdown。
2. 保留原意、核心卖点和信息顺序。
3. 更适合中文口播，短句、顺口、有节奏。
4. 不要新增品牌、日期、事实或承诺。
5. 字数尽量接近原文，不能明显变长。

原台词：
{text}

相关剧本知识段：
{knowledge["contextText"] or "（知识库未返回相关内容，请只依据原台词）"}

用户系统提示词：
{read_business_prompt() or "（无）"}
"""
    polished = _clean_polished_tts_narration(llm(prompt))
    if not polished:
        raise RuntimeError("AI 润色返回为空")
    result = {
        "ok": True,
        "text": polished,
        "textChars": len(polished),
        "knowledge": knowledge["meta"],
    }
    append_prompt_trace("tts_narration_polish_complete", payload={"textChars": len(polished), "knowledge": knowledge["meta"]})
    return result


def _expand_tts_narration_text(raw_text: object, duration_seconds: object = None) -> dict:
    text = prepare_narration_text(str(raw_text or ""))
    if not text:
        raise LookupError("台词已删除")
    config = AI8VideoConfig.from_env()
    knowledge = _tts_script_knowledge(text, config)
    llm = build_openai_compat_llm(
        config,
        timeout_seconds=45,
        system_prompt="你是短视频 TTS 口播台词扩写助手，只输出扩写后的中文台词。",
    )
    if llm is None:
        raise RuntimeError("文本/视频规划模型没有配置完整，不能 AI 扩写")
    duration = max(0.0, float(duration_seconds or 0))
    prompt = f"""请扩写下面这段短视频 TTS 口播台词。

当前视频时长：{duration:.2f} 秒。扩写后的台词必须适合在该时长内自然说完，不得为了达到倍数而超过可用口播时长。

要求：
1. 只输出扩写后的台词正文，不要解释，不要标题，不要 Markdown。
2. 保留原意、核心卖点和信息顺序。
3. 更适合中文口播，短句、顺口、有节奏。
4. 可以补充承接句、情绪递进和口播节奏，但不要新增品牌、日期、事实或承诺。
5. 字数扩写到原文的 1.5 到 2 倍左右，不要无限拉长。

原台词：
{text}

相关剧本知识段：
{knowledge["contextText"] or "（知识库未返回相关内容，请只依据原台词）"}

用户系统提示词：
{read_business_prompt() or "（无）"}
"""
    expanded = _clean_polished_tts_narration(llm(prompt))
    if not expanded:
        raise RuntimeError("AI 扩写返回为空")
    result = {
        "ok": True,
        "text": expanded,
        "textChars": len(expanded),
        "knowledge": knowledge["meta"],
    }
    append_prompt_trace("tts_narration_expand_complete", payload={"textChars": len(expanded), "knowledge": knowledge["meta"]})
    return result


def _tts_script_knowledge(text: str, config: AI8VideoConfig) -> dict[str, Any]:
    item = load_default_script_reference()
    if not item:
        result = {"contextText": "", "meta": {"used": False, "reason": "no_script_reference"}}
        append_prompt_trace("tts_knowledge_retrieval", payload=result["meta"])
        return result
    retrieval = retrieve_script_reference_context(
        text,
        item,
        query_llm=build_script_query_llm(config),
        rerank_llm=build_script_rerank_llm(config),
    )
    if not retrieval.get("ok"):
        result = {
            "contextText": "",
            "meta": {"used": False, "reason": str(retrieval.get("fallbackReason") or "retrieval_failed")},
        }
        append_prompt_trace("tts_knowledge_retrieval", payload=result["meta"])
        return result
    result = {
        "contextText": str(retrieval.get("contextText") or ""),
        "meta": {
            "used": True,
            "query": str(retrieval.get("query") or ""),
            "recallCount": int(retrieval.get("recallCount") or 0),
            "topK": int(retrieval.get("topK") or 0),
            "rerankApplied": bool(retrieval.get("rerankApplied")),
        },
    }
    append_prompt_trace("tts_knowledge_retrieval", payload=result["meta"])
    return result


def _regenerate_user_generated_tts(raw_key: object) -> dict:
    video_path, relative_key = _resolve_user_generated_video_key(raw_key)
    narration_text, record = _tts_narration_text_for_user_generated_video(relative_key, video_path)
    if not narration_text:
        return {
            "ok": True,
            "deleted": True,
            "userGeneratedKey": relative_key,
            "videoUrl": f"/user-generated-results/{relative_key}",
            "textChars": 0,
        }
    video_index = _coerce_positive_int(record.get("videoIndex") if record else None)
    job_id = str((record or {}).get("jobId") or Path(relative_key).stem).strip()
    result = attach_local_tts_to_video(
        video_path,
        narration_text=narration_text,
        video_index=video_index,
        job_id=job_id,
        preserve_original_audio=False,
    )
    if result.get("status") != "mixed":
        raise RuntimeError(str(result.get("reason") or "重新生成 TTS 配音失败"))
    background_music_result = mix_background_music(
        video_path,
        preserve_original_audio_override=True,
        preserved_audio_volume_override=1.0,
    )
    if background_music_result.get("enabled") is True and background_music_result.get("status") == "failed":
        raise RuntimeError(str(background_music_result.get("reason") or "重新混入背景音乐失败"))
    review_audio_result = sync_html_motion_review_audio(video_path, relative_key)
    if review_audio_result.get("status") == "failed":
        raise RuntimeError(str(review_audio_result.get("reason") or "HTML 动效候选音轨同步失败"))
    return {
        "ok": True,
        "userGeneratedKey": relative_key,
        "videoUrl": f"/user-generated-results/{relative_key}",
        "localTts": result,
        "backgroundMusic": background_music_result,
        "htmlMotionReviewAudio": review_audio_result,
        "textChars": result.get("textChars") or len(narration_text),
    }


def _regenerate_user_generated_html_motion(
    raw_key: object,
    *,
    stage_callback=None,
    cancel_event=None,
) -> dict:
    video_path, relative_key = _resolve_user_generated_video_key(raw_key)
    prompt, record, prompt_source, dialogue_text = _html_motion_source_for_user_generated_video(
        relative_key,
        video_path,
    )
    generation_meta = record.get("generationMeta") if isinstance(record.get("generationMeta"), dict) else {}
    dialogue_source = (
        "userTtsNarrationText"
        if "userTtsNarrationText" in generation_meta
        else ("retainedNarrationText" if dialogue_text else "none")
    )
    request_snapshot = _html_motion_request_snapshot(record, prompt)
    video = VideoPrompt(
        index=_coerce_positive_int(record.get("videoIndex")) or 1,
        title=str(record.get("videoTitle") or Path(relative_key).stem).strip(),
        prompt=prompt,
        source_summary=dialogue_text,
    )
    job = QuickVideoJob(
        video_index=video.index,
        job_id=str(record.get("jobId") or Path(relative_key).stem).strip(),
        status="succeeded",
        prompt=prompt,
    )
    llm = build_html_motion_llm(
        AI8VideoConfig.from_env(),
        on_delta=(
            lambda chunk: stage_callback("generating", {"streamDelta": chunk})
            if stage_callback is not None else None
        ),
    )
    flower_settings = video_text_overlay_status()
    motion_text_style = {
        "textColor": flower_settings.get("textColor"),
        "strokeColor": flower_settings.get("strokeColor"),
        "strokeWidth": flower_settings.get("strokeWidth"),
    }
    result_metadata = {
        "regeneratedAt": datetime.now(timezone.utc).isoformat(),
        "promptSource": prompt_source,
        "promptChars": len(prompt),
        "dialogueSource": dialogue_source,
        "dialogueChars": len(dialogue_text),
        "textStyle": motion_text_style,
    }
    result = prepare_html_motion_review(
        video_path,
        relative_key,
        lambda candidate: apply_html_motion_overlay(
            candidate,
            request_snapshot,
            video,
            job,
            llm=llm,
            stage_callback=stage_callback,
            cancel_event=cancel_event,
            trigger="video_playback",
            text_style=motion_text_style,
        ),
        result_metadata,
    )
    updated_record = save_restored_result_html_motion_overlay(
        ensure_user_generated_result_dir(),
        relative_key,
        result,
    )
    if not updated_record:
        updated_record = _asset_maintenance_service().save_html_motion_overlay_result(
            relative_key,
            video_path,
            result,
        )
    manifest_update = _update_html_motion_manifest(updated_record, result)
    return {
        "ok": True,
        "userGeneratedKey": relative_key,
        "videoUrl": result.get("previewUrl") or f"/user-generated-results/{relative_key}",
        "htmlMotionOverlay": result,
        "manifestUpdate": manifest_update,
        "previewGeneration": {"ok": False, "status": "pending_confirmation"},
    }


def _confirm_user_generated_html_motion(raw_key: object) -> dict:
    video_path, relative_key = _resolve_user_generated_video_key(raw_key)
    result = confirm_html_motion_review(video_path, relative_key)
    record = save_restored_result_html_motion_overlay(
        ensure_user_generated_result_dir(),
        relative_key,
        result,
    )
    if not record:
        record = _asset_maintenance_service().save_html_motion_overlay_result(
            relative_key,
            video_path,
            result,
        )
    manifest_update = _update_html_motion_manifest(record, result)
    preview = _refresh_html_motion_preview(video_path, relative_key, result)
    return {
        "ok": True,
        "userGeneratedKey": relative_key,
        "videoUrl": f"/user-generated-results/{relative_key}",
        "htmlMotionOverlay": result,
        "manifestUpdate": manifest_update,
        "previewGeneration": preview,
    }


def _video_prompt_for_user_generated_video(
    relative_key: str,
    video_path: Path,
) -> tuple[str, dict, str]:
    restored_record = load_restored_result_metadata(
        ensure_user_generated_result_dir(),
        relative_key,
    )
    asset_record = _asset_record_for_user_generated_key(relative_key, video_path)
    record = _merge_user_generated_records(restored_record, asset_record)
    manifest = _load_json_file(record.get("archiveManifestPath") if record else None)
    manifest_video = manifest.get("video") if isinstance(manifest.get("video"), dict) else {}
    manifest_job = manifest.get("job") if isinstance(manifest.get("job"), dict) else {}
    manifest_generation = manifest.get("generation") if isinstance(manifest.get("generation"), dict) else {}
    candidates: list[tuple[str, str]] = []
    _add_video_prompt_candidate(candidates, "asset.prompt", record.get("prompt"))
    _add_video_prompt_candidate(candidates, "manifest.video.prompt", manifest_video.get("prompt"))
    _add_video_prompt_candidate(candidates, "manifest.job.prompt", manifest_job.get("prompt"))
    _add_segment_prompt_candidates(candidates, "asset.generationMeta", record.get("generationMeta"))
    _add_segment_prompt_candidates(candidates, "asset.archiveMeta", record.get("archiveMeta"))
    _add_segment_prompt_candidates(candidates, "asset.usage", record.get("usage"))
    _add_segment_prompt_candidates(candidates, "manifest.generation", manifest_generation)
    _add_segment_prompt_candidates(candidates, "manifest.generation.meta", manifest_generation.get("meta"))
    _add_segment_prompt_candidates(candidates, "manifest.job", manifest_job)
    _add_segment_prompt_candidates(candidates, "manifest.job.usage", manifest_job.get("usage"))
    _add_segment_prompt_candidates(candidates, "manifest.postprocess", manifest.get("postprocess"))
    if not candidates:
        return "", record, ""
    source, prompt = candidates[0]
    return prompt, record, source


def _html_motion_source_for_user_generated_video(
    relative_key: str,
    video_path: Path,
) -> tuple[str, dict, str, str]:
    prompt, record, prompt_source = _video_prompt_for_user_generated_video(relative_key, video_path)
    dialogue_text, _ = _tts_narration_text_for_user_generated_video(relative_key, video_path)
    if not prompt and dialogue_text:
        prompt = dialogue_text
        prompt_source = "tts_narration"
    if not prompt:
        raise LookupError("台词已删除")
    return prompt, record, prompt_source, dialogue_text


def _extension_video_prompt_for_user_generated_video(raw_key: object) -> tuple[str, str]:
    video_path, relative_key = _resolve_user_generated_video_key(raw_key)
    record = _asset_record_for_user_generated_key(relative_key, video_path)
    generation_meta = record.get("generationMeta") if isinstance(record.get("generationMeta"), dict) else {}
    return str(generation_meta.get("extensionVideoPrompt") or "").strip(), relative_key


def _save_extension_video_prompt_for_user_generated_video(raw_key: object, text: object) -> dict:
    video_path, relative_key = _resolve_user_generated_video_key(raw_key)
    prompt = str(text or "").strip()
    record = _asset_maintenance_service().save_extension_video_prompt(relative_key, video_path, prompt)
    manifest_path = record.get("archiveManifestPath")
    manifest = _load_json_file(manifest_path)
    if manifest and manifest_path:
        manifest["extensionVideoPrompt"] = prompt
        path = Path(str(manifest_path))
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    return {"ok": True, "userGeneratedKey": relative_key, "text": prompt, "textChars": len(prompt)}


def _continue_extension_video_prompt(raw_key: object) -> dict:
    video_path, relative_key = _resolve_user_generated_video_key(raw_key)
    source_prompt, record, _source = _video_prompt_for_user_generated_video(relative_key, video_path)
    if not source_prompt:
        raise LookupError("原视频提示词已删除，无法续写")
    source_dialogue, _ = _tts_narration_text_for_user_generated_video(relative_key, video_path)
    request_settings = record.get("request") if isinstance(record.get("request"), dict) else {}
    target_duration = max(1, int(request_settings.get("durationSeconds") or 10))
    archive_meta = record.get("archiveMeta") if isinstance(record.get("archiveMeta"), dict) else {}
    extension_frame = archive_meta.get("extensionFrame") if isinstance(archive_meta.get("extensionFrame"), dict) else {}
    frame_time = max(0.0, float(extension_frame.get("frameTime") or 0))
    config = AI8VideoConfig.from_env()
    llm = build_openai_compat_llm(
        config,
        timeout_seconds=60,
        system_prompt="你是 AI8video 的短视频提示词规划与续写模型。",
    )
    if llm is None:
        raise RuntimeError("文本/视频规划模型没有配置完整，不能续写视频")
    continuation_source = f"""请续写下面这条视频，生成 1 条新的独立短视频方案。

原视频提示词：
{source_prompt}

原视频台词/口播：
{source_dialogue or '（原视频没有可用台词）'}
"""
    continuation_source, material_context = apply_default_script_reference(
        continuation_source,
        None,
        prefer_full=False,
        rerank_llm=build_script_rerank_llm(config),
        query_llm=build_script_query_llm(config),
    )
    task_constraints = (
        f"续写必须从原视频第 {frame_time:.2f} 秒的持久化截帧动作自然开始，保持人物、服饰、道具、"
        "场景与空间关系连续，不得跳到原视频之后尚未发生的场景。"
        f"成片为独立 {target_duration} 秒视频，时间轴从【0秒】开始并在【{target_duration}秒】结束。"
        "必须同时生成画面提示词与可直接口播/对白的台词，并将台词写入 prompt 的“台词/口播”字段。"
        "不要擅自新增地点、人物、品牌、台词或事实；不要提及原视频、续写、截帧、知识库或内部处理。"
    )
    videos = plan_video_prompts_with_ai(
        continuation_source,
        1,
        task_constraints=task_constraints,
        final_duration_seconds=target_duration,
        llm=llm,
    )
    continued = videos[0].prompt
    return {
        "ok": True,
        "text": continued,
        "textChars": len(continued),
        "frameTime": round(frame_time, 3),
        "targetDuration": target_duration,
        "knowledge": {"scripts": material_context.get("scripts", [])},
    }


def _transform_extension_video_prompt(raw_text: object, mode: str) -> dict:
    text = str(raw_text or "").strip()
    if not text:
        raise LookupError("视频提示词为空")
    config = AI8VideoConfig.from_env()
    knowledge = _tts_script_knowledge(text, config)
    action = "润色" if mode == "polish" else "扩写"
    llm = build_openai_compat_llm(
        config,
        timeout_seconds=60,
        system_prompt=f"你是短视频视频提示词{action}助手，只输出可直接交给视频模型的中文提示词。",
    )
    if llm is None:
        raise RuntimeError(f"文本/视频规划模型没有配置完整，不能{action}")
    mode_rule = (
        "优化镜头语言、画面可拍摄性、动作连贯性和表达精度，字数不要明显变长。"
        if mode == "polish"
        else "在不延长总时长的前提下，补充景别、运镜、人物动作、光线、情绪、环境和音效细节。"
    )
    prompt = f"""请{action}下面的视频生成提示词。

要求：
1. {mode_rule}
2. 保持原时间轴的起止时间和总时长，禁止改成累计时间。
3. 保留原人物、场景、服饰、道具、品牌与核心事实，不得凭空新增。
4. 知识库只用于提高风格、结构和表达质量，不要在成品中提及知识库、脚本编号或来源。
5. 遵守用户系统提示词，只输出完整提示词。

当前视频提示词：
{text}

相关剧本知识段：
{knowledge['contextText'] or '（未召回到相关知识段，请仅依据当前提示词）'}

用户系统提示词：
{read_business_prompt() or '（无）'}
"""
    transformed = str(llm(prompt) or "").strip()
    transformed = re.sub(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", "", transformed).strip()
    if not transformed:
        raise RuntimeError(f"文本模型{action}结果为空")
    return {"ok": True, "text": transformed, "textChars": len(transformed), "knowledge": knowledge["meta"]}


def _repair_continuation_timeline(llm, text: str, target_duration: int) -> str:
    ranges = re.findall(r"[\[【](\d+(?:\.\d+)?)\s*[-–—~至]\s*(\d+(?:\.\d+)?)\s*秒", text)
    valid = bool(ranges) and float(ranges[0][0]) == 0 and float(ranges[-1][1]) == float(target_duration)
    if valid and all(0 <= float(start) < float(end) <= target_duration for start, end in ranges):
        return text
    repaired = str(llm(f"""只修正下面视频提示词的时间轴，不改变人物、场景、动作和内容。
必须从【0秒】开始，按连续时间段排列，最后在【{target_duration}秒】结束。只输出修正后的完整提示词。

{text}
""") or "").strip()
    repaired = re.sub(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", "", repaired).strip()
    if not repaired:
        raise RuntimeError("文本模型修正续写时间轴失败")
    return repaired


def _generate_extension_video(
    raw_key: object,
    session_id: object = None,
    frame_key: object = None,
) -> dict:
    video_path, relative_key = _resolve_user_generated_video_key(raw_key)
    prompt, _ = _extension_video_prompt_for_user_generated_video(relative_key)
    if not prompt:
        raise LookupError("视频提示词已删除")
    record = _asset_record_for_user_generated_key(relative_key, video_path)
    root = ensure_user_generated_result_dir().resolve()
    frame_name = hashlib.sha256(relative_key.encode("utf-8")).hexdigest()[:24]
    frame_path = (
        _resolve_extension_frame_path(frame_key)
        if frame_key
        else (root / "extension-frame" / f"{frame_name}.png").resolve()
    )
    if not _is_within(root, frame_path) or not frame_path.is_file():
        raise FileNotFoundError("延长截帧已丢失，请重新截取")
    settings = record.get("request") if isinstance(record.get("request"), dict) else {}
    request_snapshot = ParsedRequest(
        raw_text=prompt,
        mode="single_video",
        video_count=1,
        duration_seconds=int(settings.get("durationSeconds") or 10),
        ratio=str(settings.get("ratio") or "9:16"),
        resolution=str(settings.get("resolution") or "480p"),
        preset=str(settings.get("preset") or "custom"),
    )
    video = VideoPrompt(
        index=1,
        title=f"{str(record.get('videoTitle') or Path(relative_key).stem).strip()}-延长",
        prompt=prompt,
        archive_subdir="extensions/video",
    )
    result = AI8VideoPipeline(config=AI8VideoConfig.from_env()).retry_video(
        request_snapshot,
        video,
        FirstFrameAsset(source=str(frame_path)),
        progress_session_id=str(session_id or "").strip() or f"extension-{frame_name}",
    )
    archive = result.archives[0] if result.archives else None
    if archive is None or not archive.archive_key:
        raise RuntimeError("延长视频生成完成，但未获得本地归档")
    return {
        "ok": True,
        "userGeneratedKey": archive.archive_key,
        "videoUrl": f"/user-generated-results/{archive.archive_key}",
        "result": result.to_dict(),
    }


def _copy_extension_frame_variant(frame_path: Path, variant_index: object) -> Path:
    index = int(variant_index)
    if index not in {1, 2, 3, 4}:
        raise ValueError("批量修图编号无效")
    root = ensure_user_generated_result_dir().resolve()
    target = frame_path.with_name(f"{frame_path.stem}-batch-{index}{frame_path.suffix}").resolve()
    if not _is_within(root, target):
        raise ValueError("批量截帧输出路径无效")
    shutil.copy2(frame_path, target)
    return target


def _extension_frame_variant_status(frame_path: Path) -> str:
    state_path = frame_path.with_suffix(".state.json")
    state = _load_json_file(state_path)
    if isinstance(state, dict) and state.get("status") in {"repairing", "completed", "failed"}:
        return str(state["status"])
    source = re.sub(r"-batch-[1-4](\.[^.]+)$", r"\1", frame_path.name)
    source_path = frame_path.with_name(source)
    if source_path.is_file() and source_path.read_bytes() != frame_path.read_bytes():
        return "completed"
    return "idle"


def _write_extension_frame_variant_status(frame_path: Path, status: str) -> None:
    state_path = frame_path.with_suffix(".state.json")
    temporary = state_path.with_name(f".{state_path.name}.tmp")
    temporary.write_text(json.dumps({"status": status}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(state_path)


def _register_extension_frame_variant_archive(frame_path: Path) -> None:
    base_stem = re.sub(r"(?:-batch-[1-4])+$", "", frame_path.stem)
    base_path = frame_path.with_name(f"{base_stem}{frame_path.suffix}")
    metadata = _load_json_file(base_path.with_suffix(".json"))
    source_key = str(metadata.get("sourceVideoKey") or "").strip()
    if not source_key:
        return
    root = ensure_user_generated_result_dir().resolve()
    variant_key = frame_path.relative_to(root).as_posix()
    variant = {
        "status": "archived",
        "frameKey": variant_key,
        "frameLocalPath": str(frame_path),
        "sourceFrameKey": str(metadata.get("frameKey") or ""),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    frame_path.with_suffix(".json").write_text(json.dumps(variant, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        video_path, relative_key = _resolve_user_generated_video_key(source_key)
        record = _asset_maintenance_service().save_extension_frame_variant_result(relative_key, video_path, variant)
        archive_manifest_path = record.get("archiveManifestPath")
        archive_manifest = _load_json_file(archive_manifest_path)
        if archive_manifest and archive_manifest_path:
            variants = [item for item in archive_manifest.get("extensionFrameVariants", []) if item.get("frameKey") != variant_key]
            archive_manifest["extensionFrameVariants"] = [*variants, variant]
            path = Path(str(archive_manifest_path)).resolve()
            path.write_text(json.dumps(archive_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except LookupError:
        pass


def _extension_frame_variant_paths(source: Path) -> list[Path]:
    pattern = re.compile(rf"{re.escape(source.stem)}(?:-batch-[1-4])+$")
    return [
        path for path in source.parent.glob(f"{source.stem}-batch-*.png")
        if pattern.fullmatch(path.stem)
    ]


def _completed_extension_video(raw_key: object) -> dict:
    _, relative_key = _resolve_user_generated_video_key(raw_key)
    root = ensure_user_generated_result_dir().resolve()
    frame_name = hashlib.sha256(relative_key.encode("utf-8")).hexdigest()[:24]
    frame_path = (root / "extension-frame" / f"{frame_name}.png").resolve()
    for record in reversed(JsonlAssetStore(_asset_store_path()).read_all()):
        first_frame = record.get("firstFrame") if isinstance(record.get("firstFrame"), dict) else {}
        source = Path(str(first_frame.get("source") or ""))
        if not source.is_absolute():
            source = PROJECT_ROOT / source
        source = source.resolve()
        archive_key = str(record.get("archiveKey") or "").strip().lstrip("/")
        archive_path = (root / archive_key).resolve() if archive_key else None
        if (
            source == frame_path
            and str(record.get("generationStatus") or "") == "generated"
            and archive_path is not None
            and _is_within(root, archive_path)
            and archive_path.is_file()
        ):
            return {
                "ok": True,
                "status": "completed",
                "userGeneratedKey": archive_key,
                "videoUrl": f"/user-generated-results/{archive_key}",
            }
    return {"ok": True, "status": "pending"}


def _completed_extension_video_for_frame_key(frame_key: object) -> dict:
    frame_path = _resolve_extension_frame_path(frame_key)
    root = ensure_user_generated_result_dir().resolve()
    for record in reversed(JsonlAssetStore(_asset_store_path()).read_all()):
        first_frame = record.get("firstFrame") if isinstance(record.get("firstFrame"), dict) else {}
        source = Path(str(first_frame.get("source") or ""))
        if not source.is_absolute():
            source = PROJECT_ROOT / source
        source = source.resolve()
        archive_key = str(record.get("archiveKey") or "").strip().lstrip("/")
        archive_path = (root / archive_key).resolve() if archive_key else None
        if (
            source == frame_path
            and str(record.get("generationStatus") or "") == "generated"
            and archive_path is not None
            and _is_within(root, archive_path)
            and archive_path.is_file()
        ):
            return {
                "status": "completed",
                "frameKey": str(frame_key or "").strip().lstrip("/"),
                "userGeneratedKey": archive_key,
                "videoUrl": f"/user-generated-results/{archive_key}",
            }
    return {"status": "pending", "frameKey": str(frame_key or "").strip().lstrip("/")}


def _completed_extension_video_records(frame_key: object) -> list[dict]:
    frame_path = _resolve_extension_frame_path(frame_key)
    root = ensure_user_generated_result_dir().resolve()
    matches = []
    for record in JsonlAssetStore(_asset_store_path()).read_all():
        first_frame = record.get("firstFrame") if isinstance(record.get("firstFrame"), dict) else {}
        source = Path(str(first_frame.get("source") or ""))
        source = source if source.is_absolute() else PROJECT_ROOT / source
        archive_key = str(record.get("archiveKey") or "").strip().lstrip("/")
        archive_path = (root / archive_key).resolve() if archive_key else None
        if (
            source.resolve() == frame_path
            and str(record.get("generationStatus") or "") == "generated"
            and archive_path is not None
            and _is_within(root, archive_path)
            and archive_path.is_file()
        ):
            matches.append({
                "status": "completed",
                "frameKey": str(frame_key or "").strip().lstrip("/"),
                "userGeneratedKey": archive_key,
                "videoUrl": f"/user-generated-results/{archive_key}",
            })
    return matches


def _completed_extension_videos_for_frame_keys(raw_frames: object) -> dict:
    frames = raw_frames if isinstance(raw_frames, list) else []
    frame_keys = [frame.get("frameKey") if isinstance(frame, dict) else frame for frame in frames[:4]]
    grouped: dict[str, list[dict]] = {}
    for frame_key in dict.fromkeys(str(key or "") for key in frame_keys):
        try:
            grouped[frame_key] = _completed_extension_video_records(frame_key)
        except (FileNotFoundError, ValueError):
            grouped[frame_key] = []
    offsets: dict[str, int] = {}
    results = []
    for index, frame_key_value in enumerate(frame_keys):
        frame_key = str(frame_key_value or "")
        records = grouped.get(frame_key, [])
        offset = offsets.get(frame_key, 0)
        start = max(0, len(records) - frame_keys.count(frame_key))
        record_index = start + offset
        offsets[frame_key] = offset + 1
        result = records[record_index] if record_index < len(records) else {
            "status": "pending",
            "frameKey": frame_key.strip().lstrip("/"),
        }
        results.append({"index": index, **result})
    return {"ok": True, "videos": results}


def _add_video_prompt_candidate(
    candidates: list[tuple[str, str]],
    source: str,
    value: object,
) -> None:
    prompt = str(value or "").strip()
    if prompt:
        candidates.append((source, prompt))


def _add_segment_prompt_candidates(
    candidates: list[tuple[str, str]],
    source: str,
    value: object,
) -> None:
    if not isinstance(value, dict):
        return
    segment_records = value.get("segmentRecords") or value.get("segments") or []
    prompts = [
        str(item.get("segmentPrompt") or "").strip()
        for item in segment_records
        if isinstance(item, dict) and str(item.get("segmentPrompt") or "").strip()
    ]
    if prompts:
        candidates.append((f"{source}.segmentPrompt", "\n\n".join(prompts)))


def _html_motion_request_snapshot(record: dict, prompt: str) -> ParsedRequest:
    request_data = record.get("request") if isinstance(record.get("request"), dict) else {}
    return ParsedRequest(
        raw_text=prompt,
        mode=str(request_data.get("mode") or "single_video"),
        video_count=_coerce_positive_int(request_data.get("videoCount")),
        duration_seconds=_coerce_positive_int(request_data.get("durationSeconds")) or 10,
        ratio=str(request_data.get("ratio") or "9:16"),
        resolution=str(request_data.get("resolution") or "480p"),
        preset=str(request_data.get("preset") or "custom"),
        html_motion_overlay_enabled=True,
    )


def _update_html_motion_manifest(record: dict, result: dict) -> dict:
    raw_path = str(record.get("archiveManifestPath") or "").strip()
    if not raw_path:
        return {"status": "skipped", "reason": "manifest 不存在"}
    path = Path(raw_path)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    payload = _load_json_file(path)
    if not payload:
        return {"status": "skipped", "reason": "manifest 不存在或不可读"}
    payload["htmlMotionOverlayRegeneration"] = result
    if result.get("status") in {"applied", "degraded"}:
        payload["htmlMotionOverlay"] = result
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        return {"status": "failed", "reason": str(exc)[:300]}
    return {"status": "updated", "path": str(path)}


def _refresh_html_motion_preview(video_path: Path, relative_key: str, result: dict) -> dict:
    if result.get("status") != "applied":
        return {"ok": False, "status": "skipped", "reason": "HTML 动效未叠加"}
    root = ensure_user_generated_result_dir()
    return generate_preview_for_video(video_path, root, relative_key)


def _next_available_path(directory: Path, filename: str) -> Path:
    safe_name = Path(filename).name
    candidate = directory / safe_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 1000):
        numbered = directory / f"{stem}-{index}{suffix}"
        if not numbered.exists():
            return numbered
    raise RuntimeError("too many duplicate material filenames")


def _index_uploaded_script(target: Path, root: Path) -> dict[str, Any]:
    try:
        document = index_script_path(target, root=root)
    except Exception as exc:
        return {
            "ok": False,
            "name": target.name,
            "error": _script_knowledge_error(exc),
            "status": get_script_knowledge_store().status(),
        }
    return {
        "ok": True,
        "id": int(document.get("id") or 0),
        "name": str(document.get("name") or target.name),
        "indexStatus": str(document.get("indexStatus") or "ready"),
        "sectionCount": int(document.get("sectionCount") or 0),
    }


def _script_knowledge_error(exc: Exception) -> str:
    if isinstance(exc, ScriptKnowledgeUnavailable):
        return str(exc)
    message = str(exc).splitlines()[0].strip()
    return message[:300] or "PostgreSQL 剧本知识库不可用"


def _clear_directory_files(root: Path) -> None:
    resolved = root.resolve()
    if not resolved.exists():
        return
    for path in resolved.rglob("*"):
        if not path.is_file():
            continue
        if not _is_within(resolved, path.resolve()):
            continue
        try:
            path.unlink()
        except OSError:
            pass
    for path in sorted(resolved.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            pass


def _path_stats_recursive(path: Path, extensions: set[str] | None = None) -> dict[str, int | str]:
    path.mkdir(parents=True, exist_ok=True)
    root = path.resolve()
    file_count = 0
    total_bytes = 0
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        if extensions and item.suffix.lower() not in extensions:
            continue
        file_count += 1
        try:
            total_bytes += item.stat().st_size
        except OSError:
            continue
    return {
        "path": str(root),
        "fileCount": file_count,
        "sizeBytes": total_bytes,
        "display": f"{file_count} 个 · {_format_bytes(total_bytes)}",
    }


def _path_stats_direct(path: Path, extensions: set[str] | None = None) -> dict[str, int | str]:
    path.mkdir(parents=True, exist_ok=True)
    root = path.resolve()
    files = [
        item for item in root.iterdir()
        if item.is_file() and (not extensions or item.suffix.lower() in extensions)
    ]
    total_bytes = sum(item.stat().st_size for item in files if item.exists())
    return {
        "path": str(root),
        "fileCount": len(files),
        "sizeBytes": total_bytes,
        "display": f"{len(files)} 个 · {_format_bytes(total_bytes)}",
    }


def _selected_files_stats(path: Path, files: list[Path]) -> dict[str, int | str]:
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    file_count = 0
    total_bytes = 0
    for item in files:
        if not item.is_file():
            continue
        file_count += 1
        try:
            total_bytes += item.stat().st_size
        except OSError:
            continue
    return {
        "path": str(root),
        "fileCount": file_count,
        "sizeBytes": total_bytes,
        "display": f"{file_count} 个 · {_format_bytes(total_bytes)}",
    }


def _html_motion_work_files() -> list[Path]:
    root = HTML_MOTION_DIR.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return [
        item
        for work_dir in root.iterdir()
        if work_dir.is_dir() and work_dir.name.startswith("render-")
        for item in work_dir.rglob("*")
        if item.is_file()
    ]


def _restored_metadata_root() -> Path:
    return (ensure_user_generated_result_dir() / RESTORED_RESULT_METADATA_DIR).resolve()


def _restored_metadata_video_path(path: Path) -> Path:
    root = ensure_user_generated_result_dir().resolve()
    metadata_root = _restored_metadata_root()
    try:
        payload = normalize_legacy_video_payload(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        payload = {}
    relative_key = str(payload.get("userGeneratedKey") or "").strip() if isinstance(payload, dict) else ""
    if not relative_key:
        relative_key = path.relative_to(metadata_root).with_suffix("").as_posix()
    candidate = (root / relative_key).resolve()
    return candidate if candidate.is_relative_to(root) else root / ".invalid-restored-metadata"


def _restored_metadata_stats() -> dict[str, int | str]:
    root = _restored_metadata_root()
    stats = _path_stats_recursive(root, {".json"})
    orphan_count = sum(1 for item in root.rglob("*.json") if not _restored_metadata_video_path(item).is_file())
    stats["orphanCount"] = orphan_count
    stats["display"] = f"{stats['fileCount']} 个 · {_format_bytes(int(stats['sizeBytes']))} · 孤儿 {orphan_count}"
    return stats


ARCHIVE_RESULT_JUNK_NAMES = {".DS_Store"}


def _result_junk_stats() -> dict[str, int | str]:
    root = ensure_user_generated_result_dir().resolve()
    files = [root / name for name in ARCHIVE_RESULT_JUNK_NAMES if (root / name).is_file()]
    return _selected_files_stats(root, files)


def _archive_manifest_root() -> Path:
    configured = Path(AI8VideoConfig.from_env().archive_local_dir)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _asset_store_path() -> Path:
    configured = Path(AI8VideoConfig.from_env().asset_store_path)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _known_user_generated_video_stems() -> set[str]:
    root = ensure_user_generated_result_dir()
    return {
        item.stem
        for item in root.rglob("*")
        if item.is_file() and item.suffix.lower() in USER_GENERATED_VIDEO_EXTENSIONS
    }


def _count_orphan_cover_files() -> int:
    root = ensure_user_generated_result_dir()
    video_stems = _known_user_generated_video_stems()
    count = 0
    for item in (root / "cover").rglob("*"):
        if item.is_file() and item.suffix.lower() in USER_GENERATED_IMAGE_EXTENSIONS and item.stem not in video_stems:
            count += 1
    return count


def _asset_record_video_path(record: dict) -> Path | None:
    raw_path = str(
        record.get("archiveLocalPath")
        or record.get("userGeneratedLocalPath")
        or record.get("localVideoPath")
        or ""
    ).strip()
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()
    return _resolve_legacy_result_video_path(candidate)


def _resolve_legacy_result_video_path(candidate: Path) -> Path:
    if candidate.is_file():
        return candidate
    root = ensure_user_generated_result_dir().resolve()
    try:
        relative = candidate.resolve().relative_to(root)
    except ValueError:
        return candidate
    canonical_candidate = (root / "video" / relative.name).resolve()
    return canonical_candidate if canonical_candidate.is_file() else candidate


def _asset_record_is_orphan(record: dict) -> bool:
    if str(record.get("archiveBackend") or "").strip().lower() not in {"", "local"}:
        return False
    if str(record.get("archiveStatus") or "").strip().lower() != "archived":
        return False
    candidate = _asset_record_video_path(record)
    return candidate is not None and not candidate.is_file()


def _asset_index_stats() -> dict[str, int | str]:
    path = _asset_store_path()
    records = JsonlAssetStore(path).read_all()
    total_bytes = path.stat().st_size if path.is_file() else 0
    orphan_count = sum(1 for record in records if _asset_record_is_orphan(record))
    return {
        "path": str(path),
        "fileCount": len(records),
        "sizeBytes": total_bytes,
        "orphanCount": orphan_count,
        "display": f"{len(records)} 条 · {_format_bytes(total_bytes)} · 孤儿 {orphan_count}",
    }


def _manifest_video_path(manifest: dict) -> Path | None:
    candidates = [
        manifest.get("localVideo"),
        manifest.get("archiveLocalPath"),
        manifest.get("local_video"),
    ]
    for container_key in ("generation", "postprocess", "job"):
        container = manifest.get(container_key)
        if isinstance(container, dict):
            candidates.extend([
                container.get("archiveLocalPath"),
                container.get("localVideo"),
                container.get("video"),
            ])
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        path = Path(text)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        if path.suffix.lower() in USER_GENERATED_VIDEO_EXTENSIONS:
            return _resolve_legacy_result_video_path(path)
    return None


def _manifest_is_orphan(path: Path) -> bool:
    try:
        manifest = normalize_legacy_video_payload(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return False
    if not isinstance(manifest, dict):
        return False
    video_path = _manifest_video_path(manifest)
    return video_path is not None and not video_path.is_file()


def _manifest_stats() -> dict[str, int | str]:
    root = _archive_manifest_root()
    stats = _path_stats_recursive(root, {".json"})
    orphan_count = sum(1 for item in root.rglob("*.json") if item.is_file() and _manifest_is_orphan(item))
    stats["orphanCount"] = orphan_count
    stats["display"] = f"{stats['fileCount']} 个 · {_format_bytes(int(stats['sizeBytes']))} · 孤儿 {orphan_count}"
    return stats


def _archive_intermediate_artifacts_status() -> dict[str, Any]:
    root = ensure_user_generated_result_dir()
    video_stats = _path_stats_direct(root / "video", USER_GENERATED_VIDEO_EXTENSIONS)
    cover_stats = _folder_stats(root / "cover", USER_GENERATED_IMAGE_EXTENSIONS)
    cover_stats["orphanCount"] = _count_orphan_cover_files()
    if int(cover_stats.get("orphanCount") or 0):
        cover_stats["display"] = f"{cover_stats['fileCount']} 张 · {_format_bytes(int(cover_stats['sizeBytes']))} · 孤儿 {cover_stats['orphanCount']}"
    preview_stats = _folder_stats(root / PREVIEW_DIR_NAME, USER_GENERATED_IMAGE_EXTENSIONS)
    extension_stats = _path_stats_recursive(root / "extensions")
    extension_frame_stats = _path_stats_recursive(root / "extension-frame")
    html_motion_work_stats = _selected_files_stats(HTML_MOTION_DIR, _html_motion_work_files())
    html_motion_review_stats = _path_stats_recursive(HTML_MOTION_REVIEW_ROOT)
    tts_stats = _path_stats_recursive(local_tts_output_dir(), ARCHIVE_ARTIFACT_AUDIO_EXTENSIONS)
    merge_stats = _path_stats_recursive(MERGE_TEMP_MEDIA_DIR, ARCHIVE_ARTIFACT_ALL_EXTENSIONS)
    reference_stats = _path_stats_recursive(TRANSFORMED_REFERENCE_DIR, USER_GENERATED_IMAGE_EXTENSIONS)
    recycle_stats = _path_stats_recursive(ensure_user_recycle_bin_dir(), {".mp4", ".mov", ".m4v", ".json"})
    items = {
        "result-videos": {**video_stats, "label": "结果视频", "cleanup": "none"},
        "covers": {**cover_stats, "label": "封面图", "cleanup": "orphan-covers"},
        "previews": {**preview_stats, "label": "预览图", "cleanup": "regenerate"},
        "extension-archive": {**extension_stats, "label": "延长视频归档", "cleanup": "clear"},
        "extension-frames": {**extension_frame_stats, "label": "延长截帧缓存", "cleanup": "clear"},
        "html-motion-work": {**html_motion_work_stats, "label": "HTML 动效失败工作目录", "cleanup": "clear"},
        "html-motion-reviews": {**html_motion_review_stats, "label": "HTML 动效审核缓存", "cleanup": "clear"},
        "restored-metadata": {**_restored_metadata_stats(), "label": "结果恢复元数据", "cleanup": "orphan-restored-metadata"},
        "result-junk": {**_result_junk_stats(), "label": "结果目录杂项", "cleanup": "junk"},
        "tts-output": {**tts_stats, "label": "TTS 配音输出", "cleanup": "clear"},
        "merge-temp": {**merge_stats, "label": "视频合并临时媒体", "cleanup": "clear"},
        "reference-temp": {**reference_stats, "label": "参考图图生图临时结果", "cleanup": "clear"},
        "manifests": {**_manifest_stats(), "label": "归档元数据", "cleanup": "orphan-manifests"},
        "asset-index": {**_asset_index_stats(), "label": "资产索引", "cleanup": "asset-index-orphans"},
        "recycle-bin": {**recycle_stats, "label": "失败任务回收站", "cleanup": "clear"},
    }
    total_bytes = sum(int(item.get("sizeBytes") or 0) for item in items.values())
    return {
        "ok": True,
        "items": items,
        "totalBytes": total_bytes,
        "totalDisplay": _format_bytes(total_bytes),
    }


def _cleanup_orphan_covers() -> dict[str, Any]:
    root = ensure_user_generated_result_dir().resolve()
    cover_root = root / "cover"
    video_stems = _known_user_generated_video_stems()
    deleted: list[str] = []
    for item in cover_root.rglob("*"):
        if not item.is_file() or item.suffix.lower() not in USER_GENERATED_IMAGE_EXTENSIONS:
            continue
        if item.stem in video_stems:
            continue
        resolved = item.resolve()
        if not _is_within(root, resolved):
            continue
        deleted.append(resolved.relative_to(root).as_posix())
        resolved.unlink()
    _prune_empty_dirs(cover_root)
    return {"ok": True, "kind": "orphan-covers", "deletedCount": len(deleted), "deleted": deleted[:50]}


def _cleanup_orphan_manifests() -> dict[str, Any]:
    root = _archive_manifest_root().resolve()
    deleted: list[str] = []
    for item in root.rglob("*.json"):
        if not item.is_file() or not _manifest_is_orphan(item):
            continue
        resolved = item.resolve()
        if not _is_within(root, resolved):
            continue
        deleted.append(resolved.relative_to(root).as_posix())
        resolved.unlink()
    _prune_empty_dirs(root)
    return {"ok": True, "kind": "orphan-manifests", "deletedCount": len(deleted), "deleted": deleted[:50]}


def _cleanup_asset_index_orphans() -> dict[str, Any]:
    removed_count, remaining_count = _asset_maintenance_service().remove_records(
        _asset_record_is_orphan
    )
    return {
        "ok": True,
        "kind": "asset-index-orphans",
        "deletedCount": removed_count,
        "remainingCount": remaining_count,
    }


def _cleanup_orphan_restored_metadata() -> dict[str, Any]:
    root = _restored_metadata_root()
    deleted: list[str] = []
    removed_bytes = 0
    for item in root.rglob("*.json"):
        if _restored_metadata_video_path(item).is_file():
            continue
        try:
            removed_bytes += item.stat().st_size
        except OSError:
            pass
        deleted.append(item.relative_to(root).as_posix())
        item.unlink()
    _prune_empty_dirs(root)
    return {
        "ok": True,
        "kind": "orphan-restored-metadata",
        "deletedCount": len(deleted),
        "removedBytes": removed_bytes,
        "removedDisplay": _format_bytes(removed_bytes),
        "deleted": deleted[:50],
    }


def _cleanup_result_junk() -> dict[str, Any]:
    root = ensure_user_generated_result_dir().resolve()
    removed_bytes = 0
    deleted: list[str] = []
    for name in ARCHIVE_RESULT_JUNK_NAMES:
        item = root / name
        if not item.is_file():
            continue
        removed_bytes += item.stat().st_size
        deleted.append(name)
        item.unlink()
    return {
        "ok": True,
        "kind": "result-junk",
        "deletedCount": len(deleted),
        "removedBytes": removed_bytes,
        "removedDisplay": _format_bytes(removed_bytes),
        "deleted": deleted,
    }


def _cleanup_html_motion_work_dirs() -> dict[str, Any]:
    root = HTML_MOTION_DIR.resolve()
    files = _html_motion_work_files()
    removed_bytes = sum(item.stat().st_size for item in files if item.is_file())
    work_dirs = [item for item in root.iterdir() if item.is_dir() and item.name.startswith("render-")]
    for work_dir in work_dirs:
        if work_dir.parent.resolve() == root:
            shutil.rmtree(work_dir)
    return {
        "ok": True,
        "kind": "html-motion-work",
        "deletedCount": len(files),
        "removedBytes": removed_bytes,
        "removedDisplay": _format_bytes(removed_bytes),
        "deleted": [item.name for item in work_dirs[:50]],
    }


def _cleanup_directory_contents(root: Path, *, kind: str, extensions: set[str] | None = None) -> dict[str, Any]:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    deleted: list[str] = []
    removed_bytes = 0
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        if extensions and item.suffix.lower() not in extensions:
            continue
        resolved = item.resolve()
        if not _is_within(root, resolved):
            continue
        try:
            removed_bytes += resolved.stat().st_size
        except OSError:
            pass
        deleted.append(resolved.relative_to(root).as_posix())
        resolved.unlink()
    _prune_empty_dirs(root)
    return {
        "ok": True,
        "kind": kind,
        "deletedCount": len(deleted),
        "removedBytes": removed_bytes,
        "removedDisplay": _format_bytes(removed_bytes),
        "deleted": deleted[:50],
    }


def _prune_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for item in sorted(root.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        if not item.is_dir():
            continue
        try:
            item.rmdir()
        except OSError:
            pass


def _archive_artifact_root(kind: str) -> Path:
    roots = {
        "result-videos": ensure_user_generated_result_dir() / "video",
        "covers": ensure_user_generated_result_dir() / "cover",
        "previews": ensure_user_generated_result_dir() / PREVIEW_DIR_NAME,
        "extension-archive": ensure_user_generated_result_dir() / "extensions",
        "extension-frames": ensure_user_generated_result_dir() / "extension-frame",
        "html-motion-work": HTML_MOTION_DIR,
        "html-motion-reviews": HTML_MOTION_REVIEW_ROOT,
        "restored-metadata": _restored_metadata_root(),
        "result-junk": ensure_user_generated_result_dir(),
        "tts-output": local_tts_output_dir(),
        "merge-temp": MERGE_TEMP_MEDIA_DIR,
        "reference-temp": TRANSFORMED_REFERENCE_DIR,
        "manifests": _archive_manifest_root(),
        "asset-index": _asset_store_path().parent,
        "recycle-bin": ensure_user_recycle_bin_dir(),
    }
    if kind not in roots:
        raise ValueError("unknown artifact kind")
    return roots[kind].resolve()


def _cleanup_archive_artifacts(kind: str) -> dict[str, Any]:
    normalized = str(kind or "").strip()
    if normalized == "all":
        return _cleanup_all_archive_artifacts()
    if normalized == "covers":
        return _cleanup_orphan_covers()
    if normalized == "previews":
        return regenerate_previews_for_videos(ensure_user_generated_result_dir(), USER_GENERATED_VIDEO_EXTENSIONS)
    if normalized == "extension-archive":
        return _cleanup_directory_contents(ensure_user_generated_result_dir() / "extensions", kind=normalized)
    if normalized == "extension-frames":
        return _cleanup_directory_contents(ensure_user_generated_result_dir() / "extension-frame", kind=normalized)
    if normalized == "html-motion-work":
        return _cleanup_html_motion_work_dirs()
    if normalized == "html-motion-reviews":
        return _cleanup_directory_contents(HTML_MOTION_REVIEW_ROOT, kind=normalized)
    if normalized == "restored-metadata":
        return _cleanup_orphan_restored_metadata()
    if normalized == "result-junk":
        return _cleanup_result_junk()
    if normalized == "tts-output":
        return _cleanup_directory_contents(local_tts_output_dir(), kind=normalized, extensions=ARCHIVE_ARTIFACT_AUDIO_EXTENSIONS)
    if normalized == "merge-temp":
        return _cleanup_directory_contents(MERGE_TEMP_MEDIA_DIR, kind=normalized)
    if normalized == "reference-temp":
        return _cleanup_directory_contents(TRANSFORMED_REFERENCE_DIR, kind=normalized, extensions=USER_GENERATED_IMAGE_EXTENSIONS)
    if normalized == "manifests":
        return _cleanup_orphan_manifests()
    if normalized == "asset-index":
        return _cleanup_asset_index_orphans()
    if normalized == "recycle-bin":
        return _cleanup_directory_contents(ensure_user_recycle_bin_dir(), kind=normalized, extensions={".mp4", ".mov", ".m4v", ".json"})
    raise ValueError("unknown artifact kind")


ARCHIVE_ONE_CLICK_CLEANUP_KINDS = (
    "extension-archive",
    "extension-frames",
    "html-motion-work",
    "html-motion-reviews",
    "restored-metadata",
    "result-junk",
    "tts-output",
    "merge-temp",
    "reference-temp",
    "recycle-bin",
    "covers",
    "manifests",
    "asset-index",
)


def _cleanup_all_archive_artifacts() -> dict[str, Any]:
    results = [_cleanup_archive_artifacts(kind) for kind in ARCHIVE_ONE_CLICK_CLEANUP_KINDS]
    deleted_count = sum(int(item.get("deletedCount") or 0) for item in results)
    removed_bytes = sum(int(item.get("removedBytes") or 0) for item in results)
    return {
        "ok": True,
        "kind": "all",
        "deletedCount": deleted_count,
        "removedBytes": removed_bytes,
        "removedDisplay": _format_bytes(removed_bytes),
        "results": results,
    }




def _batch_report_root() -> Path:
    configured = Path(AI8VideoConfig.from_env().batch_report_dir)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _resolve_batch_report_path(raw_path: str) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError("reportPath is required")
    root = _batch_report_root()
    target = Path(text)
    if not target.is_absolute():
        project_relative = (PROJECT_ROOT / target).resolve()
        root_relative = (root / target).resolve()
        if _is_within(root, project_relative):
            target = project_relative
        elif _is_within(root, root_relative):
            target = root_relative
        else:
            target = project_relative
    else:
        target = target.resolve()
    if not _is_within(root, target):
        raise ValueError("reportPath is outside batch report dir")
    return target


def _batch_alert_root() -> Path:
    configured = Path(AI8VideoConfig.from_env().batch_alert_dir)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _resolve_batch_alert_path(raw_path: str) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError("alertPath is required")
    root = _batch_alert_root()
    target = Path(text)
    if not target.is_absolute():
        project_relative = (PROJECT_ROOT / target).resolve()
        root_relative = (root / target).resolve()
        if _is_within(root, project_relative):
            target = project_relative
        elif _is_within(root, root_relative):
            target = root_relative
        else:
            target = project_relative
    else:
        target = target.resolve()
    if not _is_within(root, target):
        raise ValueError("alertPath is outside batch alert dir")
    return target


def _batch_supervisor_state_path() -> Path:
    configured = Path(AI8VideoConfig.from_env().batch_supervisor_state_path)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _batch_supervisor_admin_state_path() -> Path:
    return get_supervisor_admin_result_path(refresh=True)


def _batch_supervisor_lock_path() -> Path:
    configured = Path(AI8VideoConfig.from_env().batch_supervisor_lock_path)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _batch_supervisor_deployment_path() -> Path:
    return default_launchd_plist_path().expanduser().resolve()


def _batch_seed_file_path() -> Path:
    return resolve_batch_seed_file_path(AI8VideoConfig.from_env())[0].resolve()


def _resolve_archive_local_target(raw_path: str) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError("localPath is required")
    target = Path(text)
    if not target.is_absolute():
        target = (PROJECT_ROOT / target).resolve()
    else:
        target = target.resolve()
    for root in _archive_roots():
        if _is_within(root, target):
            return target
    raise ValueError("localPath is outside archive roots")


def _parse_int_payload(value: object, *, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return max(minimum, int(default))
    return max(minimum, parsed)


def _parse_float_payload(value: object, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return min(maximum, max(minimum, parsed))


def _parse_bool_payload(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _ensure_batch_seed_file_ready(*, auto_build: bool = True) -> Path:
    target = _batch_seed_file_path()
    if target.exists() and target.is_file():
        return target
    if auto_build:
        build_batch_seed_file_payload(report_limit=8, max_messages=40, refresh=True)
        target = _batch_seed_file_path()
        if target.exists() and target.is_file():
            return target
    raise ValueError("值守种子文件还没生成，请先生成种子文件。")


def _resolve_batch_supervisor_schedule_text(payload: dict, deployment: dict | None) -> str:
    raw_value = str(payload.get("scheduleTimes") or "").strip()
    if raw_value:
        return raw_value
    configured = str(AI8VideoConfig.from_env().batch_schedule_times or "").strip()
    if configured:
        return configured
    if isinstance(deployment, dict):
        deployed_times = [str(item).strip() for item in deployment.get("scheduleTimes") or [] if str(item).strip()]
        if deployed_times:
            return ",".join(deployed_times)
    return ""


def _batch_supervisor_request_options(payload: dict) -> dict:
    config = AI8VideoConfig.from_env()
    deployment = inspect_launchd_deployment(plist_path=_batch_supervisor_deployment_path())
    schedule_times = _resolve_batch_supervisor_schedule_text(payload, deployment)
    if not schedule_times:
        raise ValueError("请先提供自动排期，例如 09:00,13:15。")
    seed_path = _ensure_batch_seed_file_ready(
        auto_build=_parse_bool_payload(payload.get("autoBuildSeedFile"), default=True),
    )
    return {
        "config": config,
        "deployment": deployment,
        "plist_path": _batch_supervisor_deployment_path(),
        "seed_file": str(seed_path),
        "schedule_times": schedule_times,
        "target_pass_count": _parse_int_payload(
            payload.get("targetPassCount") or deployment.get("targetPassCount"),
            default=config.batch_target_pass_count,
            minimum=1,
        ),
        "style_hint": str(payload.get("styleHint") or config.batch_style_hint or deployment.get("styleHint") or "").strip() or None,
        "poll_seconds": _parse_int_payload(
            payload.get("pollSeconds") or deployment.get("pollSeconds"),
            default=30,
            minimum=5,
        ),
        "min_pass_rate": _parse_float_payload(
            payload.get("minPassRate") or deployment.get("minPassRate"),
            default=config.batch_alert_min_pass_rate,
            minimum=0.0,
            maximum=1.0,
        ),
        "consecutive_low_pass_runs": _parse_int_payload(
            payload.get("consecutiveLowPassRuns") or deployment.get("consecutiveLowPassRuns"),
            default=config.batch_alert_consecutive_low_pass_runs,
            minimum=1,
        ),
    }


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _open_in_file_manager(target: Path) -> None:
    resolved = target.resolve()
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(resolved)])
        return
    if os.name == "nt":
        subprocess.Popen(["explorer", str(resolved)])
        return
    subprocess.Popen(["xdg-open", str(resolved)])


def _open_path(target: Path) -> None:
    resolved = target.resolve()
    if resolved.is_dir():
        _open_in_file_manager(resolved)
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(resolved)])
        return
    if os.name == "nt":
        os.startfile(str(resolved))  # type: ignore[attr-defined]
        return
    subprocess.Popen(["xdg-open", str(resolved)])


def _web_chat_timeout_seconds() -> int:
    raw = str(os.getenv("AI8VIDEO_WEB_CHAT_TIMEOUT_SECONDS") or DEFAULT_WEB_CHAT_TIMEOUT_SECONDS).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_WEB_CHAT_TIMEOUT_SECONDS
    return max(30, min(300, value))


@app.route("/media/<archive_key:path>", method=["GET", "OPTIONS"])
def media_file(archive_key: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    clean_key = archive_key.strip().lstrip("/")
    if not clean_key:
        response.status = 400
        return {"error": "archive key is required"}
    for root in _archive_roots():
        candidate = (root / clean_key).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return static_file(clean_key, root=str(root))
    response.status = 404
    return {"error": "media file not found", "archiveKey": clean_key}


@app.route("/api/health", method=["GET", "OPTIONS"])
def api_health():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = get_health_payload(refresh=True)
    payload.setdefault("chatBackend", CHAT_BACKEND)
    return payload


@app.route("/api/system-prompt", method=["GET", "POST", "OPTIONS"])
def api_system_prompt():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "POST":
        payload = request.json or {}
        saved = write_business_prompt(str(payload.get("content") or ""))
        saved.pop("path", None)
        saved["editable"] = True
        return saved
    meta = business_prompt_meta()
    return {
        "ok": True,
        "updatedAt": meta["updatedAt"],
        "content": read_business_prompt(),
        "editable": True,
    }


@app.route("/api/auth-settings", method=["GET", "OPTIONS"])
def api_auth_settings():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    config = AI8VideoConfig.from_env()
    video_settings = load_video_model_settings(
        llm_base_url=getattr(config, "llm_base_url", None),
        llm_api_key=getattr(config, "llm_api_key", None),
    )
    core_model = load_ai8video_core_model_settings() or {}
    core_source = str(core_model.get("source") or "missing")
    tts = local_tts_status()
    tts_voice_label = "音色"
    if int(tts.get("voiceCount") or 0) > 0:
        tts_voice_label = f"音色（{tts['voiceCount']} 个）"
    archive_artifacts = _archive_intermediate_artifacts_status()
    html_motion = html_motion_overlay_status()
    narration_review = narration_review_status()
    archive_items = archive_artifacts["items"]
    fields = [
        _settings_field("视频合并", "AI8VIDEO_VIDEO_MERGE", load_video_merge_mode(), "用户文件夹/视频合并", sensitive=False, category="运行模式"),
        _settings_field("接口地址", "AI8VIDEO_LOCAL_TTS_API_BASE_URL", tts.get("apiBaseUrl"), "用户文件夹/TTS", sensitive=False, category="TTS"),
        _settings_field("API Key", "AI8VIDEO_LOCAL_TTS_API_KEY", tts.get("apiKey"), "用户文件夹/TTS", category="TTS"),
        _settings_field("模型名", "AI8VIDEO_LOCAL_TTS_MODEL", tts.get("model"), "用户文件夹/TTS", sensitive=False, category="TTS"),
        _settings_field("音色克隆模型", "AI8VIDEO_LOCAL_TTS_CLONE_MODEL", tts.get("cloneModel"), "用户文件夹/TTS/音色克隆", sensitive=False, category="TTS"),
        _settings_field(tts_voice_label, "AI8VIDEO_LOCAL_TTS_VOICE", tts["voice"], "用户文件夹/TTS", sensitive=False, category="TTS"),
        _settings_field("旁白音量", "AI8VIDEO_LOCAL_TTS_VOLUME", str(tts["volume"]), "用户文件夹/TTS", sensitive=False, category="TTS"),
        _settings_field("输出目录", "AI8VIDEO_LOCAL_TTS_OUTPUT_DIR", tts["outputSizeDisplay"], "用户文件夹/TTS/输出", sensitive=False, category="TTS"),
        _settings_field("接口地址", "mykey.py apibase", core_model.get("apibase"), core_source, sensitive=False, category="AI8video"),
        _settings_field("API Key", "mykey.py apikey", core_model.get("apikey"), core_source, category="AI8video"),
        _settings_field("模型名", "mykey.py model", core_model.get("model"), core_source, sensitive=False, category="AI8video"),
        _settings_field("接口地址", "AI8VIDEO_LLM_BASE_URL", config.llm_base_url, config.llm_source, sensitive=False, category="文本/视频规划模型"),
        _settings_field("API Key", "AI8VIDEO_LLM_API_KEY", config.llm_api_key, config.llm_source, category="文本/视频规划模型"),
        _settings_field("模型名", "AI8VIDEO_LLM_MODEL", config.llm_model, config.llm_source, sensitive=False, category="文本/视频规划模型"),
        _settings_field(
            "台词审核次数",
            "NARRATION_REVIEW_COUNT",
            str(narration_review["reviewCount"]),
            "用户文件夹/台词审核/settings.json",
            sensitive=False,
            category="文本/视频规划模型",
        ),
        _settings_field("接口地址", "AI8VIDEO_MULTIMODAL_BASE_URL", getattr(config, "multimodal_base_url", None), getattr(config, "multimodal_source", "missing"), sensitive=False, category="多模态模型"),
        _settings_field("API Key", "AI8VIDEO_MULTIMODAL_API_KEY", getattr(config, "multimodal_api_key", None), getattr(config, "multimodal_source", "missing"), category="多模态模型"),
        _settings_field("模型名", "AI8VIDEO_MULTIMODAL_MODEL", getattr(config, "multimodal_model", None), getattr(config, "multimodal_source", "missing"), sensitive=False, category="多模态模型"),
        _settings_field("接口地址", "AI8VIDEO_IMAGE_BASE_URL", config.image_base_url, config.image_source, sensitive=False, category="图片模型"),
        _settings_field("API Key", "AI8VIDEO_IMAGE_API_KEY", config.image_api_key, config.image_source, category="图片模型"),
        _settings_field("模型名", "AI8VIDEO_IMAGE_MODEL", config.image_model, config.image_source, sensitive=False, category="图片模型"),
        _settings_field("接口地址", "AI8VIDEO_VIDEO_BASE_URL", video_settings.base_url, video_settings.source, sensitive=False, category="视频模型"),
        _settings_field("API Key", "AI8VIDEO_VIDEO_API_KEY", video_settings.api_key, video_settings.source, category="视频模型"),
        _settings_field("模型名", "AI8VIDEO_VIDEO_MODEL", video_settings.model, video_settings.source, sensitive=False, category="视频模型"),
        _settings_field("模板", "AI8VIDEO_VIDEO_TEMPLATE", video_settings.template, video_settings.source, sensitive=False, category="视频模型"),
        _settings_field(
            "不合格重试次数",
            "HTML_MOTION_QUALITY_RETRY_COUNT",
            str(html_motion["qualityRetryCount"]),
            "用户文件夹/HTML动效/settings.json",
            sensitive=False,
            category="HTML 动效",
        ),
        _settings_field(
            "每拍间隔秒数",
            "HTML_MOTION_BEAT_INTERVAL_SECONDS",
            str(html_motion["beatIntervalSeconds"]),
            "用户文件夹/HTML动效/settings.json",
            sensitive=False,
            category="HTML 动效",
        ),
        _settings_field("后端", "AI8VIDEO_ARCHIVE_BACKEND", config.archive_backend, "env/default", sensitive=False, category="归档"),
        _settings_field(
            "结果视频",
            "AI8VIDEO_ARCHIVE_RESULT_VIDEO_DIR",
            archive_items["result-videos"]["display"],
            "用户文件夹/用户生成结果/video",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "封面图",
            "AI8VIDEO_ARCHIVE_COVER_DIR",
            archive_items["covers"]["display"],
            "用户文件夹/用户生成结果/cover",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "预览图",
            "AI8VIDEO_ARCHIVE_PREVIEW_DIR",
            archive_items["previews"]["display"],
            f"用户文件夹/用户生成结果/{PREVIEW_DIR_NAME}",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "延长视频归档",
            "AI8VIDEO_ARCHIVE_EXTENSION_DIR",
            archive_items["extension-archive"]["display"],
            "用户文件夹/用户生成结果/extensions",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "延长截帧缓存",
            "AI8VIDEO_ARCHIVE_EXTENSION_FRAME_DIR",
            archive_items["extension-frames"]["display"],
            "用户文件夹/用户生成结果/extension-frame",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "HTML 动效失败工作目录",
            "AI8VIDEO_ARCHIVE_HTML_MOTION_WORK_DIR",
            archive_items["html-motion-work"]["display"],
            "用户文件夹/HTML动效/render-*",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "HTML 动效审核缓存",
            "AI8VIDEO_ARCHIVE_HTML_MOTION_REVIEW_DIR",
            archive_items["html-motion-reviews"]["display"],
            "用户文件夹/HTML动效/reviews",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "结果恢复元数据",
            "AI8VIDEO_ARCHIVE_RESTORED_METADATA_DIR",
            archive_items["restored-metadata"]["display"],
            "用户文件夹/用户生成结果/.restored-meta",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "结果目录杂项",
            "AI8VIDEO_ARCHIVE_RESULT_JUNK",
            archive_items["result-junk"]["display"],
            "用户文件夹/用户生成结果",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "TTS 配音输出",
            "AI8VIDEO_ARCHIVE_TTS_OUTPUT_DIR",
            archive_items["tts-output"]["display"],
            "用户文件夹/TTS/输出",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "视频合并临时媒体",
            "AI8VIDEO_ARCHIVE_MERGE_TEMP_DIR",
            archive_items["merge-temp"]["display"],
            "用户文件夹/临时媒体/视频合并",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "参考图图生图临时结果",
            "AI8VIDEO_ARCHIVE_REFERENCE_TEMP_DIR",
            archive_items["reference-temp"]["display"],
            "用户文件夹/参考图/图生图结果",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "归档元数据",
            "AI8VIDEO_ARCHIVE_MANIFEST_DIR",
            archive_items["manifests"]["display"],
            "media_resources/ai8video/archive",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "资产索引",
            "AI8VIDEO_ARCHIVE_ASSET_INDEX",
            archive_items["asset-index"]["display"],
            "temp/ai8video/assets.jsonl",
            sensitive=False,
            category="归档",
        ),
        _settings_field(
            "失败任务回收站",
            "AI8VIDEO_ARCHIVE_RECYCLE_BIN_DIR",
            archive_items["recycle-bin"]["display"],
            "用户文件夹/回收站",
            sensitive=False,
            category="归档",
        ),
    ]
    show_oss_fields = (
        config.archive_backend in {"s3", "oss", "r2"}
        or bool(
            config.archive_s3_endpoint
            or config.archive_s3_bucket
            or config.archive_s3_access_key
            or config.archive_s3_secret_key
        )
    )
    if show_oss_fields:
        fields.extend([
            _settings_field("OSS Endpoint", "AI8VIDEO_ARCHIVE_S3_ENDPOINT", config.archive_s3_endpoint, "env", sensitive=False, category="归档"),
            _settings_field("OSS Bucket", "AI8VIDEO_ARCHIVE_S3_BUCKET", config.archive_s3_bucket, "env", sensitive=False, category="归档"),
            _settings_field("OSS AccessKey", "AI8VIDEO_ARCHIVE_S3_ACCESS_KEY", config.archive_s3_access_key, "env", category="归档"),
            _settings_field("OSS SecretKey", "AI8VIDEO_ARCHIVE_S3_SECRET_KEY", config.archive_s3_secret_key, "env", category="归档"),
        ])
    return {
        "ok": True,
        "dryRun": config.dry_run,
        "readyForRealGeneration": bool((not config.dry_run) and config.has_llm() and video_settings.configured()),
        "videoModelConfigured": video_settings.configured(),
        "fields": fields,
        "localTts": tts,
        "archiveArtifacts": archive_artifacts,
        "modelCatalogs": load_model_catalogs(),
    }


@app.route("/api/archive-artifacts", method=["GET", "OPTIONS"])
def api_archive_artifacts():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    return _archive_intermediate_artifacts_status()


@app.route("/api/archive-artifacts/open", method=["POST", "OPTIONS"])
def api_open_archive_artifact_folder():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    kind = str(payload.get("kind") or "").strip() if isinstance(payload, dict) else ""
    try:
        target = _archive_artifact_root(kind)
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    target.mkdir(parents=True, exist_ok=True)
    _open_in_file_manager(target)
    return {"ok": True, "kind": kind, "path": str(target)}


@app.route("/api/archive-artifacts/cleanup", method=["POST", "OPTIONS"])
def api_cleanup_archive_artifacts():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    kind = str(payload.get("kind") or "").strip() if isinstance(payload, dict) else ""
    try:
        result = _cleanup_archive_artifacts(kind)
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    result["archiveArtifacts"] = _archive_intermediate_artifacts_status()
    return result


@app.route("/api/video-merge-mode", method=["GET", "POST", "OPTIONS"])
def api_video_merge_mode():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "POST":
        payload = request.json or {}
        return save_video_merge_mode(payload.get("mergeMode") if isinstance(payload, dict) else None)
    return video_merge_mode_status()


def _settings_field(
    label: str,
    env_name: str,
    value: str | None,
    source: str,
    *,
    sensitive: bool = True,
    category: str = "其他",
) -> dict:
    text = str(value or "")
    return {
        "label": label,
        "envName": env_name,
        "value": text,
        "configured": bool(text),
        "source": source if text else "missing",
        "sensitive": sensitive,
        "category": category,
    }


def _folder_stats(path: Path, extensions: set[str] | None = None) -> dict[str, int | str]:
    path.mkdir(parents=True, exist_ok=True)
    file_count = 0
    total_bytes = 0
    for item in path.iterdir():
        if not item.is_file():
            continue
        if extensions and item.suffix.lower() not in extensions:
            continue
        file_count += 1
        try:
            total_bytes += item.stat().st_size
        except OSError:
            continue
    return {
        "path": str(path),
        "fileCount": file_count,
        "sizeBytes": total_bytes,
        "display": f"{file_count} 张 · {_format_bytes(total_bytes)}",
    }


def _format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(0, size))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


@app.route("/api/video-model-settings", method=["GET", "POST", "OPTIONS"])
def api_video_model_settings():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    config = AI8VideoConfig.from_env()
    if request.method == "POST":
        payload = request.json or {}
        settings = save_video_model_settings(payload if isinstance(payload, dict) else {})
    else:
        settings = load_video_model_settings(
            llm_base_url=getattr(config, "llm_base_url", None),
            llm_api_key=getattr(config, "llm_api_key", None),
        )
    return {
        "ok": True,
        "settings": settings.public_dict(include_api_key=False),
        "modelCatalog": load_model_catalog("AI8VIDEO_VIDEO_MODEL"),
    }


@app.route("/api/video-model-settings/models", method=["POST", "OPTIONS"])
def api_video_model_settings_models():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    config = AI8VideoConfig.from_env()
    settings = load_video_model_settings(
        llm_base_url=getattr(config, "llm_base_url", None),
        llm_api_key=getattr(config, "llm_api_key", None),
    )
    result = pull_video_model_catalog(settings, timeout_seconds=min(max(config.timeout_seconds, 5), 20))
    if result.get("ok"):
        result["models"] = save_model_catalog("AI8VIDEO_VIDEO_MODEL", result.get("models") or [])
    if not result.get("ok"):
        response.status = 400
    return result


@app.route("/api/auth-settings/models", method=["POST", "OPTIONS"])
def api_auth_settings_models():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    env_name = str((payload if isinstance(payload, dict) else {}).get("envName") or "").strip()
    context = _auth_model_context(env_name)
    if not context:
        response.status = 400
        return {"ok": False, "error": "不支持这个模型设置项。"}
    if not context.get("base_url") or not context.get("api_key"):
        response.status = 400
        return {
            "ok": False,
            "models": [],
            "attempts": [],
            "error": context.get("missing_error") or "未配置接口地址或 API Key。",
        }
    result = pull_model_catalog(
        base_url=context["base_url"],
        api_key=context["api_key"],
        provider="openai-compatible",
        timeout_seconds=20,
        allowed_types=context["allowed_types"],
    )
    if result.get("ok"):
        result["models"] = save_model_catalog(env_name, result.get("models") or [])
    if not result.get("ok"):
        response.status = 400
    return result


@app.route("/api/auth-settings/model-selection", method=["POST", "OPTIONS"])
def api_auth_settings_model_selection():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    env_name = str(payload.get("envName") or "").strip()
    model = str(payload.get("model") or "").strip()
    if not _auth_model_context(env_name):
        response.status = 400
        return {"ok": False, "error": "不支持这个模型设置项。"}
    try:
        save_model_override(env_name, model)
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "envName": env_name, "model": model}


def _auth_model_context(env_name: str) -> dict | None:
    config = AI8VideoConfig.from_env()
    core_model = load_ai8video_core_model_settings() or {}
    if env_name == "mykey.py model":
        return {
            "base_url": core_model.get("apibase"),
            "api_key": core_model.get("apikey"),
            "allowed_types": {"llm"},
            "missing_error": "AI8video 模型没有真实接口地址或 API Key，不能拉取模型。",
        }
    if env_name == "AI8VIDEO_LLM_MODEL":
        return {
            "base_url": config.llm_base_url,
            "api_key": config.llm_api_key,
            "allowed_types": {"llm"},
            "missing_error": "文本/视频规划模型没有真实接口地址或 API Key，不能拉取模型。",
        }
    if env_name == "AI8VIDEO_MULTIMODAL_MODEL":
        return {
            "base_url": getattr(config, "multimodal_base_url", None),
            "api_key": getattr(config, "multimodal_api_key", None),
            "allowed_types": {"llm"},
            "missing_error": "多模态模型没有真实接口地址或 API Key，不能拉取模型。",
        }
    if env_name == "AI8VIDEO_IMAGE_MODEL":
        return {
            "base_url": config.image_base_url,
            "api_key": config.image_api_key,
            "allowed_types": {"image"},
            "missing_error": "图片模型没有真实接口地址或 API Key，不能拉取模型。",
        }
    return None


@app.route("/api/live-preflight", method=["POST", "OPTIONS"])
def api_live_preflight():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    raw_checks = payload.get("checks")
    if raw_checks is None:
        checks = list(SAFE_PREFLIGHT_CHECKS)
    elif isinstance(raw_checks, list):
        checks = [str(item).strip() for item in raw_checks if str(item).strip()]
    else:
        response.status = 400
        return {"error": "checks must be a list"}
    report = run_preflight_checks(AI8VideoConfig.from_env(), checks)
    report["requestedChecks"] = checks
    return report


@app.route("/api/open-archive-dir", method=["POST", "OPTIONS"])
def api_open_archive_dir():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    archive_key = str(payload.get("archiveKey") or "").strip().lstrip("/")
    local_path = str(payload.get("localPath") or "").strip()
    roots = _archive_roots()
    root = roots[0]
    target: Path | None = None
    if local_path:
        try:
            candidate = _resolve_archive_local_target(local_path)
        except ValueError as exc:
            response.status = 400
            return {"error": str(exc)}
        target = candidate.parent if candidate.suffix else candidate
    elif archive_key:
        for candidate_root in roots:
            candidate = (candidate_root / archive_key).resolve()
            try:
                candidate.relative_to(candidate_root)
            except ValueError:
                continue
            target = candidate.parent if candidate.suffix else candidate
            break
    if target is None or not target.exists():
        target = root
    _open_in_file_manager(target)
    return {"ok": True, "path": str(target)}


@app.route("/api/open-user-generated-results-folder", method=["POST", "OPTIONS"])
def api_open_user_generated_results_folder():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target_root = ensure_user_generated_result_dir()
    _open_in_file_manager(target_root)
    return {"ok": True, "path": str(target_root)}


@app.route("/api/open-user-generated-cover-folder", method=["POST", "OPTIONS"])
def api_open_user_generated_cover_folder():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = ensure_user_generated_result_dir() / "cover"
    target.mkdir(parents=True, exist_ok=True)
    _open_in_file_manager(target)
    return {"ok": True, "path": str(target)}


@app.route("/api/open-user-generated-preview-folder", method=["POST", "OPTIONS"])
def api_open_user_generated_preview_folder():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = ensure_user_generated_result_dir() / PREVIEW_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    _open_in_file_manager(target)
    return {"ok": True, "path": str(target)}


@app.route("/api/open-user-recycle-bin-folder", method=["POST", "OPTIONS"])
def api_open_user_recycle_bin_folder():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = ensure_user_recycle_bin_dir()
    _open_in_file_manager(target)
    return {"ok": True, "path": str(target)}


@app.route("/api/user-generated-previews/regenerate", method=["POST", "OPTIONS"])
def api_regenerate_user_generated_previews():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    return regenerate_previews_for_videos(ensure_user_generated_result_dir(), USER_GENERATED_VIDEO_EXTENSIONS)


@app.route("/api/user-generated-results", method=["GET", "OPTIONS"])
def api_user_generated_results():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    limit = max(1, min(200, int(request.query.get("limit", "200"))))
    return {"items": _user_generated_result_items(limit=limit)}








@app.route("/api/user-recycle-bin", method=["GET", "OPTIONS"])
def api_user_recycle_bin():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    limit = max(1, min(200, int(request.query.get("limit", "100"))))
    return list_failed_video_tasks(limit=limit)


@app.route("/api/user-recycle-bin/delete", method=["POST", "OPTIONS"])
def api_delete_user_recycle_bin_tasks():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict) or not isinstance(payload.get("folders"), list):
        response.status = 400
        return {"ok": False, "error": "folders must be an array"}
    try:
        return delete_failed_video_tasks(payload["folders"])
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-recycle-bin/restore", method=["POST", "OPTIONS"])
def api_restore_user_recycle_bin_task():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict) or not isinstance(payload.get("folder"), str):
        response.status = 400
        return {"ok": False, "error": "folder must be a string"}
    try:
        return restore_failed_video_task(payload["folder"])
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/delete", method=["POST", "OPTIONS"])
def api_delete_user_generated_result():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return _delete_user_generated_video(payload.get("userGeneratedKey"))
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/merge", method=["POST", "OPTIONS"])
def api_merge_user_generated_results():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        left_path, left_key = _resolve_user_generated_video_key(payload.get("leftKey"))
        right_path, _ = _resolve_user_generated_video_key(payload.get("rightKey"))
        left_narration, _ = _tts_narration_text_for_user_generated_video(left_key, left_path)
        root = ensure_user_generated_result_dir().resolve()
        raw_name = str(payload.get("outputName") or "延长合并视频").strip()
        safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", raw_name).strip(".-_") or "延长合并视频"
        merge_mode = str(payload.get("mergeMode") or "direct").strip()
        if merge_mode not in {"direct", "continuation"}:
            raise ValueError("合并模式无效")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = (root / "video" / f"{safe_name[:60]}-{timestamp}.mp4").resolve()
        if not _is_within(root, target):
            raise ValueError("合并输出路径无效")
        trim_result = None
        merge_inputs = [left_path, right_path]
        with tempfile.TemporaryDirectory(prefix="ai8video-merge-") as tempdir:
            if merge_mode == "continuation":
                trimmed_left = Path(tempdir) / "left-until-frame.mp4"
                trim_result = trim_video_end(
                    left_path,
                    trimmed_left,
                    end_seconds=float(payload.get("splitTime") or 0),
                )
                merge_inputs = [trimmed_left, right_path]
            merge_result = concat_videos(merge_inputs, target)
        relative_key = target.relative_to(root).as_posix()
        _save_merged_narration_metadata(relative_key, left_key, left_narration)
        return {
            "ok": True,
            "userGeneratedKey": relative_key,
            "videoUrl": f"/user-generated-results/{relative_key}",
            "mergeMode": merge_mode,
            "trim": trim_result,
            "merge": merge_result,
        }
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/extension-frame", method=["POST", "OPTIONS"])
def api_save_user_generated_extension_frame():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        source, relative_video_key = _resolve_user_generated_video_key(payload.get("userGeneratedKey"))
        root = ensure_user_generated_result_dir().resolve()
        frame_name = hashlib.sha256(relative_video_key.encode("utf-8")).hexdigest()[:24]
        target = (root / "extension-frame" / f"{frame_name}.png").resolve()
        if not _is_within(root, target):
            raise ValueError("延长截帧输出路径无效")
        frame_time = float(payload.get("frameTime") or 0)
        extract_frame_at_time(source, target, time_seconds=frame_time)
        frame_key = target.relative_to(root).as_posix()
        archive = _register_extension_frame_archive(
            source,
            relative_video_key,
            target,
            frame_key,
            frame_time,
        )
        return {
            "ok": True,
            "frameKey": frame_key,
            "frameUrl": f"/user-generated-results/{frame_key}",
            "archive": archive,
        }
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/extension-frame/repair", method=["POST", "OPTIONS"])
def api_repair_user_generated_extension_frame():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        source_frame_path = _resolve_extension_frame_path(payload.get("frameKey"))
        frame_path = (
            _copy_extension_frame_variant(source_frame_path, payload.get("variantIndex"))
            if payload.get("variantIndex") is not None
            else source_frame_path
        )
        if frame_path != source_frame_path:
            _write_extension_frame_variant_status(frame_path, "repairing")
        references = _resolve_frame_repair_references(payload.get("referencePaths"))
        if not references:
            raise ValueError("请至少选择一张参考图后再修图")
        output_path = Path(
            ReferenceImagePreprocessor(AI8VideoConfig.from_env()).repair_frame_with_references(
                str(frame_path),
                references,
                max_concurrency=4 if payload.get("batch") else None,
                custom_prompt=str(payload.get("customPrompt") or ""),
            )
        )
        if not output_path.is_file():
            raise RuntimeError("图片模型没有返回可用的修图结果")
        shutil.copy2(output_path, frame_path)
        if frame_path != source_frame_path:
            _write_extension_frame_variant_status(frame_path, "completed")
            _register_extension_frame_variant_archive(frame_path)
        return {
            "ok": True,
            "frameKey": frame_path.relative_to(ensure_user_generated_result_dir()).as_posix(),
            "frameUrl": f"/user-generated-results/{frame_path.relative_to(ensure_user_generated_result_dir()).as_posix()}",
        }
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/extension-frame/batch-status", method=["POST", "OPTIONS"])
def api_user_generated_extension_frame_batch_status():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        source = _resolve_extension_frame_path(payload.get("frameKey"))
        root = ensure_user_generated_result_dir().resolve()
        for variant in _extension_frame_variant_paths(source):
            _register_extension_frame_variant_archive(variant)
        frames = []
        for index in range(1, 5):
            target = source.with_name(f"{source.stem}-batch-{index}{source.suffix}")
            frame = target if target.is_file() else source
            key = frame.relative_to(root).as_posix()
            frames.append({
                "frameKey": key,
                "frameUrl": f"/user-generated-results/{key}",
                "status": _extension_frame_variant_status(target) if target.is_file() else "idle",
            })
        return {"ok": True, "frames": frames}
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/extension-state/delete", method=["POST", "OPTIONS"])
def api_delete_user_generated_extension_state():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return _delete_extension_state_assets(payload.get("leftKey"), payload.get("rightKey"))
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/video-prompt", method=["POST", "OPTIONS"])
def api_user_generated_video_prompt():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        raw_key = payload.get("userGeneratedKey")
        if "text" in payload:
            return _save_extension_video_prompt_for_user_generated_video(raw_key, payload.get("text"))
        prompt, relative_key = _extension_video_prompt_for_user_generated_video(raw_key)
        return {"ok": True, "userGeneratedKey": relative_key, "text": prompt, "textChars": len(prompt), "source": "extensionVideoPrompt"}
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except LookupError as exc:
        response.status = 410
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/extension-video/generate", method=["POST", "OPTIONS"])
def api_generate_user_generated_extension_video():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return _generate_extension_video(
            payload.get("userGeneratedKey"),
            payload.get("sessionId"),
            payload.get("frameKey"),
        )
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except LookupError as exc:
        response.status = 410
        return {"ok": False, "error": str(exc), "code": "VIDEO_PROMPT_DELETED"}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/extension-video/status", method=["POST", "OPTIONS"])
def api_user_generated_extension_video_status():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return _completed_extension_video(payload.get("userGeneratedKey"))
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/extension-video/batch-status", method=["POST", "OPTIONS"])
def api_user_generated_extension_video_batch_status():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    return _completed_extension_videos_for_frame_keys(payload.get("frames"))


@app.route("/api/user-generated-results/video-prompt/continue", method=["POST", "OPTIONS"])
def api_continue_user_generated_video_prompt():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return _continue_extension_video_prompt(payload.get("userGeneratedKey"))
    except FileNotFoundError as exc:
        response.status = 404
        return {"ok": False, "error": str(exc)}
    except LookupError as exc:
        response.status = 410
        return {"ok": False, "error": str(exc)}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/video-prompt/<mode>", method=["POST", "OPTIONS"])
def api_transform_user_generated_video_prompt(mode: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if mode not in {"polish", "expand"}:
        response.status = 404
        return {"ok": False, "error": "unsupported video prompt operation"}
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return _transform_extension_video_prompt(payload.get("text"), mode)
    except LookupError as exc:
        response.status = 410
        return {"ok": False, "error": str(exc)}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/tts-narration", method=["GET", "POST", "OPTIONS"])
def api_user_generated_tts_narration():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    try:
        if request.method == "GET":
            return _tts_narration_text_payload_for_user_generated_video(request.query.get("userGeneratedKey"))
        payload = request.json or {}
        if not isinstance(payload, dict):
            response.status = 400
            return {"ok": False, "error": "payload must be an object"}
        if "text" not in payload:
            return _tts_narration_text_payload_for_user_generated_video(payload.get("userGeneratedKey"))
        return _save_tts_narration_text_for_user_generated_video(
            payload.get("userGeneratedKey"),
            payload.get("text"),
        )
    except FileNotFoundError:
        response.status = 404
        return {"ok": False, "error": "视频已删除"}
    except LookupError as exc:
        response.status = 410
        return {"ok": False, "error": str(exc) or "台词已删除", "code": "TTS_TEXT_DELETED"}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/tts-narration/polish", method=["POST", "OPTIONS"])
def api_polish_user_generated_tts_narration():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return _polish_tts_narration_text(payload.get("text"), payload.get("durationSeconds"))
    except LookupError as exc:
        response.status = 410
        return {"ok": False, "error": str(exc) or "台词已删除", "code": "TTS_TEXT_DELETED"}
    except RuntimeError as exc:
        response.status = 500
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/tts-narration/expand", method=["POST", "OPTIONS"])
def api_expand_user_generated_tts_narration():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return _expand_tts_narration_text(payload.get("text"), payload.get("durationSeconds"))
    except LookupError as exc:
        response.status = 410
        return {"ok": False, "error": str(exc) or "台词已删除", "code": "TTS_TEXT_DELETED"}
    except RuntimeError as exc:
        response.status = 500
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/regenerate-tts", method=["POST", "OPTIONS"])
def api_regenerate_user_generated_tts():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return _regenerate_user_generated_tts(payload.get("userGeneratedKey"))
    except FileNotFoundError:
        response.status = 404
        return {"ok": False, "error": "视频已删除"}
    except LookupError as exc:
        response.status = 410
        return {"ok": False, "error": str(exc) or "台词已删除", "code": "TTS_TEXT_DELETED"}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    except RuntimeError as exc:
        response.status = 500
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/regenerate-html-motion", method=["POST", "OPTIONS"])
def api_regenerate_user_generated_html_motion():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        raw_key = payload.get("userGeneratedKey")
        video_path, relative_key = _resolve_user_generated_video_key(raw_key)
        _html_motion_source_for_user_generated_video(relative_key, video_path)
        task = html_motion_task_service.submit(
            relative_key,
            lambda **kwargs: _regenerate_user_generated_html_motion(raw_key, **kwargs),
        )
        response.status = 202
        task_id = str(task.get("taskId") or "")
        return {
            **task,
            "pollUrl": f"/api/user-generated-results/html-motion-tasks/{task_id}",
        }
    except FileNotFoundError:
        response.status = 404
        return {"ok": False, "error": "视频已删除"}
    except LookupError as exc:
        response.status = 410
        return {
            "ok": False,
            "error": str(exc) or "视频提示词已删除",
            "code": "VIDEO_PROMPT_DELETED",
        }
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    except RuntimeError as exc:
        response.status = 500
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/html-motion-tasks/<task_id>", method=["GET", "OPTIONS"])
def api_html_motion_task_status(task_id: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    task = html_motion_task_service.get(task_id)
    if task is None:
        response.status = 404
        return {"ok": False, "error": "HTML 动效任务不存在"}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    body = {**task}
    timing_fields = {
        "elapsedSeconds": task.get("elapsedSeconds"),
        "phaseElapsedSeconds": task.get("phaseElapsedSeconds"),
        "phaseTimings": task.get("phaseTimings"),
        "createdAt": task.get("createdAt"),
        "updatedAt": task.get("updatedAt"),
    }
    if result:
        body.update(result)
        body["taskId"] = task["taskId"]
        body["taskStatus"] = task["status"]
        body["taskPhase"] = task["phase"]
    for key, value in timing_fields.items():
        if value is not None:
            body[key] = value
    return body


@app.route("/api/user-generated-results/html-motion-active", method=["POST", "OPTIONS"])
def api_html_motion_active_task():
    """Lookup an in-flight HTML motion task by video key (survives preview modal close)."""
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        _, relative_key = _resolve_user_generated_video_key(payload.get("userGeneratedKey"))
    except FileNotFoundError:
        response.status = 404
        return {"ok": False, "error": "视频已删除"}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    task = html_motion_task_service.get_active(relative_key)
    if task is None:
        return {
            "ok": True,
            "active": False,
            "userGeneratedKey": relative_key,
            "taskId": "",
            "pollUrl": "",
        }
    task_id = str(task.get("taskId") or "")
    return {
        **task,
        "ok": True,
        "active": True,
        "userGeneratedKey": relative_key,
        "pollUrl": f"/api/user-generated-results/html-motion-tasks/{task_id}",
    }


@app.route("/api/user-generated-results/html-motion-tasks/<task_id>/cancel", method=["POST", "OPTIONS"])
def api_cancel_html_motion_task(task_id: str):
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    task = html_motion_task_service.cancel(task_id)
    if task is None:
        response.status = 404
        return {"ok": False, "error": "HTML 动效任务不存在"}
    return task


@app.route("/api/user-generated-results/html-motion-review", method=["POST", "OPTIONS"])
def api_user_generated_html_motion_review():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        _, relative_key = _resolve_user_generated_video_key(payload.get("userGeneratedKey"))
        return html_motion_review_status(relative_key)
    except FileNotFoundError:
        response.status = 404
        return {"ok": False, "error": "视频已删除"}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


@app.route("/api/user-generated-results/confirm-html-motion", method=["POST", "OPTIONS"])
def api_confirm_user_generated_html_motion():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return _confirm_user_generated_html_motion(payload.get("userGeneratedKey"))
    except FileNotFoundError:
        response.status = 404
        return {"ok": False, "error": "视频已删除"}
    except LookupError as exc:
        response.status = 409
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}
    except RuntimeError as exc:
        response.status = 500
        return {"ok": False, "error": str(exc)}


@app.route("/api/assets", method=["GET", "OPTIONS"])
def api_assets():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    limit = max(1, min(50, int(request.query.get("limit", "12"))))
    return get_assets_payload(limit=limit)


@app.route("/api/batch-reports", method=["GET", "OPTIONS"])
def api_batch_reports():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    limit = max(1, min(50, int(request.query.get("limit", "10"))))
    return get_batch_reports_payload(limit=limit)


@app.route("/api/batch-alerts", method=["GET", "OPTIONS"])
def api_batch_alerts():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    limit = max(1, min(50, int(request.query.get("limit", "10"))))
    return get_batch_alerts_payload(limit=limit)


@app.route("/api/open-batch-report", method=["POST", "OPTIONS"])
def api_open_batch_report():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    try:
        target = _resolve_batch_report_path(str(payload.get("reportPath") or ""))
    except ValueError as exc:
        response.status = 400
        return {"error": str(exc)}
    if not target.exists() or not target.is_file():
        response.status = 404
        return {"error": "batch report not found"}
    _open_path(target)
    return {"ok": True, "path": str(target)}


@app.route("/api/open-batch-alert", method=["POST", "OPTIONS"])
def api_open_batch_alert():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    try:
        target = _resolve_batch_alert_path(str(payload.get("alertPath") or ""))
    except ValueError as exc:
        response.status = 400
        return {"error": str(exc)}
    if not target.exists() or not target.is_file():
        response.status = 404
        return {"error": "batch alert not found"}
    _open_path(target)
    return {"ok": True, "path": str(target)}


@app.route("/api/open-batch-supervisor-state", method=["POST", "OPTIONS"])
def api_open_batch_supervisor_state():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = _batch_supervisor_state_path()
    if target.exists() and target.is_file():
        _open_path(target)
        return {"ok": True, "path": str(target), "kind": "file"}
    parent = target.parent.resolve()
    if not parent.exists():
        response.status = 404
        return {"error": "batch supervisor state path not found"}
    _open_in_file_manager(parent)
    return {"ok": True, "path": str(parent), "kind": "directory"}


@app.route("/api/open-batch-supervisor-admin-state", method=["POST", "OPTIONS"])
def api_open_batch_supervisor_admin_state():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = _batch_supervisor_admin_state_path()
    if target.exists() and target.is_file():
        _open_path(target)
        return {"ok": True, "path": str(target), "kind": "file"}
    parent = target.parent.resolve()
    if not parent.exists():
        response.status = 404
        return {"error": "batch supervisor admin state path not found"}
    _open_in_file_manager(parent)
    return {"ok": True, "path": str(parent), "kind": "directory"}


@app.route("/api/open-batch-supervisor-lock", method=["POST", "OPTIONS"])
def api_open_batch_supervisor_lock():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = _batch_supervisor_lock_path()
    if target.exists() and target.is_file():
        _open_path(target)
        return {"ok": True, "path": str(target), "kind": "file"}
    parent = target.parent.resolve()
    if not parent.exists():
        response.status = 404
        return {"error": "batch supervisor lock path not found"}
    _open_in_file_manager(parent)
    return {"ok": True, "path": str(parent), "kind": "directory"}


@app.route("/api/open-batch-supervisor-deployment", method=["POST", "OPTIONS"])
def api_open_batch_supervisor_deployment():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = _batch_supervisor_deployment_path()
    if target.exists() and target.is_file():
        _open_path(target)
        return {"ok": True, "path": str(target), "kind": "file"}
    parent = target.parent.resolve()
    if not parent.exists():
        response.status = 404
        return {"error": "batch supervisor deployment path not found"}
    _open_in_file_manager(parent)
    return {"ok": True, "path": str(parent), "kind": "directory"}


@app.route("/api/open-batch-seed-file", method=["POST", "OPTIONS"])
def api_open_batch_seed_file():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    target = _batch_seed_file_path()
    if target.exists() and target.is_file():
        _open_path(target)
        return {"ok": True, "path": str(target), "kind": "file"}
    parent = target.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    _open_in_file_manager(parent)
    return {"ok": True, "path": str(parent), "kind": "directory"}


@app.route("/api/build-batch-seed-file", method=["POST", "OPTIONS"])
def api_build_batch_seed_file():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    report_limit = max(1, min(30, int(payload.get("reportLimit") or 8)))
    max_messages = max(1, min(120, int(payload.get("maxMessages") or 40)))
    try:
        return build_batch_seed_file_payload(
            report_limit=report_limit,
            max_messages=max_messages,
            refresh=True,
        )
    except ValueError as exc:
        response.status = 400
        return {"error": str(exc)}


@app.route("/api/write-batch-supervisor-deployment", method=["POST", "OPTIONS"])
def api_write_batch_supervisor_deployment():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    try:
        options = _batch_supervisor_request_options(payload)
        plist_payload = build_launchd_plist(
            config=options["config"],
            plist_path=options["plist_path"],
            seed_file=options["seed_file"],
            schedule_times=options["schedule_times"],
            target_pass_count=options["target_pass_count"],
            style_hint=options["style_hint"],
            poll_seconds=options["poll_seconds"],
            min_pass_rate=options["min_pass_rate"],
            consecutive_low_pass_runs=options["consecutive_low_pass_runs"],
        )
        target = write_launchd_plist(options["plist_path"], plist_payload)
        deployment = inspect_launchd_deployment(plist_path=target)
        admin_result = write_supervisor_admin_result_payload(
            action="write",
            path=str(target),
            seed_file=options["seed_file"],
            deployment=deployment,
            refresh=True,
        )
        return {
            "ok": True,
            "action": "write",
            "path": str(target),
            "seedFile": options["seed_file"],
            "deployment": deployment,
            "adminResult": admin_result,
        }
    except ValueError as exc:
        response.status = 400
        return {"error": str(exc)}
    except Exception as exc:
        response.status = 500
        return {"error": str(exc)}


@app.route("/api/install-batch-supervisor-deployment", method=["POST", "OPTIONS"])
def api_install_batch_supervisor_deployment():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    try:
        options = _batch_supervisor_request_options(payload)
        plist_payload = build_launchd_plist(
            config=options["config"],
            plist_path=options["plist_path"],
            seed_file=options["seed_file"],
            schedule_times=options["schedule_times"],
            target_pass_count=options["target_pass_count"],
            style_hint=options["style_hint"],
            poll_seconds=options["poll_seconds"],
            min_pass_rate=options["min_pass_rate"],
            consecutive_low_pass_runs=options["consecutive_low_pass_runs"],
        )
        target = write_launchd_plist(options["plist_path"], plist_payload)
        deployment = install_launchd_service(target)
        admin_result = write_supervisor_admin_result_payload(
            action="install",
            path=str(target),
            seed_file=options["seed_file"],
            deployment=deployment,
            refresh=True,
        )
        return {
            "ok": True,
            "action": "install",
            "path": str(target),
            "seedFile": options["seed_file"],
            "deployment": deployment,
            "adminResult": admin_result,
        }
    except ValueError as exc:
        response.status = 400
        return {"error": str(exc)}
    except Exception as exc:
        response.status = 500
        return {"error": str(exc)}


@app.route("/api/uninstall-batch-supervisor-deployment", method=["POST", "OPTIONS"])
def api_uninstall_batch_supervisor_deployment():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    keep_plist = _parse_bool_payload(payload.get("keepPlist"), default=False)
    try:
        target = _batch_supervisor_deployment_path()
        deployment = uninstall_launchd_service(
            target,
            delete_plist=not keep_plist,
        )
        admin_result = write_supervisor_admin_result_payload(
            action="uninstall",
            path=str(target),
            deployment=deployment,
            keep_plist=keep_plist,
            refresh=True,
        )
        return {
            "ok": True,
            "action": "uninstall",
            "keepPlist": keep_plist,
            "deployment": deployment,
            "adminResult": admin_result,
        }
    except Exception as exc:
        response.status = 500
        return {"error": str(exc)}


@app.route("/api/batch-run", method=["POST", "OPTIONS"])
def api_batch_run():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    seed_messages = payload.get("seedMessages") or []
    if not isinstance(seed_messages, list):
        response.status = 400
        return {"error": "seedMessages must be a list"}
    normalized = [str(item).strip() for item in seed_messages if str(item).strip()]
    if not normalized:
        response.status = 400
        return {"error": "seedMessages is required"}
    target_pass_count = max(1, int(payload.get("targetPassCount") or 30))
    style_hint = str(payload.get("styleHint") or "").strip() or None
    session_id = str(payload.get("sessionId") or "batch-api").strip() or "batch-api"
    source = str(payload.get("source") or "web_api").strip() or "web_api"
    trigger = str(payload.get("trigger") or "api_batch_run").strip() or "api_batch_run"
    return run_batch_payload(
        normalized,
        target_pass_count=target_pass_count,
        style_hint=style_hint,
        trigger=trigger,
        source=source,
        session_id=session_id,
    )


@app.route("/api/chat", method=["POST", "OPTIONS"])
def api_chat():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    message = (payload.get("message") or "").strip()
    session_id = (payload.get("sessionId") or "default").strip() or "default"
    refresh = bool(payload.get("refresh", False))
    if not message:
        response.status = 400
        return {"error": "message is required"}
    config = AI8VideoConfig.from_env()
    if not config.has_llm():
        response.status = 503
        return {
            "error": (
                "未配置可用的 AI8video 核心模型，无法开始任务。"
                "请在左侧设置里补充 AI8VIDEO_LLM_BASE_URL、AI8VIDEO_LLM_API_KEY、AI8VIDEO_LLM_MODEL，"
                "或确保 mykey.py 已经提供同等配置。"
            ),
            "code": "MISSING_CORE_LLM",
            "requiredEnv": [
                "AI8VIDEO_LLM_BASE_URL",
                "AI8VIDEO_LLM_API_KEY",
                "AI8VIDEO_LLM_MODEL",
            ],
        }
    video_settings = load_video_model_settings(
        llm_base_url=getattr(config, "llm_base_url", None),
        llm_api_key=getattr(config, "llm_api_key", None),
    )
    if not config.dry_run and not video_settings.configured():
        response.status = 400
        return {
            "error": "未配置视频鉴权，无法开始任务。请在设置里补齐视频模型地址、密钥、模型名和模板。",
            "code": "MISSING_VIDEO_MODEL",
            "requiredEnv": [
                "AI8VIDEO_VIDEO_BASE_URL",
                "AI8VIDEO_VIDEO_API_KEY",
                "AI8VIDEO_VIDEO_MODEL",
                "AI8VIDEO_VIDEO_TEMPLATE",
            ],
        }
    clear_generation_progress(session_id)
    try:
        body = handle_chat_via_ai8video(
            session_id=session_id,
            message=message,
            refresh=refresh,
            timeout_seconds=_web_chat_timeout_seconds(),
        )
        body.setdefault("chatBackend", CHAT_BACKEND)
        _apply_deleted_asset_progress_state(body)
        return body
    except TimeoutError as exc:
        logger.exception("AI8video chat timed out before payload returned")
        pending_status = get_chat_status_via_ai8video(session_id=session_id)
        generation_progress = pending_status.get("generationProgress")
        if not isinstance(generation_progress, dict):
            if pending_status.get("status") == "pending":
                pending_since = _parse_iso_datetime(pending_status.get("pendingSince") or "")
                trace_progress = _query_prompt_trace_planning_progress(
                    session_id,
                    video_count=None,
                    pending_since=pending_since,
                ) or {}
                generation_progress = trace_progress.get("generationProgress")
                timeout_body = {
                    **pending_status,
                    **trace_progress,
                    **({"generationProgress": generation_progress} if isinstance(generation_progress, dict) else {}),
                }
                stale_planning = _settle_stale_planning_progress(timeout_body, pending_since=pending_since)
                if stale_planning:
                    return _timeout_unsubmitted_chat_payload(
                        session_id=session_id,
                        pending_status=stale_planning,
                        error=exc,
                    )
                return {
                    "reply": {
                        "text": "AI8video 还在分析和规划，本轮尚未提交视频任务。结果会自动回填到当前对话。",
                        "stage": "pending",
                        "awaiting": None,
                        "draft": None,
                        "meta": {
                            "operation": "planning",
                            "errorType": exc.__class__.__name__,
                        },
                        "result": None,
                    },
                    "status": "pending",
                    "sessionId": pending_status.get("sessionId", session_id),
                    "generationBatchId": pending_status.get("generationBatchId"),
                    "pendingSince": pending_status.get("pendingSince"),
                    "elapsedSeconds": pending_status.get("elapsedSeconds", 0),
                    "phase": pending_status.get("phase", "planning"),
                    "statusLabel": trace_progress.get("statusLabel") or pending_status.get("statusLabel", "正在分析文档并规划剧本"),
                    **({"generationProgress": generation_progress} if isinstance(generation_progress, dict) else {}),
                    "chatBackend": "ai8video-timeout",
                    "chatBackendError": str(exc),
                }
            response.status = 504
            return {
                "error": (
                    "AI8video 聊天层等待超时，但尚未检测到本轮真实视频生成任务。"
                    "本次不会伪装成后台生成中，也不会展示假进度。请重新发送或缩短输入后再试。"
                ),
                "code": "AI8VIDEO_CHAT_TIMEOUT_NO_GENERATION",
                "status": "idle",
                "sessionId": session_id,
                "generationBatchId": pending_status.get("generationBatchId"),
                "chatBackend": "ai8video-timeout",
                "chatBackendError": str(exc),
            }
        stale_planning = _settle_stale_planning_progress(pending_status)
        if stale_planning:
            return _timeout_unsubmitted_chat_payload(
                session_id=session_id,
                pending_status=stale_planning,
                error=exc,
            )
        return {
            "reply": {
                "text": (
                    "视频任务已提交，正在等待生成结果。"
                    "完成后会自动显示在当前对话。"
                ),
                "stage": "pending",
                "awaiting": None,
                "draft": None,
                "meta": {
                    "operation": "pending",
                    "errorType": exc.__class__.__name__,
                },
                "result": None,
            },
            "status": pending_status.get("status", "pending"),
            "sessionId": pending_status.get("sessionId", session_id),
            "generationBatchId": pending_status.get("generationBatchId"),
            "pendingSince": pending_status.get("pendingSince"),
            "elapsedSeconds": pending_status.get("elapsedSeconds", 0),
            "generationProgress": generation_progress,
            "chatBackend": "ai8video-timeout",
            "chatBackendError": str(exc),
        }
    except Exception as exc:
        logger.exception("AI8video chat path failed")
        response.status = 502
        return {
            "error": f"短视频运行时调用失败：{str(exc).strip() or exc.__class__.__name__}",
            "code": "AI8VIDEO_RUNTIME_FAILED",
            "chatBackend": CHAT_BACKEND,
            "chatBackendError": str(exc),
        }


def _timeout_unsubmitted_chat_payload(
    *,
    session_id: str,
    pending_status: dict,
    error: Exception,
) -> dict:
    reason = str(
        ((pending_status.get("generationProgress") or {}).get("error") if isinstance(pending_status.get("generationProgress"), dict) else "")
        or "本地任务超时，视频没有提交给上游生成服务。请重新发送或缩短输入后再试。"
    )
    return {
        "reply": {
            "text": reason,
            "stage": "error",
            "awaiting": None,
            "draft": None,
            "meta": {
                "operation": "pending",
                "phase": "failed",
                "errorType": error.__class__.__name__,
            },
            "result": None,
        },
        **pending_status,
        "sessionId": pending_status.get("sessionId", session_id),
        "chatBackend": "ai8video-timeout",
        "chatBackendError": str(error),
    }


@app.route("/api/chat-status", method=["GET", "OPTIONS"])
def api_chat_status():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    session_id = str(request.query.get("sessionId") or "").strip()
    if not session_id:
        response.status = 400
        return {"error": "sessionId is required"}
    generation_batch_id = str(request.query.get("generationBatchId") or "").strip() or None
    requested_video_count = _parse_chat_status_video_count()
    pending_since = _parse_chat_status_pending_since()
    if generation_batch_id:
        body = get_chat_status_via_ai8video(
            session_id=session_id,
            generation_batch_id=generation_batch_id,
        )
        if body.get("status") == "not_found":
            response.status = 404
            return body
    stale_first_frame = settle_stale_first_frame_progress(session_id)
    if stale_first_frame:
        return {
            "status": "completed_with_error",
            "phase": "completed",
            "statusLabel": "视频生成失败",
            "sessionId": session_id,
            **({"generationBatchId": generation_batch_id} if generation_batch_id else {}),
            "pendingSince": pending_since.isoformat() if pending_since else None,
            "elapsedSeconds": _elapsed_seconds_since(pending_since),
            "generationProgress": stale_first_frame,
        }
    body = get_chat_status_via_ai8video(
        session_id=session_id,
        generation_batch_id=generation_batch_id,
    )
    _apply_deleted_asset_progress_state(body)
    stale_pending = _stale_status_for_pending_query(body, pending_since=pending_since)
    if stale_pending:
        return stale_pending
    if body.get("status") == "pending" and body.get("phase") == "planning" and not isinstance(body.get("generationProgress"), dict):
        trace_planning = _query_prompt_trace_planning_progress(
            session_id,
            video_count=requested_video_count,
            pending_since=pending_since,
        )
        if trace_planning:
            body = {**body, **trace_planning}
    stale_planning = _settle_stale_planning_progress(body, pending_since=pending_since)
    if stale_planning:
        local_terminal = _query_local_terminal_generation_progress(
            session_id,
            video_count=requested_video_count,
            pending_since=pending_since,
        )
        if _should_prefer_local_terminal_progress(local_terminal, stale_planning):
            return _guard_chat_status_pending_freshness(local_terminal, pending_since=pending_since)
        return stale_planning
    body_progress = body.get("generationProgress")
    if isinstance(body_progress, dict):
        progress_total = _coerce_positive_int(body_progress.get("totalRequested"))
        local_terminal = _query_local_terminal_generation_progress(
            session_id,
            video_count=progress_total or requested_video_count,
            pending_since=pending_since,
        )
        if _should_prefer_local_terminal_progress(local_terminal, body):
            return _guard_chat_status_pending_freshness(local_terminal, pending_since=pending_since)
    if body.get("status") == "idle":
        video_count = requested_video_count
        fallback_jobs = _parse_chat_status_jobs()
        if fallback_jobs:
            fallback = _query_video_jobs_progress(
                session_id,
                fallback_jobs,
                video_count=video_count,
                pending_since=pending_since,
            )
            if fallback:
                return _guard_chat_status_pending_freshness(fallback, pending_since=pending_since)
        trace_fallback = _query_prompt_trace_generation_progress(
            session_id,
            video_count=video_count,
            pending_since=pending_since,
        )
        local_terminal = _query_local_terminal_generation_progress(
            session_id,
            video_count=video_count,
            pending_since=pending_since,
        )
        if _should_prefer_local_terminal_progress(local_terminal, trace_fallback):
            return _guard_chat_status_pending_freshness(local_terminal, pending_since=pending_since)
        if trace_fallback:
            return _guard_chat_status_pending_freshness(trace_fallback, pending_since=pending_since)
        if local_terminal:
            return _guard_chat_status_pending_freshness(local_terminal, pending_since=pending_since)
        trace_planning = _query_prompt_trace_planning_progress(
            session_id,
            video_count=video_count,
            pending_since=pending_since,
        )
        if trace_planning:
            trace_body = {**body, **trace_planning, "traceRecovered": True, "statelessProgress": True}
            stale_planning = _settle_stale_planning_progress(trace_body, pending_since=pending_since)
            if stale_planning:
                return stale_planning
            return trace_body
    return body


def _guard_chat_status_pending_freshness(body: dict | None, *, pending_since: datetime | None) -> dict | None:
    if not body:
        return body
    stale_pending = _stale_status_for_pending_query(body, pending_since=pending_since)
    return stale_pending or body


@app.route("/api/generation/retry", method=["POST", "OPTIONS"])
def api_retry_failed_generation():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    session_id = str(payload.get("sessionId") or "").strip()
    video_index = int(payload.get("videoIndex") or 0)
    if not session_id or video_index < 1:
        response.status = 400
        return {"ok": False, "error": "缺少重试会话或视频序号"}
    try:
        config = AI8VideoConfig.from_env()
        record = _find_retryable_asset_record(config, session_id, video_index)
        retry_request, video, first_frame = _build_retry_inputs(record)
        result = AI8VideoPipeline(config=config).retry_video(
            retry_request,
            video,
            first_frame,
            progress_session_id=session_id,
        )
        return {"ok": True, "result": result.to_dict()}
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


def _find_retryable_asset_record(config: AI8VideoConfig, session_id: str, video_index: int) -> dict[str, Any]:
    records = JsonlAssetStore(config.asset_store_path).read_all()
    matches = [
        item for item in records
        if str(item.get("sessionId") or "") == session_id
        and int(item.get("videoIndex") or 0) == video_index
        and str(item.get("generationStatus") or "") == "failed"
    ]
    if not matches:
        raise ValueError("未找到该视频的失败记录，无法重试")
    return matches[-1]


def _build_retry_inputs(record: dict[str, Any]) -> tuple[ParsedRequest, VideoPrompt, FirstFrameAsset]:
    prompt = str(record.get("prompt") or "").strip()
    first_frame_data = record.get("firstFrame") if isinstance(record.get("firstFrame"), dict) else {}
    first_frame = FirstFrameAsset(**{key: first_frame_data.get(key) for key in FirstFrameAsset.__dataclass_fields__})
    source = str(first_frame.source or "").strip()
    reusable = bool(first_frame.first_frame_storage_key or first_frame.first_frame_image_url or first_frame.first_frame_token)
    if source:
        reusable = urlsplit(source).scheme in {"http", "https", "data"} or Path(source).is_file()
    if not prompt or not reusable:
        raise ValueError("重试所需的方案或首帧中间产物已丢失，无法原样重试")
    settings = record.get("request") if isinstance(record.get("request"), dict) else {}
    video_index = int(record.get("videoIndex") or 0)
    retry_request = ParsedRequest(
        raw_text=prompt,
        mode=str(settings.get("mode") or "single_video"),
        video_count=1,
        duration_seconds=int(settings.get("durationSeconds") or 10),
        ratio=str(settings.get("ratio") or "9:16"),
        resolution=str(settings.get("resolution") or "480p"),
        preset=str(settings.get("preset") or "custom"),
        html_motion_overlay_enabled=bool(settings.get("htmlMotionOverlayEnabled")),
    )
    video = VideoPrompt(video_index, str(record.get("videoTitle") or f"视频 {video_index}"), prompt)
    return retry_request, video, first_frame


def _stale_status_for_pending_query(body: dict, *, pending_since: datetime | None) -> dict | None:
    if pending_since is None or not isinstance(body, dict):
        return None
    status = str(body.get("status") or "").strip()
    if status == "pending":
        return None
    body_pending_since = _parse_iso_datetime(body.get("pendingSince") or "")
    completed_at = _parse_iso_datetime(body.get("completedAt") or "")
    progress = body.get("generationProgress")
    progress_completed_at = None
    progress_updated_at = None
    if isinstance(progress, dict):
        progress_completed_at = _parse_iso_datetime(progress.get("completedAt") or "")
        progress_updated_at = _parse_iso_datetime(progress.get("updatedAt") or "")
        item_times = [
            _parse_iso_datetime(item.get("completedAt") or item.get("updatedAt") or "")
            for item in (progress.get("items") or [])
            if isinstance(item, dict)
        ]
    else:
        item_times = []
    latest_terminal_at = max(
        [item for item in (completed_at, progress_completed_at, progress_updated_at, *item_times) if item is not None],
        default=None,
    )
    stale_by_completed_time = latest_terminal_at is not None and latest_terminal_at.timestamp() < pending_since.timestamp() - 1
    stale_by_started_time = body_pending_since is not None and body_pending_since.timestamp() < pending_since.timestamp() - 1
    if not (stale_by_completed_time or stale_by_started_time):
        return None
    return {
        "status": "idle",
        "phase": "stale",
        "sessionId": str(body.get("sessionId") or "").strip(),
        "pendingSince": pending_since.isoformat(),
        "elapsedSeconds": 0,
        "statusLabel": "这条等待状态已失效",
        "stalePending": True,
        "staleReason": "本次查询时间晚于该会话已有终态，已拒绝回填旧结果。",
    }


def _settle_stale_planning_progress(
    body: dict,
    *,
    pending_since: datetime | None = None,
    force: bool = False,
) -> dict | None:
    if not isinstance(body, dict):
        return None
    progress = body.get("generationProgress")
    if not isinstance(progress, dict):
        return None
    if str(body.get("status") or "").strip() != "pending":
        return None
    if str(body.get("phase") or "").strip() != "planning":
        return None
    items = progress.get("items")
    if not isinstance(items, list) or not items:
        return None
    if any(isinstance(item, dict) and str(item.get("jobId") or "").strip() for item in items):
        return None
    submitted_count = int(progress.get("submittedCount") or 0)
    succeeded_count = int(progress.get("succeededCount") or 0)
    failed_count = int(progress.get("failedCount") or 0)
    deleted_count = int(progress.get("deletedCount") or 0)
    if submitted_count or succeeded_count or failed_count or deleted_count:
        return None
    reference = (
        _parse_iso_datetime(progress.get("updatedAt") or "")
        or _parse_iso_datetime(progress.get("startedAt") or "")
        or _parse_iso_datetime(body.get("pendingSince") or "")
        or pending_since
    )
    if reference is None:
        return None
    raw_timeout = str(
        os.getenv("AI8VIDEO_STALE_PLANNING_TIMEOUT_SECONDS")
        or os.getenv("AI8VIDEO_WEB_CHAT_TIMEOUT_SECONDS")
        or DEFAULT_WEB_CHAT_TIMEOUT_SECONDS
    ).strip()
    try:
        base_timeout_seconds = max(90, int(raw_timeout))
    except ValueError:
        base_timeout_seconds = DEFAULT_WEB_CHAT_TIMEOUT_SECONDS
    timeout_seconds = max(
        base_timeout_seconds,
        _planning_timeout_for_progress(progress, base_timeout_seconds=base_timeout_seconds),
    )
    now = datetime.now(timezone.utc)
    if not force:
        elapsed_seconds = _coerce_positive_int(body.get("elapsedSeconds"))
        if elapsed_seconds is not None:
            if elapsed_seconds < timeout_seconds:
                return None
        else:
            has_current_pending_anchor = bool(str(body.get("pendingSince") or "").strip() or pending_since)
            if not has_current_pending_anchor:
                return None
            if now.timestamp() - reference.timestamp() < timeout_seconds:
                return None
    reason = "本地任务超时，视频没有提交给上游生成服务。请重新发送或缩短输入后再试。"
    settled_items = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            item = {}
        settled_items.append({
            **item,
            "videoIndex": _coerce_video_index(item.get("videoIndex")) or index,
            "jobId": None,
            "status": "failed",
            "statusLabel": "生成失败",
            "providerStatus": "local_timeout",
            "providerProgress": 100,
            "error": reason,
        })
    completed_at = now.isoformat()
    settled_progress = {
        **progress,
        "status": "failed",
        "items": settled_items,
        "submittedCount": 0,
        "runningCount": 0,
        "postProcessingCount": 0,
        "waitingCount": 0,
        "succeededCount": 0,
        "failedCount": len(settled_items),
        "deletedCount": 0,
        "skippedCount": 0,
        "error": reason,
        "completedAt": completed_at,
    }
    stop_unsubmitted_generation_progress(
        str(body.get("sessionId") or "").strip(),
        settled_progress,
        reason,
    )
    return {
        **body,
        "status": "failed",
        "phase": "failed",
        "statusLabel": "本地任务超时，视频未提交给生成服务",
        "completedAt": completed_at,
        "generationProgress": settled_progress,
        "stalePlanningRecovered": True,
    }


def _planning_timeout_for_progress(progress: dict, *, base_timeout_seconds: int) -> int:
    items = progress.get("items")
    item_count = len(items) if isinstance(items, list) else 0
    total_requested = _coerce_positive_int(progress.get("totalRequested")) or item_count
    if total_requested <= 1:
        return base_timeout_seconds
    # Long script references are finalized in model batches. A 5-video request
    # can legitimately take longer than 5 minutes before any video task exists.
    return max(base_timeout_seconds, 180 + int(total_requested) * 60)


def _should_prefer_local_terminal_progress(local_terminal: dict | None, trace_fallback: dict | None) -> bool:
    if not local_terminal:
        return False
    if not trace_fallback:
        return True
    local_progress = local_terminal.get("generationProgress") if isinstance(local_terminal, dict) else None
    trace_progress = trace_fallback.get("generationProgress") if isinstance(trace_fallback, dict) else None
    if not isinstance(local_progress, dict) or not isinstance(trace_progress, dict):
        return False
    local_success = int(local_progress.get("succeededCount") or 0) + int(local_progress.get("deletedCount") or 0)
    trace_success = int(trace_progress.get("succeededCount") or 0) + int(trace_progress.get("deletedCount") or 0)
    local_failed = int(local_progress.get("failedCount") or 0) + int(local_progress.get("skippedCount") or 0)
    trace_failed = int(trace_progress.get("failedCount") or 0)
    trace_running = int(trace_progress.get("runningCount") or 0) + int(trace_progress.get("waitingCount") or 0)
    if trace_running > 0:
        local_latest_at = _latest_progress_timestamp(local_progress)
        trace_latest_at = _latest_progress_timestamp(trace_progress)
        if local_latest_at and trace_latest_at and local_latest_at < trace_latest_at:
            return False
        local_running = (
            int(local_progress.get("runningCount") or 0)
            + int(local_progress.get("waitingCount") or 0)
            + int(local_progress.get("postProcessingCount") or 0)
        )
        return local_running == 0 and local_success > trace_success and local_failed <= trace_failed
    return local_success > 0 and trace_success == 0 and trace_failed > 0


def _query_local_terminal_generation_progress(
    session_id: str,
    *,
    video_count: int | None = None,
    pending_since: datetime | None = None,
) -> dict | None:
    local_items = _session_local_terminal_progress_items(session_id, pending_since=pending_since)
    if not local_items:
        return None
    items_by_video = {
        int(item.get("videoIndex") or index + 1): dict(item)
        for index, item in enumerate(local_items)
    }
    inferred_total = _infer_requested_video_count_from_progress_items(items_by_video.values())
    if inferred_total > 0:
        requested_total = max(inferred_total, max(items_by_video))
    else:
        requested_total = max(int(video_count or 0), max(items_by_video))
    trace_jobs_by_video = _trace_video_jobs_by_video(session_id, pending_since=pending_since)
    client = AI8VideoModelClient() if trace_jobs_by_video else None
    for video_index in range(1, requested_total + 1):
        if video_index in items_by_video:
            continue
        trace_job = trace_jobs_by_video.get(video_index)
        if trace_job and client is not None:
            job_id = str(trace_job.get("jobId") or "").strip()
            base = {
                "videoIndex": video_index,
                "title": trace_job.get("title") or f"视频 {video_index}",
                "jobId": job_id,
            }
            try:
                latest = client.get_job(job_id, video_index=video_index)
            except Exception as exc:
                items_by_video[video_index] = {
                    **base,
                    "status": "polling",
                    "statusLabel": "等待生成结果",
                    "providerStatus": "query_failed",
                    "error": str(exc),
                }
                continue
            if latest.status == "failed":
                items_by_video[video_index] = {
                    **base,
                    "status": "failed",
                    "statusLabel": "生成失败",
                    "providerStatus": latest.provider_status or "failed",
                    "providerProgress": 100,
                    "error": humanize_failed_video_reason(latest.error or "生成服务没有成功"),
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }
                continue
            if latest.status == "succeeded":
                items_by_video[video_index] = {
                    **base,
                    "status": "archiving",
                    "statusLabel": "后台处理中",
                    "providerStatus": latest.provider_status or "completed",
                    "providerProgress": latest.provider_progress,
                    "videoUrl": latest.video_url,
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }
                continue
            items_by_video[video_index] = {
                **base,
                "status": "polling",
                "statusLabel": "等待生成结果",
                "providerStatus": latest.provider_status or "pending",
                "providerProgress": latest.provider_progress,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }
            continue
        items_by_video[video_index] = {
            "videoIndex": video_index,
            "title": f"视频 {video_index}",
            "jobId": None,
            "status": "skipped",
            "statusLabel": "未提交",
            "error": "本地没有找到这一条的真实生成结果。",
        }
    items = [items_by_video[key] for key in sorted(items_by_video)]
    latest_updated_at = max(
        [
            parsed
            for parsed in (_parse_iso_datetime(item.get("updatedAt") or "") for item in items)
            if parsed is not None
        ],
        default=None,
    )
    succeeded_count = sum(1 for item in items if item.get("status") == "succeeded")
    failed_count = sum(1 for item in items if item.get("status") == "failed")
    deleted_count = sum(1 for item in items if item.get("status") == "deleted")
    skipped_count = sum(1 for item in items if item.get("status") == "skipped")
    running_count = sum(1 for item in items if item.get("status") in {"polling", "archiving"})
    post_processing_count = sum(1 for item in items if item.get("status") == "archiving")
    if running_count:
        status = "pending"
        progress_status = "active"
        status_label = "后台处理中" if post_processing_count == running_count else "真实视频生成中"
    elif succeeded_count and not failed_count and not skipped_count:
        status = "completed"
        progress_status = "completed"
        status_label = "视频已生成"
    elif succeeded_count:
        status = "completed_with_error"
        progress_status = "completed_with_error"
        status_label = "部分视频已生成"
    elif deleted_count and not failed_count:
        status = "completed"
        progress_status = "completed"
        status_label = "文件已删除"
    else:
        status = "failed"
        progress_status = "failed"
        status_label = "视频生成失败"
    progress = {
        "sessionId": session_id,
        "status": progress_status,
        "updatedAt": latest_updated_at.isoformat() if latest_updated_at else "",
        "completedAt": latest_updated_at.isoformat() if latest_updated_at else "",
        "totalRequested": len(items),
        "items": items,
        "submittedCount": sum(1 for item in items if _has_video_submission(item)),
        "runningCount": running_count,
        "postProcessingCount": post_processing_count,
        "waitingCount": 0,
        "succeededCount": succeeded_count,
        "failedCount": failed_count,
        "deletedCount": deleted_count,
        "skippedCount": skipped_count,
    }
    return {
        "status": status,
        "phase": "completed",
        "statusLabel": status_label,
        "sessionId": session_id,
        "elapsedSeconds": 0,
        "completedAt": latest_updated_at.isoformat() if latest_updated_at else "",
        "generationProgress": progress,
        "statelessProgress": True,
        "localTerminalRecovered": True,
    }


@app.route("/api/chat-cancel", method=["POST", "OPTIONS"])
def api_chat_cancel():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    session_id = str(payload.get("sessionId") or "").strip()
    if not session_id:
        response.status = 400
        return {"error": "sessionId is required"}
    reason = str(payload.get("reason") or "").strip() or None
    return cancel_chat_via_ai8video(session_id=session_id, reason=reason)


def _parse_chat_status_jobs() -> list[dict]:
    raw = str(request.query.get("jobs") or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(payload, list):
        return []
    jobs: list[dict] = []
    for item in payload[:12]:
        if not isinstance(item, dict):
            continue
        job_id = str(item.get("jobId") or "").strip()
        if not job_id:
            continue
        try:
            video_index = int(item.get("videoIndex") or len(jobs) + 1)
        except (TypeError, ValueError):
            video_index = len(jobs) + 1
        jobs.append({
            "jobId": job_id,
            "videoIndex": video_index,
            "title": f"视频 {video_index}",
        })
    return jobs


def _parse_chat_status_video_count() -> int | None:
    try:
        value = int(request.query.get("videoCount") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _coerce_positive_int(value: Any) -> int | None:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _parse_chat_status_pending_since() -> datetime | None:
    return _parse_iso_datetime(request.query.get("pendingSince") or "")


def _asset_record_local_video_exists(record: dict) -> bool | None:
    raw_path = str(
        record.get("archiveLocalPath")
        or record.get("userGeneratedLocalPath")
        or record.get("localVideoPath")
        or ""
    ).strip()
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()
    return candidate.is_file()


def _asset_record_job_ids(record: dict) -> set[str]:
    job_ids = {str(record.get("jobId") or "").strip()}
    containers = [
        record.get("generationMeta"),
        record.get("archiveMeta"),
        record.get("usage"),
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("segmentRecords", "segments"):
            for segment in container.get(key) or []:
                if not isinstance(segment, dict):
                    continue
                job_ids.add(str(segment.get("jobId") or "").strip())
    return {job_id for job_id in job_ids if job_id}


def _asset_records_by_job_id() -> dict[str, dict]:
    config = AI8VideoConfig.from_env()
    records_by_job_id: dict[str, dict] = {}
    for record in JsonlAssetStore(config.asset_store_path).read_all():
        for job_id in _asset_record_job_ids(record):
            records_by_job_id[job_id] = record
    return records_by_job_id


def _asset_record_segment_statuses(record: dict | None) -> list[dict]:
    if not isinstance(record, dict) or not record:
        return []
    containers = [
        record.get("generationMeta"),
        record.get("archiveMeta"),
        record.get("usage"),
    ]
    record_status = str(record.get("status") or "").strip().lower()
    archive_status = str(record.get("archiveStatus") or "").strip().lower()
    final_succeeded = record_status == "succeeded" or archive_status == "archived"
    by_index: dict[int, dict] = {}
    order = 0
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("segmentRecords", "segments"):
            for segment in container.get(key) or []:
                if not isinstance(segment, dict):
                    continue
                order += 1
                segment_index = (
                    _coerce_segment_index(segment.get("segmentIndex"))
                    or _coerce_segment_index(segment.get("segmentLabel"))
                    or _coerce_segment_index(segment.get("role"))
                    or order
                )
                if segment_index <= 0 or segment_index in by_index:
                    continue
                segment_label = str(segment.get("segmentLabel") or f"片段 {segment_index}").strip()
                job_id = str(segment.get("jobId") or "").strip()
                provider_status = str(segment.get("providerStatus") or segment.get("status") or "").strip()
                provider_progress = segment.get("providerProgress")
                try:
                    provider_progress = int(provider_progress) if provider_progress is not None else None
                except (TypeError, ValueError):
                    provider_progress = None
                normalized_status = provider_status.lower()
                video_url = str(segment.get("videoUrl") or "").strip()
                storage_key = str(segment.get("storageKey") or segment.get("localVideoPath") or "").strip()
                if normalized_status in {"succeeded", "success", "completed", "complete", "done"}:
                    status = "succeeded"
                    label = f"{segment_label}：已生成"
                    provider_status = "completed"
                    provider_progress = 100
                elif normalized_status in {"failed", "failure", "error", "cancelled", "canceled"}:
                    status = "failed"
                    label = f"{segment_label}：生成失败"
                    provider_status = provider_status or "failed"
                    provider_progress = 100
                elif final_succeeded and (job_id or video_url or storage_key):
                    status = "succeeded"
                    label = f"{segment_label}：已生成"
                    provider_status = "completed"
                    provider_progress = 100
                else:
                    status = "polling"
                    provider_status = provider_status or "pending"
                    label = f"{segment_label}：上游状态：{provider_status}"
                item = {
                    "segmentIndex": segment_index,
                    "segmentLabel": segment_label,
                    "jobId": job_id,
                    "status": status,
                    "statusLabel": label,
                    "providerStatus": provider_status,
                    "providerProgress": provider_progress,
                    "videoUrl": video_url,
                }
                raw_error = str(segment.get("error") or segment.get("archiveError") or "").strip()
                if raw_error:
                    item["error"] = humanize_failed_video_reason(raw_error)
                by_index[segment_index] = item
    return [by_index[index] for index in sorted(by_index)]


def _session_local_terminal_progress_items(
    session_id: str,
    *,
    pending_since: datetime | None = None,
) -> list[dict]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return []
    config = AI8VideoConfig.from_env()
    asset_records = [
        record for record in JsonlAssetStore(config.asset_store_path).read_all()
        if _record_mentions_progress_session(record, normalized_session_id)
    ]
    recycle_items = [
        item for item in (list_failed_video_tasks(limit=200).get("items") or [])
        if isinstance(item, dict) and _record_mentions_progress_session(item, normalized_session_id)
    ]
    reference_time = _latest_local_terminal_time(asset_records, recycle_items)
    items_by_video: dict[int, dict] = {}
    for record in asset_records:
        created_at = _parse_iso_datetime(record.get("createdAt") or record.get("updatedAt") or "")
        if not _local_terminal_time_in_scope(created_at, pending_since, reference_time):
            continue
        video_index = _coerce_video_index(record.get("videoIndex"))
        if video_index <= 0:
            continue
        local_exists = _asset_record_local_video_exists(record)
        archive_status = str(record.get("archiveStatus") or "").strip().lower()
        if archive_status == "archived" and local_exists is False:
            status = "deleted"
            status_label = "已生成，文件已删除"
            provider_status = "deleted"
            has_local_asset = False
        elif str(record.get("status") or "").strip().lower() == "succeeded" or archive_status == "archived":
            status = "succeeded" if local_exists is not False else "deleted"
            status_label = "已生成" if status == "succeeded" else "已生成，文件已删除"
            provider_status = "completed" if status == "succeeded" else "deleted"
            has_local_asset = local_exists is not False
        else:
            continue
        item = {
            "videoIndex": video_index,
            "title": record.get("videoTitle") or f"视频 {video_index}",
            "jobId": str(record.get("jobId") or "").strip(),
            "status": status,
            "statusLabel": status_label,
            "providerStatus": provider_status,
            "providerProgress": 100,
            "videoUrl": record.get("archiveUrl") or record.get("videoUrl") or "",
            "archiveLocalPath": record.get("archiveLocalPath") or "",
            "assetRecord": record,
            "hasLocalAsset": has_local_asset,
            "updatedAt": created_at.isoformat() if created_at else "",
            "_localTerminalAt": created_at,
        }
        segment_status = _asset_record_segment_statuses(record)
        if segment_status:
            item["segmentStatus"] = segment_status
        _put_latest_video_item(items_by_video, video_index, item)
    for failed in recycle_items:
        created_at = _parse_iso_datetime(failed.get("createdAt") or "")
        if not _local_terminal_time_in_scope(created_at, pending_since, reference_time):
            continue
        video_index = _coerce_video_index(failed.get("videoIndex"))
        if video_index <= 0:
            continue
        reason = str(failed.get("displayReason") or "").strip()
        if not reason:
            reason = humanize_failed_video_reason(str(failed.get("reason") or "生成失败"))
        item = {
            "videoIndex": video_index,
            "title": failed.get("videoTitle") or f"视频 {video_index}",
            "jobId": str(failed.get("jobId") or f"merge2-failed-{video_index}").strip(),
            "status": "failed",
            "statusLabel": "生成失败",
            "providerStatus": "local_failed",
            "providerProgress": 100,
            "error": reason,
            "hasLocalAsset": False,
            "updatedAt": created_at.isoformat() if created_at else "",
            "_localTerminalAt": created_at,
        }
        _put_latest_video_item(items_by_video, video_index, item)
    return [
        _strip_private_progress_fields(item)
        for _, item in sorted(items_by_video.items(), key=lambda pair: pair[0])
    ]


def _apply_deleted_asset_progress_state(body: dict) -> None:
    progress = body.get("generationProgress")
    if not isinstance(progress, dict):
        return
    items = progress.get("items")
    if not isinstance(items, list):
        return
    records_by_job_id = _asset_records_by_job_id()
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status not in {"succeeded", "archiving", "polling", "submitted"}:
            continue
        record = item.get("assetRecord")
        if not isinstance(record, dict) or not record:
            job_id = str(item.get("jobId") or "").strip()
            record = records_by_job_id.get(job_id) or {}
        if not record:
            continue
        archive_status = str(record.get("archiveStatus") or "").strip().lower()
        local_exists = _asset_record_local_video_exists(record)
        if archive_status == "archived" and local_exists is True:
            item["status"] = "succeeded"
            item["statusLabel"] = "已生成"
            item["providerStatus"] = "completed"
            item["providerProgress"] = 100
            item["videoUrl"] = record.get("archiveUrl") or record.get("videoUrl") or item.get("videoUrl") or ""
            item["archiveLocalPath"] = record.get("archiveLocalPath") or item.get("archiveLocalPath") or ""
            item["hasLocalAsset"] = True
            record_job_id = str(record.get("jobId") or "").strip()
            if record_job_id:
                item["jobId"] = record_job_id
            video_title = str(record.get("videoTitle") or "").strip()
            if video_title:
                item["title"] = video_title
            if not item.get("segmentStatus"):
                segment_status = _asset_record_segment_statuses(record)
                if segment_status:
                    item["segmentStatus"] = segment_status
            changed = True
        elif archive_status == "archived" and local_exists is False:
            item["status"] = "deleted"
            item["statusLabel"] = "已生成，文件已删除"
            item["providerStatus"] = "deleted"
            item["providerProgress"] = 100
            item["hasLocalAsset"] = False
            if not item.get("segmentStatus"):
                segment_status = _asset_record_segment_statuses(record)
                if segment_status:
                    item["segmentStatus"] = segment_status
            changed = True
    if not changed:
        return
    _refresh_generation_progress_summary(body)


def _refresh_generation_progress_summary(body: dict) -> None:
    progress = body.get("generationProgress")
    if not isinstance(progress, dict):
        return
    items = progress.get("items")
    if not isinstance(items, list):
        return
    running_statuses = {"submitting", "preparing_first_frame", "submitted", "polling", "archiving", "planning"}
    waiting_statuses = {"pending_submission", "planning"}
    progress["submittedCount"] = sum(1 for item in items if isinstance(item, dict) and _has_video_submission(item))
    progress["runningCount"] = sum(
        1 for item in items
        if isinstance(item, dict) and str(item.get("status") or "").strip() in running_statuses
    )
    progress["postProcessingCount"] = sum(
        1 for item in items
        if isinstance(item, dict) and str(item.get("status") or "").strip() == "archiving"
    )
    progress["waitingCount"] = sum(
        1 for item in items
        if isinstance(item, dict) and str(item.get("status") or "").strip() in waiting_statuses
    )
    progress["succeededCount"] = sum(
        1 for item in items
        if isinstance(item, dict) and str(item.get("status") or "").strip() == "succeeded"
    )
    progress["failedCount"] = sum(
        1 for item in items
        if isinstance(item, dict) and str(item.get("status") or "").strip() == "failed"
    )
    progress["deletedCount"] = sum(
        1 for item in items
        if isinstance(item, dict) and str(item.get("status") or "").strip() == "deleted"
    )
    progress["skippedCount"] = sum(
        1 for item in items
        if isinstance(item, dict) and str(item.get("status") or "").strip() == "skipped"
    )
    running_count = int(progress.get("runningCount") or 0)
    post_count = int(progress.get("postProcessingCount") or 0)
    succeeded_count = int(progress.get("succeededCount") or 0)
    failed_count = int(progress.get("failedCount") or 0)
    deleted_count = int(progress.get("deletedCount") or 0)
    skipped_count = int(progress.get("skippedCount") or 0)
    if running_count:
        progress["status"] = "active"
        body["status"] = "pending"
        body["phase"] = "postprocessing" if post_count == running_count else "generating"
        body["statusLabel"] = "后台处理中" if post_count == running_count else "真实视频生成中"
        return
    if succeeded_count and not failed_count and not skipped_count and not deleted_count:
        progress["status"] = "completed"
        body["status"] = "completed"
        body["phase"] = "completed"
        body["statusLabel"] = "视频已生成"
    elif failed_count or skipped_count:
        progress["status"] = "completed_with_error" if succeeded_count or deleted_count else "failed"
        body["status"] = progress["status"]
        body["phase"] = "completed"
        body["statusLabel"] = "部分视频已生成" if succeeded_count or deleted_count else "视频生成失败"
    elif deleted_count:
        progress["status"] = "completed_with_error" if succeeded_count else "completed"
        body["status"] = progress["status"]
        body["phase"] = "completed"
        body["statusLabel"] = "部分视频已生成" if succeeded_count else "文件已删除"
    else:
        progress["status"] = "completed"
        body["status"] = "completed"
        body["phase"] = "completed"
        body["statusLabel"] = "视频已生成"


def _find_related_user_generated_asset_identity(relative_key: str) -> dict[str, set[str]]:
    key = str(relative_key or "").strip()
    name = Path(key).name
    related_keys = {key} if key else set()
    related_job_ids: set[str] = set()
    config = AI8VideoConfig.from_env()
    for record in JsonlAssetStore(config.asset_store_path).read_all():
        archive_key = str(record.get("archiveKey") or "").strip()
        archive_cover_key = str(record.get("archiveCoverKey") or "").strip()
        candidates = {
            archive_key,
            archive_cover_key,
            Path(archive_key).name if archive_key else "",
            Path(archive_cover_key).name if archive_cover_key else "",
        }
        if key not in candidates and name not in candidates:
            continue
        related_keys.update(item for item in (archive_key, archive_cover_key) if item)
        related_job_ids.update(_asset_record_job_ids(record))
    return {"keys": related_keys, "jobIds": related_job_ids}


def _query_video_jobs_progress(
    session_id: str,
    jobs: list[dict],
    *,
    video_count: int | None = None,
    pending_since: datetime | None = None,
) -> dict | None:
    client = AI8VideoModelClient()
    records_by_job_id = _asset_records_by_job_id()
    local_items = _session_local_terminal_progress_items(session_id, pending_since=pending_since)
    items_by_video = {
        int(item.get("videoIndex") or index + 1): dict(item)
        for index, item in enumerate(local_items)
    }
    jobs_by_video: dict[int, list[dict]] = {}
    latest_jobs_by_video: dict[int, dict] = {}
    for job in jobs:
        video_index = int(job["videoIndex"])
        jobs_by_video.setdefault(video_index, []).append(job)
        latest_jobs_by_video[video_index] = job
    for job in latest_jobs_by_video.values():
        job_id = job["jobId"]
        video_index = int(job["videoIndex"])
        if _is_local_failed_video_job_id(job_id) and local_items:
            continue
        existing_item = items_by_video.get(video_index)
        existing_job_id = str((existing_item or {}).get("jobId") or "").strip()
        existing_is_local_failed = _is_local_failed_video_job_id(existing_job_id)
        if existing_item is not None and not (existing_is_local_failed and not _is_local_failed_video_job_id(job_id)):
            segment_status = _query_video_segment_statuses(
                client,
                jobs_by_video.get(video_index) or [job],
                records_by_job_id,
            )
            if not segment_status:
                record = existing_item.get("assetRecord") if isinstance(existing_item.get("assetRecord"), dict) else None
                record = record or records_by_job_id.get(existing_job_id) or records_by_job_id.get(job_id) or {}
                segment_status = _asset_record_segment_statuses(record)
            if segment_status and not existing_item.get("segmentStatus"):
                existing_item["segmentStatus"] = segment_status
            continue
        base = {
            "videoIndex": video_index,
            "title": job.get("title") or f"视频 {video_index}",
            "jobId": job_id,
        }
        segment_status = _query_video_segment_statuses(
            client,
            jobs_by_video.get(video_index) or [job],
            records_by_job_id,
        )
        if segment_status:
            base["segmentStatus"] = segment_status
            segment_index = _coerce_segment_index(job.get("segmentIndex") or job.get("segmentLabel"))
            if segment_index:
                base["segmentIndex"] = segment_index
                base["segmentLabel"] = str(job.get("segmentLabel") or f"片段 {segment_index}").strip()
        record = records_by_job_id.get(job_id) or {}
        if record and not base.get("segmentStatus"):
            segment_status = _asset_record_segment_statuses(record)
            if segment_status:
                base["segmentStatus"] = segment_status
        record_status = str(record.get("status") or "").strip().lower()
        record_archive_status = str(record.get("archiveStatus") or "").strip().lower()
        record_error = str(record.get("archiveError") or record.get("error") or "").strip()
        local_exists = _asset_record_local_video_exists(record) if record else None
        if record and (record_status == "failed" or record_archive_status == "failed"):
            reason = humanize_failed_video_reason(record_error or "生成失败")
            items_by_video[video_index] = {
                **base,
                "status": "failed",
                "statusLabel": "生成失败",
                "providerStatus": "failed",
                "providerProgress": 100,
                "error": reason,
                "hasLocalAsset": False,
            }
            continue
        if record and record_archive_status == "archived" and local_exists is True:
            items_by_video[video_index] = {
                **base,
                "title": record.get("videoTitle") or base.get("title") or f"视频 {video_index}",
                "status": "succeeded",
                "statusLabel": "已生成",
                "providerStatus": "completed",
                "providerProgress": 100,
                "videoUrl": record.get("archiveUrl") or record.get("videoUrl") or "",
                "archiveLocalPath": record.get("archiveLocalPath") or "",
                "assetRecord": record,
                "hasLocalAsset": True,
            }
            continue
        if record and record_archive_status == "archived" and local_exists is False:
            items_by_video[video_index] = {
                **base,
                "status": "deleted",
                "statusLabel": "已生成，文件已删除",
                "providerStatus": "deleted",
                "providerProgress": 100,
                "videoUrl": record.get("videoUrl") or "",
                "archiveLocalPath": record.get("archiveLocalPath") or "",
                "hasLocalAsset": False,
            }
            continue
        if _is_local_failed_video_job_id(job_id):
            items_by_video[video_index] = {
                **base,
                "status": "failed",
                "statusLabel": "生成失败",
                "providerStatus": "local_failed",
                "providerProgress": 100,
                "error": "视频合成失败，没有拿到可继续查看的生成结果。",
                "hasLocalAsset": False,
            }
            continue
        try:
            latest = client.get_job(job_id, video_index=video_index)
        except Exception as exc:
            items_by_video[video_index] = {
                **base,
                "status": "polling",
                "statusLabel": "等待生成结果",
                "error": str(exc),
            }
            continue
        provider_progress = latest.provider_progress
        if latest.status == "succeeded":
            has_local_asset = bool(record) and local_exists is True
            item = {
                **base,
                "status": "succeeded" if has_local_asset else "archiving",
                "statusLabel": "已生成" if has_local_asset else "后台处理中",
                "providerStatus": latest.provider_status or "completed",
                "providerProgress": provider_progress,
                "videoUrl": latest.video_url,
                "hasLocalAsset": has_local_asset,
            }
        elif latest.status == "failed":
            item = {
                **base,
                "status": "failed",
                "statusLabel": "生成失败",
                "providerStatus": latest.provider_status or "failed",
                "providerProgress": 100,
                "error": humanize_failed_video_reason(latest.error or "生成服务没有成功"),
            }
        else:
            segment_label = str(base.get("segmentLabel") or "").strip()
            status_label = (
                f"真实生成进度 {provider_progress}%"
                if provider_progress
                else f"上游状态：{latest.provider_status or 'pending'}"
            )
            if segment_label and not status_label.startswith(segment_label):
                status_label = f"{segment_label}：{status_label}"
            item = {
                **base,
                "status": "polling",
                "statusLabel": status_label,
                "providerStatus": latest.provider_status or "pending",
                "providerProgress": provider_progress,
            }
        items_by_video[video_index] = item
    requested_total = max(
        int(video_count or 0),
        _infer_requested_video_count_from_progress_items(items_by_video.values()),
    )
    if requested_total > 0:
        has_running = any(
            str(item.get("status") or "").strip() in {"submitting", "preparing_first_frame", "submitted", "polling", "archiving"}
            for item in items_by_video.values()
        )
        has_terminal = any(
            str(item.get("status") or "").strip() in {"succeeded", "failed", "deleted"}
            for item in items_by_video.values()
        )
        terminal_failure_error = next(
            (
                str(item.get("error") or "").strip()
                for _, item in sorted(items_by_video.items())
                if str(item.get("status") or "").strip() == "failed" and str(item.get("error") or "").strip()
            ),
            "",
        )
        for video_index in range(1, requested_total + 1):
            if video_index in items_by_video:
                continue
            items_by_video[video_index] = {
                "videoIndex": video_index,
                "title": f"视频 {video_index}",
                "jobId": None,
                "status": "pending_submission" if has_running and not has_terminal else "skipped",
                "statusLabel": "待生成" if has_running and not has_terminal else "未继续生成",
                "error": "" if has_running and not has_terminal else (
                    terminal_failure_error or "这条未提交给生成服务；没有上游返回。"
                ),
            }
    items = [
        items_by_video[key]
        for key in sorted(items_by_video)
    ]
    if not items:
        return None
    total = len(items)
    progress = {
        "sessionId": session_id,
        "totalRequested": total,
        "items": items,
        "submittedCount": sum(1 for item in items if _has_video_submission(item)),
        "runningCount": sum(1 for item in items if item["status"] in {"polling", "archiving"}),
        "postProcessingCount": sum(1 for item in items if item["status"] == "archiving"),
        "waitingCount": 0,
        "succeededCount": sum(1 for item in items if item["status"] == "succeeded"),
        "failedCount": sum(1 for item in items if item["status"] == "failed"),
        "deletedCount": sum(1 for item in items if item["status"] == "deleted"),
        "skippedCount": sum(1 for item in items if item["status"] == "skipped"),
    }
    if progress["runningCount"]:
        progress["status"] = "active"
        status = "pending"
        status_label = "后台处理中" if progress["postProcessingCount"] == progress["runningCount"] else "真实视频生成中"
    elif progress["failedCount"] and progress["succeededCount"]:
        progress["status"] = "completed_with_error"
        status = "completed_with_error"
        status_label = "部分视频已生成"
    elif progress["deletedCount"] and not progress["succeededCount"] and not progress["failedCount"]:
        progress["status"] = "completed"
        status = "completed"
        status_label = "文件已删除"
    elif progress["deletedCount"]:
        progress["status"] = "completed_with_error"
        status = "completed_with_error"
        status_label = "部分视频已生成"
    elif progress["failedCount"]:
        progress["status"] = "failed"
        status = "failed"
        status_label = "视频生成失败"
    else:
        progress["status"] = "completed"
        status = "completed"
        status_label = "视频已生成"
    return {
        "status": status,
        "phase": "generating" if status == "pending" else "completed",
        "statusLabel": status_label,
        "sessionId": session_id,
        "elapsedSeconds": 0,
        "generationProgress": progress,
        "statelessProgress": True,
    }


def _trace_video_jobs_by_video(
    session_id: str,
    *,
    pending_since: datetime | None = None,
) -> dict[int, dict]:
    records = list(_iter_prompt_trace_records(str(session_id or "").strip(), pending_since=pending_since))
    records = _prompt_trace_attempt_records_for_pending(records, pending_since=pending_since)
    jobs: dict[int, dict] = {}
    for record in records:
        if str(record.get("event") or "").strip() != "video_job_created":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        video_index = _coerce_video_index(payload.get("videoIndex"))
        job_id = str(payload.get("jobId") or "").strip()
        if video_index <= 0 or not job_id:
            continue
        jobs[video_index] = {
            "videoIndex": video_index,
            "title": payload.get("title") or payload.get("videoTitle") or f"视频 {video_index}",
            "jobId": job_id,
        }
    return jobs


def _query_video_segment_statuses(
    client: AI8VideoModelClient,
    jobs: list[dict],
    records_by_job_id: dict[str, dict],
) -> list[dict]:
    if not jobs:
        return []
    if len(jobs) == 1:
        job_id = str(jobs[0].get("jobId") or "").strip()
        restored = _asset_record_segment_statuses(records_by_job_id.get(job_id) or {})
        if restored:
            return restored
    has_segment = any(_coerce_segment_index(job.get("segmentIndex") or job.get("segmentLabel")) for job in jobs)
    if len(jobs) <= 1 and not has_segment:
        return []
    statuses: list[dict] = []
    for order, job in enumerate(jobs, start=1):
        job_id = str(job.get("jobId") or "").strip()
        if not job_id:
            continue
        video_index = int(job.get("videoIndex") or 1)
        segment_index = _coerce_segment_index(job.get("segmentIndex") or job.get("segmentLabel")) or order
        segment_label = str(job.get("segmentLabel") or f"片段 {segment_index}").strip()
        base = {
            "segmentIndex": segment_index,
            "segmentLabel": segment_label,
            "jobId": job_id,
        }
        record = records_by_job_id.get(job_id) or {}
        local_exists = _asset_record_local_video_exists(record) if record else None
        record_status = str(record.get("status") or "").strip().lower()
        record_archive_status = str(record.get("archiveStatus") or "").strip().lower()
        if record and (record_status == "failed" or record_archive_status == "failed"):
            statuses.append({
                **base,
                "status": "failed",
                "statusLabel": f"{segment_label}：生成失败",
                "providerStatus": "failed",
                "providerProgress": 100,
                "error": humanize_failed_video_reason(record.get("archiveError") or record.get("error") or "生成失败"),
            })
            continue
        if record and record_archive_status == "archived" and local_exists is True:
            statuses.append({
                **base,
                "status": "succeeded",
                "statusLabel": f"{segment_label}：已生成",
                "providerStatus": "completed",
                "providerProgress": 100,
                "videoUrl": record.get("videoUrl") or "",
            })
            continue
        try:
            latest = client.get_job(job_id, video_index=video_index)
        except Exception as exc:
            statuses.append({
                **base,
                "status": "polling",
                "statusLabel": f"{segment_label}：等待生成结果",
                "error": str(exc),
            })
            continue
        provider_progress = latest.provider_progress
        if latest.status == "succeeded":
            statuses.append({
                **base,
                "status": "succeeded",
                "statusLabel": f"{segment_label}：已生成",
                "providerStatus": latest.provider_status or "completed",
                "providerProgress": provider_progress or 100,
                "videoUrl": latest.video_url or "",
            })
        elif latest.status == "failed":
            statuses.append({
                **base,
                "status": "failed",
                "statusLabel": f"{segment_label}：生成失败",
                "providerStatus": latest.provider_status or "failed",
                "providerProgress": 100,
                "error": humanize_failed_video_reason(latest.error or "生成服务没有成功"),
            })
        else:
            label = f"真实生成进度 {provider_progress}%" if provider_progress else f"上游状态：{latest.provider_status or 'pending'}"
            statuses.append({
                **base,
                "status": "polling",
                "statusLabel": f"{segment_label}：{label}",
                "providerStatus": latest.provider_status or "pending",
                "providerProgress": provider_progress,
                "videoUrl": latest.video_url or "",
            })
    return statuses


def _merge_trace_items_into_job_progress(job_progress: dict, trace_items_by_video: dict[int, dict]) -> None:
    progress = job_progress.get("generationProgress")
    if not isinstance(progress, dict):
        return
    items = progress.get("items")
    if not isinstance(items, list):
        return
    by_video = {
        int(item.get("videoIndex") or index + 1): item
        for index, item in enumerate(items)
        if isinstance(item, dict)
    }
    changed = False
    for video_index, trace_item in trace_items_by_video.items():
        if not isinstance(trace_item, dict):
            continue
        trace_status = str(trace_item.get("status") or "").strip()
        if trace_status not in {"failed", "skipped"}:
            continue
        existing = by_video.get(int(video_index))
        existing_status = str((existing or {}).get("status") or "").strip()
        if existing is None:
            items.append(_strip_private_progress_fields(dict(trace_item)))
            changed = True
            continue
        if existing_status in {"succeeded", "archiving", "polling"}:
            continue
        if existing_status in {"pending_submission", "skipped", ""}:
            existing.update(_strip_private_progress_fields(dict(trace_item)))
            changed = True
    if not changed:
        return
    items.sort(key=lambda item: int(item.get("videoIndex") or 0))
    progress["items"] = items
    progress["totalRequested"] = len(items)
    progress["submittedCount"] = sum(1 for item in items if _has_video_submission(item))
    progress["runningCount"] = sum(1 for item in items if str(item.get("status") or "").strip() in {"polling", "archiving"})
    progress["postProcessingCount"] = sum(1 for item in items if str(item.get("status") or "").strip() == "archiving")
    progress["waitingCount"] = sum(1 for item in items if str(item.get("status") or "").strip() == "pending_submission")
    progress["succeededCount"] = sum(1 for item in items if str(item.get("status") or "").strip() == "succeeded")
    progress["failedCount"] = sum(1 for item in items if str(item.get("status") or "").strip() == "failed")
    progress["deletedCount"] = sum(1 for item in items if str(item.get("status") or "").strip() == "deleted")
    progress["skippedCount"] = sum(1 for item in items if str(item.get("status") or "").strip() == "skipped")
    if progress["runningCount"]:
        progress["status"] = "active"
        job_progress["status"] = "pending"
        job_progress["phase"] = "generating"
        job_progress["statusLabel"] = "真实视频生成中，部分任务已失败" if progress["failedCount"] else "真实视频生成中"
    elif progress["failedCount"] and progress["succeededCount"]:
        progress["status"] = "completed_with_error"
        job_progress["status"] = "completed_with_error"
        job_progress["phase"] = "completed"
        job_progress["statusLabel"] = "部分视频已生成"
    elif progress["failedCount"]:
        progress["status"] = "failed"
        job_progress["status"] = "failed"
        job_progress["phase"] = "completed"
        job_progress["statusLabel"] = "视频生成失败"


def _query_prompt_trace_planning_progress(
    session_id: str,
    *,
    video_count: int | None = None,
    pending_since: datetime | None = None,
) -> dict | None:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return None
    records = list(_iter_prompt_trace_records(normalized_session_id, pending_since=pending_since))
    records = _prompt_trace_attempt_records_for_pending(records, pending_since=pending_since)
    if not records:
        return None
    generation_events = {
        "first_frame_image_prompt",
        "first_frame_image_output",
        "first_frame_image_error",
        "video_submit",
        "video_job_created",
    }
    if any(str(record.get("event") or "").strip() in generation_events for record in records):
        return None
    planning_events = {
        "keyword_model_input",
        "keyword_model_output",
        "video_planning_model_input",
        "video_planning_model_output",
        "business_prompt_batch_model_input",
        "business_prompt_batch_model_output",
        "business_prompt_batch_model_error",
        "business_prompt_validation_model_input",
        "business_prompt_validation_model_output",
        "business_prompt_validation_model_error",
        "merged_final_video_prompt",
    }
    if not any(str(record.get("event") or "").strip() in planning_events for record in records):
        return None

    latest_at: datetime | None = None
    video_total = int(video_count or 0)
    titles_by_video: dict[int, str] = {}
    validation_state_by_video: dict[int, str] = {}
    event_names: set[str] = set()
    active_validation_video = 0
    for record in records:
        event = str(record.get("event") or "").strip()
        if event:
            event_names.add(event)
        created_at = _parse_iso_datetime(record.get("createdAt") or "")
        if created_at and (latest_at is None or created_at > latest_at):
            latest_at = created_at
        payload = record.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        try:
            video_total = max(video_total, int(payload.get("videoCount") or 0))
        except (TypeError, ValueError):
            pass
        video_index = _coerce_video_index(payload.get("videoIndex"))
        if video_index > 0:
            video_total = max(video_total, video_index)
            title = str(payload.get("title") or payload.get("videoTitle") or "").strip()
            if title:
                titles_by_video[video_index] = title
            if event == "business_prompt_validation_model_input":
                validation_state_by_video[video_index] = "input"
                active_validation_video = video_index
            elif event == "business_prompt_validation_model_output":
                validation_state_by_video[video_index] = "output"
            elif event == "business_prompt_validation_model_error":
                validation_state_by_video[video_index] = "error"
    if video_total <= 0:
        video_total = 1

    if "merged_final_video_prompt" in event_names:
        global_label = "视频方案已完成，正在进入生成"
    elif active_validation_video:
        global_label = f"正在检查第 {active_validation_video}/{video_total} 条视频脚本"
    elif "business_prompt_batch_model_output" in event_names:
        global_label = "正在完善每条视频脚本"
    elif "business_prompt_batch_model_input" in event_names:
        global_label = "正在生成每条视频方案"
    elif "video_planning_model_output" in event_names:
        global_label = "批量视频规划完成，正在写每条方案"
    elif "video_planning_model_input" in event_names:
        global_label = "正在智能规划批量视频"
    elif "keyword_model_output" in event_names:
        global_label = "已读懂重点，正在规划独立视频"
    else:
        global_label = "正在理解全文关键词"

    items = []
    for video_index in range(1, video_total + 1):
        state = validation_state_by_video.get(video_index)
        if state == "output":
            item_label = "视频脚本检查完成"
            provider_progress = 72
        elif state == "error":
            item_label = "脚本检查异常，使用安全兜底"
            provider_progress = 64
        elif state == "input":
            item_label = "正在检查视频脚本"
            provider_progress = 58
        elif "business_prompt_batch_model_output" in event_names:
            item_label = "正在完善视频脚本"
            provider_progress = 50
        elif "business_prompt_batch_model_input" in event_names:
            item_label = "正在生成视频方案"
            provider_progress = 38
        elif "video_planning_model_output" in event_names:
            item_label = "规划完成，正在写方案"
            provider_progress = 32
        elif "video_planning_model_input" in event_names:
            item_label = "智能规划视频"
            provider_progress = 22
        elif "keyword_model_output" in event_names:
            item_label = "关键词理解完成"
            provider_progress = 16
        else:
            item_label = "理解全文关键词"
            provider_progress = 8
        items.append({
            "videoIndex": video_index,
            "title": titles_by_video.get(video_index) or f"视频 {video_index}",
            "jobId": None,
            "status": "planning",
            "statusLabel": item_label,
            "providerStatus": "planning",
            "providerProgress": provider_progress,
        })

    progress = {
        "sessionId": normalized_session_id,
        "status": "planning",
        "summary": global_label,
        "totalRequested": video_total,
        "items": items,
        "submittedCount": 0,
        "runningCount": len(items),
        "postProcessingCount": 0,
        "waitingCount": len(items),
        "succeededCount": 0,
        "failedCount": 0,
        "deletedCount": 0,
        "skippedCount": 0,
    }
    if latest_at:
        progress["updatedAt"] = latest_at.isoformat()
    return {
        "status": "pending",
        "phase": "planning",
        "statusLabel": global_label,
        "generationProgress": progress,
    }


def _query_prompt_trace_generation_progress(
    session_id: str,
    *,
    video_count: int | None = None,
    pending_since: datetime | None = None,
) -> dict | None:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return None
    records = list(_iter_prompt_trace_records(normalized_session_id, pending_since=pending_since))
    records = _prompt_trace_attempt_records_for_pending(records, pending_since=pending_since)
    if not records:
        return None
    titles_by_video: dict[int, str] = {}
    items_by_video: dict[int, dict] = {}
    trace_jobs: list[dict] = []
    latest_at: datetime | None = None
    saw_generation_trace = False
    for record in records:
        event = str(record.get("event") or "").strip()
        payload = record.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        created_at = _parse_iso_datetime(record.get("createdAt") or "")
        if created_at and (latest_at is None or created_at > latest_at):
            latest_at = created_at
        video_index = _coerce_video_index(payload.get("videoIndex"))
        if video_index <= 0:
            continue
        title = str(payload.get("title") or payload.get("videoTitle") or "").strip()
        if event == "merged_final_video_prompt" and title:
            titles_by_video[video_index] = title
            continue
        if event not in {
            "first_frame_image_prompt",
            "first_frame_image_request",
            "first_frame_image_output",
            "first_frame_image_error",
            "video_submit",
            "video_job_created",
        }:
            continue
        saw_generation_trace = True
        title = titles_by_video.get(video_index) or title or f"视频 {video_index}"
        base = {
            "videoIndex": video_index,
            "title": title,
            "jobId": None,
            "_localTerminalAt": created_at,
        }
        if event == "first_frame_image_prompt":
            item = {
                **base,
                "status": "preparing_first_frame",
                "statusLabel": "正在生成首帧图",
            }
        elif event == "first_frame_image_request":
            item = {
                **base,
                "status": "preparing_first_frame",
                "statusLabel": "正在等待首帧图返回",
                "providerStatus": "first_frame_requested",
                "providerProgress": 70,
                "_firstFrameRequestStarted": True,
            }
        elif event == "first_frame_image_output":
            item = {
                **base,
                "status": "pending_submission",
                "statusLabel": "正在提交视频生成",
                "firstFrameSource": payload.get("outputSource") or "",
            }
        elif event == "video_submit":
            segment_label = str(payload.get("segmentLabel") or "").strip()
            item = {
                **base,
                "status": "submitting",
                "statusLabel": f"{segment_label}提交中" if segment_label else "视频提交中",
                "providerStatus": "video_submit_sent",
                "providerProgress": 1,
                "segmentLabel": segment_label,
            }
        elif event == "video_job_created":
            job_id = str(payload.get("jobId") or "").strip()
            segment_label = str(payload.get("segmentLabel") or "").strip()
            if job_id:
                segment_index = _coerce_segment_index(segment_label)
                trace_jobs.append({
                    "videoIndex": video_index,
                    "title": title,
                    "jobId": job_id,
                    "segmentIndex": segment_index,
                    "segmentLabel": segment_label,
                })
            item = {
                **base,
                "jobId": job_id or None,
                "status": "polling",
                "statusLabel": f"{segment_label}等待生成结果" if segment_label else "等待生成结果",
                "providerStatus": str(payload.get("providerStatus") or payload.get("status") or "submitted"),
                "providerProgress": payload.get("providerProgress") or 1,
                "videoUrl": payload.get("videoUrl") or "",
            }
        else:
            raw_error = str(payload.get("error") or "首帧图结果未回填").strip()
            item = {
                **base,
                "jobId": f"first-frame-failed-{video_index}",
                "status": "failed",
                "statusLabel": "首帧图未回填" if _is_lost_first_frame_response(raw_error) else "首帧图生成失败",
                "providerStatus": "first_frame_response_lost" if _is_lost_first_frame_response(raw_error) else "first_frame_failed",
                "providerProgress": 100,
                "error": _humanize_first_frame_trace_error(raw_error),
                "rawError": raw_error,
            }
        _put_latest_video_item(items_by_video, video_index, item)
    if not saw_generation_trace or not items_by_video:
        return None
    if trace_jobs:
        job_progress = _query_video_jobs_progress(
            normalized_session_id,
            trace_jobs,
            video_count=video_count,
            pending_since=pending_since,
        )
        if job_progress:
            _merge_trace_items_into_job_progress(job_progress, items_by_video)
            job_progress["traceRecovered"] = True
            job_progress["statelessProgress"] = True
            return job_progress

    now = datetime.now(timezone.utc)
    for item in items_by_video.values():
        if _first_frame_lost_still_recovering(item, now):
            item["status"] = "polling"
            item["statusLabel"] = "等待生成结果回填"
            item["providerStatus"] = "first_frame_response_lost"
            item["providerProgress"] = 90
            item["error"] = _first_frame_response_lost_recovering_message()

    requested_total = max(int(video_count or 0), max(items_by_video))
    for video_index in range(1, requested_total + 1):
        item = items_by_video.get(video_index)
        if item is None:
            items_by_video[video_index] = {
                "videoIndex": video_index,
                "title": titles_by_video.get(video_index) or f"视频 {video_index}",
                "jobId": f"interrupted-before-submit-{video_index}",
                "status": "skipped",
                "statusLabel": "未继续生成",
                "providerStatus": "local_interrupted",
                "providerProgress": 100,
                "error": "后台中断了，这条视频未提交给生成服务。请重新生成。",
            }
            continue
        status = str(item.get("status") or "").strip()
        if status in {"succeeded", "failed", "deleted", "skipped"}:
            continue
        if status == "polling" and str(item.get("providerStatus") or "") == "first_frame_response_lost":
            continue
        if status == "submitting":
            segment_label = str(item.get("segmentLabel") or "").strip()
            item["jobId"] = None
            item["status"] = "polling"
            item["providerStatus"] = "video_create_response_lost"
            item["providerProgress"] = item.get("providerProgress") or 1
            item["statusLabel"] = (
                f"{segment_label} 已提交上游，等待任务号回填" if segment_label else "已提交上游，等待任务号回填"
            )
            item["error"] = _video_create_response_lost_message()
            continue
        item["jobId"] = item.get("jobId") or f"interrupted-before-submit-{video_index}"
        item["status"] = "failed"
        item["providerProgress"] = 100
        if status == "preparing_first_frame" and item.get("_firstFrameRequestStarted"):
            item["jobId"] = f"first-frame-failed-{video_index}"
            item["statusLabel"] = "首帧图未回填"
            item["providerStatus"] = "first_frame_response_lost"
            item["error"] = _first_frame_response_lost_message()
        elif status == "preparing_first_frame":
            item["providerStatus"] = "local_interrupted"
            item["statusLabel"] = "首帧图未回填"
            item["error"] = "首帧图准备过程中后台进程中断，尚无证据显示图片接口已收到请求。"
        elif status == "pending_submission":
            item["providerStatus"] = "local_interrupted"
            item["statusLabel"] = "未继续生成"
            item["error"] = "首帧图已经生成，但视频没有继续生成。请重新生成。"
        else:
            item["providerStatus"] = "local_interrupted"
            item["statusLabel"] = "未继续生成"
            item["error"] = "后台中断了，这条视频未提交给生成服务。请重新生成。"

    items = [
        _strip_private_progress_fields(items_by_video[key])
        for key in sorted(items_by_video)
    ]
    failed_count = sum(1 for item in items if item.get("status") == "failed")
    skipped_count = sum(1 for item in items if item.get("status") == "skipped")
    succeeded_count = sum(1 for item in items if item.get("status") == "succeeded")
    running_count = sum(1 for item in items if item.get("status") in {"polling", "archiving"})
    if running_count:
        progress_status = "active"
        status = "pending"
        status_label = "等待生成结果回填"
    else:
        progress_status = "completed_with_error" if succeeded_count and (failed_count or skipped_count) else "failed"
        status = "completed_with_error" if succeeded_count else "failed"
        status_label = "部分视频已生成" if succeeded_count else "首帧图结果未回填"
    progress = {
        "sessionId": normalized_session_id,
        "status": progress_status,
        "totalRequested": len(items),
        "items": items,
        "submittedCount": sum(1 for item in items if _has_video_submission(item)),
        "runningCount": running_count,
        "postProcessingCount": 0,
        "waitingCount": 0,
        "succeededCount": succeeded_count,
        "failedCount": failed_count,
        "deletedCount": sum(1 for item in items if item.get("status") == "deleted"),
        "skippedCount": skipped_count,
    }
    if latest_at:
        progress["updatedAt"] = latest_at.isoformat()
        if not running_count:
            progress["completedAt"] = latest_at.isoformat()
    if failed_count:
            progress["error"] = "首帧图结果未回填，本地没有拿到可确认结果。"
    elapsed = 0
    if pending_since is not None:
        elapsed = max(0, int(((latest_at or now).timestamp()) - pending_since.timestamp()))
    return {
        "status": status,
        "phase": "generating" if status == "pending" else "completed",
        "statusLabel": status_label,
        "sessionId": normalized_session_id,
        "pendingSince": str(request.query.get("pendingSince") or "") or None,
        "elapsedSeconds": elapsed,
        **({} if running_count else {"completedAt": (latest_at or now).isoformat()}),
        "generationProgress": progress,
        "statelessProgress": True,
        "traceRecovered": True,
    }


def _iter_prompt_trace_records(session_id: str, *, pending_since: datetime | None = None):
    path = PROMPT_TRACE_PATH
    try:
        size = path.stat().st_size
    except OSError:
        return
    raw_limit = str(os.getenv("AI8VIDEO_PROMPT_TRACE_RECOVERY_BYTES") or "").strip()
    try:
        default_max_bytes = 64 * 1024 * 1024 if pending_since is not None else 8 * 1024 * 1024
        max_bytes = max(512 * 1024, int(raw_limit)) if raw_limit else default_max_bytes
    except ValueError:
        max_bytes = 64 * 1024 * 1024 if pending_since is not None else 8 * 1024 * 1024
    try:
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()
            for raw_line in handle:
                line = raw_line.decode("utf-8", errors="ignore")
                try:
                    record = normalize_legacy_video_payload(json.loads(line))
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                if str(record.get("sessionId") or "").strip() != session_id:
                    continue
                created_at = _parse_iso_datetime(record.get("createdAt") or "")
                if pending_since is not None and created_at is not None:
                    if created_at.timestamp() < pending_since.timestamp() - 1:
                        continue
                yield record
    except OSError:
        return


def _prompt_trace_attempt_records_for_pending(
    records: list[dict],
    *,
    pending_since: datetime | None = None,
) -> list[dict]:
    primary_attempt_start_events = {
        "keyword_model_input",
    }
    attempt_start_events = {
        "keyword_model_input",
        "video_planning_model_input",
        "business_prompt_batch_model_input",
    }
    if pending_since is not None:
        start_indexes: list[int] = []
        for index, record in enumerate(records):
            event = str(record.get("event") or "").strip()
            if event not in primary_attempt_start_events:
                continue
            created_at = _parse_iso_datetime(record.get("createdAt") or "")
            if created_at is None:
                continue
            if created_at.timestamp() >= pending_since.timestamp() - 1:
                start_indexes.append(index)
        if start_indexes:
            selected_index = start_indexes[0]
            next_index = next((index for index in start_indexes[1:] if index > selected_index), None)
            return records[selected_index:next_index]
    latest_start_index = -1
    for index, record in enumerate(records):
        event = str(record.get("event") or "").strip()
        if event in attempt_start_events:
            latest_start_index = index
    if latest_start_index > 0:
        return records[latest_start_index:]
    return records


def _is_lost_first_frame_response(error: str) -> bool:
    lowered = str(error or "").lower()
    return any(marker in lowered for marker in (
        "remotedisconnected",
        "remote end closed connection",
        "gateway timeout",
        "read timed out",
        "ssl: unexpected_eof_while_reading",
    ))


def _has_video_submission(item: dict) -> bool:
    status = str(item.get("status") or "").strip()
    if status not in {"submitted", "polling", "archiving", "succeeded", "failed", "deleted"}:
        return False
    provider_status = str(item.get("providerStatus") or "").strip()
    if provider_status in {"first_frame_failed", "first_frame_response_lost", "local_interrupted"}:
        return False
    job_id = str(item.get("jobId") or "").strip()
    if job_id.startswith(("create-failed-", "first-frame-failed-", "interrupted-before-submit-", "merge2-failed-")):
        return False
    return True


def _humanize_first_frame_trace_error(error: str) -> str:
    text = str(error or "").strip()
    lowered = text.lower()
    if not text:
        return "首帧图生成失败，视频任务没有提交。请重新生成。"
    if "429" in lowered or "too many requests" in lowered:
        return "图片模型当前限流，请稍后重新生成。"
    if "invalid image base64" in lowered:
        return "参考图图片数据无效，图生图失败；视频任务没有提交。请重新选择或上传有效图片后再试。"
    if _is_lost_first_frame_response(text):
        return _first_frame_response_lost_message()
    return humanize_failed_video_reason(text)


def _first_frame_response_lost_message() -> str:
    return (
        "首帧图生成时连接断开，本地没有拿到图片结果。"
        "这类长时间图片生成请求仍可能在服务端完成并扣费；当前不会用原图冒充成功。"
        "请改用更快的图片模型，或关闭参考图图生图后再生成。"
    )


def _first_frame_response_lost_recovering_message() -> str:
    return (
        "首帧图接口响应丢失，本地暂时没有拿到图片 URL。"
        "正在等待生成结果回填。"
    )


def _video_create_response_lost_message() -> str:
    return (
        "创建视频任务的响应没有回填到本地，但请求已经发给上游。"
        "上游后台可能仍在生成；请以上游后台任务状态为准，不要立刻重复提交。"
    )


def _first_frame_lost_recovery_seconds() -> int:
    raw = str(os.getenv("AI8VIDEO_FIRST_FRAME_LOST_RECOVERY_SECONDS") or "").strip()
    try:
        return max(0, int(raw)) if raw else 0
    except ValueError:
        return 0


def _first_frame_lost_still_recovering(item: dict, now: datetime) -> bool:
    if str(item.get("providerStatus") or "") != "first_frame_response_lost":
        return False
    if str(item.get("status") or "") != "failed":
        return False
    recovery_seconds = _first_frame_lost_recovery_seconds()
    if recovery_seconds <= 0:
        return False
    event_time = item.get("_localTerminalAt")
    if not isinstance(event_time, datetime):
        return False
    return now.timestamp() - event_time.timestamp() < recovery_seconds


def _record_mentions_progress_session(value, session_id: str) -> bool:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return False
    if isinstance(value, dict):
        if str(value.get("progressSessionId") or "").strip() == normalized_session_id:
            return True
        return any(_record_mentions_progress_session(item, normalized_session_id) for item in value.values())
    if isinstance(value, list):
        return any(_record_mentions_progress_session(item, normalized_session_id) for item in value)
    if isinstance(value, str):
        text = value.strip()
        return normalized_session_id in text and ("视频合并" in text or "progressSessionId" in text)
    return False


def _latest_progress_timestamp(progress: dict) -> datetime | None:
    candidates: list[datetime] = []
    for key in ("updatedAt", "completedAt", "startedAt"):
        parsed = _parse_iso_datetime(progress.get(key) or "")
        if parsed:
            candidates.append(parsed)
    for item in progress.get("items") or []:
        if not isinstance(item, dict):
            continue
        for key in ("updatedAt", "completedAt", "createdAt"):
            parsed = _parse_iso_datetime(item.get(key) or "")
            if parsed:
                candidates.append(parsed)
        record = item.get("assetRecord")
        if isinstance(record, dict):
            parsed = _parse_iso_datetime(record.get("updatedAt") or record.get("createdAt") or "")
            if parsed:
                candidates.append(parsed)
    return max(candidates) if candidates else None


def _infer_requested_video_count_from_progress_items(items) -> int:
    requested_total = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        record = item.get("assetRecord")
        if not isinstance(record, dict):
            continue
        request_info = record.get("request")
        if not isinstance(request_info, dict):
            continue
        for key in ("videoCount", "videoCount", "count"):
            try:
                requested_total = max(requested_total, int(request_info.get(key) or 0))
            except (TypeError, ValueError):
                continue
    return requested_total


def _latest_local_terminal_time(asset_records: list[dict], recycle_items: list[dict]) -> datetime | None:
    candidates = []
    for record in asset_records:
        parsed = _parse_iso_datetime(record.get("createdAt") or record.get("updatedAt") or "")
        if parsed:
            candidates.append(parsed)
    for item in recycle_items:
        parsed = _parse_iso_datetime(item.get("createdAt") or "")
        if parsed:
            candidates.append(parsed)
    return max(candidates) if candidates else None


def _local_terminal_time_in_scope(
    value: datetime | None,
    pending_since: datetime | None,
    reference_time: datetime | None,
) -> bool:
    if value is None:
        return pending_since is None and reference_time is None
    if pending_since is not None:
        return value.timestamp() >= pending_since.timestamp() - 1
    if reference_time is not None:
        return abs(value.timestamp() - reference_time.timestamp()) <= 3600
    return True


def _parse_iso_datetime(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone(timezone.utc)


def _coerce_video_index(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_segment_index(value) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        parsed = int(text)
        return parsed if parsed > 0 else None
    match = re.search(r"(\d+)", text)
    if not match:
        return None
    parsed = int(match.group(1))
    return parsed if parsed > 0 else None


def _put_latest_video_item(items_by_video: dict[int, dict], video_index: int, item: dict) -> None:
    previous = items_by_video.get(video_index)
    if previous is None:
        items_by_video[video_index] = item
        return
    previous_time = previous.get("_localTerminalAt")
    next_time = item.get("_localTerminalAt")
    if not isinstance(previous_time, datetime) or (
        isinstance(next_time, datetime) and next_time >= previous_time
    ):
        items_by_video[video_index] = item


def _strip_private_progress_fields(item: dict) -> dict:
    cleaned = dict(item)
    for key in list(cleaned):
        if str(key).startswith("_"):
            cleaned.pop(key, None)
    return cleaned


def _is_local_failed_video_job_id(job_id: str) -> bool:
    text = str(job_id or "").strip().lower()
    return text.startswith("merge") and "-failed-" in text


def _find_free_port(lo: int = 18720, hi: int = 18820) -> int:
    for port in range(lo, hi + 1):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free port for ai8video_web")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI8video 本地 Web 工作台")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    port = args.port or _find_free_port()
    migration = migrate_legacy_result_layout()
    if migration.get("movedVideos") or migration.get("movedMetadata"):
        logging.info(
            "已将结果统一到用户生成结果/video：视频 %d 个，恢复元数据 %d 个",
            len(migration.get("movedVideos") or []),
            len(migration.get("movedMetadata") or []),
        )
    health = get_health_payload()
    print(json.dumps({
        "url": f"http://127.0.0.1:{port}",
        "dryRun": health["dryRun"],
        "hasLLM": health["hasLLM"],
        "assetStorePath": health["assetStorePath"],
        "archiveBackend": health["archiveBackend"],
        "archiveLocalDir": health["archiveLocalDir"],
    }, ensure_ascii=False))
    try:
        try:
            start_specialist_agent_scheduler()
        except Exception as exc:
            logging.warning(
                "specialist agent scheduler startup failed error_type=%s",
                exc.__class__.__name__,
            )
        run(
            app=app,
            host="127.0.0.1",
            port=port,
            debug=False,
            reloader=False,
            server=ThreadingWSGIRefServer,
        )
    finally:
        try:
            shutdown_specialist_agent_scheduler()
        except Exception as exc:
            logging.warning(
                "specialist agent scheduler shutdown failed error_type=%s",
                exc.__class__.__name__,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
