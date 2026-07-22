"""HyperFrames composition 工作目录和 Node Worker 适配。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ai8video.media.motion.hyperframes_overlay_harness import HarnessResult
from ai8video.media.motion.hyperframes_worker import HyperFramesWorkerResult, render_with_hyperframes_worker


WAAPI_RUNTIME_SOURCE = Path(__file__).with_name("waapi_timeline_runtime.js")


def prepare_hyperframes_composition(
    work_dir: Path,
    harness: HarnessResult,
) -> Path:
    """只写入 Worker 所需的 composition 文件，不启动外部进程。"""
    return write_hyperframes_files(
        work_dir,
        harness.composition_html,
        harness.motion_manifest,
    )


def write_hyperframes_files(
    work_dir: Path,
    composition_html: str,
    motion_manifest: dict[str, Any],
) -> Path:
    composition = work_dir / "index.html"
    composition.write_text(composition_html, encoding="utf-8")
    (work_dir / "index.motion.json").write_text(
        json.dumps(motion_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not WAAPI_RUNTIME_SOURCE.is_file():
        raise RuntimeError("WAAPI 动效运行时缺失")
    (work_dir / "waapi-timeline-runtime.js").write_bytes(WAAPI_RUNTIME_SOURCE.read_bytes())
    return composition


def prepare_and_render_hyperframes(
    work_dir: Path,
    harness: HarnessResult,
    *,
    cli_path: Path,
    timeout_ms: int,
    cancel_event=None,
    stage_callback: Callable[[str, dict[str, Any] | None], None] | None = None,
) -> tuple[Path, HyperFramesWorkerResult]:
    prepare_hyperframes_composition(work_dir, harness)
    return render_prepared_hyperframes(
        work_dir,
        cli_path=cli_path,
        timeout_ms=timeout_ms,
        cancel_event=cancel_event,
        stage_callback=stage_callback,
    )


def render_prepared_hyperframes(
    work_dir: Path,
    *,
    cli_path: Path,
    timeout_ms: int,
    cancel_event=None,
    stage_callback: Callable[[str, dict[str, Any] | None], None] | None = None,
) -> tuple[Path, HyperFramesWorkerResult]:
    output = work_dir / "overlay.webm"
    result = render_with_hyperframes_worker(
        work_dir,
        output,
        cli_path=cli_path,
        timeout_ms=timeout_ms,
        cancel_event=cancel_event,
        stage_callback=stage_callback,
    )
    return output, result
