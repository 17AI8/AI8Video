from __future__ import annotations

import json
import importlib
import os
import re
import subprocess
import tempfile
from hashlib import sha1
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Any

from ai8video.media.ffmpeg_utils import resolve_ffmpeg_bin, resolve_ffprobe_bin
from ai8video.assets.user_materials import IMAGE_MATERIAL_EXTENSIONS, USER_FLOWER_WATERMARK_DIR
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root
from ai8video.core.paths import PROJECT_ROOT
from ai8video.media.video_encoding import append_video_postprocess_encoding_args


VIDEO_TEXT_OVERLAY_DIR = (USER_FILE_ROOT / "花字").resolve()
VIDEO_TEXT_OVERLAY_SETTINGS_PATH = VIDEO_TEXT_OVERLAY_DIR / "settings.json"
VIDEO_TEXT_PREVIEW_BACKGROUND_DIR = (VIDEO_TEXT_OVERLAY_DIR / "预览背景图").resolve()
USER_FONT_DIR = (PROJECT_ROOT / "用户字体").resolve()
USER_FONT_PREVIEW_DIR = (USER_FONT_DIR / "字体预览").resolve()
SUPPORTED_FONT_EXTENSIONS = {".ttf", ".otf", ".ttc"}
FONT_NAME_OVERRIDES = {
    "SourceHanSansSC-ExtraLight.otf": "思源黑体 ExtraLight",
    "SourceHanSansSC-Light.otf": "思源黑体 Light",
    "SourceHanSansSC-Normal.otf": "思源黑体 Normal",
    "SourceHanSansSC-Regular.otf": "思源黑体 Regular",
    "SourceHanSansSC-Medium.otf": "思源黑体 Medium",
    "SourceHanSansSC-Bold.otf": "思源黑体 Bold",
    "SourceHanSansSC-Heavy.otf": "思源黑体 Heavy",
    "SourceHanSerifSC-ExtraLight.otf": "思源宋体 ExtraLight",
    "SourceHanSerifSC-Light.otf": "思源宋体 Light",
    "SourceHanSerifSC-Regular.otf": "思源宋体 Regular",
    "SourceHanSerifSC-Medium.otf": "思源宋体 Medium",
    "SourceHanSerifSC-SemiBold.otf": "思源宋体 SemiBold",
    "SourceHanSerifSC-Bold.otf": "思源宋体 Bold",
    "SourceHanSerifSC-Heavy.otf": "思源宋体 Heavy",
    "ZCOOLQingKeHuangYou-Regular.ttf": "站酷庆科黄油体",
    "ZCOOLKuaiLe-Regular.ttf": "站酷快乐体",
    "ZCOOLXiaoWei-Regular.ttf": "站酷小薇 LOGO 体",
    "MaShanZheng-Regular.ttf": "马善政毛笔楷书",
    "ZhiMangXing-Regular.ttf": "志莽行书",
    "LongCang-Regular.ttf": "龙藏体",
    "LiuJianMaoCao-Regular.ttf": "刘建毛草",
    "LXGWMarkerGothic-Regular.ttf": "霞鹜漫黑",
    "jf-openhuninn-2.1.ttf": "粉圆体",
    "HachiMaruPop-Regular.ttf": "Hachi Maru Pop",
    "ReggaeOne-Regular.ttf": "Reggae One",
    "RocknRollOne-Regular.ttf": "RocknRoll One",
    "RampartOne-Regular.ttf": "Rampart One",
    "DelaGothicOne-Regular.ttf": "Dela Gothic One",
    "YujiMai-Regular.ttf": "佑字舞",
    "YujiSyuku-Regular.ttf": "佑字熟",
    "YujiBoku-Regular.ttf": "佑字朴",
    "KleeOne-Regular.ttf": "Klee One Regular",
    "KleeOne-SemiBold.ttf": "Klee One SemiBold",
    "KaiseiDecol-Regular.ttf": "Kaisei Decol Regular",
    "KaiseiDecol-Bold.ttf": "Kaisei Decol Bold",
    "KaiseiHarunoUmi-Regular.ttf": "Kaisei HarunoUmi",
    "KaiseiOpti-Regular.ttf": "Kaisei Opti",
    "KaiseiTokumin-Regular.ttf": "Kaisei Tokumin",
    "ShipporiMincho-Regular.ttf": "Shippori Mincho Regular",
    "ShipporiMincho-Bold.ttf": "Shippori Mincho Bold",
    "ShipporiMinchoB1-Regular.ttf": "Shippori Mincho B1",
    "SawarabiGothic-Regular.ttf": "Sawarabi Gothic",
    "SawarabiMincho-Regular.ttf": "Sawarabi Mincho",
    "ZenMaruGothic-Regular.ttf": "Zen Maru Gothic Regular",
    "ZenMaruGothic-Bold.ttf": "Zen Maru Gothic Bold",
    "ZenKakuGothicNew-Regular.ttf": "Zen Kaku Gothic New Regular",
    "ZenKakuGothicNew-Bold.ttf": "Zen Kaku Gothic New Bold",
    "ZenKurenaido-Regular.ttf": "Zen Kurenaido",
}
DEFAULT_CANVAS_WIDTH = 9
DEFAULT_CANVAS_HEIGHT = 16
MAX_CANVAS_SIDE = 1600
MIN_FONT_SIZE = 24
DEFAULT_TEXT_COLOR = "#ffee43"
DEFAULT_STROKE_COLOR = "#121826"
DEFAULT_FONT_SIZE = 16
DEFAULT_FONT_WEIGHT = 800
DEFAULT_FONT_FAMILY = ""
DEFAULT_STROKE_WIDTH = 8
DEFAULT_POSITION = "center"
DEFAULT_TEXT_X = 50
DEFAULT_TEXT_Y = 50
DEFAULT_WATERMARK_ENABLED = False
DEFAULT_WATERMARK_IMAGE = ""
DEFAULT_WATERMARK_SIZE = 18
LEGACY_WATERMARK_OPACITY = 42
DEFAULT_WATERMARK_OPACITY = 100
DEFAULT_WATERMARK_POSITION = "bottom-right"
WATERMARK_POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right", "center"}
DEFAULT_WATERMARK_X = 92
DEFAULT_WATERMARK_Y = 92
DEFAULT_PREVIEW_BACKGROUND_COLOR = "#303844"
DEFAULT_PREVIEW_BACKGROUND_IMAGE = ""
DEFAULT_ANIMATION_DELAY_SECONDS = 0
ANIMATION_DELAY_SECONDS_OPTIONS = {0, 1, 3, 5, 10}
DEFAULT_ANIMATION_TYPE = "fade"
ANIMATION_TYPE_OPTIONS = {"fade", "none"}


