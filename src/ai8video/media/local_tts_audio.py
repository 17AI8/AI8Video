from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai8video.media.local_tts_settings import (
    DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME,
    DEFAULT_LOCAL_TTS_VOLUME,
    LOCAL_TTS_CLONE_AUDIO_FILTER,
    LOCAL_TTS_CLONE_MAX_SECONDS,
    LOCAL_TTS_DURATION_FIT_TOLERANCE_SECONDS,
    LOCAL_TTS_END_GUARD_SECONDS,
    LOCAL_TTS_LOUDNESS_FILTER,
    clean_tts_duration_seconds,
    format_volume,
)


DurationProbe = Callable[..., float | None]
ReplaceAudio = Callable[[Path, Path, str, str], dict[str, Any]]


@dataclass(frozen=True)
class AudioRuntime:
    ffmpeg: str
    probe_duration: DurationProbe


@dataclass(frozen=True)
class FitMetrics:
    video_duration: float | None
    audio_duration: float
    target_duration: float
    tempo: float


def convert_audio_to_m4a(
    source: Path,
    target: Path,
    *,
    ffmpeg: str,
    volume_multiplier: float | None = None,
) -> None:
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vn"]
    audio_filters = [LOCAL_TTS_LOUDNESS_FILTER]
    if volume_multiplier is not None:
        audio_filters.append(f"volume={format_volume(volume_multiplier, DEFAULT_LOCAL_TTS_VOLUME)}")
    cmd.extend(["-filter:a", ",".join(audio_filters)])
    cmd.extend(["-c:a", "aac", "-b:a", "128k", str(target)])
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)


def fit_tts_audio_to_video_duration(
    audio: Path,
    video: Path,
    *,
    target_duration_seconds: float | int | str | None,
    runtime: AudioRuntime,
) -> dict[str, Any]:
    video_duration = runtime.probe_duration(video, ffmpeg_bin=runtime.ffmpeg)
    audio_duration = runtime.probe_duration(audio, ffmpeg_bin=runtime.ffmpeg)
    explicit_target = clean_tts_duration_seconds(target_duration_seconds)
    target_duration = _resolve_target_duration(explicit_target, video_duration)
    if not target_duration or not audio_duration:
        return _duration_unavailable(video_duration, audio_duration, explicit_target)
    target_duration = max(0.1, float(target_duration))
    if float(audio_duration) <= target_duration + LOCAL_TTS_DURATION_FIT_TOLERANCE_SECONDS:
        return _already_fits(video_duration, audio_duration, target_duration)
    return _render_fitted_audio(audio, video_duration, audio_duration, target_duration, runtime.ffmpeg)


def _resolve_target_duration(explicit_target: float | None, video_duration: float | None) -> float | None:
    target_duration = explicit_target or clean_tts_duration_seconds(video_duration)
    if target_duration and video_duration:
        return min(float(target_duration), float(video_duration))
    return target_duration


def _duration_unavailable(
    video_duration: float | None,
    audio_duration: float | None,
    explicit_target: float | None,
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": "duration unavailable",
        "videoDurationSeconds": video_duration,
        "audioDurationSeconds": audio_duration,
        "targetDurationSeconds": explicit_target,
    }


def _already_fits(
    video_duration: float | None,
    audio_duration: float,
    target_duration: float,
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": "audio already fits",
        "videoDurationSeconds": round(float(video_duration), 3) if video_duration else None,
        "audioDurationSeconds": round(float(audio_duration), 3),
        "targetDurationSeconds": round(float(target_duration), 3),
    }


def _render_fitted_audio(
    audio: Path,
    video_duration: float | None,
    audio_duration: float,
    target_duration: float,
    ffmpeg: str,
) -> dict[str, Any]:
    tempo = max(0.5, float(audio_duration) / target_duration)
    metrics = FitMetrics(video_duration, audio_duration, target_duration, tempo)
    atempo_filter = build_atempo_filter(tempo)
    temp_audio = audio.with_name(f"{audio.stem}.fit.tmp{audio.suffix or '.m4a'}")
    if temp_audio.exists():
        temp_audio.unlink()
    cmd = _fit_audio_command(ffmpeg, audio, temp_audio, atempo_filter, target_duration)
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        os.replace(temp_audio, audio)
    except Exception as exc:
        _remove_if_exists(temp_audio)
        return _fit_result("failed", metrics, reason=str(exc)[-500:])
    return _fit_result("fitted", metrics, filter_value=atempo_filter)


def _fit_audio_command(
    ffmpeg: str,
    audio: Path,
    temp_audio: Path,
    atempo_filter: str,
    target_duration: float,
) -> list[str]:
    return [
        ffmpeg, "-y", "-i", str(audio), "-vn", "-filter:a", atempo_filter,
        "-c:a", "aac", "-b:a", "128k", "-t", f"{target_duration:.3f}", str(temp_audio),
    ]


def _fit_result(
    status: str,
    metrics: FitMetrics,
    *,
    reason: str | None = None,
    filter_value: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "videoDurationSeconds": round(float(metrics.video_duration), 3) if metrics.video_duration else None,
        "audioDurationSeconds": round(float(metrics.audio_duration), 3),
        "targetDurationSeconds": round(float(metrics.target_duration), 3),
        "tempo": round(metrics.tempo, 4),
    }
    if reason is not None:
        result["reason"] = reason
    if filter_value is not None:
        result["filter"] = filter_value
    return result


