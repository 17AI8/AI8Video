from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ai8video.core.models import ArchivedAsset, ParsedRequest, QuickVideoJob, VideoPrompt
from ai8video.media.video_segment_postprocess import extract_tail_frame


TAIL_FRAME_CHAIN_PROMPT_SUFFIX = "最后一秒主体必须正对镜头。"


def append_tail_frame_chain_prompt(video: VideoPrompt) -> VideoPrompt:
    prompt = str(video.prompt or "").strip()
    if TAIL_FRAME_CHAIN_PROMPT_SUFFIX in prompt:
        return video
    return replace(video, prompt=f"{prompt}\n{TAIL_FRAME_CHAIN_PROMPT_SUFFIX}".strip())


def build_next_tail_frame_request(
    request: ParsedRequest,
    job: QuickVideoJob,
    archive: ArchivedAsset,
    output_path: Path,
) -> ParsedRequest:
    source = str(archive.local_path or job.local_video_path or "").strip()
    if not source:
        raise RuntimeError("传尾帧模式无法继续：上一条视频没有本地成片")
    tail_frame = extract_tail_frame(Path(source), output_path)
    return replace(
        request,
        reference_image=str(tail_frame),
        reference_image_custom_prompt=None,
        reference_image_transform_options=None,
        concurrent_generation=False,
    )
