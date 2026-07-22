from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class BatchReportStore:
    def __init__(self, root_dir: str | Path = "media_resources/ai8video/batch_reports"):
        self.root_dir = Path(root_dir)
        self.index_path = self.root_dir / "index.jsonl"

    def save(
        self,
        report: dict[str, Any],
        *,
        trigger: str = "manual",
        source: str = "manual",
        session_id: str | None = None,
        style_hint: str | None = None,
        seed_messages: list[str] | None = None,
    ) -> dict[str, Any]:
        generated_at = _parse_timestamp(report.get("generatedAt")) or datetime.now(timezone.utc)
        saved_at = datetime.now(timezone.utc)
        report_id = f"{generated_at.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
        report_date = generated_at.strftime("%Y-%m-%d")
        day_dir = self.root_dir / report_date
        day_dir.mkdir(parents=True, exist_ok=True)
        report_path = day_dir / f"{report_id}.json"

        payload = dict(report)
        payload["reportId"] = report_id
        payload["reportDate"] = report_date
        payload["reportSavedAt"] = saved_at.isoformat()
        payload["reportTrigger"] = trigger
        payload["reportSource"] = source
        payload["reportSessionId"] = session_id
        payload["styleHint"] = style_hint or payload.get("styleHint")
        payload["seedMessageSamples"] = list(seed_messages or [])[:5]

        with report_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

        record = {
            "reportId": report_id,
            "reportDate": report_date,
            "generatedAt": payload.get("generatedAt"),
            "reportSavedAt": payload["reportSavedAt"],
            "reportPath": str(report_path),
            "reportTrigger": trigger,
            "reportSource": source,
            "reportSessionId": session_id,
            "styleHint": style_hint,
            "seedMessages": int(payload.get("seedMessages") or len(seed_messages or [])),
            "targetPassCount": int(payload.get("targetPassCount") or 0),
            "totalVideoAttempts": int(payload.get("totalVideoAttempts") or 0),
            "successCount": int(payload.get("successCount") or payload.get("passCount") or 0),
            "failedCount": int(payload.get("failedCount") or payload.get("rejectCount") or 0),
            "passCount": int(payload.get("passCount") or 0),
            "retryCount": int(payload.get("retryCount") or 0),
            "rejectCount": int(payload.get("rejectCount") or 0),
            "retryScheduledCount": int(payload.get("retryScheduledCount") or 0),
            "expansionRoundCount": int(payload.get("expansionRoundCount") or 0),
            "expandedSeedCount": int(payload.get("expandedSeedCount") or 0),
            "goalMet": bool(payload.get("goalMet")),
            "dryRun": bool(payload.get("dryRun", True)),
            "topFailureReasons": payload.get("topFailureReasons") or [],
            "topAsset": payload.get("topAsset"),
            "seedMessageSamples": payload["seedMessageSamples"],
        }
        self._append_index(record)
        return record

    def read_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit or 10)))
        if not self.index_path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.index_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        deduped: dict[str, dict[str, Any]] = {}
        ordered_ids: list[str] = []
        for item in records:
            report_id = str(item.get("reportId") or "")
            if not report_id:
                continue
            if report_id not in deduped:
                ordered_ids.append(report_id)
            deduped[report_id] = item
        latest_ids = list(reversed(ordered_ids))[:limit]
        return [deduped[report_id] for report_id in latest_ids]

    def _append_index(self, record: dict[str, Any]) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
