from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class BatchAlertStore:
    def __init__(self, root_dir: str | Path = "media_resources/ai8video/batch_alerts"):
        self.root_dir = Path(root_dir)
        self.index_path = self.root_dir / "index.jsonl"

    def save(self, alert: dict[str, Any]) -> dict[str, Any]:
        created_at = _parse_timestamp(alert.get("createdAt")) or datetime.now(timezone.utc)
        alert_id = f"{created_at.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
        alert_date = created_at.strftime("%Y-%m-%d")
        day_dir = self.root_dir / alert_date
        day_dir.mkdir(parents=True, exist_ok=True)
        alert_path = day_dir / f"{alert_id}.json"

        payload = dict(alert)
        payload["alertId"] = alert_id
        payload["alertDate"] = alert_date
        payload["alertSavedAt"] = datetime.now(timezone.utc).isoformat()

        with alert_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

        record = {
            "alertId": alert_id,
            "alertDate": alert_date,
            "createdAt": payload.get("createdAt"),
            "alertSavedAt": payload["alertSavedAt"],
            "alertPath": str(alert_path),
            "kind": str(payload.get("kind") or ""),
            "level": str(payload.get("level") or "warn"),
            "message": str(payload.get("message") or ""),
            "reportId": payload.get("reportId"),
            "reportPath": payload.get("reportPath"),
            "goalMet": payload.get("goalMet"),
            "passRate": payload.get("passRate"),
            "consecutiveLowPassRuns": payload.get("consecutiveLowPassRuns"),
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
            alert_id = str(item.get("alertId") or "")
            if not alert_id:
                continue
            if alert_id not in deduped:
                ordered_ids.append(alert_id)
            deduped[alert_id] = item
        latest_ids = list(reversed(ordered_ids))[:limit]
        return [deduped[alert_id] for alert_id in latest_ids]

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
