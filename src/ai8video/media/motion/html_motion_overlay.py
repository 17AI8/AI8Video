from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from ai8video.core.config import AI8VideoConfig
from ai8video.core.paths import PROJECT_ROOT
from ai8video.media.ffmpeg_utils import probe_media_video_info, resolve_ffmpeg_bin
from ai8video.media.motion.hyperframes_overlay_harness import HarnessResult, build_hyperframes_overlay, revise_hyperframes_overlay
from ai8video.media.motion.hyperframes_overlay_prompts import HTML_MOTION_SYSTEM_PROMPT
from ai8video.media.motion.hyperframes_runtime import WAAPI_RUNTIME_SOURCE, render_prepared_hyperframes, write_hyperframes_files
from ai8video.media.motion.hyperframes_worker import HyperFramesWorkerCancelled, HyperFramesWorkerError
from ai8video.media.overlay_video_io import assert_transparent_layer, composite_transparent_layer, validate_composited_video
from ai8video.integrations.llm_provider import build_openai_compat_splitter
from ai8video.core.models import EpisodePrompt, ParsedRequest, QuickVideoJob
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root
from ai8video.media.video_encoding import video_postprocess_encoding_meta
from ai8video.media.video_text_overlay import selected_video_text_overlay_font_path


HTML_MOTION_DIR = (USER_FILE_ROOT / "HTML动效").resolve()
HTML_MOTION_SETTINGS_PATH = HTML_MOTION_DIR / "settings.json"
HYPERFRAMES_VERSION = "0.7.59"
HTML_MOTION_TOTAL_TIMEOUT_SECONDS = 300
HTML_MOTION_LLM_TIMEOUT_SECONDS = 20
HTML_MOTION_LLM_CONCURRENCY = 1
HTML_MOTION_QUALITY_RETRY_DEFAULT = 5
HTML_MOTION_QUALITY_RETRY_MIN = 0
HTML_MOTION_QUALITY_RETRY_MAX = 10
HTML_MOTION_BEAT_INTERVAL_DEFAULT = 5
HTML_MOTION_BEAT_INTERVAL_MIN = 1
HTML_MOTION_BEAT_INTERVAL_MAX = 30
HTML_MOTION_PLAYBACK_TRIGGER = "video_playback"
HTML_MOTION_MANUAL_ONLY_REASON = "HTML 动效仅允许在视频播放界面手动生成并确认烧录"
DEFAULT_MOTION_FONT_FAMILY = "AI8VideoMotion"
MOTION_FONT_FILE = "motion-font.otf"
MOTION_FONT_SOURCE = (PROJECT_ROOT / "用户字体" / "内置字体" / "SourceHanSansSC-Bold.otf").resolve()
HTML_MOTION_SAFE_ZONE_DEFAULTS = {
    "9:16": {"x": 8.0, "y": 8.0, "width": 84.0, "height": 38.0},
    "16:9": {"x": 8.0, "y": 8.0, "width": 84.0, "height": 46.0},
    "1:1": {"x": 8.0, "y": 8.0, "width": 84.0, "height": 46.0},
}


def html_motion_overlay_status() -> dict[str, Any]:
    settings = _read_settings()
    return {
        "ok": True,
        "enabled": bool(settings.get("enabled")),
        "qualityRetryCount": _normalize_quality_retry_count(settings.get("qualityRetryCount")),
        "beatIntervalSeconds": _normalize_beat_interval_seconds(settings.get("beatIntervalSeconds")),
        "smartBeatInterval": bool(settings.get("smartBeatInterval", False)),
        "safeZones": _normalize_safe_zones(settings.get("safeZones")),
        "runtime": html_motion_runtime_status(),
    }


def default_html_motion_overlay_enabled() -> bool:
    return bool(_read_settings().get("enabled"))


def update_html_motion_overlay(*, enabled: bool) -> dict[str, Any]:
    settings = _read_settings()
    settings["enabled"] = bool(enabled)
    _write_settings(settings)
    return html_motion_overlay_status()


def update_html_motion_quality_retry_count(value: Any) -> dict[str, Any]:
    settings = _read_settings()
    settings["qualityRetryCount"] = _normalize_quality_retry_count(value)
    _write_settings(settings)
    return html_motion_overlay_status()


