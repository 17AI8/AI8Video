from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai8video.assets.asset_maintenance import AssetMaintenanceService
from ai8video.assets.asset_store import JsonlAssetStore


class AI8VideoAssetMaintenanceTest(unittest.TestCase):
    def test_find_user_generated_record_prefers_latest_matching_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            video_path = project_root / "用户生成结果" / "video" / "demo.mp4"
            service = self._build_service(
                project_root,
                [
                    {"jobId": "older-job", "archiveKey": "video/demo.mp4"},
                    {"jobId": "other-job", "archiveKey": "video/other.mp4"},
                    {"jobId": "latest-job", "archiveLocalPath": str(video_path)},
                ],
            )

            record = service.find_user_generated_record("video/demo.mp4", video_path)

        self.assertEqual(record["jobId"], "latest-job")

    def test_save_tts_narration_text_updates_only_latest_matching_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            video_path = project_root / "用户生成结果" / "video" / "demo.mp4"
            store = JsonlAssetStore(project_root / "assets.jsonl")
            store.rewrite_all(
                [
                    {"jobId": "older-job", "archiveKey": "video/demo.mp4"},
                    {"jobId": "latest-job", "archiveKey": "video/demo.mp4"},
                ]
            )
            service = AssetMaintenanceService(store, project_root)

            service.save_tts_narration_text("video/demo.mp4", video_path, "新台词")
            records = store.read_all()

        self.assertNotIn("generationMeta", records[0])
        self.assertEqual(
            records[1]["generationMeta"]["userTtsNarrationText"],
            "新台词",
        )
        self.assertIn("userTtsNarrationUpdatedAt", records[1]["generationMeta"])

    def test_save_extension_video_prompt_preserves_explicit_empty_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            video_path = project_root / "用户生成结果" / "video" / "demo.mp4"
            store = JsonlAssetStore(project_root / "assets.jsonl")
            store.rewrite_all([{"jobId": "job", "archiveKey": "video/demo.mp4", "prompt": "原提示词"}])
            service = AssetMaintenanceService(store, project_root)

            service.save_extension_video_prompt("video/demo.mp4", video_path, "")
            record = store.read_all()[0]

        self.assertIn("extensionVideoPrompt", record["generationMeta"])
        self.assertEqual(record["generationMeta"]["extensionVideoPrompt"], "")

    def test_remove_records_returns_removed_and_remaining_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            store = JsonlAssetStore(project_root / "assets.jsonl")
            store.rewrite_all(
                [
                    {"jobId": "keep-job", "orphan": False},
                    {"jobId": "remove-job", "orphan": True},
                ]
            )
            service = AssetMaintenanceService(store, project_root)

            result = service.remove_records(lambda record: record.get("orphan") is True)
            records = store.read_all()

        self.assertEqual(result, (1, 1))
        self.assertEqual(records, [{"jobId": "keep-job", "orphan": False}])

    @staticmethod
    def _build_service(
        project_root: Path,
        records: list[dict[str, object]],
    ) -> AssetMaintenanceService:
        store = JsonlAssetStore(project_root / "assets.jsonl")
        store.rewrite_all(records)
        return AssetMaintenanceService(store, project_root)


if __name__ == "__main__":
    unittest.main()
