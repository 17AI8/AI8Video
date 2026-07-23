from __future__ import annotations

from dataclasses import replace

from ai8video.core.models import ParsedRequest


MAX_ITERATIVE_VIDEO_COUNT = 5
ITERATIVE_VIDEO_DURATION_SECONDS = 10


class IterativeBatchPolicyError(ValueError):
    pass


def normalize_iterative_batch_request(request: ParsedRequest) -> ParsedRequest:
    if request.mode != "batch_videos":
        return replace(request, iterative_generation=False)
    count = int(request.video_count or 0)
    if count < 1:
        raise IterativeBatchPolicyError("批量生成需要填写视频数量，允许范围为 1 到 5 条。")
    if count > MAX_ITERATIVE_VIDEO_COUNT:
        raise IterativeBatchPolicyError(
            f"普通批量单轮最多生成 {MAX_ITERATIVE_VIDEO_COUNT} 条独立视频；"
            "每条固定 10 秒，并按生成、审查、优化下一条的顺序执行。"
        )
    return replace(
        request,
        duration_seconds=ITERATIVE_VIDEO_DURATION_SECONDS,
        concurrent_generation=False,
        iterative_generation=True,
    )