def update_html_motion_beat_interval_seconds(value: Any) -> dict[str, Any]:
    settings = _read_settings()
    settings["beatIntervalSeconds"] = _normalize_beat_interval_seconds(value)
    _write_settings(settings)
    return html_motion_overlay_status()


def update_html_motion_smart_beat_interval(enabled: Any) -> dict[str, Any]:
    settings = _read_settings()
    settings["smartBeatInterval"] = bool(enabled)
    _write_settings(settings)
    return html_motion_overlay_status()


def html_motion_safe_zone_status(aspect_ratio: str) -> dict[str, Any]:
    ratio = _normalize_aspect_ratio(aspect_ratio)
    zones = _normalize_safe_zones(_read_settings().get("safeZones"))
    return {"ok": True, "aspectRatio": ratio, "safeZone": zones[ratio]}


def update_html_motion_safe_zone(aspect_ratio: str, safe_zone: Any) -> dict[str, Any]:
    ratio = _normalize_aspect_ratio(aspect_ratio)
    zone = _normalize_safe_zone(safe_zone, HTML_MOTION_SAFE_ZONE_DEFAULTS[ratio])
    settings = _read_settings()
    zones = _normalize_safe_zones(settings.get("safeZones"))
    zones[ratio] = zone
    settings["safeZones"] = zones
    _write_settings(settings)
    return {"ok": True, "aspectRatio": ratio, "safeZone": zone}


def html_motion_runtime_status() -> dict[str, Any]:
    ready = (
        _hyperframes_cli_path() is not None
        and WAAPI_RUNTIME_SOURCE.is_file()
        and _node_source_path() is not None
    )
    return {
        "ready": ready,
        "renderer": "hyperframes",
        "rendererVersion": HYPERFRAMES_VERSION,
        "harness": "hyperframes-overlay-v5-reviewed-json",
        "reason": "" if ready else "HTML 动效运行依赖未安装，成片会保留基础视频。",
    }


def build_html_motion_llm(config: AI8VideoConfig):
    llm = build_openai_compat_splitter(
        config,
        timeout_seconds=HTML_MOTION_LLM_TIMEOUT_SECONDS,
        stream=True,
        transport_retry_count=0,
        system_prompt=HTML_MOTION_SYSTEM_PROMPT,
    )
    return None if llm is None else lambda prompt: _run_llm_race(llm, prompt)


def _run_llm_race(llm: Callable[[str], str], prompt: str) -> str:
    if HTML_MOTION_LLM_CONCURRENCY == 1:
        return llm(prompt)
    executor = ThreadPoolExecutor(max_workers=HTML_MOTION_LLM_CONCURRENCY)
    futures = [executor.submit(llm, prompt) for _ in range(HTML_MOTION_LLM_CONCURRENCY)]
    failures: list[str] = []
    try:
        for future in as_completed(futures):
            try:
                result = str(future.result() or "").strip()
            except Exception as exc:
                failures.append(_safe_error(exc))
                continue
            if result:
                return result
            failures.append("文本模型返回为空")
    finally:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
    detail = "；".join(dict.fromkeys(failures))[:280]
    raise RuntimeError(f"HTML 动效并发编排均失败：{detail or '未知错误'}")


