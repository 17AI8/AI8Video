from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from ai8video.assets.upload_utils import resolve_upload_filename
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root
from ai8video.core.paths import PROJECT_ROOT
from ai8video.media import local_tts_audio as _audio
from ai8video.media import local_tts_mimo as _mimo
from ai8video.media import local_tts_settings as _settings
from ai8video.media import local_tts_text as _text
from ai8video.media.ffmpeg_utils import probe_media_duration_seconds, resolve_ffmpeg_bin
from ai8video.media.local_tts_settings import (
    DEFAULT_LOCAL_TTS_ENABLED,
    DEFAULT_LOCAL_TTS_ENGINE,
    DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME,
    DEFAULT_LOCAL_TTS_RATE,
    DEFAULT_LOCAL_TTS_VOICE,
    DEFAULT_LOCAL_TTS_VOLUME,
    DEFAULT_MIMO_API_BASE_URL,
    DEFAULT_MIMO_API_CLONE_MODEL,
    DEFAULT_MIMO_API_MODEL,
    DEFAULT_MIMO_API_VOICE,
    LEGACY_LOCAL_TTS_CLONE_LIBRARY_DIR_NAME,
    LEGACY_LOCAL_TTS_DIR_NAME,
    LOCAL_TTS_CLONE_AUDIO_EXTENSIONS,
    LOCAL_TTS_CLONE_AUDIO_FILTER,
    LOCAL_TTS_CLONE_DATA_URI_MAX_BYTES,
    LOCAL_TTS_CLONE_LIBRARY_DIR_NAME,
    LOCAL_TTS_CLONE_MAX_SECONDS,
    LOCAL_TTS_CLONE_STORAGE_EXTENSION,
    LOCAL_TTS_CLONE_VIDEO_EXTENSIONS,
    LOCAL_TTS_DIR_NAME,
    LOCAL_TTS_DURATION_FIT_TOLERANCE_SECONDS,
    LOCAL_TTS_END_GUARD_SECONDS,
    LOCAL_TTS_LOUDNESS_FILTER,
    LOCAL_TTS_OUTPUT_DIR_NAME,
    LOCAL_TTS_SETTINGS_NAME,
    MAX_LOCAL_TTS_VOLUME,
    MIMO_API_PRESET_VOICE_OPTIONS,
)
from ai8video.media.local_tts_text import (
    DIALOGUE_CUE_DOUBLE_QUOTE_RE,
    DIALOGUE_CUE_SINGLE_QUOTE_RE,
    DIALOGUE_FIELD_RE,
    MAX_TTS_TEXT_CHARS,
    SHOT_BOUNDARY_RE,
    TAIL_FRAME_MARKER,
    TIME_BOUNDARY_RE,
)


prepare_narration_text = _text.prepare_narration_text
extract_dialogue_text = _text.extract_dialogue_text
_strip_prompt_label = _text.strip_prompt_label
_looks_like_visual_instruction = _text.looks_like_visual_instruction
_clean_engine = _settings.clean_engine
_clean_mimo_clone_model = _settings.clean_mimo_clone_model
_voice_clone_value = _settings.voice_clone_value
_is_voice_clone_selection = _settings.is_voice_clone_selection
_voice_clone_item_id = _settings.voice_clone_item_id
_voice_label = _settings.voice_label
_clean_mimo_api_base_url = _settings.clean_mimo_api_base_url
_clean_mimo_model = _settings.clean_mimo_model
_clean_secret_text = _settings.clean_secret_text
_clean_tts_duration_seconds = _settings.clean_tts_duration_seconds
_clean_bool = _settings.clean_bool
_clean_int = _settings.clean_int
_clean_float = _settings.clean_float
_format_volume = _settings.format_volume
_safe_file_part = _settings.safe_file_part
_folder_stats = _settings.folder_stats
_format_bytes = _settings.format_bytes
_next_available_path = _settings.next_available_path
_build_atempo_filter = _audio.build_atempo_filter
_looks_like_missing_original_audio = _audio.looks_like_missing_original_audio
_mimo_tts_user_instruction = _mimo.mimo_tts_user_instruction
_resolve_mimo_chat_completions_url = _mimo.resolve_mimo_chat_completions_url
_extract_mimo_audio_data = _mimo.extract_mimo_audio_data
_format_http_response_error = _mimo.format_http_response_error
_http_response_excerpt = _mimo.http_response_excerpt
_truncate_text = _mimo.truncate_text


