from __future__ import annotations

import base64
import json
import os
import platform
import re
import shutil
import subprocess
import wave
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ai8video.media.ffmpeg_utils import probe_media_duration_seconds, resolve_ffmpeg_bin
from ai8video.assets.upload_utils import resolve_upload_filename
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root
from ai8video.core.paths import PROJECT_ROOT


LOCAL_TTS_DIR_NAME = "TTS"
LEGACY_LOCAL_TTS_DIR_NAME = "本地TTS"
LOCAL_TTS_OUTPUT_DIR_NAME = "输出"
LOCAL_TTS_MODEL_DIR_NAME = "模型"
LOCAL_TTS_SETTINGS_NAME = "settings.json"
DEFAULT_LOCAL_TTS_ENABLED = False
DEFAULT_LOCAL_TTS_ENGINE = "mimo-api"
DEFAULT_LOCAL_TTS_VOICE = ""
DEFAULT_LOCAL_TTS_RATE = 185
DEFAULT_LOCAL_TTS_VOLUME = 1.0
MAX_LOCAL_TTS_VOLUME = 4.0
DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME = 0.18
DEFAULT_SHERPA_ONNX_MODEL_NAME = "vits-melo-tts-zh_en"
DEFAULT_SHERPA_ONNX_VOICE = "0"
LEGACY_SHERPA_ONNX_MODEL_NAMES = {"vits-icefall-zh-aishell3"}
LOCAL_TTS_VOICE_PROFILE_NAME = "local_tts_voice_profiles.json"
MAX_TTS_TEXT_CHARS = 1800
TAIL_FRAME_MARKER = "所有主体最后一秒尽可能全身正对着镜头"
DEFAULT_MIMO_API_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MIMO_API_MODEL = "mimo-v2.5-tts"
DEFAULT_MIMO_API_CLONE_MODEL = "mimo-v2.5-tts-voiceclone"
DEFAULT_MIMO_API_VOICE = "冰糖"
LOCAL_TTS_DURATION_FIT_TOLERANCE_SECONDS = 0.35
LOCAL_TTS_END_GUARD_SECONDS = 1.0
LOCAL_TTS_LOUDNESS_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"
LOCAL_TTS_CLONE_LIBRARY_DIR_NAME = "音色克隆"
LEGACY_LOCAL_TTS_CLONE_LIBRARY_DIR_NAME = "音色复刻"
LOCAL_TTS_CLONE_AUDIO_EXTENSIONS = {".mp3", ".wav"}
LOCAL_TTS_CLONE_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
LOCAL_TTS_CLONE_STORAGE_EXTENSION = ".wav"
LOCAL_TTS_CLONE_MAX_SECONDS = 45
LOCAL_TTS_CLONE_DATA_URI_MAX_BYTES = 10 * 1024 * 1024
LOCAL_TTS_CLONE_AUDIO_FILTER = (
    "silenceremove=start_periods=1:start_duration=0.2:start_threshold=-45dB:"
    "stop_periods=-1:stop_duration=0.6:stop_threshold=-45dB,"
    "loudnorm=I=-18:TP=-2:LRA=11"
)
MIMO_API_PRESET_VOICE_OPTIONS = [
    {
        "value": "mimo_default",
        "label": "MiMo-默认（中国集群默认冰糖）",
        "language": "自适应",
        "gender": "默认",
    },
    {"value": "冰糖", "label": "冰糖", "language": "中文", "gender": "女性"},
    {"value": "茉莉", "label": "茉莉", "language": "中文", "gender": "女性"},
    {"value": "苏打", "label": "苏打", "language": "中文", "gender": "男性"},
    {"value": "白桦", "label": "白桦", "language": "中文", "gender": "男性"},
    {"value": "Mia", "label": "Mia", "language": "英文", "gender": "女性"},
    {"value": "Chloe", "label": "Chloe", "language": "英文", "gender": "女性"},
    {"value": "Milo", "label": "Milo", "language": "英文", "gender": "男性"},
    {"value": "Dean", "label": "Dean", "language": "英文", "gender": "男性"},
]
SHERPA_SINGLE_VOICE_MODEL_PROFILES = {
    "vits-melo-tts-zh_en": {
        "speaker": "melo-zh-en",
        "label": "Melo 中文英文 单音色",
    }
}
DIALOGUE_FIELD_RE = re.compile(
    r"(?:台词\s*/\s*口播|台词|口播|旁白|解说|画外音)"
    r"\s*(?:[（(][^）)\n]{0,30}[）)])?\s*[：:]\s*"
)
SHOT_BOUNDARY_RE = re.compile(
    r"(?:镜头[一二三四五六七八九十百\d]+|第?\d+[集格段镜]?)\s*(?:[（(]|[：:、.\s-])"
)
TIME_BOUNDARY_RE = re.compile(r"\d{1,3}\s*[-—~至到]\s*\d{1,3}\s*(?:秒|s|S)\s*[：:]")


def local_tts_dir() -> Path:
    configured = os.getenv("AI8VIDEO_LOCAL_TTS_DIR")
    root = Path(configured) if configured else USER_FILE_ROOT / LOCAL_TTS_DIR_NAME
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root.resolve()


def legacy_local_tts_dir() -> Path:
    root = USER_FILE_ROOT / LEGACY_LOCAL_TTS_DIR_NAME
    return root.resolve()


def _maybe_migrate_legacy_local_tts_dir(target: Path) -> None:
    if os.getenv("AI8VIDEO_LOCAL_TTS_DIR"):
        return
    legacy = legacy_local_tts_dir()
    if target.exists() or not legacy.exists() or legacy == target:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(legacy), str(target))


def ensure_local_tts_dir() -> Path:
    ensure_user_file_root()
    root = local_tts_dir()
    _maybe_migrate_legacy_local_tts_dir(root)
    (root / LOCAL_TTS_OUTPUT_DIR_NAME).mkdir(parents=True, exist_ok=True)
    (root / LOCAL_TTS_MODEL_DIR_NAME).mkdir(parents=True, exist_ok=True)
    _maybe_migrate_legacy_voice_clone_dir(root)
    (root / LOCAL_TTS_CLONE_LIBRARY_DIR_NAME).mkdir(parents=True, exist_ok=True)
    return root