def apply_html_motion_overlay(
    video_path: Path | str,
    request: ParsedRequest,
    episode: EpisodePrompt,
    job: QuickVideoJob,
    *,
    llm: Callable[[str], str] | None,
    ffmpeg_bin: str | None = None,
    stage_callback: Callable[[str, dict[str, Any] | None], None] | None = None,
    cancel_event=None,
    trigger: str = "automatic",
    text_style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del job
    if trigger != HTML_MOTION_PLAYBACK_TRIGGER:
        return manual_only_html_motion_result()
    if not request.html_motion_overlay_enabled:
        return _result("skipped", "本批次未开启 HTML 动效")
    source = Path(video_path)
    if not source.is_file():
        return _result("degraded", "基础视频不存在，HTML 动效未叠加")
    runtime = html_motion_runtime_status()
    if not runtime["ready"]:
        return _result("degraded", runtime["reason"], runtime=runtime)
    if llm is None:
        return _result("degraded", "未配置可用文本模型，HTML 动效未叠加", runtime=runtime)
    return _render_and_composite(
        source, episode, llm, ffmpeg_bin, stage_callback, runtime, cancel_event, text_style,
    )


def manual_only_html_motion_result() -> dict[str, Any]:
    result = _result("skipped", HTML_MOTION_MANUAL_ONLY_REASON)
    result.update({"manualOnly": True, "entrypoint": HTML_MOTION_PLAYBACK_TRIGGER})
    return result


def _render_and_composite(
    source: Path,
    episode: EpisodePrompt,
    llm: Callable[[str], str],
    ffmpeg_bin: str | None,
    stage_callback: Callable[[str, dict[str, Any] | None], None] | None,
    runtime: dict[str, Any],
    cancel_event=None,
    text_style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    work_id = uuid.uuid4().hex[:12]
    work_dir = _create_work_dir(work_id)
    try:
        media = _probe_video_info(source)
        settings = _read_settings()
        media["safeZone"] = html_motion_safe_zone_for_media(media)
        media["textStyle"] = _normalize_text_style(text_style)
        media["beatIntervalSeconds"] = _normalize_beat_interval_seconds(
            settings.get("beatIntervalSeconds")
        )
        media["smartBeatInterval"] = bool(settings.get("smartBeatInterval", False))
        _raise_if_cancelled(cancel_event)
        _notify_stage(stage_callback, "preparing")
        font_family = _resolve_motion_font_family()
        _notify_stage(stage_callback, "generating")
        dialogue_text = _resolve_html_motion_dialogue(episode)
        harness = build_hyperframes_overlay(
            llm,
            episode,
            media,
            dialogue_text=dialogue_text,
            font_family=font_family,
            critique_enabled=False,
            quality_retry_count=_normalize_quality_retry_count(
                settings.get("qualityRetryCount")
            ),
            progress_callback=lambda event: _notify_stage(
                stage_callback,
                "generating",
                event,
            ),
        )
        _raise_if_cancelled(cancel_event)
        output, harness = _render_with_validation_repair(
            work_dir,
            harness,
            llm,
            episode,
            media,
            font_family,
            cancel_event=cancel_event,
            stage_callback=stage_callback,
        )
        _notify_stage(stage_callback, "validating")
        _validate_transparent_layer(output)
        _raise_if_cancelled(cancel_event)
        _notify_stage(stage_callback, "compositing")
        if cancel_event is None:
            _composite_transparent_layer(source, output, media, ffmpeg_bin)
        else:
            _composite_transparent_layer(
                source, output, media, ffmpeg_bin, cancel_event=cancel_event,
            )
        _notify_stage(stage_callback, "validating")
        _validate_composited_video(source, media)
        result = _result(
            "applied",
            "HTML 动效已叠加",
            runtime=runtime,
            work_id=work_id,
            media=media,
            timeline=harness.summary,
            work_cleaned=True,
        )
        result["compositionHtml"] = harness.composition_html
        _cleanup_work_dir(work_dir)
        return result
    except HyperFramesWorkerCancelled:
        _cleanup_work_dir(work_dir)
        raise
    except Exception as exc:
        # Keep failed work dir for diagnosis; friendly reason + raw detail both stored.
        result = _result(
            "degraded",
            f"{_safe_error(exc)}，已保留基础视频",
            runtime=runtime,
            work_id=work_id,
            work_cleaned=False,
        )
        result["detail"] = str(exc).strip()[:800]
        return result


def _render_transparent_layer(
    work_dir: Path,
    composition_html: str,
    motion_manifest: dict[str, Any],
    *,
    font_family: str = "",
    cancel_event=None,
    stage_callback: Callable[[str, dict[str, Any] | None], None] | None = None,
) -> Path:
    output = work_dir / "overlay.webm"
    cli = _hyperframes_cli_path()
    if cli is None or not WAAPI_RUNTIME_SOURCE.is_file():
        raise RuntimeError("HTML 动效运行依赖未安装")
    write_hyperframes_files(
        work_dir,
        composition_html,
        motion_manifest,
    )
    _copy_declared_motion_font(work_dir, font_family)
    render_prepared_hyperframes(
        work_dir,
        cli_path=cli,
        timeout_ms=HTML_MOTION_TOTAL_TIMEOUT_SECONDS * 1000,
        cancel_event=cancel_event,
        stage_callback=stage_callback,
    )
    return output


def _render_with_validation_repair(
    work_dir: Path,
    harness: HarnessResult,
    llm: Callable[[str], str],
    episode: EpisodePrompt,
    media: dict[str, Any],
    font_family: str,
    *,
    cancel_event=None,
    stage_callback: Callable[[str, dict[str, Any] | None], None] | None = None,
) -> tuple[Path, HarnessResult]:
    try:
        return _render_transparent_layer(
            work_dir,
            harness.composition_html,
            harness.motion_manifest,
            font_family=font_family,
            cancel_event=cancel_event,
            stage_callback=stage_callback,
        ), harness
    except HyperFramesWorkerError as exc:
        if exc.code != "CHECK_FAILED":
            raise
        revised = revise_hyperframes_overlay(
            llm,
            harness.artifact,
            episode,
            media,
            dialogue_text=_resolve_html_motion_dialogue(episode),
            validation_error=str(exc)[:1_200],
            font_family=font_family,
            semantic_spec=harness.semantic_spec,
        )
        return _render_transparent_layer(
            work_dir,
            revised.composition_html,
            revised.motion_manifest,
            font_family=font_family,
            cancel_event=cancel_event,
            stage_callback=stage_callback,
        ), revised


def _write_composition(composition: Path, composition_html: str) -> None:
    root = composition.parent.resolve()
    if not composition.resolve().is_relative_to(root):
        raise ValueError("HTML 动效工作目录越界")
    composition.write_text(composition_html, encoding="utf-8")


def _copy_selected_flower_font(work_dir: Path) -> str:
    source = selected_video_text_overlay_font_path()
    if source is None:
        return ""
    target = work_dir / "flower-font.otf"
    shutil.copyfile(source, target)
    return "AI8VideoFlower"


def _resolve_motion_font_family() -> str:
    if selected_video_text_overlay_font_path() is not None:
        return "AI8VideoFlower"
    return DEFAULT_MOTION_FONT_FAMILY if MOTION_FONT_SOURCE.is_file() else ""


def _copy_declared_motion_font(work_dir: Path, font_family: str) -> None:
    family = str(font_family or "").strip()
    if not family:
        return
    if family == "AI8VideoFlower":
        _copy_selected_flower_font(work_dir)
        return
    if family == DEFAULT_MOTION_FONT_FAMILY:
        if not MOTION_FONT_SOURCE.is_file():
            raise RuntimeError("HTML 动效默认中文字体缺失")
        shutil.copyfile(MOTION_FONT_SOURCE, work_dir / MOTION_FONT_FILE)
        return
    raise RuntimeError(f"未支持的 HTML 动效字体：{family}")


def _validate_transparent_layer(layer_path: Path) -> None:
    assert_transparent_layer(probe_media_video_info(layer_path))


def _composite_transparent_layer(
    source: Path,
    layer: Path,
    media: dict[str, Any],
    ffmpeg_bin: str | None,
    *,
    cancel_event=None,
) -> None:
    composite_transparent_layer(
        source,
        layer,
        media,
        resolve_ffmpeg_bin(ffmpeg_bin),
        run=subprocess.run,
        before_replace=lambda: _raise_if_cancelled(cancel_event),
    )


def _validate_composited_video(source: Path, expected: dict[str, Any]) -> None:
    validate_composited_video(probe_media_video_info(source), expected)


def _raise_if_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise HyperFramesWorkerCancelled()


def os_replace(source: Path, target: Path) -> None:
    source.replace(target)


def _probe_video_info(video_path: Path) -> dict[str, Any]:
    media = probe_media_video_info(video_path)
    width = int((media or {}).get("width") or 0)
    height = int((media or {}).get("height") or 0)
    duration = float((media or {}).get("durationSeconds") or 0)
    if width <= 0 or height <= 0 or duration <= 0:
        raise RuntimeError("无法读取基础视频尺寸或时长")
    return {"width": width, "height": height, "durationSeconds": duration}


def _create_work_dir(work_id: str) -> Path:
    ensure_user_file_root()
    HTML_MOTION_DIR.mkdir(parents=True, exist_ok=True)
    root = HTML_MOTION_DIR.resolve()
    path = Path(tempfile.mkdtemp(prefix=f"render-{work_id}-", dir=root)).resolve()
    if not path.is_relative_to(root):
        raise RuntimeError("HTML 动效工作目录越界")
    return path


def _cleanup_work_dir(work_dir: Path) -> None:
    if work_dir.exists() and work_dir.parent.resolve() == HTML_MOTION_DIR.resolve():
        shutil.rmtree(work_dir, ignore_errors=True)


def _result(
    status: str,
    reason: str,
    *,
    runtime: dict[str, Any] | None = None,
    work_id: str | None = None,
    media: dict[str, Any] | None = None,
    timeline: dict[str, Any] | None = None,
    work_cleaned: bool | None = None,
) -> dict[str, Any]:
    return {
        "enabled": status != "skipped",
        "status": status,
        "reason": reason,
        "renderer": (runtime or {}).get("renderer", "hyperframes"),
        "rendererVersion": (runtime or {}).get("rendererVersion", HYPERFRAMES_VERSION),
        "harness": (runtime or {}).get("harness", "hyperframes-overlay-v1"),
        "workId": work_id,
        "workCleaned": bool(work_cleaned) if work_cleaned is not None else False,
        "durationSeconds": None if media is None else media.get("durationSeconds"),
        "timeline": timeline,
        "videoEncoding": video_postprocess_encoding_meta() if status == "applied" else None,
    }


def _notify_stage(
    callback: Callable[[str, dict[str, Any] | None], None] | None,
    stage: str,
    event: dict[str, Any] | None = None,
) -> None:
    if callback is not None:
        callback(stage, event)


def _safe_error(exc: Exception) -> str:
    text = (str(exc).strip() or exc.__class__.__name__)[:500]
    lowered = text.lower()
    # Keep already-localized diagnostics instead of collapsing them.
    if any(
        token in text
        for token in (
            "版式或时间线校验未通过",
            "透明",
            "依赖未安装",
            "无法读取基础视频",
            "WAAPI",
            "并发编排均失败",
        )
    ):
        return text[:300]
    if any(token in lowered for token in ("timeout", "timed out", "超时")):
        return "动效生成等待超时"
    if any(token in lowered for token in ("connection", "response ended", "reset by peer", "连接中断")):
        return "动效方案服务连接中断"
    if any(token in lowered for token in ("ffmpeg", "candidate", "透明视频流")):
        return "预览视频合成未完成"
    if any(token in lowered for token in ("hyperframes", "composition", "artifact", "worker", "node.js")):
        return f"动效渲染未完成：{text[:160]}" if text else "动效渲染未完成"
    if any(char.isascii() and char.isalpha() for char in text) and not any(
        char >= "\u4e00" and char <= "\u9fff" for char in text
    ):
        return "动效生成未完成"
    return text[:300]


def _normalize_text_style(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    # HTML motion uses a much lighter stroke than flower-text burn-in.
    raw_stroke = source.get("strokeWidth")
    try:
        stroke = float(raw_stroke)
    except (TypeError, ValueError):
        stroke = 0.8
    if stroke > 3.0:
        stroke = min(1.2, stroke / 8.0)
    return {
        "textColor": _safe_hex(source.get("textColor"), "#F7F3EC"),
        "strokeColor": _safe_hex(source.get("strokeColor"), "#121826"),
        "strokeWidth": _safe_number(stroke, 0.8, 0.0, 1.5),
    }


def _resolve_html_motion_dialogue(episode: EpisodePrompt) -> str:
    summary = str(getattr(episode, "source_summary", "") or "").strip()
    if summary:
        return summary
    prompt = str(getattr(episode, "prompt", "") or "").strip()
    return prompt


def _safe_hex(value: Any, fallback: str) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if re.fullmatch(r"#[0-9A-F]{6}", candidate) else fallback


def _safe_number(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return round(min(max(number, minimum), maximum), 2)


def _tail_process_output(value: object, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-4:])[-limit:]


def _hyperframes_check_error(result: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    findings = _hyperframes_check_findings(output)
    if findings:
        return f"HyperFrames 版式或时间线校验未通过：{'；'.join(findings)[:180]}"
    errors = [line.strip() for line in output.splitlines() if _is_hyperframes_error_line(line)]
    detail = " ".join(errors).strip()
    return f"HyperFrames 版式或时间线校验未通过：{detail[:180]}" if detail else "HyperFrames 版式或时间线校验未通过"


def _hyperframes_check_findings(output: str) -> list[str]:
    payload = _hyperframes_check_payload(output)
    if not isinstance(payload, dict):
        return []
    findings: list[str] = []
    for section in ("lint", "runtime", "layout", "motion", "contrast"):
        values = payload.get(section, {}).get("findings", [])
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict) or str(item.get("severity", "")).lower() != "error":
                continue
            detail = str(item.get("message") or item.get("detail") or item.get("rule") or item.get("code") or "").strip()
            if not detail:
                detail = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            if detail and detail not in findings:
                findings.append(detail)
    return findings


def _hyperframes_check_payload(output: str) -> dict[str, Any] | None:
    start = output.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(output[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _is_hyperframes_error_line(line: str) -> bool:
    lowered = line.strip().lower()
    return bool(lowered) and not lowered.startswith("[info]") and any(
        token in lowered for token in ("error", "fail", "invalid", "violation", "mismatch")
    )


def _hyperframes_cli_path() -> Path | None:
    candidates = (
        PROJECT_ROOT / "node_modules" / "hyperframes" / "dist" / "cli.js",
        PROJECT_ROOT / "node_modules" / ".bin" / "hyperframes",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _node_source_path() -> str | None:
    configured = str(os.getenv("AI8VIDEO_NODE_BIN") or "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path(shutil.which("node") or ""),
        Path.home() / ".local" / "bin" / "node",
        Path("/opt/homebrew/bin/node"),
        Path("/usr/local/bin/node"),
        PROJECT_ROOT / ".node" / "bin" / "node",
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _read_settings() -> dict[str, Any]:
    try:
        data = json.loads(HTML_MOTION_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(data: dict[str, Any]) -> None:
    ensure_user_file_root()
    HTML_MOTION_DIR.mkdir(parents=True, exist_ok=True)
    HTML_MOTION_SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_quality_retry_count(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = HTML_MOTION_QUALITY_RETRY_DEFAULT
    return min(max(number, HTML_MOTION_QUALITY_RETRY_MIN), HTML_MOTION_QUALITY_RETRY_MAX)


def _normalize_beat_interval_seconds(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(HTML_MOTION_BEAT_INTERVAL_DEFAULT)
    clamped = min(max(number, HTML_MOTION_BEAT_INTERVAL_MIN), HTML_MOTION_BEAT_INTERVAL_MAX)
    return round(clamped, 1)


def html_motion_safe_zone_for_media(media: dict[str, Any]) -> dict[str, float]:
    ratio = _aspect_ratio_for_dimensions(int(media["width"]), int(media["height"]))
    return html_motion_safe_zone_status(ratio)["safeZone"]


def _normalize_safe_zones(value: Any) -> dict[str, dict[str, float]]:
    source = value if isinstance(value, dict) else {}
    return {
        ratio: _normalize_safe_zone(source.get(ratio), fallback)
        for ratio, fallback in HTML_MOTION_SAFE_ZONE_DEFAULTS.items()
    }


def _normalize_safe_zone(value: Any, fallback: dict[str, float]) -> dict[str, float]:
    source = value if isinstance(value, dict) else {}
    width = _safe_zone_number(source.get("width"), fallback["width"], 16.0, 96.0)
    height = _safe_zone_number(source.get("height"), fallback["height"], 16.0, 96.0)
    x = _safe_zone_number(source.get("x"), fallback["x"], 0.0, 100.0 - width)
    y = _safe_zone_number(source.get("y"), fallback["y"], 0.0, 100.0 - height)
    return {"x": x, "y": y, "width": width, "height": height}


def _safe_zone_number(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return round(min(max(number, minimum), maximum), 2)


def _normalize_aspect_ratio(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in HTML_MOTION_SAFE_ZONE_DEFAULTS else "9:16"


def _aspect_ratio_for_dimensions(width: int, height: int) -> str:
    if abs(width - height) <= max(width, height) * 0.08:
        return "1:1"
    return "16:9" if width > height else "9:16"
