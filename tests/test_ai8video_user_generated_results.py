from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.assets import user_generated_results


class AI8VideoUserGeneratedResultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_existing_result_dir_does_not_restore_deleted_legacy_cover(self) -> None:
        user_root = self.root / "用户文件夹"
        generated_root = user_root / "用户生成结果"
        legacy_root = self.root / "用户生成结果"
        generated_root.mkdir(parents=True)
        legacy_cover = legacy_root / "cover" / "deleted.jpg"
        legacy_cover.parent.mkdir(parents=True)
        legacy_cover.write_bytes(b"legacy-cover")

        with patch.object(user_generated_results, "ensure_user_file_root", lambda: user_root.mkdir(parents=True, exist_ok=True) or user_root), patch.object(
            user_generated_results,
            "USER_GENERATED_RESULT_ROOT",
            generated_root,
        ):
            result = user_generated_results.ensure_user_generated_result_dir()

        self.assertEqual(result, generated_root)
        self.assertFalse((generated_root / "cover" / "deleted.jpg").exists())

    def test_missing_result_dir_never_migrates_legacy_copy(self) -> None:
        user_root = self.root / "用户文件夹"
        generated_root = user_root / "用户生成结果"
        legacy_root = self.root / "用户生成结果"
        legacy_cover = legacy_root / "cover" / "initial.jpg"
        legacy_cover.parent.mkdir(parents=True)
        legacy_cover.write_bytes(b"legacy-cover")

        with patch.object(user_generated_results, "ensure_user_file_root", lambda: user_root.mkdir(parents=True, exist_ok=True) or user_root), patch.object(
            user_generated_results,
            "USER_GENERATED_RESULT_ROOT",
            generated_root,
        ):
            first = user_generated_results.ensure_user_generated_result_dir()
            second = user_generated_results.ensure_user_generated_result_dir()

        self.assertEqual(first, generated_root)
        self.assertEqual(second, generated_root)
        self.assertFalse((generated_root / "cover" / "initial.jpg").exists())

    def test_migrate_legacy_result_layout_restores_video_root_and_metadata(self) -> None:
        generated_root = self.root / "用户生成结果"
        legacy_video = generated_root / "video" / "restored" / "legacy.mp4"
        legacy_meta = generated_root / ".restored-meta" / "video" / "restored" / "legacy.mp4.json"
        legacy_video.parent.mkdir(parents=True)
        legacy_meta.parent.mkdir(parents=True)
        legacy_video.write_bytes(b"video")
        legacy_meta.write_text('{"videoTitle":"恢复","userGeneratedKey":"video/restored/legacy.mp4"}', encoding="utf-8")

        result = user_generated_results.migrate_legacy_result_layout(generated_root)

        self.assertEqual(result["movedVideos"], ["video/legacy.mp4"])
        self.assertTrue((generated_root / "video" / "legacy.mp4").is_file())
        self.assertFalse(legacy_video.exists())
        migrated_meta = generated_root / ".restored-meta" / "video" / "legacy.mp4.json"
        self.assertTrue(migrated_meta.is_file())
        self.assertEqual(
            json.loads(migrated_meta.read_text(encoding="utf-8"))["userGeneratedKey"],
            "video/legacy.mp4",
        )
        self.assertTrue((generated_root / "video").is_dir())

    def test_reconciliation_resolves_legacy_flat_key_to_video_root(self) -> None:
        generated_root = self.root / "用户生成结果"
        result_file = generated_root / "video" / "done.mp4"
        result_file.parent.mkdir(parents=True)
        result_file.write_bytes(b"video")

        result = user_generated_results.build_generation_result_reconciliation(
            {"items": [{"jobId": "job-1", "status": "succeeded"}]},
            [{"jobId": "job-1", "archiveKey": "done.mp4"}],
            result_root=generated_root,
        )

        self.assertEqual(result["items"][0]["resultState"], "available")
        self.assertEqual(result["items"][0]["resultPath"], str(result_file.resolve()))

    def test_mirror_and_sync_are_disabled_to_avoid_hidden_copies(self) -> None:
        user_root = self.root / "用户文件夹"
        generated_root = user_root / "用户生成结果"
        archive_root = self.root / "archive"
        source = archive_root / "video" / "done.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"mp4")

        with patch.object(user_generated_results, "ensure_user_file_root", lambda: user_root.mkdir(parents=True, exist_ok=True) or user_root), patch.object(
            user_generated_results,
            "USER_GENERATED_RESULT_ROOT",
            generated_root,
        ):
            self.assertIsNone(user_generated_results.mirror_generated_result_file(source))
            self.assertEqual(user_generated_results.sync_generated_results_from_archive_root(archive_root), generated_root)

        self.assertFalse((generated_root / "video" / "done.mp4").exists())

    def test_reconciliation_reports_available_succeeded_result(self) -> None:
        generated_root = self.root / "用户生成结果"
        result_file = generated_root / "video" / "done.mp4"
        result_file.parent.mkdir(parents=True)
        result_file.write_bytes(b"video")
        progress = {
            "generationBatchId": "gb-reconcile-success",
            "items": [{"videoIndex": 1, "jobId": "job-1", "status": "succeeded"}],
        }
        assets = [{"jobId": "job-1", "archiveKey": "video/done.mp4"}]

        result = user_generated_results.build_generation_result_reconciliation(
            progress,
            assets,
            result_root=generated_root,
        )

        self.assertEqual(result["summary"]["conflicts"], 0)
        self.assertEqual(result["items"][0]["assetState"], "recorded")
        self.assertEqual(result["items"][0]["resultState"], "available")
        self.assertEqual(result["orphanResults"], [])

    def test_reconciliation_keeps_degraded_html_motion_as_successful_base_video(self) -> None:
        generated_root = self.root / "用户生成结果"
        result_file = generated_root / "video" / "degraded.mp4"
        result_file.parent.mkdir(parents=True)
        result_file.write_bytes(b"video")
        progress = {
            "generationBatchId": "gb-html-degraded",
            "items": [{"videoIndex": 1, "jobId": "job-1", "status": "succeeded"}],
        }
        assets = [{
            "jobId": "job-1",
            "archiveKey": "video/degraded.mp4",
            "htmlMotionOverlay": {
                "status": "degraded",
                "reason": "透明渲染失败，已保留基础视频。",
            },
        }]

        result = user_generated_results.build_generation_result_reconciliation(
            progress,
            assets,
            result_root=generated_root,
        )

        item = result["items"][0]
        self.assertEqual(item["conflicts"], [])
        self.assertEqual(item["htmlMotionOverlay"]["status"], "degraded")

    def test_reconciliation_keeps_succeeded_task_when_result_was_deleted(self) -> None:
        generated_root = self.root / "用户生成结果"
        progress = {
            "generationBatchId": "gb-reconcile-deleted",
            "items": [{"videoIndex": 1, "jobId": "job-1", "status": "succeeded"}],
        }
        assets = [{"jobId": "job-1", "archiveKey": "video/deleted.mp4"}]

        result = user_generated_results.build_generation_result_reconciliation(
            progress,
            assets,
            result_root=generated_root,
        )

        item = result["items"][0]
        self.assertEqual(item["taskStatus"], "succeeded")
        self.assertEqual(item["assetState"], "recorded")
        self.assertEqual(item["resultState"], "missing")
        self.assertEqual(item["conflicts"], ["succeeded_without_result"])
        self.assertFalse((generated_root / "video" / "deleted.mp4").exists())

    def test_reconciliation_treats_failed_task_without_result_as_expected(self) -> None:
        generated_root = self.root / "用户生成结果"
        progress = {
            "generationBatchId": "gb-reconcile-failed",
            "items": [{"videoIndex": 1, "jobId": None, "status": "failed"}],
        }

        result = user_generated_results.build_generation_result_reconciliation(
            progress,
            [],
            result_root=generated_root,
        )

        item = result["items"][0]
        self.assertEqual(item["assetState"], "not_expected")
        self.assertEqual(item["resultState"], "not_expected")
        self.assertEqual(item["conflicts"], [])

    def test_reconciliation_reports_orphan_result_without_asset(self) -> None:
        generated_root = self.root / "用户生成结果"
        orphan_file = generated_root / "video" / "orphan.mp4"
        orphan_file.parent.mkdir(parents=True)
        orphan_file.write_bytes(b"video")

        result = user_generated_results.build_generation_result_reconciliation(
            {"generationBatchId": "gb-reconcile-orphan", "items": []},
            [],
            result_root=generated_root,
        )

        self.assertEqual(result["summary"]["conflicts"], 1)
        self.assertEqual(result["orphanResults"][0]["relativePath"], "video/orphan.mp4")
        self.assertEqual(result["orphanResults"][0]["conflicts"], ["result_without_asset"])

    def test_reconciliation_ignores_dry_run_placeholder_results(self) -> None:
        generated_root = self.root / "用户生成结果"
        placeholder = generated_root / "video" / "01-demo-dry-model-1-a.mp4"
        placeholder.parent.mkdir(parents=True)
        placeholder.write_bytes(b"placeholder")

        result = user_generated_results.build_generation_result_reconciliation(
            {"generationBatchId": "gb-real", "items": []},
            [],
            result_root=generated_root,
        )

        self.assertEqual(result["summary"]["availableResults"], 0)
        self.assertEqual(result["summary"]["conflicts"], 0)
        self.assertEqual(result["orphanResults"], [])

    def test_reconciliation_does_not_treat_historical_asset_result_as_orphan(self) -> None:
        generated_root = self.root / "用户生成结果"
        historical_file = generated_root / "video" / "historical.mp4"
        historical_file.parent.mkdir(parents=True)
        historical_file.write_bytes(b"video")
        historical_assets = [
            {"jobId": "historical-job", "archiveKey": "video/historical.mp4"},
        ]

        result = user_generated_results.build_generation_result_reconciliation(
            {"generationBatchId": "gb-current", "items": []},
            historical_assets,
            result_root=generated_root,
        )

        self.assertEqual(result["summary"]["assetRecords"], 0)
        self.assertEqual(result["summary"]["scannedAssetRecords"], 1)
        self.assertEqual(result["summary"]["conflicts"], 0)
        self.assertEqual(result["orphanResults"], [])
        self.assertEqual(result["unmatchedAssets"], [])

    def test_reconciliation_prefers_asset_from_current_generation_batch(self) -> None:
        generated_root = self.root / "用户生成结果"
        current_result = generated_root / "video" / "current.mp4"
        other_result = generated_root / "video" / "other.mp4"
        current_result.parent.mkdir(parents=True)
        current_result.write_bytes(b"current")
        other_result.write_bytes(b"other")
        progress = {
            "sessionId": "session-current",
            "generationBatchId": "gb-current",
            "items": [{"videoIndex": 1, "jobId": "shared-job", "status": "succeeded"}],
        }
        assets = [
            {
                "sessionId": "session-other",
                "generationBatchId": "gb-other",
                "jobId": "shared-job",
                "archiveKey": "video/other.mp4",
            },
            {
                "sessionId": "session-current",
                "generationBatchId": "gb-current",
                "jobId": "shared-job",
                "archiveKey": "video/current.mp4",
            },
        ]

        result = user_generated_results.build_generation_result_reconciliation(
            progress,
            assets,
            result_root=generated_root,
        )

        item = result["items"][0]
        self.assertEqual(item["archiveKey"], "video/current.mp4")
        self.assertEqual(item["resultPath"], str(current_result.resolve()))
        self.assertEqual(item["conflicts"], [])

    def test_reconciliation_rejects_asset_from_other_generation_batch(self) -> None:
        generated_root = self.root / "用户生成结果"
        other_result = generated_root / "video" / "other.mp4"
        other_result.parent.mkdir(parents=True)
        other_result.write_bytes(b"other")
        progress = {
            "sessionId": "session-current",
            "generationBatchId": "gb-current",
            "items": [{"videoIndex": 1, "jobId": "shared-job", "status": "succeeded"}],
        }
        assets = [
            {
                "sessionId": "session-other",
                "generationBatchId": "gb-other",
                "jobId": "shared-job",
                "archiveKey": "video/other.mp4",
            },
        ]

        result = user_generated_results.build_generation_result_reconciliation(
            progress,
            assets,
            result_root=generated_root,
        )

        item = result["items"][0]
        self.assertEqual(item["assetState"], "missing")
        self.assertEqual(
            item["conflicts"],
            ["succeeded_without_asset", "succeeded_without_result"],
        )

    def test_reconciliation_rejects_asset_path_outside_result_root(self) -> None:
        generated_root = self.root / "用户生成结果"
        outside_file = self.root / "outside.mp4"
        outside_file.write_bytes(b"outside")
        progress = {
            "generationBatchId": "gb-reconcile-path",
            "items": [{"videoIndex": 1, "jobId": "job-1", "status": "succeeded"}],
        }
        assets = [{"jobId": "job-1", "archiveLocalPath": str(outside_file)}]

        result = user_generated_results.build_generation_result_reconciliation(
            progress,
            assets,
            result_root=generated_root,
        )

        item = result["items"][0]
        self.assertIsNone(item["resultPath"])
        self.assertEqual(item["conflicts"], ["succeeded_without_result"])


if __name__ == "__main__":
    unittest.main()
