from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from ai8video.batch.batch_alert_store import BatchAlertStore
from ai8video.core.config import AI8VideoConfig
from ai8video.application.runtime import get_health_payload, get_runtime


class AI8VideoConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.state_path = self.root / "batch_supervisor_state.json"
        self.lock_path = self.root / "batch_supervisor.lock"
        self.env_backup = {key: os.environ.get(key) for key in self._env_keys()}
        os.environ.pop("AI8VIDEO_LLM_BASE_URL", None)
        os.environ.pop("AI8VIDEO_LLM_API_KEY", None)
        os.environ.pop("AI8VIDEO_LLM_MODEL", None)
        os.environ.pop("AI8VIDEO_IMAGE_BASE_URL", None)
        os.environ.pop("AI8VIDEO_IMAGE_API_KEY", None)
        os.environ.pop("AI8VIDEO_IMAGE_MODEL", None)
        os.environ["AI8VIDEO_BATCH_ALERT_DIR"] = str(self.root / "batch_alerts")
        os.environ["AI8VIDEO_BATCH_SUPERVISOR_STATE_PATH"] = str(self.state_path)
        os.environ["AI8VIDEO_BATCH_SUPERVISOR_LOCK_PATH"] = str(self.lock_path)
        os.environ.pop("AI8VIDEO_BATCH_SCHEDULE_TIMES", None)
        os.environ["AI8VIDEO_BATCH_SEED_FILE"] = str(self.root / "seed_messages.txt")
        os.environ.pop("AI8VIDEO_BATCH_TARGET_PASS_COUNT", None)
        os.environ.pop("AI8VIDEO_BATCH_STYLE_HINT", None)
        os.environ.pop("AI8VIDEO_BATCH_ALERT_MIN_PASS_RATE", None)
        os.environ.pop("AI8VIDEO_BATCH_ALERT_CONSECUTIVE_LOW_PASS_RUNS", None)
        os.environ["AI8VIDEO_DRY_RUN"] = "1"
        get_runtime(refresh=True)

    def tearDown(self) -> None:
        for key, value in self.env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_runtime(refresh=True)
        self.tempdir.cleanup()

    @staticmethod
    def _env_keys() -> list[str]:
        return [
            "AI8VIDEO_LLM_BASE_URL",
            "AI8VIDEO_LLM_API_KEY",
            "AI8VIDEO_LLM_MODEL",
            "AI8VIDEO_IMAGE_BASE_URL",
            "AI8VIDEO_IMAGE_API_KEY",
            "AI8VIDEO_IMAGE_MODEL",
            "AI8VIDEO_ASSET_STORE_PATH",
            "AI8VIDEO_ARCHIVE_LOCAL_DIR",
            "AI8VIDEO_REAL_JOB_AUDIT_PATH",
            "AI8VIDEO_BATCH_REPORT_DIR",
            "AI8VIDEO_BATCH_ALERT_DIR",
            "AI8VIDEO_BATCH_SUPERVISOR_STATE_PATH",
            "AI8VIDEO_BATCH_SUPERVISOR_ADMIN_STATE_PATH",
            "AI8VIDEO_BATCH_SUPERVISOR_LOCK_PATH",
            "AI8VIDEO_BATCH_SCHEDULE_TIMES",
            "AI8VIDEO_BATCH_SEED_FILE",
            "AI8VIDEO_BATCH_TARGET_PASS_COUNT",
            "AI8VIDEO_BATCH_STYLE_HINT",
            "AI8VIDEO_BATCH_ALERT_MIN_PASS_RATE",
            "AI8VIDEO_BATCH_ALERT_CONSECUTIVE_LOW_PASS_RUNS",
            "AI8VIDEO_DRY_RUN",
        ]

    def test_relative_storage_paths_resolve_under_project_root(self) -> None:
        for key in (
            "AI8VIDEO_ASSET_STORE_PATH",
            "AI8VIDEO_ARCHIVE_LOCAL_DIR",
            "AI8VIDEO_REAL_JOB_AUDIT_PATH",
            "AI8VIDEO_BATCH_REPORT_DIR",
            "AI8VIDEO_BATCH_ALERT_DIR",
            "AI8VIDEO_BATCH_SUPERVISOR_STATE_PATH",
            "AI8VIDEO_BATCH_SUPERVISOR_ADMIN_STATE_PATH",
            "AI8VIDEO_BATCH_SUPERVISOR_LOCK_PATH",
        ):
            os.environ.pop(key, None)
        os.environ["AI8VIDEO_BATCH_SEED_FILE"] = "temp/ai8video/seed_messages.txt"
        project_root = Path(__file__).resolve().parents[1]

        config = AI8VideoConfig.from_env()

        self.assertEqual(
            Path(config.asset_store_path),
            project_root / "temp/ai8video/assets.jsonl",
        )
        self.assertEqual(
            Path(config.archive_local_dir),
            project_root / "media_resources/ai8video/archive",
        )
        self.assertEqual(
            Path(config.real_job_audit_path),
            project_root / "temp/ai8video/real_generation_jobs.jsonl",
        )
        self.assertEqual(
            Path(config.batch_report_dir),
            project_root / "media_resources/ai8video/batch_reports",
        )
        self.assertEqual(
            Path(config.batch_alert_dir),
            project_root / "media_resources/ai8video/batch_alerts",
        )
        self.assertEqual(
            Path(config.batch_supervisor_state_path),
            project_root / "temp/ai8video/batch_supervisor_state.json",
        )
        self.assertEqual(
            Path(config.batch_supervisor_admin_state_path),
            project_root / "temp/ai8video/batch_supervisor_admin_state.json",
        )
        self.assertEqual(
            Path(config.batch_supervisor_lock_path),
            project_root / "temp/ai8video/batch_supervisor.lock",
        )
        self.assertEqual(
            Path(config.batch_seed_file),
            project_root / "temp/ai8video/seed_messages.txt",
        )

    def test_absolute_storage_paths_are_preserved(self) -> None:
        absolute_path = self.root / "custom-assets.jsonl"
        os.environ["AI8VIDEO_ASSET_STORE_PATH"] = str(absolute_path)

        config = AI8VideoConfig.from_env()

        self.assertEqual(Path(config.asset_store_path), absolute_path)

    def test_missing_image_model_reuses_shared_llm_credentials_but_not_model(self) -> None:
        for key in ("AI8VIDEO_IMAGE_BASE_URL", "AI8VIDEO_IMAGE_API_KEY", "AI8VIDEO_IMAGE_MODEL"):
            os.environ.pop(key, None)

        with mock.patch(
            "ai8video.core.config._load_ai8video_llm_fallback",
            return_value={
                "apibase": "https://api.example.com",
                "apikey": "sk-shared",
                "model": "deepseek-v4-flash",
            },
        ), mock.patch(
            "ai8video.core.config.load_model_overrides",
            return_value={},
        ):
            config = AI8VideoConfig.from_env()

        self.assertEqual(config.image_base_url, "https://api.example.com")
        self.assertEqual(config.image_api_key, "sk-shared")
        self.assertIsNone(config.image_model)
        self.assertEqual(config.image_source, "shared_llm_credentials")

    def test_explicit_image_model_env_still_wins_over_fallback(self) -> None:
        os.environ["AI8VIDEO_IMAGE_BASE_URL"] = "https://api.example.com"
        os.environ["AI8VIDEO_IMAGE_API_KEY"] = "sk-image"
        os.environ["AI8VIDEO_IMAGE_MODEL"] = "GPT-image2"

        with mock.patch(
            "ai8video.core.config._load_ai8video_llm_fallback",
            return_value={
                "apibase": "https://api.example.com",
                "apikey": "sk-shared",
                "model": "deepseek-v4-flash",
            },
        ), mock.patch(
            "ai8video.core.config.load_model_overrides",
            return_value={},
        ):
            config = AI8VideoConfig.from_env()

        self.assertEqual(config.image_model, "GPT-image2")
        self.assertEqual(config.image_source, "env")

    def test_runtime_health_exposes_config_sources(self) -> None:
        payload = get_health_payload(refresh=True)

        self.assertEqual(payload["videoGenerationProvider"], "direct-video-model")
        self.assertIn("videoModelSettings", payload)
        self.assertIn("hasVideoModel", payload)
        self.assertIn("base_url", payload["videoModelSettings"])
        self.assertIn("batchAlertDir", payload)
        self.assertIn("batchSupervisorStatePath", payload)
        self.assertIn("batchSupervisorLockPath", payload)
        self.assertIn("batchSupervisorState", payload)
        self.assertIn("batchSupervisorLockExists", payload)
        self.assertIn("batchScheduleTimes", payload)
        self.assertIn("batchNextScheduledSlot", payload)
        self.assertIn("batchLatestAlert", payload)
        self.assertIn("batchLatestFailureReason", payload)
        self.assertIn("batchSeedFile", payload)
        self.assertIn("batchSeedFileStatus", payload)
        self.assertIsNone(payload["batchSupervisorState"])
        self.assertFalse(payload["batchSupervisorLockExists"])
        self.assertEqual(payload["batchScheduleTimes"], [])
        self.assertIsNone(payload["batchNextScheduledSlot"])
        self.assertIsNone(payload["batchLatestAlert"])
        self.assertIsNone(payload["batchLatestFailureReason"])
        self.assertFalse(payload["batchSeedFileStatus"]["exists"])
        self.assertEqual(payload["batchSeedFileStatus"]["lineCount"], 0)

    def test_runtime_health_reads_supervisor_state_and_lock(self) -> None:
        self.state_path.write_text(
            """
            {
              "lastStatus": "ok",
              "lastRunAt": "2026-06-13T10:00:00+08:00",
              "lastReportId": "report-demo",
              "consecutiveLowPassRuns": 1
            }
            """,
            encoding="utf-8",
        )
        self.lock_path.write_text("locked", encoding="utf-8")
        os.environ["AI8VIDEO_BATCH_SCHEDULE_TIMES"] = "09:00,10:30,14:15"
        alert_store = BatchAlertStore(os.environ["AI8VIDEO_BATCH_ALERT_DIR"])
        alert = alert_store.save(
            {
                "createdAt": "2026-06-13T09:58:00+08:00",
                "kind": "goal_not_met",
                "level": "warn",
                "message": "日报未达标：通过 1/5",
                "reportId": "report-demo",
                "reportPath": "media_resources/ai8video/batch_reports/2026-06-13/report-demo.json",
                "goalMet": False,
                "passRate": 0.2,
                "consecutiveLowPassRuns": 1,
            }
        )

        with mock.patch(
            "ai8video.application.runtime._now_localtime",
            return_value=datetime.fromisoformat("2026-06-13T10:05:00+08:00"),
        ):
            payload = get_health_payload(refresh=True)

        self.assertEqual(payload["batchSupervisorState"]["lastReportId"], "report-demo")
        self.assertEqual(payload["batchSupervisorState"]["consecutiveLowPassRuns"], 1)
        self.assertTrue(payload["batchSupervisorLockExists"])
        self.assertEqual(payload["batchScheduleTimes"], ["09:00", "10:30", "14:15"])
        self.assertEqual(payload["batchNextScheduledSlot"], "2026-06-13T10:30:00+08:00")
        self.assertEqual(payload["batchLatestAlert"]["alertId"], alert["alertId"])
        self.assertEqual(payload["batchLatestAlert"]["message"], "日报未达标：通过 1/5")
        self.assertEqual(payload["batchLatestFailureReason"], "日报未达标：通过 1/5")

    def test_runtime_health_reads_batch_seed_file_status(self) -> None:
        seed_path = self.root / "seed_messages.txt"
        seed_path.write_text("老板讲封号风险\n老板讲私域承接\n", encoding="utf-8")
        os.environ["AI8VIDEO_BATCH_SEED_FILE"] = str(seed_path)

        payload = get_health_payload(refresh=True)

        self.assertEqual(payload["batchSeedFile"], str(seed_path.resolve()))
        self.assertEqual(payload["batchSeedFileConfigured"], str(seed_path))
        self.assertTrue(payload["batchSeedFileStatus"]["exists"])
        self.assertEqual(payload["batchSeedFileStatus"]["lineCount"], 2)
        self.assertEqual(payload["batchSeedFileStatus"]["preview"], ["老板讲封号风险", "老板讲私域承接"])

    def test_runtime_health_can_fallback_to_deployment_schedule_and_surface_suggestions(self) -> None:
        seed_path = self.root / "seed_messages.txt"
        seed_path.write_text("老板讲封号风险\n", encoding="utf-8")
        os.environ["AI8VIDEO_BATCH_SEED_FILE"] = str(seed_path)
        self.state_path.write_text(
            """
            {
              "lastStatus": "ok",
              "lastGoalMet": false,
              "consecutiveLowPassRuns": 2
            }
            """,
            encoding="utf-8",
        )

        with mock.patch(
            "ai8video.application.runtime.inspect_launchd_deployment",
            return_value={
                "platformSupported": True,
                "exists": True,
                "loaded": False,
                "scheduleTimes": ["09:00", "13:15"],
                "targetPassCount": 5,
            },
        ), mock.patch(
            "ai8video.application.runtime._now_localtime",
            return_value=datetime.fromisoformat("2026-06-13T08:30:00+08:00"),
        ):
            payload = get_health_payload(refresh=True)

        self.assertEqual(payload["batchScheduleTimes"], ["09:00", "13:15"])
        self.assertEqual(payload["batchConfiguredScheduleTimes"], [])
        self.assertEqual(payload["batchNextScheduledSlot"], "2026-06-13T09:00:00+08:00")
        self.assertIn("部署文件已写好，确认后可直接安装值守。", payload["batchSupervisorSuggestions"])
        self.assertIn("最近连续低成功，先看最近日报和告警，再补候选或下调目标生成数。", payload["batchSupervisorSuggestions"])


if __name__ == "__main__":
    unittest.main()
