from __future__ import annotations

import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

from ai8video.core.config import AI8VideoConfig
from ai8video.batch.supervisor_launchd import (
    DEFAULT_LABEL,
    build_launchd_plist,
    inspect_launchd_deployment,
    write_launchd_plist,
)


class AI8VideoSupervisorLaunchdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.plist_env_backup = os.environ.get("AI8VIDEO_BATCH_SUPERVISOR_LAUNCHD_PLIST_PATH")
        os.environ["AI8VIDEO_BATCH_SUPERVISOR_LAUNCHD_PLIST_PATH"] = str(self.root / f"{DEFAULT_LABEL}.plist")

    def tearDown(self) -> None:
        if self.plist_env_backup is None:
            os.environ.pop("AI8VIDEO_BATCH_SUPERVISOR_LAUNCHD_PLIST_PATH", None)
        else:
            os.environ["AI8VIDEO_BATCH_SUPERVISOR_LAUNCHD_PLIST_PATH"] = self.plist_env_backup
        self.tempdir.cleanup()

    def _build_config(self) -> AI8VideoConfig:
        return AI8VideoConfig(
            dry_run=True,
            batch_schedule_times="09:00,13:15",
            batch_seed_file=str(self.root / "seed_messages.txt"),
            batch_target_pass_count=5,
            batch_style_hint="商务",
            batch_alert_min_pass_rate=0.7,
            batch_alert_consecutive_low_pass_runs=2,
        )

    def test_build_launchd_plist_contains_supervisor_loop_arguments(self) -> None:
        config = self._build_config()
        payload = build_launchd_plist(
            config=config,
            python_executable="/usr/bin/python3",
            seed_file=config.batch_seed_file,
            schedule_times=config.batch_schedule_times,
            target_pass_count=config.batch_target_pass_count,
            style_hint=config.batch_style_hint,
            poll_seconds=45,
        )

        self.assertEqual(payload["Label"], DEFAULT_LABEL)
        self.assertTrue(payload["RunAtLoad"])
        self.assertTrue(payload["KeepAlive"])
        self.assertEqual(payload["WorkingDirectory"], str(Path(__file__).resolve().parents[1]))
        self.assertIn("--loop", payload["ProgramArguments"])
        self.assertIn("--seed-file", payload["ProgramArguments"])
        self.assertIn(str(config.batch_seed_file), payload["ProgramArguments"])
        self.assertIn("--schedule-times", payload["ProgramArguments"])
        self.assertIn("09:00,13:15", payload["ProgramArguments"])
        self.assertIn("--target-pass-count", payload["ProgramArguments"])
        self.assertIn("5", payload["ProgramArguments"])
        self.assertEqual(payload["EnvironmentVariables"]["AI8VIDEO_DRY_RUN"], "1")

    def test_write_and_inspect_launchd_plist(self) -> None:
        config = self._build_config()
        payload = build_launchd_plist(config=config)
        plist_path = self.root / f"{DEFAULT_LABEL}.plist"
        written = write_launchd_plist(plist_path, payload)
        self.assertTrue(written.exists())

        loaded = plistlib.loads(written.read_bytes())
        self.assertEqual(loaded["Label"], DEFAULT_LABEL)
        self.assertEqual(loaded["ProgramArguments"][0], str(Path(sys.executable).resolve()))

        status = inspect_launchd_deployment(plist_path=written, label=DEFAULT_LABEL)
        self.assertTrue(status["exists"])
        self.assertEqual(status["manager"], "launchd")
        self.assertEqual(status["seedFile"], str(config.batch_seed_file))
        self.assertEqual(status["scheduleTimes"], ["09:00", "13:15"])
        self.assertEqual(status["targetPassCount"], 5)
        self.assertEqual(status["styleHint"], "商务")
        self.assertEqual(status["pollSeconds"], 30)
        self.assertEqual(status["minPassRate"], 0.7)
        self.assertEqual(status["consecutiveLowPassRuns"], 2)

    def test_build_launchd_plist_requires_schedule_and_can_fallback_to_default_seed_path(self) -> None:
        config = AI8VideoConfig(
            dry_run=True,
            batch_alert_min_pass_rate=0.7,
            batch_alert_consecutive_low_pass_runs=2,
        )
        with self.assertRaisesRegex(ValueError, "schedule_times is required"):
            build_launchd_plist(config=config, seed_file=str(self.root / "seed_messages.txt"), schedule_times="")
        payload = build_launchd_plist(config=config, schedule_times="09:00")
        self.assertIn("--seed-file", payload["ProgramArguments"])
        self.assertIn("--schedule-times", payload["ProgramArguments"])


if __name__ == "__main__":
    unittest.main()
