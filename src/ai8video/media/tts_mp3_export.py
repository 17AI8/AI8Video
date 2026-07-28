from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root
from ai8video.media.ffmpeg_utils import probe_media_duration_seconds, resolve_ffmpeg_bin
from ai8video.media.tts_timeline_review import (
    build_tts_timeline_audio_filter,
    normalize_tts_timeline_chunks,
)


TTS_MP3_EXPORT_SETTINGS_PATH = (USER_FILE_ROOT / "TTS" / "export-settings.json").resolve()
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def load_tts_mp3_export_directory() -> Path:
    payload = _load_settings()
    configured_value = str(payload.get("exportDirectory") or "").strip()
    if configured_value:
        configured = Path(configured_value).expanduser()
        if configured.is_dir():
            return configured.resolve()
    downloads = Path.home() / "Downloads"
    return downloads.resolve() if downloads.is_dir() else Path.home().resolve()


def choose_tts_mp3_export_path(relative_key: str) -> Path | None:
    export_directory = load_tts_mp3_export_directory()
    suggested_path = _unique_export_path(export_directory, relative_key)
    selected = _pick_native_save_path(suggested_path)
    if selected is None:
        return None
    target = _normalize_mp3_target(selected)
    _save_settings({"exportDirectory": str(target.parent)})
    return target


def export_tts_timeline_mp3(
    audio_path: Path,
    chunks: Any,
    *,
    duration_seconds: float,
    tts_volume: float,
    export_path: Path,
) -> dict[str, Any]:
    if not audio_path.is_file():
        raise FileNotFoundError("TTS 配音文件已删除")
    target = _normalize_mp3_target(export_path)
    normalized = _normalize_export_timeline(audio_path, chunks, duration_seconds, None)
    _render_tts_mp3(audio_path, target, normalized, duration_seconds, tts_volume, None)
    return {
        "ok": True,
        "canceled": False,
        "exportDirectory": str(target.parent),
        "outputPath": str(target),
        "fileName": target.name,
        "sizeBytes": target.stat().st_size,
        "durationSeconds": round(float(duration_seconds), 3),
        "timelineChunks": normalized,
    }


def export_segmented_audio_mp3(
    segments: list[dict[str, Any]],
    *,
    duration_seconds: float,
    export_path: Path,
) -> dict[str, Any]:
    target = _normalize_mp3_target(export_path)
    normalized = _normalize_segmented_audio(segments, duration_seconds)
    _render_segmented_audio_mp3(normalized, duration_seconds, target)
    return {
        "ok": True,
        "canceled": False,
        "exportDirectory": str(target.parent),
        "outputPath": str(target),
        "fileName": target.name,
        "sizeBytes": target.stat().st_size,
        "durationSeconds": round(float(duration_seconds), 3),
        "segmentCount": len(normalized),
    }


def _normalize_segmented_audio(
    segments: list[dict[str, Any]],
    duration_seconds: float,
) -> list[dict[str, Any]]:
    duration = max(0.1, float(duration_seconds))
    normalized: list[dict[str, Any]] = []
    for segment in segments:
        audio_path = Path(str(segment.get("audioPath") or "")).resolve()
        start = max(0.0, float(segment.get("start") or 0))
        end = min(duration, float(segment.get("end") or start))
        if audio_path.is_file() and end > start:
            normalized.append({"audioPath": audio_path, "start": start, "end": end})
    return normalized


def _build_segmented_audio_filter(segments: list[dict[str, Any]], duration_seconds: float) -> str:
    filters: list[str] = []
    labels: list[str] = []
    for index, segment in enumerate(segments):
        slot_duration = float(segment["end"]) - float(segment["start"])
        delay_ms = max(0, round(float(segment["start"]) * 1000))
        label = f"slot{index}"
        filters.append(
            f"[{index}:a]atrim=0:{slot_duration:.3f},asetpts=PTS-STARTPTS,"
            f"apad,atrim=0:{slot_duration:.3f},adelay={delay_ms}:all=1[{label}]"
        )
        labels.append(f"[{label}]")
    mixed = "".join(labels)
    filters.append(
        f"{mixed}amix=inputs={len(labels)}:duration=longest:dropout_transition=0,"
        f"atrim=0:{float(duration_seconds):.3f}[aout]"
    )
    return ";".join(filters)