def local_tts_settings_path() -> Path:
    return local_tts_dir() / LOCAL_TTS_SETTINGS_NAME


def local_tts_output_dir() -> Path:
    return local_tts_dir() / LOCAL_TTS_OUTPUT_DIR_NAME


def local_tts_voice_clone_dir() -> Path:
    return local_tts_dir() / LOCAL_TTS_CLONE_LIBRARY_DIR_NAME


def legacy_local_tts_voice_clone_dir() -> Path:
    return local_tts_dir() / LEGACY_LOCAL_TTS_CLONE_LIBRARY_DIR_NAME


def _maybe_migrate_legacy_voice_clone_dir(root: Path) -> None:
    legacy = root / LEGACY_LOCAL_TTS_CLONE_LIBRARY_DIR_NAME
    target = root / LOCAL_TTS_CLONE_LIBRARY_DIR_NAME
    if not legacy.exists() or legacy == target:
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in legacy.iterdir():
        if not item.is_file():
            continue
        destination = target / item.name
        if destination.exists():
            destination = _next_available_path(target, item.name)
        shutil.move(str(item), str(destination))
    try:
        legacy.rmdir()
    except OSError:
        pass


def local_tts_model_root() -> Path:
    return local_tts_dir() / LOCAL_TTS_MODEL_DIR_NAME


def default_sherpa_onnx_model_dir() -> Path:
    return local_tts_model_root() / DEFAULT_SHERPA_ONNX_MODEL_NAME


def local_tts_status() -> dict[str, Any]:
    ensure_local_tts_dir()
    settings = _read_local_tts_settings()
    engine = _clean_engine(settings.get("engine"))
    api_base_url = _clean_mimo_api_base_url(settings.get("apiBaseUrl"))
    api_key = _clean_secret_text(settings.get("apiKey"))
    model = _clean_mimo_model(settings.get("model"))
    clone_model = _clean_mimo_clone_model(settings.get("cloneModel"))
    voice = _clean_voice_selection(settings.get("voice"), engine=engine, model_dir=default_sherpa_onnx_model_dir())
    voice_options = _voice_options_for_engine(engine, default_sherpa_onnx_model_dir())
    available = _engine_available(engine, default_sherpa_onnx_model_dir(), settings=settings)
    output_stats = _folder_stats(local_tts_output_dir())
    model_status = _engine_model_status(engine, default_sherpa_onnx_model_dir())
    clone_items = _voice_clone_items()
    return {
        "ok": True,
        "enabled": _clean_bool(settings.get("enabled"), DEFAULT_LOCAL_TTS_ENABLED),
        "engine": engine,
        "apiBaseUrl": api_base_url,
        "apiKey": api_key,
        "model": model,
        "cloneModel": clone_model,
        "voice": voice,
        "voiceLabel": _voice_label(voice, voice_options),
        "voiceCount": len(voice_options),
        "voiceOptions": voice_options,
        "voiceCloneCount": len(clone_items),
        "voiceCloneItems": clone_items,
        "voiceCloneDir": str(local_tts_voice_clone_dir()),
        "rate": _clean_int(settings.get("rate"), DEFAULT_LOCAL_TTS_RATE, 80, 360),
        "volume": _clean_float(settings.get("volume"), DEFAULT_LOCAL_TTS_VOLUME, 0.0, MAX_LOCAL_TTS_VOLUME),
        "originalAudioVolume": _clean_float(
            settings.get("originalAudioVolume"),
            DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME,
            0.0,
            1.0,
        ),
        "available": available["available"],
        "availabilityReason": available["reason"],
        "modelDir": "",
        "modelAvailable": model_status["available"],
        "modelReason": model_status["reason"],
        "outputDir": str(local_tts_output_dir()),
        "outputFileCount": output_stats["fileCount"],
        "outputSizeBytes": output_stats["sizeBytes"],
        "outputSizeDisplay": output_stats["display"],
    }


