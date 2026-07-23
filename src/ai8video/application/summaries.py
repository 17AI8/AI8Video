from __future__ import annotations


def build_summary(data: dict, meta: dict | None = None) -> dict | None:
    meta = meta or {}
    if meta.get("operation") == "batch_run" or ("goalMet" in data and "passCount" in data):
        return build_batch_summary(data)
    if "request" not in data:
        return None
    return build_pipeline_summary(data)


def build_pipeline_summary(data: dict) -> dict:
    jobs = data.get("jobs") or []
    archives = data.get("archives") or []
    generated = sum(
        1
        for item in jobs
        if str(item.get("status") or "").strip().lower() in {"succeeded", "completed"}
        and (item.get("video_url") or item.get("local_video_path"))
    )
    failed = max(0, len(data.get("videos") or []) - generated)
    archived = sum(1 for item in archives if item.get("status") == "archived")
    simulated = sum(1 for item in archives if item.get("status") == "simulated")
    return {
        "mode": data["request"]["mode"],
        "videoCount": len(data.get("videos") or []),
        "successCount": generated,
        "failedCount": failed,
        "passCount": generated,
        "retryCount": 0,
        "rejectCount": failed,
        "archiveCount": archived,
        "simulatedArchiveCount": simulated,
        "dryRun": data.get("dryRun", True),
    }


def build_batch_summary(data: dict) -> dict:
    target = int(data.get("targetGenerationCount") or data.get("targetPassCount") or 0)
    success_count = int(data.get("successCount") or data.get("passCount") or 0)
    failed_count = int(data.get("failedCount") or data.get("rejectCount") or 0)
    return {
        "reportId": str(data.get("reportId") or ""),
        "reportPath": str(data.get("reportPath") or ""),
        "targetGenerationCount": target,
        "targetPassCount": target,
        "seedMessages": int(data.get("seedMessages") or 0),
        "seededTasksUsed": int(data.get("seededTasksUsed") or 0),
        "totalVideoAttempts": int(data.get("totalVideoAttempts") or 0),
        "successCount": success_count,
        "failedCount": failed_count,
        "passCount": success_count,
        "retryCount": 0,
        "rejectCount": failed_count,
        "retryScheduledCount": int(data.get("retryScheduledCount") or 0),
        "expansionRoundCount": int(data.get("expansionRoundCount") or 0),
        "expandedSeedCount": int(data.get("expandedSeedCount") or 0),
        "topUpStrategies": list(data.get("topUpStrategies") or []),
        "expansionError": str(data.get("expansionError") or "").strip(),
        "goalMet": bool(data.get("goalMet")),
        "dryRun": bool(data.get("dryRun", True)),
    }