def _render_segmented_audio_mp3(
    segments: list[dict[str, Any]],
    duration_seconds: float,
    target: Path,
) -> None:
    temporary = target.with_name(f".{target.stem}.exporting.mp3")
    temporary.unlink(missing_ok=True)
    ffmpeg = resolve_ffmpeg_bin(None)
    inputs = [part for segment in segments for part in ("-i", str(segment["audioPath"]))]
    if segments:
        audio_source = ["-filter_complex", _build_segmented_audio_filter(segments, duration_seconds), "-map", "[aout]"]
    else:
        inputs = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        audio_source = ["-map", "0:a"]
    try:
        if _ffmpeg_supports_libmp3lame(ffmpeg):
            command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *inputs, *audio_source,
                       "-vn", "-c:a", "libmp3lame", "-b:a", "192k", "-t", f"{duration_seconds:.3f}", str(temporary)]
            _run_export_command(command)
        else:
            _render_segmented_audio_with_lame(ffmpeg, inputs, audio_source, duration_seconds, temporary)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("MP3 导出完成但未生成文件")
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _render_segmented_audio_with_lame(
    ffmpeg: str,
    inputs: list[str],
    audio_source: list[str],
    duration_seconds: float,
    target: Path,
) -> None:
    lame = _resolve_lame_bin()
    if not lame:
        raise RuntimeError("当前电脑缺少 MP3 编码器（FFmpeg libmp3lame 或 LAME）")
    wav_target = target.with_suffix(".wav")
    wav_target.unlink(missing_ok=True)
    try:
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *inputs, *audio_source,
                   "-vn", "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2",
                   "-t", f"{duration_seconds:.3f}", str(wav_target)]
        _run_export_command(command)
        _run_export_command([lame, "--silent", "-b", "192", str(wav_target), str(target)])
    finally:
        wav_target.unlink(missing_ok=True)


def _normalize_export_timeline(
    audio_path: Path,
    chunks: Any,
    duration_seconds: float,
    ffmpeg_bin: str | None,
) -> list[dict[str, Any]]:
    audio_duration = probe_media_duration_seconds(audio_path, ffmpeg_bin=ffmpeg_bin)
    return normalize_tts_timeline_chunks(
        chunks,
        audio_duration_seconds=audio_duration,
        video_duration_seconds=duration_seconds,
    )


def _render_tts_mp3(
    audio_path: Path,
    target: Path,
    chunks: list[dict[str, Any]],
    duration_seconds: float,
    tts_volume: float,
    ffmpeg_bin: str | None,
) -> None:
    temporary = target.with_name(f".{target.stem}.exporting.mp3")
    temporary.unlink(missing_ok=True)
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    filter_complex = build_tts_timeline_audio_filter(
        chunks,
        duration_seconds,
        tts_volume,
    )
    try:
        if _ffmpeg_supports_libmp3lame(ffmpeg):
            _render_mp3_with_ffmpeg(
                ffmpeg,
                audio_path,
                temporary,
                filter_complex,
                duration_seconds,
            )
        else:
            _render_mp3_with_lame(
                ffmpeg,
                audio_path,
                temporary,
                filter_complex,
                duration_seconds,
            )
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("MP3 导出完成但未生成文件")
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _render_mp3_with_ffmpeg(
    ffmpeg: str,
    audio_path: Path,
    target: Path,
    filter_complex: str,
    duration_seconds: float,
) -> None:
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(audio_path), "-filter_complex", filter_complex,
        "-map", "[aout]", "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
        "-t", f"{float(duration_seconds):.3f}", str(target),
    ]
    _run_export_command(command)


def _render_mp3_with_lame(
    ffmpeg: str,
    audio_path: Path,
    target: Path,
    filter_complex: str,
    duration_seconds: float,
) -> None:
    lame = _resolve_lame_bin()
    if not lame:
        raise RuntimeError("当前电脑缺少 MP3 编码器（FFmpeg libmp3lame 或 LAME）")
    wav_target = target.with_suffix(".wav")
    wav_target.unlink(missing_ok=True)
    render_command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(audio_path), "-filter_complex", filter_complex,
        "-map", "[aout]", "-vn", "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2",
        "-t", f"{float(duration_seconds):.3f}", str(wav_target),
    ]
    try:
        _run_export_command(render_command)
        _run_export_command([lame, "--silent", "-b", "192", str(wav_target), str(target)])
    finally:
        wav_target.unlink(missing_ok=True)