def update_local_tts_settings(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_local_tts_dir()
    current = _read_local_tts_settings()
    if "enabled" in payload:
        current["enabled"] = _clean_bool(payload.get("enabled"), DEFAULT_LOCAL_TTS_ENABLED)
    if "engine" in payload:
        current["engine"] = _clean_engine(payload.get("engine"))
    if "modelDir" in payload:
        current["modelDir"] = ""
    if "apiBaseUrl" in payload:
        current["apiBaseUrl"] = _clean_mimo_api_base_url(payload.get("apiBaseUrl"))
    if "apiKey" in payload:
        current["apiKey"] = _clean_secret_text(payload.get("apiKey"))
    if "model" in payload:
        current["model"] = _clean_mimo_model(payload.get("model"))
    if "cloneModel" in payload:
        current["cloneModel"] = _clean_mimo_clone_model(payload.get("cloneModel"))
    current.pop("stylePrompt", None)
    current.pop("audioTag", None)
    engine = _clean_engine(current.get("engine"))
    model_dir = default_sherpa_onnx_model_dir()
    if any(key in payload for key in ("voice", "engine")):
        current["voice"] = _clean_voice_selection(
            payload.get("voice", current.get("voice")),
            engine=engine,
            model_dir=model_dir,
        )
    if "rate" in payload:
        current["rate"] = _clean_int(payload.get("rate"), DEFAULT_LOCAL_TTS_RATE, 80, 360)
    if "volume" in payload:
        current["volume"] = _clean_float(payload.get("volume"), DEFAULT_LOCAL_TTS_VOLUME, 0.0, MAX_LOCAL_TTS_VOLUME)
    if "originalAudioVolume" in payload:
        current["originalAudioVolume"] = _clean_float(
            payload.get("originalAudioVolume"),
            DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME,
            0.0,
            1.0,
        )
    _write_local_tts_settings(current)
    return local_tts_status()


def attach_local_tts_to_video(
    video_path: Path | str,
    *,
    narration_text: str | None,
    episode_index: int | None = None,
    job_id: str | None = None,
    ffmpeg_bin: str | None = None,
    preserve_original_audio: bool = True,
) -> dict[str, Any]:
    status = local_tts_status()
    if not status["enabled"]:
        return {"enabled": False, "status": "skipped", "reason": "local tts disabled"}
    if not status["available"]:
        return {"enabled": True, "status": "failed", "reason": status["availabilityReason"]}
    video = Path(video_path)
    if not video.is_file():
        return {"enabled": True, "status": "skipped", "reason": "video file missing"}

    text = prepare_narration_text(narration_text or "")
    if not text:
        return {"enabled": True, "status": "skipped", "reason": "empty narration text"}

    duration_target = _tts_duration_target_for_video(video, ffmpeg_bin=ffmpeg_bin)
    synth_settings = {
        **status,
        "videoDurationSeconds": duration_target.get("videoDurationSeconds"),
        "targetDurationSeconds": duration_target.get("targetDurationSeconds"),
        "durationAutoSpeed": True,
    }
    output_dir = local_tts_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    episode_part = f"e{episode_index:02d}" if episode_index is not None else "episode"
    job_part = _safe_file_part(job_id or "local")
    audio_path = output_dir / f"{stamp}-{episode_part}-{job_part}.m4a"
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
        return {
            "enabled": True,
            "status": "failed",
            "reason": duration_fit.get("reason") or "tts duration fit failed",
            "audioPath": str(audio_path),
            "ttsDurationFit": duration_fit,
        }

    if preserve_original_audio:
        mixed = _mix_tts_audio(video, audio_path, settings=status, ffmpeg_bin=ffmpeg_bin)
    else:
        narration_volume = _format_volume(status.get("volume"), DEFAULT_LOCAL_TTS_VOLUME)
        mixed = _replace_video_audio_with_tts(
            video,
            audio_path,
            narration_volume,
            resolve_ffmpeg_bin(ffmpeg_bin),
        )
        if mixed.get("status") == "mixed":
            mixed["originalAudio"] = "replaced"
    return {
        **mixed,
        "enabled": True,
        "audioPath": str(audio_path),
        "textChars": len(text),
        "engine": status["engine"],
        "voice": status["voice"],
        "rate": status["rate"],
        "videoDurationSeconds": duration_target.get("videoDurationSeconds"),
        "targetDurationSeconds": duration_target.get("targetDurationSeconds"),
        "ttsDurationFit": duration_fit,
    }


def synthesize_local_tts(
    text: str,
    output_path: Path | str,
    *,
    settings: dict[str, Any] | None = None,
    ffmpeg_bin: str | None = None,
    output_volume: float | None = None,
) -> dict[str, Any]:
    settings = settings or local_tts_status()
    engine = _clean_engine(settings.get("engine"))
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_audio = target.with_name(f"{target.stem}.tts.tmp.wav")
    for candidate in (target, temp_audio):
        if candidate.exists():
            candidate.unlink()
    try:
        if engine != "mimo-api":
            raise RuntimeError("当前仅保留 MiMo TTS")
        _synthesize_with_mimo_api(text, temp_audio, settings)
        _convert_audio_to_m4a(
            temp_audio,
            target,
            ffmpeg_bin=ffmpeg_bin,
            volume_multiplier=output_volume,
        )
    except Exception as exc:
        for candidate in (target, temp_audio):
            if candidate.exists():
                try:
                    candidate.unlink()
                except OSError:
                    pass
        return {"status": "failed", "reason": str(exc)[-500:]}
    finally:
        if temp_audio.exists():
            try:
                temp_audio.unlink()
            except OSError:
                pass
    return {"status": "generated", "path": str(target), "sizeBytes": target.stat().st_size}


def prepare_narration_text(text: str) -> str:
    raw = str(text or "")
    dialogue_text = extract_dialogue_text(raw)
    if dialogue_text:
        raw = dialogue_text
    lines: list[str] = []
    for line in raw.splitlines():
        clean = _strip_prompt_label(line)
        if not clean:
            continue
        if _looks_like_visual_instruction(clean):
            continue
        lines.append(clean)
    joined = " ".join(lines) if lines else _strip_prompt_label(raw)
    joined = re.sub(r"[（(][^）)]{0,80}(?:秒|镜头|画面|景别|运镜|特写|远景|近景)[^）)]*[）)]", "，", joined)
    joined = re.sub(r"\s+", " ", joined)
    joined = re.sub(r"[;；]+", "。", joined)
    joined = joined.strip(" ，。；;")
    if len(joined) > MAX_TTS_TEXT_CHARS:
        joined = joined[:MAX_TTS_TEXT_CHARS].rsplit("。", 1)[0] or joined[:MAX_TTS_TEXT_CHARS]
    return joined.strip()


def extract_dialogue_text(text: str) -> str:
    raw = str(text or "")
    if not raw.strip():
        return ""
    pieces: list[str] = []
    field_stop = (
        "情绪语气", "情绪", "音效建议", "音效", "音乐", "画面", "镜头景别",
        "景别", "场景描述", "场景", "运镜动作", "运镜", "人物动作",
        "动作", "表情", "构图", TAIL_FRAME_MARKER,
    )
    for line in raw.splitlines():
        line_text = str(line or "").strip()
        if not line_text:
            continue
        matches = list(DIALOGUE_FIELD_RE.finditer(line_text))
        if not matches:
            continue
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line_text)
            chunk = line_text[start:end]
            stop_positions: list[int] = []
            for marker in field_stop:
                for token in (marker, f"{marker}：", f"{marker}:"):
                    pos = chunk.find(token)
                    if pos >= 0:
                        stop_positions.append(pos)
            for boundary in (SHOT_BOUNDARY_RE, TIME_BOUNDARY_RE):
                boundary_match = boundary.search(chunk)
                if boundary_match and boundary_match.start() > 0:
                    stop_positions.append(boundary_match.start())
            if stop_positions:
                chunk = chunk[:min(stop_positions)]
            chunk = re.sub(r"^[“”\"'：:\s]+|[“”\"'\s]+$", "", chunk).strip()
            if chunk:
                pieces.append(chunk)
    joined = " ".join(pieces)
    joined = re.sub(r"\s+", " ", joined)
    return joined.strip(" ，。；;")