def video_text_overlay_status() -> dict[str, Any]:
    settings = _normalize_settings(_read_settings())
    return {
        "ok": True,
        **settings,
        "fontName": _selected_font_name(settings["fontFamily"]),
        "availableFonts": list_video_text_overlay_fonts(),
        "runtime": video_text_overlay_runtime_status(settings),
    }


def selected_video_text_overlay_font_path() -> Path | None:
    settings = _normalize_settings(_read_settings())
    return _resolve_user_font(settings["fontFamily"])


def video_text_overlay_runtime_status(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    config = _normalize_settings(settings if settings is not None else _read_settings())
    pillow = _pillow_runtime_status()
    enabled = bool(config.get("enabled"))
    text_present = bool(_clean_text(config.get("text")))
    watermark_present = _watermark_slot_present(config, "watermark")
    watermark2_present = _watermark_slot_present(config, "watermark2")
    blocking_reason = ""
    if enabled and (text_present or watermark_present or watermark2_present) and not pillow["available"]:
        blocking_reason = "缺少 Pillow/PIL，无法渲染花字图片"
    return {
        "enabled": enabled,
        "textPresent": text_present,
        "watermarkPresent": watermark_present or watermark2_present,
        "watermark1Present": watermark_present,
        "watermark2Present": watermark2_present,
        "ready": not blocking_reason,
        "blockingReason": blocking_reason,
        "pillow": pillow,
    }


def _watermark_slot_present(settings: dict[str, Any], prefix: str) -> bool:
    return bool(
        settings.get(f"{prefix}Enabled")
        and _resolve_watermark_image(settings.get(f"{prefix}Image"))
    )


def list_video_text_overlay_fonts() -> list[dict[str, str]]:
    USER_FONT_DIR.mkdir(parents=True, exist_ok=True)
    root = USER_FONT_DIR.resolve()
    fonts: list[dict[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_FONT_EXTENSIONS:
            continue
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        fonts.append({
            "id": relative,
            "name": _font_display_name(path, relative),
            "fontUrl": _font_file_url(path, relative),
            "previewUrl": _font_preview_url(path, relative),
        })
    return fonts


def update_video_text_overlay(
    *,
    enabled: bool | None = None,
    text: str | None = None,
    canvas_width: int | float | str | None = None,
    canvas_height: int | float | str | None = None,
    text_color: str | None = None,
    stroke_color: str | None = None,
    font_family: str | None = None,
    font_size: int | float | str | None = None,
    font_weight: int | float | str | None = None,
    stroke_width: int | float | str | None = None,
    position: str | None = None,
    text_x: int | float | str | None = None,
    text_y: int | float | str | None = None,
    animation_delay_seconds: int | float | str | None = None,
    animation_type: str | None = None,
    watermark_enabled: bool | None = None,
    watermark_image: str | None = None,
    watermark_size: int | float | str | None = None,
    watermark_opacity: int | float | str | None = None,
    watermark_animation_delay_seconds: int | float | str | None = None,
    watermark_animation_type: str | None = None,
    watermark_position: str | None = None,
    watermark_x: int | float | str | None = None,
    watermark_y: int | float | str | None = None,
    watermark2_enabled: bool | None = None,
    watermark2_image: str | None = None,
    watermark2_size: int | float | str | None = None,
    watermark2_opacity: int | float | str | None = None,
    watermark2_animation_delay_seconds: int | float | str | None = None,
    watermark2_animation_type: str | None = None,
    watermark2_position: str | None = None,
    watermark2_x: int | float | str | None = None,
    watermark2_y: int | float | str | None = None,
    preview_background_color: str | None = None,
    preview_background_image: str | None = None,
) -> dict[str, Any]:
    current = _normalize_settings(_read_settings())
    if enabled is not None:
        current["enabled"] = bool(enabled)
    if text is not None:
        current["text"] = _clean_text(text)
    if canvas_width is not None:
        current["canvasWidth"] = _clean_canvas_side(canvas_width, DEFAULT_CANVAS_WIDTH)
    if canvas_height is not None:
        current["canvasHeight"] = _clean_canvas_side(canvas_height, DEFAULT_CANVAS_HEIGHT)
    if text_color is not None:
        current["textColor"] = _clean_hex_color(text_color, DEFAULT_TEXT_COLOR)
    if stroke_color is not None:
        current["strokeColor"] = _clean_hex_color(stroke_color, DEFAULT_STROKE_COLOR)
    if font_family is not None:
        current["fontFamily"] = _clean_font_family(font_family)
    if font_size is not None:
        current["fontSize"] = _clean_percent(font_size, DEFAULT_FONT_SIZE, 6, 28)
    if font_weight is not None:
        current["fontWeight"] = _clean_font_weight(font_weight, DEFAULT_FONT_WEIGHT)
    if stroke_width is not None:
        current["strokeWidth"] = _clean_percent(stroke_width, DEFAULT_STROKE_WIDTH, 0, 18)
    if position is not None:
        current["position"] = _clean_position(position)
        if text_y is None:
            current["textY"] = _position_to_text_y(current["position"])
    if text_x is not None:
        current["textX"] = _clean_coordinate(text_x, DEFAULT_TEXT_X)
    if text_y is not None:
        current["textY"] = _clean_coordinate(text_y, DEFAULT_TEXT_Y)
    if animation_delay_seconds is not None:
        current["animationDelaySeconds"] = _clean_animation_delay_seconds(animation_delay_seconds)
    if animation_type is not None:
        current["animationType"] = _clean_animation_type(animation_type)
    if watermark_enabled is not None:
        current["watermarkEnabled"] = bool(watermark_enabled)
    if watermark_image is not None:
        current["watermarkImage"] = _clean_watermark_image(watermark_image)
    if watermark_size is not None:
        current["watermarkSize"] = _clean_percent(watermark_size, DEFAULT_WATERMARK_SIZE, 5, 200)
    if watermark_opacity is not None:
        current["watermarkOpacity"] = _clean_percent(watermark_opacity, DEFAULT_WATERMARK_OPACITY, 5, 100)
    if watermark_animation_delay_seconds is not None:
        current["watermarkAnimationDelaySeconds"] = _clean_animation_delay_seconds(watermark_animation_delay_seconds)
    if watermark_animation_type is not None:
        current["watermarkAnimationType"] = _clean_animation_type(watermark_animation_type)
    if watermark_position is not None:
        current["watermarkPosition"] = _clean_watermark_position(watermark_position)
        if watermark_x is None:
            current["watermarkX"] = _watermark_position_to_x(current["watermarkPosition"])
        if watermark_y is None:
            current["watermarkY"] = _watermark_position_to_y(current["watermarkPosition"])
    if watermark_x is not None:
        current["watermarkX"] = _clean_coordinate(
            watermark_x,
            _watermark_position_to_x(current["watermarkPosition"]),
        )
    if watermark_y is not None:
        current["watermarkY"] = _clean_coordinate(
            watermark_y,
            _watermark_position_to_y(current["watermarkPosition"]),
        )
    if watermark2_enabled is not None:
        current["watermark2Enabled"] = bool(watermark2_enabled)
    if watermark2_image is not None:
        current["watermark2Image"] = _clean_watermark_image(watermark2_image)
    if watermark2_size is not None:
        current["watermark2Size"] = _clean_percent(watermark2_size, DEFAULT_WATERMARK_SIZE, 5, 200)
    if watermark2_opacity is not None:
        current["watermark2Opacity"] = _clean_percent(watermark2_opacity, DEFAULT_WATERMARK_OPACITY, 5, 100)
    if watermark2_animation_delay_seconds is not None:
        current["watermark2AnimationDelaySeconds"] = _clean_animation_delay_seconds(watermark2_animation_delay_seconds)
    if watermark2_animation_type is not None:
        current["watermark2AnimationType"] = _clean_animation_type(watermark2_animation_type)
    if watermark2_position is not None:
        current["watermark2Position"] = _clean_watermark_position(watermark2_position)
        if watermark2_x is None:
            current["watermark2X"] = _watermark_position_to_x(current["watermark2Position"])
        if watermark2_y is None:
            current["watermark2Y"] = _watermark_position_to_y(current["watermark2Position"])
    if watermark2_x is not None:
        current["watermark2X"] = _clean_coordinate(
            watermark2_x,
            _watermark_position_to_x(current["watermark2Position"]),
        )
    if watermark2_y is not None:
        current["watermark2Y"] = _clean_coordinate(
            watermark2_y,
            _watermark_position_to_y(current["watermark2Position"]),
        )
    if preview_background_color is not None:
        current["previewBackgroundColor"] = _clean_hex_color(
            preview_background_color,
            DEFAULT_PREVIEW_BACKGROUND_COLOR,
        )
    if preview_background_image is not None:
        current["previewBackgroundImage"] = _clean_preview_background_image(preview_background_image)
    current["updatedAt"] = datetime.now(timezone.utc).isoformat()
    _write_settings(current)
    return video_text_overlay_status()


def save_video_text_preview_background_upload(upload_name: str, payload: bytes) -> dict[str, Any]:
    source_name = Path(str(upload_name or "")).name
    suffix = Path(source_name).suffix.lower()
    if not source_name or suffix not in IMAGE_MATERIAL_EXTENSIONS:
        raise ValueError("unsupported preview background extension")
    VIDEO_TEXT_PREVIEW_BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    _clear_directory_files(VIDEO_TEXT_PREVIEW_BACKGROUND_DIR)
    target = VIDEO_TEXT_PREVIEW_BACKGROUND_DIR / source_name
    target.write_bytes(payload)
    status = update_video_text_overlay(preview_background_image=target.name)
    return {
        "ok": True,
        "background": {
            "name": target.name,
            "relativePath": target.name,
            "path": str(target),
            "sizeBytes": target.stat().st_size,
            "url": video_text_preview_background_url(target.name),
        },
        "settings": status,
    }


def clear_video_text_preview_background_image() -> dict[str, Any]:
    VIDEO_TEXT_PREVIEW_BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    _clear_directory_files(VIDEO_TEXT_PREVIEW_BACKGROUND_DIR)
    return update_video_text_overlay(preview_background_image="")


def video_text_preview_background_url(relative_path: str) -> str:
    clean_path = _clean_preview_background_image(relative_path)
    if not clean_path:
        return ""
    return f"/video-text-overlay-preview-background/{quote(clean_path, safe='/')}"


def render_video_text_overlay_preview(
    settings: dict[str, Any] | None = None,
    *,
    target_width: int | float | str | None = None,
    target_height: int | float | str | None = None,
) -> bytes:
    config = _normalize_settings(settings if settings is not None else _read_settings())
    ratio_w = int(config["canvasWidth"])
    ratio_h = int(config["canvasHeight"])
    width = _clean_preview_side(target_width, 416 if ratio_h >= ratio_w else 720)
    height = _clean_preview_side(target_height, 720 if ratio_h >= ratio_w else 416)
    image = _render_overlay_image(config, target_size=(width, height))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def apply_video_text_overlay(
    video_path: Path | str,
    *,
    ffmpeg_bin: str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    video = Path(video_path)
    config = _normalize_settings(settings if settings is not None else _read_settings())
    text = _clean_text(config.get("text"))
    watermark_present = _watermark_slot_present(config, "watermark")
    watermark2_present = _watermark_slot_present(config, "watermark2")
    if not config.get("enabled"):
        return {"enabled": False, "status": "skipped", "reason": "disabled"}
    if not text and not watermark_present and not watermark2_present:
        return {"enabled": True, "status": "skipped", "reason": "empty overlay"}
    if not video.is_file():
        return {"enabled": True, "status": "skipped", "reason": "video file missing"}
    target_size = _probe_video_size(video)
    overlay_layers: list[tuple[str, Path, int, str]] = []
    try:
        text_delay = _clean_animation_delay_seconds(config.get("animationDelaySeconds"))
        text_animation_type = _clean_animation_type(config.get("animationType"))
        watermark_delay = _clean_animation_delay_seconds(config.get("watermarkAnimationDelaySeconds"))
        watermark_animation_type = _clean_animation_type(config.get("watermarkAnimationType"))
        watermark2_delay = _clean_animation_delay_seconds(config.get("watermark2AnimationDelaySeconds"))
        watermark2_animation_type = _clean_animation_type(config.get("watermark2AnimationType"))
        if text:
            overlay_layers.append(("text", _render_overlay_png(config, target_size=target_size, layer="text"), text_delay, text_animation_type))
        if watermark_present:
            overlay_layers.append(("watermark", _render_overlay_png(config, target_size=target_size, layer="watermark"), watermark_delay, watermark_animation_type))
        if watermark2_present:
            overlay_layers.append(("watermark2", _render_overlay_png(config, target_size=target_size, layer="watermark2"), watermark2_delay, watermark2_animation_type))
    except Exception as exc:
        for _, path, _, _ in overlay_layers:
            try:
                path.unlink()
            except OSError:
                pass
        return {"enabled": True, "status": "failed", "reason": f"render overlay failed: {str(exc)[:400]}"}
    if not overlay_layers:
        return {"enabled": True, "status": "skipped", "reason": "empty overlay"}
    temp_video = video.with_name(f"{video.stem}.with-text.tmp{video.suffix or '.mp4'}")
    if temp_video.exists():
        temp_video.unlink()
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    cmd = [ffmpeg, "-y", "-i", str(video)]
    for _, overlay_path, _, _ in overlay_layers:
        cmd.extend(["-loop", "1", "-i", str(overlay_path)])
    filter_parts: list[str] = []
    base_label = "0:v"
    for index, (layer_name, _, delay, animation_type) in enumerate(overlay_layers, start=1):
        overlay_label = f"overlay{index}"
        out_label = "vout" if index == len(overlay_layers) else f"v{index}"
        fade_filter = ""
        overlay_enable = ""
        if delay > 0:
            if animation_type == "fade":
                fade_filter = f",fade=t=in:st={delay}:d=1:alpha=1"
            else:
                overlay_enable = f":enable='gte(t,{delay})'"
        filter_parts.append(f"[{index}:v]format=rgba{fade_filter}[{overlay_label}]")
        filter_parts.append(f"[{base_label}][{overlay_label}]overlay=0:0{overlay_enable}:shortest=1:format=auto[{out_label}]")
        base_label = out_label
    filter_complex = ";".join(filter_parts)
    cmd.extend([
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "0:a?",
    ])
    video_encoding = append_video_postprocess_encoding_args(cmd)
    cmd.extend([
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(temp_video),
    ])
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        os.replace(temp_video, video)
    except FileNotFoundError:
        return {"enabled": True, "status": "failed", "reason": "ffmpeg not found"}
    except subprocess.CalledProcessError as exc:
        if temp_video.exists():
            temp_video.unlink()
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        return {"enabled": True, "status": "failed", "reason": message[-500:] or "花字烧录失败"}
    except Exception as exc:
        if temp_video.exists():
            temp_video.unlink()
        return {"enabled": True, "status": "failed", "reason": str(exc)[:500]}
    finally:
        for _, overlay_path, _, _ in overlay_layers:
            try:
                overlay_path.unlink()
            except OSError:
                pass
    return {
        "enabled": True,
        "status": "burned",
        "video": str(video),
        "canvasWidth": config["canvasWidth"],
        "canvasHeight": config["canvasHeight"],
        "textX": config["textX"],
        "textY": config["textY"],
        "position": config["position"],
        "fontFamily": config["fontFamily"],
        "fontName": _selected_font_name(config["fontFamily"]),
        "fontSize": config["fontSize"],
        "fontWeight": config["fontWeight"],
        "strokeWidth": config["strokeWidth"],
        "textColor": config["textColor"],
        "strokeColor": config["strokeColor"],
        "watermarkEnabled": config["watermarkEnabled"],
        "watermarkImage": config["watermarkImage"],
        "watermarkSize": config["watermarkSize"],
        "watermarkOpacity": config["watermarkOpacity"],
        "watermarkAnimationDelaySeconds": config["watermarkAnimationDelaySeconds"],
        "watermarkPosition": config["watermarkPosition"],
        "watermarkX": config["watermarkX"],
        "watermarkY": config["watermarkY"],
        "watermark2Enabled": config["watermark2Enabled"],
        "watermark2Image": config["watermark2Image"],
        "watermark2Size": config["watermark2Size"],
        "watermark2Opacity": config["watermark2Opacity"],
        "watermark2AnimationDelaySeconds": config["watermark2AnimationDelaySeconds"],
        "watermark2Position": config["watermark2Position"],
        "watermark2X": config["watermark2X"],
        "watermark2Y": config["watermark2Y"],
        "watermarkPresent": watermark_present or watermark2_present,
        "watermark1Present": watermark_present,
        "watermark2Present": watermark2_present,
        "targetVideoSize": None if target_size is None else {"width": target_size[0], "height": target_size[1]},
        "textLength": len(text),
        "videoEncoding": video_encoding,
    }


def _probe_video_size(video_path: Path) -> tuple[int, int] | None:
    cmd = [
        resolve_ffprobe_bin(),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(video_path),
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=15)
        data = json.loads(proc.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except Exception:
        return _probe_video_size_with_ffmpeg(video_path)
    if width <= 0 or height <= 0:
        return _probe_video_size_with_ffmpeg(video_path)
    return width, height


def _probe_video_size_with_ffmpeg(video_path: Path) -> tuple[int, int] | None:
    cmd = [
        resolve_ffmpeg_bin(),
        "-hide_banner",
        "-i",
        str(video_path),
    ]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    output = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    for match in re.finditer(r"Video:.*?(\d{2,5})x(\d{2,5})", output):
        width = int(match.group(1))
        height = int(match.group(2))
        if width > 0 and height > 0:
            return width, height
    return None


def _render_overlay_png(
    settings: dict[str, Any],
    target_size: tuple[int, int] | None = None,
    *,
    layer: str = "all",
) -> Path:
    image = _render_overlay_image(settings, target_size=target_size, layer=layer)
    fd, temp_path = tempfile.mkstemp(prefix="ai8video-text-overlay-", suffix=".png")
    os.close(fd)
    target = Path(temp_path)
    image.save(target)
    return target


def _render_overlay_image(
    settings: dict[str, Any],
    target_size: tuple[int, int] | None = None,
    *,
    layer: str = "all",
) -> Any:
    from PIL import Image, ImageDraw

    settings = _normalize_settings(settings)
    ratio_w = int(settings["canvasWidth"])
    ratio_h = int(settings["canvasHeight"])
    if target_size:
        width, height = target_size
    elif ratio_h >= ratio_w:
        height = MAX_CANVAS_SIDE
        width = max(240, round(height * ratio_w / ratio_h))
    else:
        width = MAX_CANVAS_SIDE
        height = max(240, round(width * ratio_h / ratio_w))
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    text = _clean_text(settings.get("text"))
    box_x, box_y, box_w, box_h = _fit_ratio_box(width, height, ratio_w, ratio_h)
    padding_x = max(12, round(box_w * 0.07))
    padding_y = max(12, round(box_h * 0.08))
    if layer in {"all", "watermark"}:
        _draw_watermark_image(image, settings, (box_x, box_y, box_w, box_h), prefix="watermark")
    if layer in {"all", "watermark2"}:
        _draw_watermark_image(image, settings, (box_x, box_y, box_w, box_h), prefix="watermark2")
    if layer not in {"all", "text"}:
        return image
    max_text_width = box_w - padding_x * 2
    max_text_height = box_h - padding_y * 2
    font = None
    lines: list[str] = []
    line_gap = 0
    requested_size = max(MIN_FONT_SIZE, round(min(box_w, box_h) * int(settings["fontSize"]) / 100))
    font_family = _clean_font_family(settings.get("fontFamily"))
    font_weight = _clean_font_weight(settings.get("fontWeight"), DEFAULT_FONT_WEIGHT)
    for size in range(requested_size, MIN_FONT_SIZE - 1, -2):
        candidate_font = _load_font(size, font_weight, font_family)
        candidate_stroke_width = round(size * int(settings["strokeWidth"]) / 100)
        candidate_lines = _wrap_text(
            draw,
            text,
            candidate_font,
            max_text_width,
            font_weight,
            candidate_stroke_width,
        )
        candidate_gap = max(4, round(size * 0.18))
        total_height = _text_block_height(
            draw,
            candidate_lines,
            candidate_font,
            candidate_gap,
            font_weight,
            candidate_stroke_width,
        )
        widest = max(
            (
                _text_size(draw, line, candidate_font, font_weight, candidate_stroke_width)[0]
                for line in candidate_lines
            ),
            default=0,
        )
        if widest <= max_text_width and total_height <= max_text_height:
            font = candidate_font
            lines = candidate_lines
            line_gap = candidate_gap
            break
    if font is None:
        font = _load_font(MIN_FONT_SIZE, font_weight, font_family)
        fallback_stroke_width = round(getattr(font, "size", MIN_FONT_SIZE) * int(settings["strokeWidth"]) / 100)
        lines = _wrap_text(draw, text, font, max_text_width, font_weight, fallback_stroke_width)
        line_gap = 4
    stroke_width = round(getattr(font, "size", MIN_FONT_SIZE) * int(settings["strokeWidth"]) / 100)
    total_height = _text_block_height(draw, lines, font, line_gap, font_weight, stroke_width)
    text_x = _clean_coordinate(settings.get("textX"), DEFAULT_TEXT_X)
    text_y = _clean_coordinate(settings.get("textY"), DEFAULT_TEXT_Y)
    center_x = round(box_x + box_w * text_x / 100)
    center_y = round(box_y + box_h * text_y / 100)
    y = round(center_y - total_height / 2)
    # textY describes the visual center selected in the draggable preview. Keep
    # wrapping padding separate from placement, otherwise a value near 95% is
    # silently pulled upward again when the rendered preview replaces the live
    # editor after pointer release.
    y = max(box_y, min(y, box_y + box_h - total_height))
    fill = _hex_to_rgba(settings["textColor"], (255, 238, 67, 255))
    stroke_fill = _hex_to_rgba(settings["strokeColor"], (18, 24, 38, 245))
    weight_offsets = _font_weight_offsets(font_weight)
    for line in lines:
        line_width, line_height = _text_size(draw, line, font, font_weight, stroke_width)
        x = round(center_x - line_width / 2)
        x = max(box_x + padding_x, min(x, box_x + box_w - padding_x - line_width))
        bbox = draw.textbbox((0, 0), line or " ", font=font, stroke_width=stroke_width)
        draw_x = x - bbox[0]
        draw_y = y - bbox[1]
        if stroke_width > 0:
            draw.text(
                (draw_x, draw_y),
                line,
                font=font,
                fill=(0, 0, 0, 0),
                stroke_width=stroke_width,
                stroke_fill=stroke_fill,
            )
        for offset_x, offset_y in weight_offsets:
            draw.text(
                (draw_x + offset_x, draw_y + offset_y),
                line,
                font=font,
                fill=fill,
                stroke_width=0,
            )
        y += line_height + line_gap
    return image


def _draw_watermark_image(
    base_image: Any,
    settings: dict[str, Any],
    box: tuple[int, int, int, int],
    *,
    prefix: str = "watermark",
) -> bool:
    enabled_key = f"{prefix}Enabled"
    image_key = f"{prefix}Image"
    size_key = f"{prefix}Size"
    opacity_key = f"{prefix}Opacity"
    position_key = f"{prefix}Position"
    x_key = f"{prefix}X"
    y_key = f"{prefix}Y"
    if not settings.get(enabled_key):
        return False
    path = _resolve_watermark_image(settings.get(image_key))
    if not path:
        return False
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as source:
            watermark = ImageOps.exif_transpose(source).convert("RGBA")
    except Exception:
        return False
    if watermark.width <= 0 or watermark.height <= 0:
        return False
    box_x, box_y, box_w, box_h = box
    size_percent = _clean_percent(settings.get(size_key), DEFAULT_WATERMARK_SIZE, 5, 200)
    max_width = max(1, round(box_w * size_percent / 100))
    max_height = max(1, round(box_h * size_percent / 100))
    scale = min(max_width / watermark.width, max_height / watermark.height)
    target_size = (
        max(1, round(watermark.width * scale)),
        max(1, round(watermark.height * scale)),
    )
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    watermark = watermark.resize(target_size, resample=resample)
    opacity = _clean_percent(settings.get(opacity_key), DEFAULT_WATERMARK_OPACITY, 5, 100) / 100
    if opacity < 1:
        alpha = watermark.getchannel("A").point(lambda value: min(255, max(0, round(value * opacity))))
        watermark.putalpha(alpha)
    position = _clean_watermark_position(settings.get(position_key))
    watermark_x = _clean_coordinate(settings.get(x_key), _watermark_position_to_x(position))
    watermark_y = _clean_coordinate(settings.get(y_key), _watermark_position_to_y(position))
    x = round(box_x + box_w * watermark_x / 100 - watermark.width / 2)
    y = round(box_y + box_h * watermark_y / 100 - watermark.height / 2)
    x = max(box_x, min(x, box_x + box_w - watermark.width))
    y = max(box_y, min(y, box_y + box_h - watermark.height))
    base_image.alpha_composite(watermark, (x, y))
    return True


def _pillow_runtime_status() -> dict[str, Any]:
    try:
        pil_module = importlib.import_module("PIL")
        importlib.import_module("PIL.Image")
        importlib.import_module("PIL.ImageDraw")
        importlib.import_module("PIL.ImageFont")
    except Exception as exc:
        return {
            "available": False,
            "version": "",
            "error": str(exc)[:300],
        }
    return {
        "available": True,
        "version": str(getattr(pil_module, "__version__", "")),
        "error": "",
    }


def _fit_ratio_box(width: int, height: int, ratio_w: int, ratio_h: int) -> tuple[int, int, int, int]:
    video_ratio = width / height
    target_ratio = ratio_w / ratio_h
    if target_ratio >= video_ratio:
        box_w = width
        box_h = max(1, round(width / target_ratio))
    else:
        box_h = height
        box_w = max(1, round(height * target_ratio))
    box_x = round((width - box_w) / 2)
    box_y = round((height - box_h) / 2)
    return box_x, box_y, box_w, box_h


def _wrap_text(
    draw: Any,
    text: str,
    font: Any,
    max_width: int,
    font_weight: int = DEFAULT_FONT_WEIGHT,
    stroke_width: int = 0,
) -> list[str]:
    result: list[str] = []
    for paragraph in text.splitlines() or [""]:
        if not paragraph:
            result.append("")
            continue
        line = ""
        for char in paragraph:
            candidate = f"{line}{char}"
            if line and _text_size(draw, candidate, font, font_weight, stroke_width)[0] > max_width:
                result.append(line)
                line = char
            else:
                line = candidate
        result.append(line)
    while result and result[0] == "":
        result.pop(0)
    while result and result[-1] == "":
        result.pop()
    return result or [""]


def _text_block_height(
    draw: Any,
    lines: list[str],
    font: Any,
    line_gap: int,
    font_weight: int = DEFAULT_FONT_WEIGHT,
    stroke_width: int = 0,
) -> int:
    if not lines:
        return 0
    return (
        sum(_text_size(draw, line or " ", font, font_weight, stroke_width)[1] for line in lines)
        + line_gap * max(0, len(lines) - 1)
    )


def _text_size(
    draw: Any,
    text: str,
    font: Any,
    font_weight: Any = DEFAULT_FONT_WEIGHT,
    stroke_width: int = 0,
) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text or " ", font=font, stroke_width=max(0, int(stroke_width)))
    extra = max(abs(x) for x, _ in _font_weight_offsets(_clean_font_weight(font_weight, DEFAULT_FONT_WEIGHT)))
    return max(0, bbox[2] - bbox[0] + extra * 2), max(0, bbox[3] - bbox[1])


def _load_font(size: int, font_weight: int = DEFAULT_FONT_WEIGHT, font_family: str = DEFAULT_FONT_FAMILY) -> Any:
    from PIL import ImageFont

    weight = _clean_font_weight(font_weight, DEFAULT_FONT_WEIGHT)
    user_font = _resolve_user_font(font_family)
    if user_font:
        try:
            return ImageFont.truetype(str(user_font), size=size, index=0)
        except Exception:
            pass
    candidates = []
    if weight >= 700:
        candidates.extend([
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "C:/Windows/Fonts/simhei.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        ])
    elif weight <= 400:
        candidates.extend([
            "/System/Library/Fonts/STHeiti Light.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        ])
    candidates.extend([
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ])
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size=size, index=0)
        except Exception:
            continue
    return ImageFont.load_default()


def _normalize_settings(data: dict[str, Any] | None) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    position = _clean_position(source.get("position"))
    watermark_position = _clean_watermark_position(source.get("watermarkPosition"))
    watermark_opacity = _clean_percent(source.get("watermarkOpacity"), DEFAULT_WATERMARK_OPACITY, 5, 100)
    if watermark_opacity == LEGACY_WATERMARK_OPACITY:
        watermark_opacity = DEFAULT_WATERMARK_OPACITY
    watermark2_position = source.get("watermark2Position")
    if watermark2_position:
        watermark2_position = _clean_watermark_position(watermark2_position)
    else:
        watermark2_position = "bottom-left"
    watermark2_opacity = _clean_percent(source.get("watermark2Opacity"), DEFAULT_WATERMARK_OPACITY, 5, 100)
    if watermark2_opacity == LEGACY_WATERMARK_OPACITY:
        watermark2_opacity = DEFAULT_WATERMARK_OPACITY
    preview_background_image = _clean_preview_background_image(source.get("previewBackgroundImage"))
    return {
        "enabled": bool(source.get("enabled")),
        "text": _clean_text(source.get("text")),
        "canvasWidth": _clean_canvas_side(source.get("canvasWidth"), DEFAULT_CANVAS_WIDTH),
        "canvasHeight": _clean_canvas_side(source.get("canvasHeight"), DEFAULT_CANVAS_HEIGHT),
        "textColor": _clean_hex_color(source.get("textColor"), DEFAULT_TEXT_COLOR),
        "strokeColor": _clean_hex_color(source.get("strokeColor"), DEFAULT_STROKE_COLOR),
        "fontFamily": _clean_font_family(source.get("fontFamily")),
        "fontSize": _clean_percent(source.get("fontSize"), DEFAULT_FONT_SIZE, 6, 28),
        "fontWeight": _clean_font_weight(source.get("fontWeight"), DEFAULT_FONT_WEIGHT),
        "strokeWidth": _clean_percent(source.get("strokeWidth"), DEFAULT_STROKE_WIDTH, 0, 18),
        "position": position,
        "textX": _clean_coordinate(source.get("textX"), DEFAULT_TEXT_X),
        "textY": _clean_coordinate(source.get("textY"), _position_to_text_y(position)),
        "animationDelaySeconds": _clean_animation_delay_seconds(source.get("animationDelaySeconds")),
        "animationType": _clean_animation_type(source.get("animationType")),
        "animationType": _clean_animation_type(source.get("animationType")),
        "watermarkEnabled": bool(source.get("watermarkEnabled", DEFAULT_WATERMARK_ENABLED)),
        "watermarkImage": _clean_watermark_image(source.get("watermarkImage")),
        "watermarkSize": _clean_percent(source.get("watermarkSize"), DEFAULT_WATERMARK_SIZE, 5, 200),
        "watermarkOpacity": watermark_opacity,
        "watermarkAnimationDelaySeconds": _clean_animation_delay_seconds(source.get("watermarkAnimationDelaySeconds")),
        "watermarkAnimationType": _clean_animation_type(source.get("watermarkAnimationType")),
        "watermarkAnimationType": _clean_animation_type(source.get("watermarkAnimationType")),
        "watermarkPosition": watermark_position,
        "watermarkX": _clean_coordinate(source.get("watermarkX"), _watermark_position_to_x(watermark_position)),
        "watermarkY": _clean_coordinate(source.get("watermarkY"), _watermark_position_to_y(watermark_position)),
        "watermark2Enabled": bool(source.get("watermark2Enabled", DEFAULT_WATERMARK_ENABLED)),
        "watermark2Image": _clean_watermark_image(source.get("watermark2Image")),
        "watermark2Size": _clean_percent(source.get("watermark2Size"), DEFAULT_WATERMARK_SIZE, 5, 200),
        "watermark2Opacity": watermark2_opacity,
        "watermark2AnimationDelaySeconds": _clean_animation_delay_seconds(source.get("watermark2AnimationDelaySeconds")),
        "watermark2AnimationType": _clean_animation_type(source.get("watermark2AnimationType")),
        "watermark2AnimationType": _clean_animation_type(source.get("watermark2AnimationType")),
        "watermark2Position": watermark2_position,
        "watermark2X": _clean_coordinate(source.get("watermark2X"), _watermark_position_to_x(watermark2_position)),
        "watermark2Y": _clean_coordinate(source.get("watermark2Y"), _watermark_position_to_y(watermark2_position)),
        "previewBackgroundColor": _clean_hex_color(source.get("previewBackgroundColor"), DEFAULT_PREVIEW_BACKGROUND_COLOR),
        "previewBackgroundImage": preview_background_image,
        "previewBackgroundImageUrl": video_text_preview_background_url(preview_background_image),
        "updatedAt": str(source.get("updatedAt") or ""),
    }


def _clean_canvas_side(value: int | float | str | None, default: int) -> int:
    try:
        number = int(float(str(value)))
    except Exception:
        return default
    return min(100, max(1, number))


def _clean_percent(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(float(str(value)))
    except Exception:
        return default
    return min(maximum, max(minimum, number))


def _clean_font_weight(value: Any, default: int) -> int:
    try:
        number = int(round(float(str(value)) / 100) * 100)
    except Exception:
        return default
    return min(900, max(300, number))


def _font_weight_offsets(font_weight: int) -> list[tuple[int, int]]:
    weight = _clean_font_weight(font_weight, DEFAULT_FONT_WEIGHT)
    radius = max(0, round((weight - 400) / 200))
    offsets = [(0, 0)]
    if radius >= 1:
        offsets.extend([(-1, 0), (1, 0)])
    if radius >= 2:
        offsets.extend([(0, -1), (0, 1)])
    return offsets


def _clean_preview_side(value: Any, default: int) -> int:
    try:
        number = int(float(str(value)))
    except Exception:
        return default
    return min(1920, max(160, number))


def _clean_hex_color(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if len(text) == 4 and text.startswith("#"):
        text = "#" + "".join(char * 2 for char in text[1:])
    if len(text) != 7 or not text.startswith("#"):
        return default
    try:
        int(text[1:], 16)
    except ValueError:
        return default
    return text.lower()


def _clean_font_family(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not text or text.startswith(".") or ".." in Path(text).parts:
        return DEFAULT_FONT_FAMILY
    path = _resolve_user_font(text)
    if not path:
        return DEFAULT_FONT_FAMILY
    try:
        return path.relative_to(USER_FONT_DIR.resolve()).as_posix()
    except ValueError:
        return DEFAULT_FONT_FAMILY


def _resolve_user_font(value: Any) -> Path | None:
    text = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not text or text.startswith(".") or ".." in Path(text).parts:
        return None
    root = USER_FONT_DIR.resolve()
    candidate = (root / text).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_FONT_EXTENSIONS:
        return candidate
    return None


def _clean_watermark_image(value: Any) -> str:
    return _normalize_watermark_image(value)


def _normalize_watermark_image(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not text or text.startswith(".") or ".." in Path(text).parts:
        return ""
    root = USER_FLOWER_WATERMARK_DIR.resolve()
    candidate = (root / text).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return ""
    if candidate.is_file() and candidate.suffix.lower() in IMAGE_MATERIAL_EXTENSIONS:
        return relative.as_posix()
    return ""

def _resolve_watermark_image(value: Any) -> Path | None:
    root = USER_FLOWER_WATERMARK_DIR.resolve()
    relative = _normalize_watermark_image(value)
    if not relative:
        return None
    return (root / relative).resolve()


def _clean_preview_background_image(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not text or text.startswith(".") or ".." in Path(text).parts:
        return DEFAULT_PREVIEW_BACKGROUND_IMAGE
    root = VIDEO_TEXT_PREVIEW_BACKGROUND_DIR.resolve()
    candidate = (root / text).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return DEFAULT_PREVIEW_BACKGROUND_IMAGE
    if candidate.is_file() and candidate.suffix.lower() in IMAGE_MATERIAL_EXTENSIONS:
        return candidate.relative_to(root).as_posix()
    return DEFAULT_PREVIEW_BACKGROUND_IMAGE


def _font_display_name(path: Path, relative: str) -> str:
    override = FONT_NAME_OVERRIDES.get(path.name)
    if override:
        return override
    try:
        from PIL import ImageFont

        name = ImageFont.truetype(str(path), size=24, index=0).getname()
        display = " ".join(part for part in name if part).strip()
        if display:
            return display
    except Exception:
        pass
    return Path(relative).stem


def _font_preview_url(path: Path, relative: str) -> str:
    preview = _ensure_font_preview(path, relative)
    if not preview:
        return ""
    return f"/user-font-previews/{quote(preview, safe='/')}"


def _font_file_url(path: Path, relative: str) -> str:
    try:
        path.resolve().relative_to(USER_FONT_DIR.resolve())
    except ValueError:
        return ""
    if path.suffix.lower() not in SUPPORTED_FONT_EXTENSIONS:
        return ""
    return f"/user-fonts/{quote(relative, safe='/')}"


def _ensure_font_preview(path: Path, relative: str) -> str:
    digest = sha1(relative.encode("utf-8")).hexdigest()[:12]
    target_name = f"{Path(relative).stem}-{digest}-tight.png"
    target = USER_FONT_PREVIEW_DIR / target_name
    if target.is_file() and target.stat().st_size > 0:
        return target_name
    try:
        from PIL import Image, ImageDraw

        USER_FONT_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        measure_image = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
        draw = ImageDraw.Draw(measure_image)
        font = ImageFont = None
        try:
            from PIL import ImageFont as PILImageFont

            ImageFont = PILImageFont
            font = ImageFont.truetype(str(path), size=58, index=0)
        except Exception:
            if ImageFont is None:
                from PIL import ImageFont as PILImageFont

                ImageFont = PILImageFont
            font = ImageFont.load_default()
        sample = _font_display_name(path, relative)
        for size in (64, 58, 52, 46, 40, 34, 30):
            try:
                font = ImageFont.truetype(str(path), size=size, index=0)
            except Exception:
                break
            bbox = draw.textbbox((0, 0), sample, font=font, stroke_width=0)
            if bbox[2] - bbox[0] <= 660:
                break
        bbox = draw.textbbox((0, 0), sample, font=font, stroke_width=0)
        text_width = max(1, bbox[2] - bbox[0])
        text_height = max(1, bbox[3] - bbox[1])
        horizontal_padding = 18
        vertical_padding = 8
        image = Image.new(
            "RGBA",
            (text_width + horizontal_padding * 2, text_height + vertical_padding * 2),
            (255, 255, 255, 255),
        )
        draw = ImageDraw.Draw(image)
        x = horizontal_padding - bbox[0]
        y = vertical_padding - bbox[1]
        draw.text((x, y), sample, font=font, fill=(17, 24, 39, 255))
        image.save(target)
        return target_name
    except Exception:
        return ""


def _selected_font_name(font_family: str) -> str:
    path = _resolve_user_font(font_family)
    if not path:
        return "系统默认"
    try:
        relative = path.relative_to(USER_FONT_DIR.resolve()).as_posix()
    except ValueError:
        relative = path.name
    return _font_display_name(path, relative)


def _clean_position(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"top", "center", "bottom"}:
        return text
    return DEFAULT_POSITION


def _clean_watermark_position(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in WATERMARK_POSITIONS:
        return text
    return DEFAULT_WATERMARK_POSITION


def _watermark_position_to_x(position: str) -> int:
    if position in {"top-left", "bottom-left"}:
        return 8
    if position in {"top-right", "bottom-right"}:
        return 92
    return 50


def _watermark_position_to_y(position: str) -> int:
    if position in {"top-left", "top-right"}:
        return 8
    if position in {"bottom-left", "bottom-right"}:
        return 92
    return 50


def _position_to_text_y(position: str) -> int:
    if position == "top":
        return 18
    if position == "bottom":
        return 82
    return DEFAULT_TEXT_Y


def _clean_animation_delay_seconds(value: Any) -> int:
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError):
        return DEFAULT_ANIMATION_DELAY_SECONDS
    return number if number in ANIMATION_DELAY_SECONDS_OPTIONS else DEFAULT_ANIMATION_DELAY_SECONDS


def _clean_animation_type(value: Any) -> str:
    return "none" if str(value or "").strip() == "none" else DEFAULT_ANIMATION_TYPE


def _clean_animation_type(value: Any) -> str:
    return "none" if str(value or "").strip() == "none" else DEFAULT_ANIMATION_TYPE


def _clean_coordinate(value: Any, default: int) -> int:
    try:
        number = int(float(str(value)))
    except Exception:
        return default
    return min(95, max(5, number))


def _hex_to_rgba(value: str, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    color = _clean_hex_color(value, "")
    if not color:
        return fallback
    return (
        int(color[1:3], 16),
        int(color[3:5], 16),
        int(color[5:7], 16),
        fallback[3],
    )


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _read_settings() -> dict[str, Any]:
    try:
        data = json.loads(VIDEO_TEXT_OVERLAY_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
def _write_settings(data: dict[str, Any]) -> None:
    ensure_user_file_root()
    VIDEO_TEXT_OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_TEXT_OVERLAY_SETTINGS_PATH.write_text(
        json.dumps(_normalize_settings(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def _clear_directory_files(root: Path) -> None:
    resolved = root.resolve()
    if not resolved.exists():
        return
    for path in resolved.rglob("*"):
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(resolved)
        except ValueError:
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
