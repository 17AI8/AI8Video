from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RealGenerationGuard:
    """Persisted guardrail for low-volume real video testing."""

    _lock = threading.Lock()

    def __init__(
        self,
        path: str | Path,
        max_jobs_per_window: int = 0,
        window_seconds: int = 3600,
        forced_duration_seconds: int = 0,
    ):
        self.path = Path(path)
        self.max_jobs_per_window = max(0, int(max_jobs_per_window or 0))
        self.window_seconds = max(0, int(window_seconds or 0))
        self.forced_duration_seconds = max(0, int(forced_duration_seconds or 0))

    def enabled(self) -> bool:
        return self.max_jobs_per_window > 0 and self.window_seconds > 0

    def snapshot(self) -> dict[str, Any]:
        active = self._active_records()
        remaining = max(0, self.max_jobs_per_window - len(active))
        next_available_at = None
        if active and remaining <= 0:
            earliest_ts = min(float(item.get("createdAtTs") or 0) for item in active)
            next_available_at = datetime.fromtimestamp(
                earliest_ts + self.window_seconds,
                tz=timezone.utc,
            ).isoformat()
        return {
            "enabled": self.enabled(),
            "maxJobsPerWindow": self.max_jobs_per_window,
            "windowSeconds": self.window_seconds,
            "forcedDurationSeconds": self.forced_duration_seconds,
            "usedInWindow": len(active),
            "remainingInWindow": remaining,
            "nextAvailableAt": next_available_at,
            "auditPath": str(self.path),
        }

    def assert_can_create(self) -> None:
        self.assert_can_create_count(1)

    def assert_can_create_count(self, count: int) -> None:
        if not self.enabled():
            return
        requested = max(1, int(count or 1))
        active = self._active_records()
        remaining = self.max_jobs_per_window - len(active)
        if requested <= remaining:
            return
        next_available_part = ""
        if active:
            earliest_ts = min(float(item.get("createdAtTs") or 0) for item in active)
            next_available = datetime.fromtimestamp(
                earliest_ts + self.window_seconds,
                tz=timezone.utc,
            ).astimezone()
            next_available_part = f"请在 {next_available.strftime('%Y-%m-%d %H:%M:%S %Z')} 后再试。"
        else:
            next_available_part = "请减少本轮提交数量后再试。"
        raise RuntimeError(
            "真实生成额度已用完：当前时间窗口内最多生成 "
            f"{self.max_jobs_per_window} 条"
            f"{f' {self.forced_duration_seconds} 秒' if self.forced_duration_seconds else ''}视频，"
            f"本轮需要提交 {requested} 条，当前剩余 {max(0, remaining)} 条；"
            f"{next_available_part}"
        )

    def record_job(self, job_id: str, video_index: int, prompt: str) -> None:
        if not self.enabled():
            return
        created_at_ts = time.time()
        record = {
            "createdAt": datetime.fromtimestamp(created_at_ts, tz=timezone.utc).isoformat(),
            "createdAtTs": created_at_ts,
            "jobId": job_id,
            "videoIndex": int(video_index or 0),
            "durationSeconds": self.forced_duration_seconds or None,
            "promptPreview": prompt[:160],
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _active_records(self) -> list[dict[str, Any]]:
        if not self.enabled():
            return []
        cutoff = time.time() - self.window_seconds
        active = []
        with self._lock:
            for item in self._read_all():
                try:
                    created_at_ts = float(item.get("createdAtTs") or 0)
                except Exception:
                    continue
                if created_at_ts >= cutoff:
                    active.append(item)
        return active

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
        return rows