def _synthesize_with_sherpa_onnx(text: str, output_path: Path, settings: dict[str, Any]) -> None:
    try:
        import sherpa_onnx
    except Exception as exc:
        raise RuntimeError("缺少 sherpa-onnx 依赖，请先安装项目短视频依赖") from exc

    model_dir = _clean_model_dir(settings.get("modelDir"))
    model_status = _sherpa_onnx_model_status(model_dir)
    if not model_status["available"]:
        raise RuntimeError(model_status["reason"])

    rule_paths = [
        model_dir / "phone.fst",
        model_dir / "date.fst",
        model_dir / "number.fst",
    ]
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(model_dir / "model.onnx"),
                lexicon=str(model_dir / "lexicon.txt"),
                tokens=str(model_dir / "tokens.txt"),
            ),
            num_threads=2,
            debug=False,
            provider="cpu",
        ),
        rule_fsts=",".join(str(path) for path in rule_paths if path.is_file()),
        max_num_sentences=1,
    )
    tts = sherpa_onnx.OfflineTts(config)
    sid = _clean_int(settings.get("voice"), 0, 0, 9999)
    rate = _clean_int(settings.get("rate"), DEFAULT_LOCAL_TTS_RATE, 80, 360)
    speed = max(0.5, min(2.0, rate / DEFAULT_LOCAL_TTS_RATE))
    audio = tts.generate(text, sid=sid, speed=speed)
    _write_wav(output_path, audio.samples, audio.sample_rate)


def _synthesize_with_mimo_api(text: str, output_path: Path, settings: dict[str, Any]) -> None:
    api_key = _clean_secret_text(settings.get("apiKey"))
    if not api_key:
        raise RuntimeError("未配置 MiMo API Key")
    base_url = _clean_mimo_api_base_url(settings.get("apiBaseUrl"))
    voice = _clean_voice_selection(settings.get("voice"), engine="mimo-api", model_dir=default_sherpa_onnx_model_dir())
    if _is_voice_clone_selection(voice):
        sample_path = _voice_clone_sample_path(voice)
        if sample_path is None:
            raise RuntimeError("所选克隆音色样本不存在")
        mime_type = "audio/wav" if sample_path.suffix.lower() == ".wav" else "audio/mpeg"
        sample_b64 = base64.b64encode(sample_path.read_bytes()).decode("ascii")
        voice_payload = f"data:{mime_type};base64,{sample_b64}"
        if len(voice_payload.encode("utf-8")) > LOCAL_TTS_CLONE_DATA_URI_MAX_BYTES:
            raise RuntimeError("音色克隆样本过大，请缩短到更短的人声片段后重试")
        model = _clean_mimo_clone_model(settings.get("cloneModel"))
    else:
        voice_payload = voice
        model = _clean_mimo_model(settings.get("model"))
    payload = {
        "model": model,
        "audio": {
            "voice": voice_payload,
            "format": "wav",
        },
        "messages": [
            {"role": "user", "content": _mimo_tts_user_instruction(settings)},
            {"role": "assistant", "content": str(text or "").strip()},
        ],
    }
    try:
        response = requests.post(
            _resolve_mimo_chat_completions_url(base_url),
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=180,
        )
    except requests.Timeout as exc:
        raise RuntimeError("MiMo TTS 请求超时，请稍后重试") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"MiMo TTS 请求失败：{exc}") from exc
    if not response.ok:
        raise RuntimeError(_format_http_response_error(response, "MiMo TTS"))
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError("MiMo TTS 返回了无法解析的 JSON") from exc
    audio_b64 = _extract_mimo_audio_data(body)
    if not audio_b64:
        raise RuntimeError(f"MiMo TTS 响应缺少音频数据：{_truncate_text(json.dumps(body, ensure_ascii=False), 400)}")
    try:
        wav_bytes = base64.b64decode(audio_b64)
    except Exception as exc:
        raise RuntimeError("MiMo TTS 音频解码失败") from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(wav_bytes)


def _mimo_tts_user_instruction(settings: dict[str, Any]) -> str:
    rate = _clean_int(settings.get("rate"), DEFAULT_LOCAL_TTS_RATE, 80, 360)
    target_duration = _clean_tts_duration_seconds(settings.get("targetDurationSeconds"))
    video_duration = _clean_tts_duration_seconds(settings.get("videoDurationSeconds"))
    lines = [
        "请只朗读 assistant 消息中的文本，不要朗读本条指令。",
        "不要省略、改写或新增 assistant 文本。",
        f"语速设置基准为 {DEFAULT_LOCAL_TTS_RATE}，当前为 {rate}，请按这个相对快慢处理。",
    ]
    if settings.get("durationAutoSpeed") and target_duration:
        lines.insert(
            1,
            f"请根据目标时长自动调整语速，整段音频必须在 {target_duration:.2f} 秒内自然读完，收尾留一点余量。",
        )
        if video_duration:
            lines.insert(2, f"对应视频实际时长约 {video_duration:.2f} 秒。")
    else:
        lines.insert(1, "请用自然清晰的短视频口播语气朗读。")
    return "\n".join(lines)


def _synthesize_with_system_tts(text: str, target: Path, settings: dict[str, Any]) -> Path:
    system_name = platform.system().lower()
    if system_name == "darwin":
        temp_audio = target.with_name(f"{target.stem}.tts.tmp.aiff")
        _synthesize_with_macos_say(text, temp_audio, settings)
        return temp_audio
    if system_name == "windows":
        temp_audio = target.with_name(f"{target.stem}.tts.tmp.wav")
        _synthesize_with_windows_sapi(text, temp_audio, settings)
        return temp_audio
    raise RuntimeError(f"当前系统暂不支持系统内置 TTS：{platform.system()}")