def _ffmpeg_supports_libmp3lame(ffmpeg: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = f"{result.stdout}\n{result.stderr}"
    return any(
        len(parts) >= 2 and parts[1] == "libmp3lame"
        for parts in (line.split() for line in output.splitlines())
    )


def _resolve_lame_bin() -> str | None:
    resolved = shutil.which("lame")
    if resolved:
        return resolved
    candidates = [Path("/opt/homebrew/bin/lame"), Path("/usr/local/bin/lame")]
    for root in (Path("/opt/homebrew/Cellar/lame"), Path("/usr/local/Cellar/lame")):
        if root.is_dir():
            candidates.extend(sorted(root.glob("*/bin/lame"), reverse=True))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _run_export_command(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(message[-500:] or "MP3 导出失败") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("MP3 导出超时") from exc


def _pick_native_save_path(initial_path: Path) -> Path | None:
    if sys.platform == "darwin":
        return _pick_macos_save_path(initial_path)
    if os.name == "nt":
        return _pick_windows_save_path(initial_path)
    return _pick_linux_save_path(initial_path)


def _pick_macos_save_path(initial_path: Path) -> Path | None:
    script = (
        'on run argv\n'
        'set defaultFolder to POSIX file (item 1 of argv) as alias\n'
        'try\n'
        'set chosenFile to choose file name with prompt "保存 TTS MP3" default location defaultFolder default name (item 2 of argv)\n'
        'return POSIX path of chosenFile\n'
        'on error number -128\n'
        'return ""\n'
        'end try\n'
        'end run'
    )
    result = subprocess.run(
        ["osascript", "-e", script, str(initial_path.parent), initial_path.name],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "系统保存窗口打开失败").strip())
    value = result.stdout.strip()
    return Path(value) if value else None


def _pick_windows_save_path(initial_path: Path) -> Path | None:
    initial_directory = str(initial_path.parent).replace("'", "''")
    initial_name = initial_path.name.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$dialog = New-Object System.Windows.Forms.SaveFileDialog; "
        f"$dialog.InitialDirectory = '{initial_directory}'; "
        f"$dialog.FileName = '{initial_name}'; "
        "$dialog.Filter = 'MP3 音频 (*.mp3)|*.mp3'; "
        "$dialog.DefaultExt = 'mp3'; $dialog.AddExtension = $true; $dialog.OverwritePrompt = $true; "
        "if ($dialog.ShowDialog() -eq 'OK') { Write-Output $dialog.FileName }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "系统保存窗口打开失败").strip())
    value = result.stdout.strip()
    return Path(value) if value else None


def _pick_linux_save_path(initial_path: Path) -> Path | None:
    zenity = shutil.which("zenity")
    if not zenity:
        raise RuntimeError("当前系统缺少可用的文件保存选择器")
    result = subprocess.run(
        [
            zenity,
            "--file-selection",
            "--save",
            "--confirm-overwrite",
            "--title=保存 TTS MP3",
            "--file-filter=MP3 音频 | *.mp3",
            f"--filename={initial_path}",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "系统保存窗口打开失败").strip())
    value = result.stdout.strip()
    return Path(value) if value else None


def _normalize_mp3_target(raw_path: Path) -> Path:
    target = raw_path.expanduser()
    if not target.name.strip():
        raise ValueError("请输入 MP3 文件名")
    if target.suffix.lower() != ".mp3":
        target = (
            target.with_suffix(".mp3")
            if target.suffix
            else target.with_name(f"{target.name}.mp3")
        )
    resolved = target.resolve()
    if not resolved.parent.is_dir():
        raise ValueError("MP3 保存文件夹不存在")
    return resolved


def _unique_export_path(export_directory: Path, relative_key: str) -> Path:
    stem = _INVALID_FILENAME_CHARS.sub("-", Path(str(relative_key or "")).stem).strip(" .-")
    stem = stem or "TTS配音"
    candidate = export_directory / f"{stem}-TTS配音.mp3"
    if not candidate.exists():
        return candidate
    for index in range(2, 10_000):
        candidate = export_directory / f"{stem}-TTS配音-{index}.mp3"
        if not candidate.exists():
            return candidate
    raise RuntimeError("导出目录中的同名 MP3 文件过多")


def _load_settings() -> dict[str, Any]:
    try:
        payload = json.loads(TTS_MP3_EXPORT_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_settings(payload: dict[str, Any]) -> None:
    ensure_user_file_root()
    TTS_MP3_EXPORT_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = TTS_MP3_EXPORT_SETTINGS_PATH.with_name(f".{TTS_MP3_EXPORT_SETTINGS_PATH.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, TTS_MP3_EXPORT_SETTINGS_PATH)