def tts_duration_target_for_video(
    video: Path,
    *,
    ffmpeg_bin: str | None,
    probe_duration: DurationProbe,
) -> dict[str, Any]:
    video_duration = clean_tts_duration_seconds(probe_duration(video, ffmpeg_bin=ffmpeg_bin))
    if not video_duration:
        return {"videoDurationSeconds": None, "targetDurationSeconds": None, "guardSeconds": None}
    guard = min(LOCAL_TTS_END_GUARD_SECONDS, max(0.0, float(video_duration) - 0.1))
    target_duration = max(0.1, float(video_duration) - guard)
    return {
        "videoDurationSeconds": round(float(video_duration), 3),
        "targetDurationSeconds": round(target_duration, 3),
        "guardSeconds": round(guard, 3),
    }


def build_atempo_filter(tempo: float) -> str:
    remaining = max(0.5, min(100.0, float(tempo or 1.0)))
    factors: list[float] = []
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={factor:.6g}" for factor in factors)


def mix_tts_audio(
    video: Path,
    audio: Path,
    *,
    settings: dict[str, Any],
    runtime: AudioRuntime,
    replace_audio: ReplaceAudio,
) -> dict[str, Any]:
    temp_video = video.with_name(f"{video.stem}.with-tts.tmp{video.suffix or '.mp4'}")
    _remove_if_exists(temp_video)
    video_duration = runtime.probe_duration(video, ffmpeg_bin=runtime.ffmpeg)
    if not video_duration:
        return {"status": "failed", "reason": "无法读取视频时长，未执行 TTS 混音"}
    narration_volume = format_volume(settings.get("volume"), DEFAULT_LOCAL_TTS_VOLUME)
    original_volume = format_volume(settings.get("originalAudioVolume"), DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME)
    cmd = _mix_audio_command(
        video,
        audio,
        temp_video,
        runtime,
        (narration_volume, original_volume, video_duration),
    )
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        os.replace(temp_video, video)
        return _mixed_result(video, narration_volume, original_volume)
    except subprocess.CalledProcessError as exc:
        _remove_if_exists(temp_video)
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        if looks_like_missing_original_audio(message):
            return replace_audio(video, audio, narration_volume, runtime.ffmpeg)
        return {"status": "failed", "reason": message[-500:]}
    except Exception as exc:
        _remove_if_exists(temp_video)
        return {"status": "failed", "reason": str(exc)[-500:]}


def _mix_audio_command(
    video: Path,
    audio: Path,
    temp_video: Path,
    runtime: AudioRuntime,
    mix_values: tuple[str, str, float],
) -> list[str]:
    narration_volume, original_volume, video_duration = mix_values
    audio_filter = (
        f"[0:a:0]volume={original_volume},apad[orig];[1:a:0]volume={narration_volume},apad[tts];"
        "[orig][tts]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]"
    )
    return [
        runtime.ffmpeg, "-y", "-i", str(video), "-i", str(audio), "-filter_complex", audio_filter,
        "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
        "-t", f"{video_duration:.3f}", "-movflags", "+faststart", str(temp_video),
    ]


def _mixed_result(video: Path, narration_volume: str, original_volume: str) -> dict[str, Any]:
    return {
        "status": "mixed",
        "video": str(video),
        "originalAudio": "ducked",
        "ttsVolume": float(narration_volume),
        "originalAudioVolume": float(original_volume),
    }


def replace_video_audio_with_tts(
    video: Path,
    audio: Path,
    narration_volume: str,
    *,
    runtime: AudioRuntime,
) -> dict[str, Any]:
    temp_video = video.with_name(f"{video.stem}.tts-only.tmp{video.suffix or '.mp4'}")
    _remove_if_exists(temp_video)
    video_duration = runtime.probe_duration(video, ffmpeg_bin=runtime.ffmpeg)
    if not video_duration:
        return {"status": "failed", "reason": "无法读取视频时长，未执行 TTS 替换"}
    cmd = _replace_audio_command(video, audio, temp_video, runtime, (video_duration, narration_volume))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        os.replace(temp_video, video)
    except Exception as exc:
        _remove_if_exists(temp_video)
        return {"status": "failed", "reason": str(exc)[-500:]}
    return {
        "status": "mixed",
        "video": str(video),
        "originalAudio": "missing",
        "fallback": "tts_only",
        "ttsVolume": float(narration_volume),
    }


def _replace_audio_command(
    video: Path,
    audio: Path,
    temp_video: Path,
    runtime: AudioRuntime,
    replace_values: tuple[float, str],
) -> list[str]:
    video_duration, narration_volume = replace_values
    return [
        runtime.ffmpeg, "-y", "-i", str(video), "-i", str(audio), "-filter_complex",
        f"[1:a:0]volume={narration_volume},apad[aout]", "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-t", f"{video_duration:.3f}",
        "-movflags", "+faststart", str(temp_video),
    ]


def prepare_voice_clone_sample(source: Path, target: Path, *, ffmpeg: str) -> None:
    cmd = [
        ffmpeg, "-y", "-i", str(source), "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "48000",
        "-af", LOCAL_TTS_CLONE_AUDIO_FILTER, "-c:a", "pcm_s16le", "-t", str(LOCAL_TTS_CLONE_MAX_SECONDS),
        str(target),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(message[-500:] or "音色克隆样本处理失败") from exc


def looks_like_missing_original_audio(message: str) -> bool:
    text = str(message or "").lower()
    return (
        "matches no streams" in text
        or "stream specifier ':a" in text
        or "stream specifier a" in text
        or ("0:a:0" in text and "not" in text and "match" in text)
    )


def _remove_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()
