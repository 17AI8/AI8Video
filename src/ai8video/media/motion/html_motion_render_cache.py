"""HTML 动效透明层的渲染输入指纹与文件完整性门禁。"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai8video.media.motion.html_motion_overlay import (
    DEFAULT_MOTION_FONT_FAMILY,
    HYPERFRAMES_VERSION,
    MOTION_FONT_SOURCE,
)
from ai8video.media.motion.hyperframes_overlay_renderer import (
    build_composition_html,
    build_motion_manifest,
)
from ai8video.media.motion.hyperframes_runtime import WAAPI_RUNTIME_SOURCE
from ai8video.media.video_text_overlay import selected_video_text_overlay_font_path


RENDER_FINGERPRINT_SCHEMA = "ai8-html-motion-overlay/v1"
HYPERFRAMES_WORKER_SOURCE = Path(__file__).with_name("hyperframes_worker.cjs")


@dataclass(frozen=True)
class HtmlMotionRenderPlan:
    fingerprint: str
    composition_html: str
    motion_manifest: dict[str, Any]


def build_html_motion_render_plan(
    artifact: dict[str, Any],
    media: dict[str, Any],
    font_family: str,
) -> HtmlMotionRenderPlan:
    composition_html = build_composition_html(artifact, media, font_family=font_family)
    motion_manifest = build_motion_manifest(artifact, media)
    contract = {
        "schema": RENDER_FINGERPRINT_SCHEMA,
        "rendererVersion": HYPERFRAMES_VERSION,
        "compositionSha256": _sha256_bytes(composition_html.encode("utf-8")),
        "manifestSha256": _sha256_bytes(_canonical_json(motion_manifest).encode("utf-8")),
        "runtimeSha256": _file_sha256(WAAPI_RUNTIME_SOURCE),
        "workerSha256": _file_sha256(HYPERFRAMES_WORKER_SOURCE),
        "fontFamily": font_family,
        "fontSha256": _file_sha256(_font_source(font_family)),
    }
    fingerprint = "v1:" + _sha256_bytes(_canonical_json(contract).encode("utf-8"))
    return HtmlMotionRenderPlan(fingerprint, composition_html, motion_manifest)


def layer_matches_render_plan(
    payload: dict[str, Any],
    layer: Path,
    plan: HtmlMotionRenderPlan,
) -> bool:
    if payload.get("renderedHash") != plan.fingerprint or not layer.is_file():
        return False
    try:
        size = layer.stat().st_size
        expected_size = int(payload.get("renderedOverlayBytes") or 0)
    except (OSError, TypeError, ValueError):
        return False
    expected_sha = str(payload.get("renderedOverlaySha256") or "")
    return bool(size > 0 and size == expected_size and expected_sha == _file_sha256(layer))


def render_metadata(plan: HtmlMotionRenderPlan, layer: Path) -> dict[str, Any]:
    size = layer.stat().st_size
    if size <= 0:
        raise RuntimeError("HTML 动效透明层为空")
    return {
        "renderedHash": plan.fingerprint,
        "renderedAt": datetime.now(timezone.utc).isoformat(),
        "renderedOverlayBytes": size,
        "renderedOverlaySha256": _file_sha256(layer),
    }


def sync_live_preview_font(review_dir: Path, font_family: str) -> None:
    for name in ("motion-font.otf", "flower-font.otf"):
        (review_dir / name).unlink(missing_ok=True)
    source = selected_video_text_overlay_font_path()
    if source is None or not source.is_file() or not font_family:
        return
    target_name = "flower-font.otf" if font_family == "AI8VideoFlower" else "motion-font.otf"
    shutil.copy2(source, review_dir / target_name)


def _font_source(font_family: str) -> Path | None:
    if font_family == "AI8VideoFlower":
        return selected_video_text_overlay_font_path()
    if font_family == DEFAULT_MOTION_FONT_FAMILY:
        return MOTION_FONT_SOURCE
    return None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _file_sha256(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
