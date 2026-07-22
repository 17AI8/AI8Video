from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.core.config import AI8VideoConfig
from ai8video.batch.live_preflight import (
    SAFE_PREFLIGHT_CHECKS,
    build_archive_probe_key,
    run_archive_config_check,
    run_archive_probe_check,
    run_llm_check,
    run_preflight_checks,
)


class AI8VideoLivePreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _build_config(self, **overrides) -> AI8VideoConfig:
        payload = dict(
            dry_run=False,
            archive_backend="auto",
            archive_local_dir=str(self.root / "archive"),
            archive_s3_endpoint=None,
            archive_s3_bucket=None,
            archive_s3_region=None,
            archive_s3_access_key=None,
            archive_s3_secret_key=None,
            archive_s3_prefix="AI8video",
            archive_public_base_url=None,
        )
        payload.update(overrides)
        return AI8VideoConfig(**payload)

    def test_archive_config_check_reports_local_backend(self) -> None:
        config = self._build_config()

        result = run_archive_config_check(config)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["resolvedBackend"], "local")
        self.assertEqual(result["localDir"], str(self.root / "archive"))

    def test_run_llm_check_returns_error_when_core_model_missing(self) -> None:
        config = self._build_config()

        result = run_llm_check(config)

        self.assertEqual(result["status"], "error")
        self.assertIn("核心模型不可用", result["error"])

    def test_archive_probe_check_local_writes_and_cleans_probe_file(self) -> None:
        config = self._build_config()
        archive_dir = Path(config.archive_local_dir)

        result = run_archive_probe_check(config)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["resolvedBackend"], "local")
        self.assertTrue(archive_dir.exists())
        self.assertFalse((archive_dir / ".ai8video-archive-probe.txt").exists())

    def test_archive_config_check_reports_s3_ready(self) -> None:
        config = self._build_config(
            archive_backend="s3",
            archive_s3_endpoint="https://oss.example.com",
            archive_s3_bucket="ai8video-bucket",
            archive_s3_region="oss-cn-hangzhou",
            archive_s3_access_key="ak",
            archive_s3_secret_key="sk",
            archive_public_base_url="https://cdn.example.com/ai8video",
        )

        with patch.dict(sys.modules, {"boto3": types.SimpleNamespace()}):
            result = run_archive_config_check(config)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["resolvedBackend"], "s3")
        self.assertTrue(result["boto3Ready"])
        self.assertEqual(result["bucket"], "ai8video-bucket")

    def test_archive_probe_check_s3_uploads_and_deletes_probe_object(self) -> None:
        config = self._build_config(
            archive_backend="s3",
            archive_s3_endpoint="https://oss.example.com",
            archive_s3_bucket="ai8video-bucket",
            archive_s3_region="oss-cn-hangzhou",
            archive_s3_access_key="ak",
            archive_s3_secret_key="sk",
            archive_public_base_url="https://cdn.example.com/ai8video",
        )
        calls: list[tuple[str, str]] = []

        class _FakeClient:
            def put_object(self, **kwargs):  # noqa: ANN003
                calls.append(("put", kwargs["Key"]))

            def delete_object(self, **kwargs):  # noqa: ANN003
                calls.append(("delete", kwargs["Key"]))

        class _FakeSession:
            def client(self, *args, **kwargs):  # noqa: ANN002, ANN003
                return _FakeClient()

        fake_boto3 = types.SimpleNamespace(
            session=types.SimpleNamespace(Session=lambda: _FakeSession())
        )

        with patch.dict(sys.modules, {"boto3": fake_boto3}):
            result = run_archive_probe_check(config)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["resolvedBackend"], "s3")
        self.assertEqual(result["bucket"], "ai8video-bucket")
        self.assertEqual(calls[0][0], "put")
        self.assertEqual(calls[1][0], "delete")
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertTrue(result["probeUrl"].startswith("https://cdn.example.com/ai8video/"))

    def test_build_archive_probe_key_uses_prefix(self) -> None:
        config = self._build_config(archive_s3_prefix="AI8video")
        key = build_archive_probe_key(config)
        self.assertIn("AI8video/preflight/", key)

    def test_run_preflight_checks_keeps_known_checks_only(self) -> None:
        config = self._build_config()

        with patch(
            "ai8video.batch.live_preflight.run_llm_check",
            return_value={"status": "ok"},
        ) as llm_check, patch(
            "ai8video.batch.live_preflight.run_archive_config_check",
            return_value={"status": "ok"},
        ) as archive_check:
            report = run_preflight_checks(
                config,
                ["llm", "archive_config", "archive_config", "unknown"],
            )

        self.assertEqual(set(report["checks"].keys()), {"llm", "archive_config"})
        llm_check.assert_called_once_with(config)
        archive_check.assert_called_once_with(config)
        self.assertEqual(list(SAFE_PREFLIGHT_CHECKS), ["llm", "archive_config"])


if __name__ == "__main__":
    unittest.main()
