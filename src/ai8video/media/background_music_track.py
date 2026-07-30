from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ai8video.media.ffmpeg_utils import probe_media_duration_seconds, resolve_ffmpeg_bin


TRACK_SCHEMA = "hidden-bgm-track-v1"


def hidden_bgm_base_path(result_root: Path, relative_key: str) -> Path:
    relative = Path(relative_key)
    return result_root / ".media-tracks" / "bgm-base" / relative


def save_hidden_bgm_base(source: Path, result_root: Path, relative_key: str) -> Path:
    target = hidden_bgm_base_path(result_root, relative_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, target)
    return target


def hidden_bgm_track_metadata(base_path: Path, music: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": TRACK_SCHEMA,
        "hidden": True,
        "baseVideoPath": str(base_path),
        "musicId": str(music.get("selectedId") or music.get("id") or ""),
        "musicPath": str(music.get("path") or ""),
        "musicName": str(music.get("name") or ""),
        "volume": float(music.get("volume") or 0),
    }


def merge_hidden_bgm_tracks(
    video_path: Path,
    tracks: list[dict[str, Any]],
    durations: list[float],
    *,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    segments = build_hidden_bgm_timeline(tracks, durations)
    if not segments:
        return {"enabled": False, "status": "skipped", "reason": "no background music tracks"}
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    output = video_path.with_name(f".{video_path.name}.bgm-track.tmp.mp4")
    command = _merged_bgm_command(ffmpeg, video_path, output, segments)
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=240)
        os.replace(output, video_path)
    except subprocess.CalledProcessError as exc:
        output.unlink(missing_ok=True)
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"合并背景音乐轨道失败：{detail}") from exc
    return {"enabled": True, "status": "mixed", "schema": TRACK_SCHEMA, "segments": segments}


def track_source(track: dict[str, Any], fallback: Path) -> Path:
    candidate = Path(str(track.get("baseVideoPath") or ""))
    return candidate if candidate.is_file() else fallback


def track_duration(path: Path) -> float:
    return float(probe_media_duration_seconds(path) or 0)


def build_hidden_bgm_timeline(tracks: list[dict[str, Any]], durations: list[float]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    timeline_start = 0.0
    previous_music = ""
    continuous_offset = 0.0
    for track, duration in zip(tracks, durations):
        nested = track.get("segments") if isinstance(track.get("segments"), list) else []
        if nested:
            for item in nested:
                segment = dict(item)
                segment["startSeconds"] = round(timeline_start + float(item.get("startSeconds") or 0), 3)
                segments.append(segment)
            previous_music = str(nested[-1].get("musicPath") or "")
            continuous_offset = float(nested[-1].get("sourceOffsetSeconds") or 0) + float(
                nested[-1].get("durationSeconds") or 0
            )
            timeline_start += max(0.0, duration)
            continue
        music_path = str(track.get("musicPath") or "")
        if music_path and Path(music_path).is_file() and duration > 0:
            continuous_offset = continuous_offset if music_path == previous_music else 0.0
            segments.append({
                "musicPath": music_path,
                "musicName": str(track.get("musicName") or Path(music_path).name),
                "volume": float(track.get("volume") or 0),
                "startSeconds": round(timeline_start, 3),
                "durationSeconds": round(duration, 3),
                "sourceOffsetSeconds": round(continuous_offset, 3),
            })
            continuous_offset += duration
        else:
            continuous_offset = 0.0
        previous_music = music_path
        timeline_start += max(0.0, duration)
    return segments


def _merged_bgm_command(ffmpeg: str, video: Path, output: Path, segments: list[dict[str, Any]]) -> list[str]:
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)]
    for segment in segments:
        command.extend(["-stream_loop", "-1", "-i", segment["musicPath"]])
    filters = ["[0:a:0]apad[voice]"]
    mix_inputs = ["[voice]"]
    for index, segment in enumerate(segments, start=1):
        delay = round(float(segment["startSeconds"]) * 1000)
        duration = float(segment["durationSeconds"])
        offset = float(segment["sourceOffsetSeconds"])
        volume = float(segment["volume"])
        filters.append(
            f"[{index}:a:0]atrim=start={offset:.3f}:duration={duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={volume:.3f},adelay={delay}|{delay}[bgm{index}]"
        )
        mix_inputs.append(f"[bgm{index}]")
    filters.append(
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    command.extend(["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[aout]"])
    command.extend(["-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(output)])
    return command
