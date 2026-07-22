from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ai8video.batch.batch_alert_store import BatchAlertStore
from ai8video.core.config import AI8VideoConfig
from ai8video.batch.daily_batch_supervisor import (
    DailyBatchSupervisor,
    find_due_scheduled_slot,
    parse_schedule_times,
)


class DailyBatchSupervisorTest(unittest.TestCase):
    def _build_config(self, root: Path) -> AI8VideoConfig:
        return AI8VideoConfig(
            dry_run=True,
            batch_alert_dir=str(root / "batch_alerts"),
            batch_supervisor_state_path=str(root / "batch_supervisor_state.json"),
            batch_supervisor_lock_path=str(root / "batch_supervisor.lock"),
            batch_alert_min_pass_rate=0.7,
            batch_alert_consecutive_low_pass_runs=2,
        )

    def test_run_once_writes_goal_alert_and_consecutive_low_pass_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            config = self._build_config(root)
            reports = [
                {
                    "result": {
                        "generatedAt": "2026-06-13T01:00:00+00:00",
                        "reportId": "report-1",
                        "reportPath": str(root / "batch_reports" / "report-1.json"),
                        "targetPassCount": 30,
                        "passCount": 12,
                        "retryCount": 10,
                        "rejectCount": 8,
                        "totalVideoAttempts": 30,
                        "passRate": 0.4,
                        "goalMet": False,
                    }
                },
                {
                    "result": {
                        "generatedAt": "2026-06-13T05:00:00+00:00",
                        "reportId": "report-2",
                        "reportPath": str(root / "batch_reports" / "report-2.json"),
                        "targetPassCount": 30,
                        "passCount": 15,
                        "retryCount": 8,
                        "rejectCount": 7,
                        "totalVideoAttempts": 30,
                        "passRate": 0.5,
                        "goalMet": False,
                    }
                },
            ]

            def _run_batch(*args, **kwargs):  # noqa: ANN002, ANN003
                return reports.pop(0)

            supervisor = DailyBatchSupervisor(
                config=config,
                alert_store=BatchAlertStore(config.batch_alert_dir),
                run_batch_func=_run_batch,
                now_func=lambda: datetime(2026, 6, 13, 9, 0, tzinfo=timezone.utc),
            )

            first = supervisor.run_once(
                ["老板在会议室讲封号风险"],
                target_pass_count=30,
                style_hint="商务",
                source="tests",
                trigger="unit_test",
                session_id="supervisor-1",
            )
            second = supervisor.run_once(
                ["老板在办公室讲AI8video 承接私域"],
                target_pass_count=30,
                style_hint="商务",
                source="tests",
                trigger="unit_test",
                session_id="supervisor-2",
            )

            self.assertEqual(len(first["alerts"]), 1)
            self.assertEqual(first["alerts"][0]["kind"], "goal_not_met")
            self.assertEqual(first["state"]["consecutiveLowPassRuns"], 1)
            self.assertEqual(len(second["alerts"]), 2)
            self.assertEqual(second["alerts"][0]["kind"], "goal_not_met")
            self.assertEqual(second["alerts"][1]["kind"], "consecutive_low_pass")
            self.assertEqual(second["state"]["consecutiveLowPassRuns"], 2)
            state_path = Path(config.batch_supervisor_state_path)
            self.assertTrue(state_path.exists())

    def test_parse_schedule_times_and_find_due_slot(self) -> None:
        schedule = parse_schedule_times(["18:30,09:00", "13:15"])
        self.assertEqual(schedule, ["09:00", "13:15", "18:30"])
        due = find_due_scheduled_slot(
            schedule,
            now=datetime(2026, 6, 13, 13, 20, tzinfo=timezone.utc),
            last_scheduled_slot="2026-06-13T09:00",
        )
        self.assertEqual(due, "2026-06-13T13:15")


if __name__ == "__main__":
    unittest.main()