def local_tts_dir() -> Path:
    configured = os.getenv("AI8VIDEO_LOCAL_TTS_DIR")
    root = Path(configured) if configured else USER_FILE_ROOT / LOCAL_TTS_DIR_NAME
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root.resolve()


def legacy_local_tts_dir() -> Path:
    return (USER_FILE_ROOT / LEGACY_LOCAL_TTS_DIR_NAME).resolve()


def _maybe_migrate_legacy_local_tts_dir(target: Path) -> None:
    _settings.migrate_legacy_local_tts_dir(
        target,
        legacy_local_tts_dir(),
        configured=bool(os.getenv("AI8VIDEO_LOCAL_TTS_DIR")),
    )


def ensure_local_tts_dir() -> Path:
    ensure_user_file_root()
    root = local_tts_dir()
    _maybe_migrate_legacy_local_tts_dir(root)
    (root / LOCAL_TTS_OUTPUT_DIR_NAME).mkdir(parents=True, exist_ok=True)
    _maybe_migrate_legacy_voice_clone_dir(root)
    (root / LOCAL_TTS_CLONE_LIBRARY_DIR_NAME).mkdir(parents=True, exist_ok=True)
    return root


def local_tts_settings_path() -> Path:
    return local_tts_dir() / LOCAL_TTS_SETTINGS_NAME


def local_tts_output_dir() -> Path:
    return local_tts_dir() / LOCAL_TTS_OUTPUT_DIR_NAME


def local_tts_voice_clone_dir() -> Path:
    return local_tts_dir() / LOCAL_TTS_CLONE_LIBRARY_DIR_NAME


def _maybe_migrate_legacy_voice_clone_dir(root: Path) -> None:
    _settings.migrate_legacy_voice_clone_dir(root)


def local_tts_status() -> dict[str, Any]:
    ensure_local_tts_dir()
    return _settings.build_local_tts_status(
        _read_local_tts_settings(),
        local_tts_output_dir(),
        local_tts_voice_clone_dir(),
    )


