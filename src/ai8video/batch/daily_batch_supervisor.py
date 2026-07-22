from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ai8video.batch.batch_alert_store import BatchAlertStore
from ai8video.core.config import AI8VideoConfig
from ai8video.batch.daily_batch_job import _collect_seed_messages
from ai8video.application.runtime import run_batch_payload


class DailyBatchSupervisor:
    def __init__(
        self,
        *,
        config: AI8VideoConfig | None = None,
        alert_store: BatchAlertStore | None = None,
        run_batch_func: Callable[..., dict] | None = None,
        state_path: str | Path | None = None,
        lock_path: str | Path | None = None,
        now_func: Callable[[], datetime] | None = None,
    ):
        self.config = config or AI8VideoConfig.from_env()
        self.alert_store = alert_store or BatchAlertStore(self.config.batch_alert_dir)
        self.run_batch_func = run_batch_func or run_batch_payload
        self.state_path = Path(state_path or self.config.batch_supervisor_state_path)
        self.lock_path = Path(lock_path or self.config.batch_supervisor_lock_path)
        self.now_func = now_func or datetime.now

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_once(
        self,
        seed_messages: list[str],
        *,
        target_pass_count: int,
        style_hint: str | None,
        source: str,
        trigger: str,
        session_id: str | None,
        refresh_runtime: bool = False,
        scheduled_slot: str | None = None,
        min_pass_rate: float | None = None,
        consecutive_low_pass_runs: int | None = None,
    ) -> dict[str, Any]:
        normalized = [item.strip() for item in seed_messages if item and item.strip()]
        if not normalized:
            raise ValueError("seedMessages is required")

        previous_state = self.load_state()
        min_rate = self.config.batch_alert_min_pass_rate if min_pass_rate is None else float(min_pass_rate)
        low_pass_threshold = (
            self.config.batch_alert_consecutive_low_pass_runs
            if consecutive_low_pass_runs is None
            else max(1, int(consecutive_low_pass_runs))
        )

        self._acquire_lock()
        try:
            payload = self.run_batch_func(
                normalized,
                target_pass_count=max(1, int(target_pass_count)),
                style_hint=style_hint,
                trigger=trigger,
                source=source,
                session_id=session_id,
                refresh=refresh_runtime,
            )
            report = _extract_report(payload)
            low_success = float(report.get("successRate") or report.get("passRate") or 0.0) < min_rate
            consecutive = int(previous_state.get("consecutiveLowPassRuns") or 0) + 1 if low_success else 0
            alerts = self._build_alerts(
                report,
                consecutive_low_pass_runs=consecutive,
                min_pass_rate=min_rate,
                low_pass_threshold=low_pass_threshold,
            )
            state = {
                "updatedAt": self._now_iso(),
                "lastStatus": "ok",
                "lastRunAt": self._now_iso(),
                "lastReportId": report.get("reportId"),
                "lastReportPath": report.get("reportPath"),
                "lastSuccessRate": report.get("successRate") or report.get("passRate"),
                "lastPassRate": report.get("successRate") or report.get("passRate"),
                "lastGoalMet": report.get("goalMet"),
                "consecutiveLowPassRuns": consecutive,
                "lastAlertIds": [item["alertId"] for item in alerts],
                "lastScheduledSlot": scheduled_slot or previous_state.get("lastScheduledSlot"),
                "lastError": None,
            }
            self.save_state(state)
            return {"report": report, "alerts": alerts, "state": state}
        except Exception as exc:
            alert = self.alert_store.save(
                {
                    "createdAt": self._now_iso(),
                    "kind": "batch_run_failed",
                    "level": "error",
                    "message": f"批量调度执行失败：{exc}",
                    "reportId": previous_state.get("lastReportId"),
                    "reportPath": previous_state.get("lastReportPath"),
                    "goalMet": False,
                    "successRate": previous_state.get("lastSuccessRate") or previous_state.get("lastPassRate"),
                    "consecutiveLowPassRuns": previous_state.get("consecutiveLowPassRuns", 0),
                    "details": {"error": str(exc), "scheduledSlot": scheduled_slot},
                }
            )
            state = {
                "updatedAt": self._now_iso(),
                "lastStatus": "error",
                "lastRunAt": self._now_iso(),
                "lastReportId": previous_state.get("lastReportId"),
                "lastReportPath": previous_state.get("lastReportPath"),
                "lastSuccessRate": previous_state.get("lastSuccessRate") or previous_state.get("lastPassRate"),
                "lastPassRate": previous_state.get("lastSuccessRate") or previous_state.get("lastPassRate"),
                "lastGoalMet": previous_state.get("lastGoalMet"),
                "consecutiveLowPassRuns": int(previous_state.get("consecutiveLowPassRuns") or 0),
                "lastAlertIds": [alert["alertId"]],
                "lastScheduledSlot": scheduled_slot or previous_state.get("lastScheduledSlot"),
                "lastError": str(exc),
            }
            self.save_state(state)
            raise
        finally:
            self._release_lock()

    def run_loop(
        self,
        seed_messages: list[str],
        *,
        target_pass_count: int,
        style_hint: str | None,
        source: str,
        trigger: str,
        session_id: str | None,
        refresh_runtime: bool,
        schedule_times: list[str],
        poll_seconds: int,
        min_pass_rate: float | None = None,
        consecutive_low_pass_runs: int | None = None,
    ) -> None:
        parsed_schedule = parse_schedule_times(schedule_times)
        if not parsed_schedule:
            raise ValueError("scheduleTimes is required in loop mode")
        while True:
            state = self.load_state()
            slot = find_due_scheduled_slot(
                parsed_schedule,
                now=self.now_func(),
                last_scheduled_slot=str(state.get("lastScheduledSlot") or ""),
            )
            if slot is not None:
                self.run_once(
                    seed_messages,
                    target_pass_count=target_pass_count,
                    style_hint=style_hint,
                    source=source,
                    trigger=trigger,
                    session_id=session_id,
                    refresh_runtime=refresh_runtime,
                    scheduled_slot=slot,
                    min_pass_rate=min_pass_rate,
                    consecutive_low_pass_runs=consecutive_low_pass_runs,
                )
            time.sleep(max(5, int(poll_seconds)))

    def _build_alerts(
        self,
        report: dict[str, Any],
        *,
        consecutive_low_pass_runs: int,
        min_pass_rate: float,
        low_pass_threshold: int,
    ) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        if not bool(report.get("goalMet")):
            alerts.append(
                self.alert_store.save(
                    {
                        "createdAt": self._now_iso(),
                        "kind": "goal_not_met",
                        "level": "warn",
                        "message": (
                            f"日报未达标：已生成 {int(report.get('successCount') or report.get('passCount') or 0)}/"
                            f"{int(report.get('targetGenerationCount') or report.get('targetPassCount') or 0)}"
                        ),
                        "reportId": report.get("reportId"),
                        "reportPath": report.get("reportPath"),
                        "goalMet": report.get("goalMet"),
                        "successRate": report.get("successRate") or report.get("passRate"),
                        "consecutiveLowPassRuns": consecutive_low_pass_runs,
                        "details": {
                            "successCount": report.get("successCount") or report.get("passCount"),
                            "targetGenerationCount": report.get("targetGenerationCount") or report.get("targetPassCount"),
                            "retryCount": report.get("retryCount"),
                            "rejectCount": report.get("rejectCount"),
                        },
                    }
                )
            )
        if (
            float(report.get("successRate") or report.get("passRate") or 0.0) < min_pass_rate
            and consecutive_low_pass_runs >= low_pass_threshold
        ):
            alerts.append(
                self.alert_store.save(
                    {
                        "createdAt": self._now_iso(),
                        "kind": "consecutive_low_pass",
                        "level": "warn",
                        "message": (
                            f"连续低成功率告警：连续 {consecutive_low_pass_runs} 轮成功率低于 {min_pass_rate:.2f}"
                        ),
                        "reportId": report.get("reportId"),
                        "reportPath": report.get("reportPath"),
                        "goalMet": report.get("goalMet"),
                        "successRate": report.get("successRate") or report.get("passRate"),
                        "consecutiveLowPassRuns": consecutive_low_pass_runs,
                        "details": {
                            "threshold": min_pass_rate,
                            "successRate": report.get("successRate") or report.get("passRate"),
                            "successCount": report.get("successCount") or report.get("passCount"),
                            "totalVideoAttempts": report.get("totalVideoAttempts"),
                        },
                    }
                )
            )
        return alerts

    def _acquire_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"supervisor lock already exists: {self.lock_path}") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(self._now_iso())

    def _release_lock(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    def _now_iso(self) -> str:
        return self.now_func().astimezone().isoformat()


def parse_schedule_times(items: list[str] | tuple[str, ...] | str) -> list[str]:
    if isinstance(items, str):
        raw_items = [items]
    else:
        raw_items = list(items)
    parsed: list[str] = []
    for item in raw_items:
        for chunk in str(item or "").split(","):
            text = chunk.strip()
            if not text:
                continue
            hour_text, sep, minute_text = text.partition(":")
            if sep != ":":
                raise ValueError(f"invalid schedule time: {text}")
            hour = int(hour_text)
            minute = int(minute_text)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"invalid schedule time: {text}")
            normalized = f"{hour:02d}:{minute:02d}"
            if normalized not in parsed:
                parsed.append(normalized)
    return sorted(parsed)


