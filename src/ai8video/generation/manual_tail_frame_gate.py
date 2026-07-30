from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Callable

from ai8video.assets.user_files import USER_FILE_ROOT
from ai8video.core.models import ArchivedAsset, ParsedRequest, QuickVideoJob
from ai8video.generation.tail_frame_chaining import build_next_tail_frame_request


MANUAL_TAIL_FRAME_DIR = (USER_FILE_ROOT / "临时媒体" / "传尾帧").resolve()


@dataclass
class ManualTailFrameGate:
    session_id: str
    generation_batch_id: str
    video_index: int
    request: ParsedRequest
    previous_job: QuickVideoJob
    previous_archive: ArchivedAsset
    output_path: Path
    ready: Event

    def refresh(self) -> ParsedRequest:
        self.request = build_next_tail_frame_request(
            self.request,
            self.previous_job,
            self.previous_archive,
            self.output_path,
        )
        return self.request


_GATES: dict[tuple[str, str, int], ManualTailFrameGate] = {}
_LOCK = Lock()


def create_manual_tail_frame_gate(
    *,
    session_id: str,
    generation_batch_id: str,
    video_index: int,
    request: ParsedRequest,
    previous_job: QuickVideoJob,
    previous_archive: ArchivedAsset,
) -> ManualTailFrameGate:
    output_dir = MANUAL_TAIL_FRAME_DIR / generation_batch_id
    output_dir.mkdir(parents=True, exist_ok=True)
    gate = ManualTailFrameGate(
        session_id=session_id,
        generation_batch_id=generation_batch_id,
        video_index=video_index,
        request=request,
        previous_job=previous_job,
        previous_archive=previous_archive,
        output_path=output_dir / f"video-{video_index}-reference.png",
        ready=Event(),
    )
    gate.refresh()
    with _LOCK:
        _GATES[_gate_key(session_id, generation_batch_id, video_index)] = gate
    return gate


def wait_for_manual_tail_frame_gate(
    gate: ManualTailFrameGate,
    *,
    cancel_check: Callable[[], None],
) -> ParsedRequest:
    try:
        while not gate.ready.wait(0.5):
            cancel_check()
        cancel_check()
        return gate.request
    finally:
        with _LOCK:
            _GATES.pop(_gate_key(gate.session_id, gate.generation_batch_id, gate.video_index), None)


def continue_manual_tail_frame(session_id: str, generation_batch_id: str, video_index: int) -> dict:
    gate = _get_gate(session_id, generation_batch_id, video_index)
    gate.ready.set()
    return _gate_status(gate)


def refresh_manual_tail_frame(session_id: str, generation_batch_id: str, video_index: int) -> dict:
    gate = _get_gate(session_id, generation_batch_id, video_index)
    gate.refresh()
    return _gate_status(gate)


def resolve_manual_tail_frame_preview(generation_batch_id: str, filename: str) -> Path:
    target = (MANUAL_TAIL_FRAME_DIR / generation_batch_id / filename).resolve()
    if MANUAL_TAIL_FRAME_DIR not in target.parents or not target.is_file():
        raise FileNotFoundError("尾帧预览不存在")
    return target


def _get_gate(session_id: str, generation_batch_id: str, video_index: int) -> ManualTailFrameGate:
    with _LOCK:
        gate = _GATES.get(_gate_key(session_id, generation_batch_id, video_index))
    if gate is None:
        raise LookupError("当前视频不在等待继续状态")
    return gate


def _gate_status(gate: ManualTailFrameGate) -> dict:
    version = gate.output_path.stat().st_mtime_ns if gate.output_path.is_file() else 0
    return {
        "ok": True,
        "sessionId": gate.session_id,
        "generationBatchId": gate.generation_batch_id,
        "videoIndex": gate.video_index,
        "tailFramePreviewUrl": (
            f"/tail-frame-previews/{gate.generation_batch_id}/{gate.output_path.name}?v={version}"
        ),
    }


def _gate_key(session_id: str, generation_batch_id: str, video_index: int) -> tuple[str, str, int]:
    return str(session_id).strip(), str(generation_batch_id).strip(), int(video_index)