def update_local_tts_settings(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_local_tts_dir()
    updated = _settings.update_settings_values(
        _read_local_tts_settings(),
        payload,
        local_tts_voice_clone_dir(),
    )
    _write_local_tts_settings(updated)
    return local_tts_status()


def attach_local_tts_to_video(
    video_path: Path | str,
    *,
    narration_text: str | None,
    video_index: int | None = None,
    job_id: str | None = None,
    ffmpeg_bin: str | None = None,
    preserve_original_audio: bool = True,
) -> dict[str, Any]:
    status, video, text, error = _attachment_preflight(video_path, narration_text)
    if error is not None:
        return error
    duration_target = _tts_duration_target_for_video(video, ffmpeg_bin=ffmpeg_bin)
    synth_settings = _synthesis_settings(status, duration_target)
    audio_path = _new_tts_output_path(video_index, job_id)
    synth = synthesize_local_tts(text, audio_path, settings=synth_settings, ffmpeg_bin=ffmpeg_bin)
    if synth.get("status") != "generated":
        return {"enabled": True, "status": "failed", "reason": synth.get("reason") or "tts failed"}
    duration_fit = _fit_tts_audio_to_video_duration(
        audio_path,
        video,
        target_duration_seconds=duration_target.get("targetDurationSeconds"),
        ffmpeg_bin=ffmpeg_bin,
    )
    if duration_fit.get("status") == "failed":
        return _duration_fit_failure(audio_path, duration_fit)
    mixed = _attach_generated_audio(video, audio_path, status, preserve_original_audio, ffmpeg_bin)
    return {
        **mixed,
        "enabled": True,
        "audioPath": str(audio_path),
        "textChars": len(text),
        "engine": status["engine"],
        "voice": status["voice"],
        "rate": status["rate"],
        **duration_target,
        "ttsDurationFit": duration_fit,
    }


def _attachment_preflight(
    video_path: Path | str,
    narration_text: str | None,
) -> tuple[dict[str, Any], Path, str, dict[str, Any] | None]:
    status = local_tts_status()
    video = Path(video_path)
    if not status["enabled"]:
        return status, video, "", {"enabled": False, "status": "skipped", "reason": "local tts disabled"}
    if not status["available"]:
        error = {"enabled": True, "status": "failed", "reason": status["availabilityReason"]}
        return status, video, "", error
    if not video.is_file():
        return status, video, "", {"enabled": True, "status": "skipped", "reason": "video file missing"}
    text = prepare_narration_text(narration_text or "")
    if not text:
        return status, video, text, {"enabled": True, "status": "skipped", "reason": "empty narration text"}
    return status, video, text, None


def _synthesis_settings(status: dict[str, Any], duration_target: dict[str, Any]) -> dict[str, Any]:
    return {
        **status,
        "videoDurationSeconds": duration_target.get("videoDurationSeconds"),
        "targetDurationSeconds": duration_target.get("targetDurationSeconds"),
        "durationAutoSpeed": True,
    }


def _new_tts_output_path(video_index: int | None, job_id: str | None) -> Path:
    output_dir = local_tts_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    video_part = f"e{video_index:02d}" if video_index is not None else "video"
    return output_dir / f"{stamp}-{video_part}-{_safe_file_part(job_id or 'local')}.m4a"


def _duration_fit_failure(audio_path: Path, duration_fit: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": True,
        "status": "failed",
        "reason": duration_fit.get("reason") or "tts duration fit failed",
        "audioPath": str(audio_path),
        "ttsDurationFit": duration_fit,
    }


def _attach_generated_audio(
    video: Path,
    audio_path: Path,
    status: dict[str, Any],
    preserve_original_audio: bool,
    ffmpeg_bin: str | None,
) -> dict[str, Any]:
    if preserve_original_audio:
        return _mix_tts_audio(video, audio_path, settings=status, ffmpeg_bin=ffmpeg_bin)
    narration_volume = _format_volume(status.get("volume"), DEFAULT_LOCAL_TTS_VOLUME)
    mixed = _replace_video_audio_with_tts(video, audio_path, narration_volume, resolve_ffmpeg_bin(ffmpeg_bin))
    if mixed.get("status") == "mixed":
        mixed["originalAudio"] = "replaced"
    return mixed


def synthesize_local_tts(
    text: str,
    output_path: Path | str,
    *,
    settings: dict[str, Any] | None = None,
    ffmpeg_bin: str | None = None,
    output_volume: float | None = None,
) -> dict[str, Any]:
    current = settings or local_tts_status()
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_audio = target.with_name(f"{target.stem}.tts.tmp.wav")
    _remove_tts_candidates(target, temp_audio)
    try:
        if _clean_engine(current.get("engine")) != "mimo-api":
            raise RuntimeError("当前仅保留 MiMo TTS")
        _synthesize_with_mimo_api(text, temp_audio, current)
        _convert_audio_to_m4a(
            temp_audio,
            target,
            ffmpeg_bin=ffmpeg_bin,
            volume_multiplier=output_volume,
        )
    except Exception as exc:
        _remove_tts_candidates(target, temp_audio)
        return {"status": "failed", "reason": str(exc)[-500:]}
    finally:
        _remove_tts_candidates(temp_audio)
    return {"status": "generated", "path": str(target), "sizeBytes": target.stat().st_size}


def _remove_tts_candidates(*paths: Path) -> None:
    for path in paths:
        if not path.exists():
            continue
        try:
            path.unlink()
        except OSError:
            pass


def _synthesize_with_mimo_api(text: str, output_path: Path, settings: dict[str, Any]) -> None:
    normalized = {**settings, "voice": _clean_voice_selection(settings.get("voice"))}
    _mimo.synthesize_with_mimo_api(
        text,
        output_path,
        normalized,
        voice_sample_resolver=_voice_clone_sample_path,
    )


def _convert_audio_to_m4a(
    source: Path,
    target: Path,
    *,
    ffmpeg_bin: str | None = None,
    volume_multiplier: float | None = None,
) -> None:
    _audio.convert_audio_to_m4a(
        source,
        target,
        ffmpeg=resolve_ffmpeg_bin(ffmpeg_bin),
        volume_multiplier=volume_multiplier,
    )


def _fit_tts_audio_to_video_duration(
    audio: Path,
    video: Path,
    *,
    target_duration_seconds: float | int | str | None = None,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    return _audio.fit_tts_audio_to_video_duration(
        audio,
        video,
        target_duration_seconds=target_duration_seconds,
        runtime=_audio_runtime(ffmpeg_bin),
    )


def _tts_duration_target_for_video(video: Path, *, ffmpeg_bin: str | None = None) -> dict[str, Any]:
    return _audio.tts_duration_target_for_video(
        video,
        ffmpeg_bin=ffmpeg_bin,
        probe_duration=probe_media_duration_seconds,
    )


def _mix_tts_audio(
    video: Path,
    audio: Path,
    *,
    settings: dict[str, Any],
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    return _audio.mix_tts_audio(
        video,
        audio,
        settings=settings,
        runtime=_audio_runtime(ffmpeg_bin),
        replace_audio=_replace_video_audio_with_tts,
    )


def _replace_video_audio_with_tts(video: Path, audio: Path, narration_volume: str, ffmpeg: str) -> dict[str, Any]:
    return _audio.replace_video_audio_with_tts(
        video,
        audio,
        narration_volume,
        runtime=_audio.AudioRuntime(ffmpeg, probe_media_duration_seconds),
    )


def _audio_runtime(ffmpeg_bin: str | None) -> _audio.AudioRuntime:
    return _audio.AudioRuntime(resolve_ffmpeg_bin(ffmpeg_bin), probe_media_duration_seconds)


def _read_local_tts_settings() -> dict[str, Any]:
    return _settings.read_local_tts_settings(local_tts_settings_path())


def _write_local_tts_settings(payload: dict[str, Any]) -> None:
    _settings.write_local_tts_settings(
        local_tts_settings_path(),
        payload,
        local_tts_voice_clone_dir(),
    )


def _voice_clone_items() -> list[dict[str, Any]]:
    ensure_local_tts_dir()
    return _settings.voice_clone_items(local_tts_voice_clone_dir())


def _voice_clone_sample_path(value: Any) -> Path | None:
    return _settings.voice_clone_sample_path(value, local_tts_voice_clone_dir())


def local_tts_voice_clone_cache_signature(value: Any) -> str:
    return _settings.voice_clone_cache_signature(value, local_tts_voice_clone_dir())


def save_local_tts_voice_clone_upload(upload: Any, *, ffmpeg_bin: str | None = None) -> dict[str, Any]:
    source_name = resolve_upload_filename(upload)
    if not source_name:
        raise ValueError("请选择 MP3、WAV 或视频文件")
    suffix = Path(source_name).suffix.lower()
    if suffix not in LOCAL_TTS_CLONE_AUDIO_EXTENSIONS and suffix not in LOCAL_TTS_CLONE_VIDEO_EXTENSIONS:
        raise ValueError("音色克隆只支持 MP3、WAV 或常见视频文件")
    library_dir = local_tts_voice_clone_dir()
    library_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{Path(source_name).stem}{LOCAL_TTS_CLONE_STORAGE_EXTENSION}"
    target_path = _next_available_path(library_dir, target_name)
    temp_source = library_dir / f".uploading-{datetime.now().strftime('%Y%m%d%H%M%S%f')}{suffix}"
    temp_target = target_path.with_name(f".uploading-{target_path.name}")
    _remove_tts_candidates(temp_source, temp_target)
    try:
        upload.save(str(temp_source), overwrite=True)
        _prepare_voice_clone_sample(temp_source, temp_target, ffmpeg_bin=ffmpeg_bin)
        os.replace(temp_target, target_path)
    finally:
        _remove_tts_candidates(temp_source, temp_target)
    current = _read_local_tts_settings()
    current["voice"] = _voice_clone_value(target_path.name)
    _write_local_tts_settings(current)
    return local_tts_status()


def _prepare_voice_clone_sample(source: Path, target: Path, *, ffmpeg_bin: str | None = None) -> None:
    _audio.prepare_voice_clone_sample(source, target, ffmpeg=resolve_ffmpeg_bin(ffmpeg_bin))


def _mimo_voice_options() -> list[dict[str, str]]:
    return _settings.mimo_voice_options(_voice_clone_items())


def _clean_voice_selection(value: Any) -> str:
    return _settings.clean_voice_selection(value, local_tts_voice_clone_dir())