def find_due_scheduled_slot(schedule_times: list[str], *, now: datetime, last_scheduled_slot: str) -> str | None:
    day_prefix = now.strftime("%Y-%m-%d")
    due_slots = [
        f"{day_prefix}T{item}"
        for item in schedule_times
        if item <= now.strftime("%H:%M")
    ]
    for slot in due_slots:
        if not last_scheduled_slot or slot > last_scheduled_slot:
            return slot
    return None


def _extract_report(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        return payload["result"]
    if isinstance(payload, dict):
        return payload
    raise TypeError(f"Unsupported batch payload: {type(payload)!r}")


def main() -> int:
    config = AI8VideoConfig.from_env()
    parser = argparse.ArgumentParser(description="AI8video 批量短视频主管调度入口")
    parser.add_argument("--seed-file", type=str, default=config.batch_seed_file)
    parser.add_argument(
        "--seed-message",
        action="append",
        dest="seed_messages",
        default=[],
        help="直接追加一条候选内容，可重复传入",
    )
    parser.add_argument("--target-pass-count", type=int, default=config.batch_target_pass_count)
    parser.add_argument("--style-hint", type=str, default=config.batch_style_hint or "")
    parser.add_argument("--session-id", type=str, default="daily-batch-supervisor")
    parser.add_argument("--source", type=str, default="supervisor")
    parser.add_argument("--trigger", type=str, default="daily_batch_supervisor")
    parser.add_argument("--refresh-runtime", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--schedule-times", type=str, default=config.batch_schedule_times)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--min-pass-rate", type=float, default=config.batch_alert_min_pass_rate)
    parser.add_argument(
        "--consecutive-low-pass-runs",
        type=int,
        default=config.batch_alert_consecutive_low_pass_runs,
    )
    parser.add_argument("--state-path", type=str, default=config.batch_supervisor_state_path)
    parser.add_argument("--lock-path", type=str, default=config.batch_supervisor_lock_path)
    parser.add_argument("--alert-dir", type=str, default=config.batch_alert_dir)
    args = parser.parse_args()

    if not args.once and not args.loop:
        args.once = True

    seed_messages = _collect_seed_messages(seed_file=args.seed_file, seed_messages=args.seed_messages)
    if not seed_messages:
        raise SystemExit("缺少候选内容。请通过 --seed-file、--seed-message 或 stdin 提供逐行候选。")

    supervisor = DailyBatchSupervisor(
        config=config,
        alert_store=BatchAlertStore(args.alert_dir),
        state_path=args.state_path,
        lock_path=args.lock_path,
    )

    if args.loop:
        schedule_times = parse_schedule_times(args.schedule_times or "")
        supervisor.run_loop(
            seed_messages,
            target_pass_count=max(1, int(args.target_pass_count or 30)),
            style_hint=args.style_hint.strip() or None,
            source=args.source,
            trigger=args.trigger,
            session_id=args.session_id.strip() or None,
            refresh_runtime=args.refresh_runtime,
            schedule_times=schedule_times,
            poll_seconds=max(5, int(args.poll_seconds or 30)),
            min_pass_rate=float(args.min_pass_rate),
            consecutive_low_pass_runs=max(1, int(args.consecutive_low_pass_runs or 2)),
        )
        return 0

    try:
        payload = supervisor.run_once(
            seed_messages,
            target_pass_count=max(1, int(args.target_pass_count or 30)),
            style_hint=args.style_hint.strip() or None,
            source=args.source,
            trigger=args.trigger,
            session_id=args.session_id.strip() or None,
            refresh_runtime=args.refresh_runtime,
            min_pass_rate=float(args.min_pass_rate),
            consecutive_low_pass_runs=max(1, int(args.consecutive_low_pass_runs or 2)),
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
