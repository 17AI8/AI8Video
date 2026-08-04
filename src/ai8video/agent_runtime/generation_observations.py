"""Structured terminal observations for Agent-owned video generation."""

from __future__ import annotations

from typing import Any, Iterable


TERMINAL_GENERATION_KINDS = frozenset({
    "generation_terminal",
    "generation_terminal_checkpoint",
    "generation_terminal_recovered",
})


def terminal_generation_observations(
    observations: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        item
        for item in observations
        if item.get("kind") in TERMINAL_GENERATION_KINDS and bool(item.get("terminal"))
    ]


def build_generation_observation(payload: dict[str, Any], batch_id: str) -> dict[str, Any]:
    outcomes = payload.get("outcomes") if isinstance(payload.get("outcomes"), list) else []
    jobs = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
    videos = payload.get("videos") if isinstance(payload.get("videos"), list) else []
    assets = payload.get("assetRecords") or payload.get("asset_records") or []
    outcomes_by_index = _items_by_video_index(outcomes)
    jobs_by_index = _items_by_video_index(jobs)
    assets_by_index = _items_by_video_index(assets)
    indexes = {
        int(item.get("index") or item.get("videoIndex") or 0)
        for item in videos
        if isinstance(item, dict)
    } | set(outcomes_by_index) | set(jobs_by_index) | set(assets_by_index)
    items = []
    for video_index in sorted(index for index in indexes if index > 0):
        outcome = outcomes_by_index.get(video_index, {})
        job = jobs_by_index.get(video_index, {})
        succeeded = (
            str(outcome.get("decision") or "").lower() == "generated"
            or str(outcome.get("status") or job.get("status") or "").lower()
            in {"succeeded", "completed"}
        )
        reasons = outcome.get("reasons") if isinstance(outcome.get("reasons"), list) else []
        error = str(job.get("error") or (reasons[0] if reasons else "")).strip()
        items.append({
            "videoIndex": video_index,
            "status": "succeeded" if succeeded else "failed",
            "retryable": False if succeeded else job.get("retryable") is not False,
            "reason": error,
            "jobId": (
                job.get("job_id")
                or job.get("jobId")
                or outcome.get("job_id")
                or outcome.get("jobId")
            ),
            "assetRecord": assets_by_index.get(video_index),
        })
    reported_success = _integer(payload.get("successCount") or payload.get("succeededCount"))
    reported_failed = _integer(payload.get("failedCount"))
    success_count = sum(item["status"] == "succeeded" for item in items) or reported_success
    total = max(
        len(items),
        len(outcomes),
        len(jobs),
        len(videos),
        success_count + reported_failed,
    )
    failed_count = max(0, total - success_count)
    return _observation_payload(
        batch_id=batch_id,
        items=items,
        assets=[item for item in assets if isinstance(item, dict)],
        success_count=success_count,
        failed_count=failed_count,
    )


def build_failed_generation_observation(
    *,
    batch_id: str,
    video_indexes: Iterable[int],
    error: str,
) -> dict[str, Any]:
    reason = str(error or "Runtime 执行失败").strip()
    items = [
        {
            "videoIndex": int(index),
            "status": "failed",
            "retryable": True,
            "reason": reason,
            "jobId": None,
            "assetRecord": None,
        }
        for index in video_indexes
        if int(index) > 0
    ]
    payload = _observation_payload(
        batch_id=batch_id,
        items=items,
        assets=[],
        success_count=0,
        failed_count=len(items),
    )
    payload["error"] = reason
    return payload


def aggregate_generation_observations(
    observations: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    items_by_index: dict[int, dict[str, Any]] = {}
    assets_by_index: dict[int, dict[str, Any]] = {}
    latest: dict[str, Any] = {}
    for observation in observations:
        payload = observation.get("payload") if isinstance(observation.get("payload"), dict) else {}
        latest = payload or latest
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            video_index = int(item.get("videoIndex") or item.get("video_index") or item.get("index") or 0)
            if video_index > 0:
                items_by_index[video_index] = {**item, "videoIndex": video_index}
        for asset in payload.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            video_index = int(asset.get("videoIndex") or asset.get("video_index") or 0)
            if video_index > 0:
                assets_by_index[video_index] = asset
    if not items_by_index:
        return latest
    items = [items_by_index[index] for index in sorted(items_by_index)]
    for item in items:
        asset = item.get("assetRecord")
        if isinstance(asset, dict):
            assets_by_index[int(item["videoIndex"])] = asset
    success_count = sum(item.get("status") == "succeeded" for item in items)
    failed_count = len(items) - success_count
    return {
        **latest,
        **_observation_payload(
            batch_id=str(latest.get("generationBatchId") or ""),
            items=items,
            assets=[assets_by_index[index] for index in sorted(assets_by_index)],
            success_count=success_count,
            failed_count=failed_count,
        ),
    }


def retryable_failed_video_indexes(observations: Iterable[dict[str, Any]]) -> set[int]:
    generation = aggregate_generation_observations(
        terminal_generation_observations(observations)
    )
    return {
        int(item.get("videoIndex") or 0)
        for item in generation.get("items") or []
        if item.get("status") != "succeeded" and item.get("retryable") is not False
    }


def _observation_payload(
    *,
    batch_id: str,
    items: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    success_count: int,
    failed_count: int,
) -> dict[str, Any]:
    status = "succeeded" if success_count and not failed_count else (
        "partial_success" if success_count else "failed"
    )
    failures = [item for item in items if item.get("status") != "succeeded"]
    total = success_count + failed_count
    return {
        "action": "generate_video_batch",
        "status": status,
        "generationBatchId": batch_id,
        "requestedCount": total,
        "succeededCount": success_count,
        "successCount": success_count,
        "failedCount": failed_count,
        "totalCount": total,
        "failures": failures,
        "items": items,
        "assets": assets,
    }


def _items_by_video_index(items: Iterable[Any]) -> dict[int, dict[str, Any]]:
    result = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        video_index = int(item.get("video_index") or item.get("videoIndex") or 0)
        if video_index > 0:
            result[video_index] = item
    return result


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