def _synthesize_with_macos_say(text: str, output_path: Path, settings: dict[str, Any]) -> None:
    say = shutil.which("say")
    if not say:
        raise RuntimeError("macOS say not found")
    cmd = [say, "-r", str(settings.get("rate") or DEFAULT_LOCAL_TTS_RATE), "-o", str(output_path)]
    voice = str(settings.get("voice") or "").strip()
    if voice and voice != DEFAULT_LOCAL_TTS_VOICE:
        cmd.extend(["-v", voice])
    cmd.append(text)
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)


def _synthesize_with_windows_sapi(text: str, output_path: Path, settings: dict[str, Any]) -> None:
    powershell = shutil.which("powershell") or shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        raise RuntimeError("PowerShell not found")
    safe_text = json.dumps(text, ensure_ascii=False)
    safe_path = json.dumps(str(output_path), ensure_ascii=False)
    volume = int(_clean_float(settings.get("volume"), DEFAULT_LOCAL_TTS_VOLUME, 0.0, 1.0) * 100)
    rate = max(-10, min(10, round((_clean_int(settings.get("rate"), DEFAULT_LOCAL_TTS_RATE, 80, 360) - 185) / 18)))
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.Volume={volume};$s.Rate={rate};"
        f"$s.SetOutputToWaveFile({safe_path});"
        f"$s.Speak({safe_text});"
        "$s.Dispose();"
    )
    subprocess.run([powershell, "-NoProfile", "-Command", script], check=True, capture_output=True, text=True, timeout=180)


def _write_wav(path: Path, samples: Any, sample_rate: int) -> None:
    pcm = array("h")
    for value in samples:
        number = max(-1.0, min(1.0, float(value)))
        pcm.append(int(number * 32767))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(pcm.tobytes())


def _convert_audio_to_m4a(
    source: Path,
    target: Path,
    *,
    ffmpeg_bin: str | None = None,
    volume_multiplier: float | None = None,
) -> None:
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
    ]
    audio_filters = [LOCAL_TTS_LOUDNESS_FILTER]
    if volume_multiplier is not None:
        audio_filters.append(f"volume={_format_volume(volume_multiplier, DEFAULT_LOCAL_TTS_VOLUME)}")
    if audio_filters:
        cmd.extend(["-filter:a", ",".join(audio_filters)])
    cmd.extend([
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(target),
    ])
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)


def _fit_tts_audio_to_video_duration(
    audio: Path,
    video: Path,
    *,
    target_duration_seconds: float | int | str | None = None,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    video_duration = probe_media_duration_seconds(video, ffmpeg_bin=ffmpeg)
    audio_duration = probe_media_duration_seconds(audio, ffmpeg_bin=ffmpeg)
    explicit_target = _clean_tts_duration_seconds(target_duration_seconds)
    target_duration = explicit_target or _clean_tts_duration_seconds(video_duration)
    if target_duration and video_duration:
        target_duration = min(float(target_duration), float(video_duration))
    if not target_duration or not audio_duration:
        return {
            "status": "skipped",
            "reason": "duration unavailable",
            "videoDurationSeconds": video_duration,
            "audioDurationSeconds": audio_duration,
            "targetDurationSeconds": explicit_target,
        }
    target_duration = max(0.1, float(target_duration))
    if float(audio_duration) <= target_duration + LOCAL_TTS_DURATION_FIT_TOLERANCE_SECONDS:
        return {
            "status": "skipped",
            "reason": "audio already fits",
            "videoDurationSeconds": round(float(video_duration), 3) if video_duration else None,
            "audioDurationSeconds": round(float(audio_duration), 3),
            "targetDurationSeconds": round(float(target_duration), 3),
        }
    tempo = max(0.5, float(audio_duration) / target_duration)
    atempo_filter = _build_atempo_filter(tempo)
    temp_audio = audio.with_name(f"{audio.stem}.fit.tmp{audio.suffix or '.m4a'}")
    if temp_audio.exists():
        temp_audio.unlink()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(audio),
        "-vn",
        "-filter:a",
        atempo_filter,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-t",
        f"{target_duration:.3f}",
        str(temp_audio),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        os.replace(temp_audio, audio)
    except Exception as exc:
        if temp_audio.exists():
            temp_audio.unlink()
        return {
            "status": "failed",
            "reason": str(exc)[-500:],
            "videoDurationSeconds": round(float(video_duration), 3) if video_duration else None,
            "audioDurationSeconds": round(float(audio_duration), 3),
            "targetDurationSeconds": round(float(target_duration), 3),
            "tempo": round(tempo, 4),
        }
    return {
        "status": "fitted",
        "videoDurationSeconds": round(float(video_duration), 3) if video_duration else None,
        "audioDurationSeconds": round(float(audio_duration), 3),
        "targetDurationSeconds": round(float(target_duration), 3),
        "tempo": round(tempo, 4),
        "filter": atempo_filter,
    }


def _tts_duration_target_for_video(video: Path, *, ffmpeg_bin: str | None = None) -> dict[str, Any]:
    video_duration = _clean_tts_duration_seconds(probe_media_duration_seconds(video, ffmpeg_bin=ffmpeg_bin))
    if not video_duration:
        return {
            "videoDurationSeconds": None,
            "targetDurationSeconds": None,
            "guardSeconds": None,
        }
    guard = min(LOCAL_TTS_END_GUARD_SECONDS, max(0.0, float(video_duration) - 0.1))
    target_duration = max(0.1, float(video_duration) - guard)
    return {
        "videoDurationSeconds": round(float(video_duration), 3),
        "targetDurationSeconds": round(target_duration, 3),
        "guardSeconds": round(guard, 3),
    }


def _build_atempo_filter(tempo: float) -> str:
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


def _mix_tts_audio(
    video: Path,
    audio: Path,
    *,
    settings: dict[str, Any],
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    temp_video = video.with_name(f"{video.stem}.with-tts.tmp{video.suffix or '.mp4'}")
    if temp_video.exists():
        temp_video.unlink()
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    video_duration = probe_media_duration_seconds(video, ffmpeg_bin=ffmpeg)
    if not video_duration:
        return {"status": "failed", "reason": "无法读取视频时长，未执行 TTS 混音"}
    narration_volume = _format_volume(settings.get("volume"), DEFAULT_LOCAL_TTS_VOLUME)
    original_volume = _format_volume(settings.get("originalAudioVolume"), DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-i",
        str(audio),
        "-filter_complex",
        f"[0:a:0]volume={original_volume},apad[orig];[1:a:0]volume={narration_volume},apad[tts];"
        "[orig][tts]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]",
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-t",
        f"{video_duration:.3f}",
        "-movflags",
        "+faststart",
        str(temp_video),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        os.replace(temp_video, video)
        return {
            "status": "mixed",
            "video": str(video),
            "originalAudio": "ducked",
            "ttsVolume": float(narration_volume),
            "originalAudioVolume": float(original_volume),
        }
    except subprocess.CalledProcessError as exc:
        if temp_video.exists():
            temp_video.unlink()
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        if _looks_like_missing_original_audio(message):
            return _replace_video_audio_with_tts(video, audio, narration_volume, ffmpeg)
        return {"status": "failed", "reason": message[-500:]}
    except Exception as exc:
        if temp_video.exists():
            temp_video.unlink()
        return {"status": "failed", "reason": str(exc)[-500:]}


def _replace_video_audio_with_tts(video: Path, audio: Path, narration_volume: str, ffmpeg: str) -> dict[str, Any]:
    temp_video = video.with_name(f"{video.stem}.tts-only.tmp{video.suffix or '.mp4'}")
    if temp_video.exists():
        temp_video.unlink()
    video_duration = probe_media_duration_seconds(video, ffmpeg_bin=ffmpeg)
    if not video_duration:
        return {"status": "failed", "reason": "无法读取视频时长，未执行 TTS 替换"}
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-i",
        str(audio),
        "-filter_complex",
        f"[1:a:0]volume={narration_volume},apad[aout]",
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-t",
        f"{video_duration:.3f}",
        "-movflags",
        "+faststart",
        str(temp_video),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        os.replace(temp_video, video)
    except Exception as exc:
        if temp_video.exists():
            temp_video.unlink()
        return {"status": "failed", "reason": str(exc)[-500:]}
    return {
        "status": "mixed",
        "video": str(video),
        "originalAudio": "missing",
        "fallback": "tts_only",
        "ttsVolume": float(narration_volume),
    }


def _engine_available(engine: str, model_dir: Path, *, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    current = settings or _read_local_tts_settings()
    api_key = _clean_secret_text(current.get("apiKey"))
    voice = _clean_voice_selection(current.get("voice"), engine=engine, model_dir=model_dir)
    if not api_key:
        return {"available": False, "reason": "MiMo API Key 未配置"}
    if _is_voice_clone_selection(voice):
        sample_path = _voice_clone_sample_path(voice)
        if sample_path is None:
            return {"available": False, "reason": "已选择克隆音色，但样本文件不存在"}
        model = _clean_mimo_clone_model(current.get("cloneModel"))
        return {"available": True, "reason": f"MiMo API · {model}"}
    model = _clean_mimo_model(current.get("model"))
    return {"available": True, "reason": f"MiMo API · {model}"}


def _read_local_tts_settings() -> dict[str, Any]:
    path = local_tts_settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_local_tts_settings(payload: dict[str, Any]) -> None:
    path = local_tts_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    engine = _clean_engine(data.get("engine"))
    data["engine"] = engine
    data["modelDir"] = ""
    data["enabled"] = _clean_bool(data.get("enabled"), DEFAULT_LOCAL_TTS_ENABLED)
    data["apiBaseUrl"] = _clean_mimo_api_base_url(data.get("apiBaseUrl"))
    data["apiKey"] = _clean_secret_text(data.get("apiKey"))
    data["model"] = _clean_mimo_model(data.get("model"))
    data["cloneModel"] = _clean_mimo_clone_model(data.get("cloneModel"))
    data.pop("stylePrompt", None)
    data.pop("audioTag", None)
    data["voice"] = _clean_voice_selection(data.get("voice"), engine=engine, model_dir=_clean_model_dir(data.get("modelDir")))
    data["rate"] = _clean_int(data.get("rate"), DEFAULT_LOCAL_TTS_RATE, 80, 360)
    data["volume"] = _clean_float(data.get("volume"), DEFAULT_LOCAL_TTS_VOLUME, 0.0, MAX_LOCAL_TTS_VOLUME)
    data["originalAudioVolume"] = _clean_float(
        data.get("originalAudioVolume"),
        DEFAULT_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME,
        0.0,
        1.0,
    )
    data["updatedAt"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _strip_prompt_label(line: str) -> str:
    text = str(line or "").strip()
    text = re.sub(r"^\s*(第?\d+[集格段镜]?|镜头[一二三四五六七八九十\d]+)[：:、.\s-]*", "", text)
    text = re.sub(
        r"^\s*[【\[]?[^】\]]{0,12}(?:台词\s*/\s*口播|台词|口播|旁白|解说|画外音|字幕)"
        r"(?:[（(][^）)]{0,30}[）)])?[】\]]?[：:]\s*",
        "",
        text,
    )
    text = re.sub(r"^\s*(画面|动作|表情|运镜|景别|场景|音乐|音效|镜头|构图)[：:]\s*.*$", "", text)
    text = re.sub(r"[“”\"']", "", text)
    return text.strip()


def _looks_like_visual_instruction(text: str) -> bool:
    lowered = text.lower()
    markers = ["画面", "镜头", "运镜", "景别", "构图", "特写", "远景", "近景", "中景", "光效", "字幕", "文字", "logo"]
    return sum(1 for marker in markers if marker in lowered) >= 2


def _looks_like_missing_original_audio(message: str) -> bool:
    text = str(message or "").lower()
    return (
        "matches no streams" in text
        or "stream specifier ':a" in text
        or "stream specifier a" in text
        or ("0:a:0" in text and "not" in text and "match" in text)
    )


def _clean_engine(value: Any) -> str:
    text = str(value or DEFAULT_LOCAL_TTS_ENGINE).strip().lower()
    aliases = {
        "mimo": "mimo-api",
        "mimo-api": "mimo-api",
        "mimo-v2.5-tts": "mimo-api",
        "sherpa-onnx": "mimo-api",
        "system": "mimo-api",
    }
    return aliases.get(text, DEFAULT_LOCAL_TTS_ENGINE)


def _clean_model_dir(value: Any) -> Path:
    text = str(value or "").strip()
    root = Path(text) if text else default_sherpa_onnx_model_dir()
    if not root.is_absolute():
        root = local_tts_model_root() / root
    root = root.resolve()
    if root.name in LEGACY_SHERPA_ONNX_MODEL_NAMES:
        return default_sherpa_onnx_model_dir().resolve()
    return root


def _clean_mimo_clone_model(value: Any) -> str:
    text = str(value or "").strip()
    return text or DEFAULT_MIMO_API_CLONE_MODEL


def _voice_clone_items() -> list[dict[str, Any]]:
    ensure_local_tts_dir()
    directory = local_tts_voice_clone_dir()
    items: list[dict[str, Any]] = []
    if not directory.is_dir():
        return items
    for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() not in LOCAL_TTS_CLONE_AUDIO_EXTENSIONS:
            continue
        stat = path.stat()
        items.append({
            "id": path.name,
            "name": path.name,
            "label": f"克隆 · {path.stem}",
            "path": str(path),
            "sizeBytes": stat.st_size,
            "updatedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    return items


def _voice_clone_value(item_id: str) -> str:
    return f"clone:{item_id}"


def _is_voice_clone_selection(value: Any) -> bool:
    return str(value or "").strip().startswith("clone:")


def _voice_clone_item_id(value: Any) -> str:
    text = str(value or "").strip()
    return text.split(":", 1)[1].strip() if text.startswith("clone:") else ""


def _voice_clone_sample_path(value: Any) -> Path | None:
    item_id = _voice_clone_item_id(value)
    if not item_id:
        return None
    target = (local_tts_voice_clone_dir() / Path(item_id).name).resolve()
    try:
        target.relative_to(local_tts_voice_clone_dir().resolve())
    except ValueError:
        return None
    if not target.is_file() or target.suffix.lower() not in LOCAL_TTS_CLONE_AUDIO_EXTENSIONS:
        return None
    return target


def local_tts_voice_clone_cache_signature(value: Any) -> str:
    sample_path = _voice_clone_sample_path(value)
    if sample_path is None:
        return ""
    try:
        stat = sample_path.stat()
    except OSError:
        return ""
    return f"{sample_path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def _next_available_path(directory: Path, filename: str) -> Path:
    safe_name = Path(str(filename or "")).name
    candidate = directory / safe_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 1000):
        numbered = directory / f"{stem}-{index}{suffix}"
        if not numbered.exists():
            return numbered
    raise RuntimeError("音色克隆文件名重复过多，请先清理文件夹")


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
    for candidate in (temp_source, temp_target):
        if candidate.exists():
            candidate.unlink()
    try:
        upload.save(str(temp_source), overwrite=True)
        _prepare_voice_clone_sample(temp_source, temp_target, ffmpeg_bin=ffmpeg_bin)
        os.replace(temp_target, target_path)
    finally:
        for candidate in (temp_source, temp_target):
            if candidate.exists():
                try:
                    candidate.unlink()
                except OSError:
                    pass
    current = _read_local_tts_settings()
    current["voice"] = _voice_clone_value(target_path.name)
    _write_local_tts_settings(current)
    return local_tts_status()


def _prepare_voice_clone_sample(source: Path, target: Path, *, ffmpeg_bin: str | None = None) -> None:
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
        "-vn",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-af",
        LOCAL_TTS_CLONE_AUDIO_FILTER,
        "-c:a",
        "pcm_s16le",
        "-t",
        str(LOCAL_TTS_CLONE_MAX_SECONDS),
        str(target),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(message[-500:] or "音色克隆样本处理失败") from exc


def _voice_options_for_engine(engine: str, model_dir: Path) -> list[dict[str, str]]:
    if engine == "mimo-api":
        clone_options = [
            {"value": _voice_clone_value(str(item.get("id") or "")), "label": str(item.get("label") or item.get("name") or "克隆音色")}
            for item in _voice_clone_items()
            if str(item.get("id") or "").strip()
        ]
        return [dict(option) for option in MIMO_API_PRESET_VOICE_OPTIONS] + clone_options
    return _sherpa_onnx_voice_options(model_dir)


def _sherpa_onnx_model_label(model_dir: Path) -> str:
    if model_dir.name == "vits-melo-tts-zh_en":
        return "sherpa-onnx Melo 中文英文"
    return f"sherpa-onnx {model_dir.name}"


def _sherpa_onnx_voice_profiles() -> dict[str, dict[str, str]]:
    profiles_path = Path(__file__).with_name(LOCAL_TTS_VOICE_PROFILE_NAME)
    if not profiles_path.is_file():
        return {}
    try:
        rows = json.loads(profiles_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(rows, list):
        return {}
    profiles: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        speaker = str(row.get("speaker") or "").strip()
        if not speaker:
            continue
        profile: dict[str, str] = {}
        for key in ("label", "style", "ageGroup", "ageLabel", "gender", "genderLabel", "accent", "accentLabel"):
            text = str(row.get(key) or "").strip()
            if text:
                profile[key] = text
        if profile:
            profiles[speaker] = profile
    return profiles


def _sherpa_onnx_voice_options(model_dir: Path) -> list[dict[str, str]]:
    speakers_path = model_dir / "speakers.txt"
    if not speakers_path.is_file():
        return _single_voice_options_for_model(model_dir)
    try:
        lines = speakers_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return _single_voice_options_for_model(model_dir)
    profiles = _sherpa_onnx_voice_profiles()
    options: list[dict[str, str]] = []
    for line in lines:
        speaker = str(line or "").strip()
        if not speaker:
            continue
        index = len(options)
        profile = profiles.get(speaker) or {}
        option = {
            "value": str(index),
            "label": str(profile.get("label") or f"音色 {index + 1:03d}"),
            "speaker": speaker,
        }
        for key in ("style", "ageGroup", "ageLabel", "gender", "genderLabel", "accent", "accentLabel"):
            text = str(profile.get(key) or "").strip()
            if text:
                option[key] = text
        options.append(option)
    return options or _single_voice_options_for_model(model_dir)


def _single_voice_options_for_model(model_dir: Path) -> list[dict[str, str]]:
    profile = SHERPA_SINGLE_VOICE_MODEL_PROFILES.get(model_dir.name)
    if profile is None and not _sherpa_onnx_model_status(model_dir)["available"]:
        return []
    profile = profile or {
        "speaker": model_dir.name,
        "label": f"{model_dir.name} 默认音色",
    }
    option = {
        "value": "0",
        "label": str(profile.get("label") or "默认音色"),
        "speaker": str(profile.get("speaker") or model_dir.name or "default"),
    }
    for key in ("style", "ageGroup", "ageLabel", "gender", "genderLabel", "accent", "accentLabel"):
        text = str(profile.get(key) or "").strip()
        if text:
            option[key] = text
    return [option]


def _clean_voice_selection(value: Any, *, engine: str, model_dir: Path) -> str:
    text = str(value or "").strip()
    if _is_voice_clone_selection(text):
        return text if _voice_clone_sample_path(text) is not None else DEFAULT_MIMO_API_VOICE
    return text or DEFAULT_MIMO_API_VOICE


def _match_sherpa_speaker_index(value: str, options: list[dict[str, str]]) -> int | None:
    needle = str(value or "").strip().lower()
    if not needle:
        return None
    for option in options:
        speaker = str(option.get("speaker") or "").strip().lower()
        if speaker == needle:
            return _clean_int(option.get("value"), 0, 0, 9999)
    return None


def _voice_label(voice: str, options: list[dict[str, str]]) -> str:
    selected = str(voice or "").strip()
    for option in options:
        if str(option.get("value") or "").strip() == selected:
            return str(option.get("label") or selected)
    return selected or DEFAULT_LOCAL_TTS_VOICE


def _sherpa_onnx_model_status(model_dir: Path) -> dict[str, Any]:
    required = ["model.onnx", "tokens.txt", "lexicon.txt"]
    missing = [name for name in required if not (model_dir / name).is_file()]
    if missing:
        return {
            "available": False,
            "reason": f"sherpa-onnx 模型文件缺失：{', '.join(missing)}",
        }
    return {"available": True, "reason": "模型文件完整"}


def _engine_model_status(engine: str, model_dir: Path) -> dict[str, Any]:
    return {"available": True, "reason": "MiMo API 不需要本地模型"}


def _clean_mimo_api_base_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return DEFAULT_MIMO_API_BASE_URL
    if text.endswith("/chat/completions"):
        text = text[: -len("/chat/completions")]
    return text or DEFAULT_MIMO_API_BASE_URL


def _clean_mimo_model(value: Any) -> str:
    text = str(value or "").strip()
    return text or DEFAULT_MIMO_API_MODEL


def _clean_secret_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_tts_duration_seconds(value: Any) -> float | None:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    return duration


def _resolve_mimo_chat_completions_url(base_url: str) -> str:
    base = _clean_mimo_api_base_url(base_url)
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _extract_mimo_audio_data(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                audio = message.get("audio")
                if isinstance(audio, dict):
                    data = audio.get("data")
                    if isinstance(data, str) and data.strip():
                        return data.strip()
            audio = first.get("audio")
            if isinstance(audio, dict):
                data = audio.get("data")
                if isinstance(data, str) and data.strip():
                    return data.strip()
    audio = payload.get("audio")
    if isinstance(audio, dict):
        data = audio.get("data")
        if isinstance(data, str) and data.strip():
            return data.strip()
    return ""


def _format_http_response_error(response: requests.Response, action: str) -> str:
    status = f"{response.status_code} {response.reason or ''}".strip()
    body = _http_response_excerpt(response)
    message = f"{action}失败：HTTP {status}"
    if body:
        message += f"，上游返回：{body}"
    return message


def _http_response_excerpt(response: requests.Response, limit: int = 500) -> str:
    text = ""
    try:
        payload = response.json()
        text = json.dumps(payload, ensure_ascii=False)
    except ValueError:
        text = response.text or ""
    text = re.sub(r"(?i)(api[_-]?key\\s*[:=]\\s*)[^\\s,;]+", r"\\1***", text)
    text = " ".join(text.split())
    return _truncate_text(text, limit)


def _truncate_text(text: str, limit: int) -> str:
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "..."


def _clean_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "开启"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "关闭"}:
        return False
    return default


def _clean_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(float(str(value)))
    except Exception:
        return default
    return max(minimum, min(maximum, number))


def _clean_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(str(value))
    except Exception:
        return default
    return round(max(minimum, min(maximum, number)), 2)


def _format_volume(value: Any, default: float) -> str:
    return f"{_clean_float(value, default, 0.0, MAX_LOCAL_TTS_VOLUME):.2f}".rstrip("0").rstrip(".")


def _safe_file_part(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z._-]+", "-", str(value or "").strip())
    return (text or "local")[:60].strip("-") or "local"


def _folder_stats(path: Path) -> dict[str, int | str]:
    path.mkdir(parents=True, exist_ok=True)
    count = 0
    size = 0
    for item in path.iterdir():
        if not item.is_file():
            continue
        count += 1
        try:
            size += item.stat().st_size
        except OSError:
            pass
    return {"fileCount": count, "sizeBytes": size, "display": _format_bytes(size)}


def _format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(0, size))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
