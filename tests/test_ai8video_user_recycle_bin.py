from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.assets import user_recycle_bin
from ai8video.assets import user_generated_results


class AI8VideoUserRecycleBinTest(unittest.TestCase):
    def test_delete_failed_video_tasks_deletes_selected_task_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recycle_root = Path(temporary_directory) / "回收站"
            first_folder = self._create_task_folder(recycle_root, "task-one")
            second_folder = self._create_task_folder(recycle_root, "task-two")
            with patch.object(user_recycle_bin, "USER_RECYCLE_BIN_ROOT", recycle_root), patch.object(
                user_recycle_bin,
                "ensure_user_file_root",
            ):
                result = user_recycle_bin.delete_failed_video_tasks(["task-one"])

            self.assertEqual(result["deletedFolders"], ["task-one"])
            self.assertFalse(first_folder.exists())
            self.assertTrue(second_folder.exists())

    def test_delete_failed_video_tasks_rejects_path_traversal_before_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recycle_root = Path(temporary_directory) / "回收站"
            selected_folder = self._create_task_folder(recycle_root, "task-one")
            with patch.object(user_recycle_bin, "USER_RECYCLE_BIN_ROOT", recycle_root), patch.object(
                user_recycle_bin,
                "ensure_user_file_root",
            ):
                with self.assertRaisesRegex(ValueError, "路径越界"):
                    user_recycle_bin.delete_failed_video_tasks(["task-one", "../outside"])

            self.assertTrue(selected_folder.exists())

    def test_restore_failed_video_task_moves_video_to_generated_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recycle_root = Path(temporary_directory) / "回收站"
            result_root = Path(temporary_directory) / "用户生成结果"
            task_folder = self._create_task_folder(recycle_root, "task-one")
            source_video = task_folder / "video" / "01-demo.mp4"
            source_preview = task_folder / "preview" / "01-demo.jpg"
            source_video.parent.mkdir(parents=True)
            source_preview.parent.mkdir(parents=True)
            source_video.write_bytes(b"video")
            source_preview.write_bytes(b"preview")
            (task_folder / user_recycle_bin.RECYCLE_MANIFEST_NAME).write_text(
                json.dumps({
                    "episodeIndex": 1,
                    "episodeTitle": "恢复测试",
                    "jobId": "job-demo",
                    "videos": [{"relativePath": "task-one/video/01-demo.mp4"}],
                    "meta": {
                        "segmentRecords": [{"narrationText": "第一句台词。第二句台词。"}],
                    },
                }),
                encoding="utf-8",
            )
            with patch.object(user_recycle_bin, "USER_RECYCLE_BIN_ROOT", recycle_root), patch.object(
                user_recycle_bin,
                "ensure_user_file_root",
            ), patch.object(user_generated_results, "USER_GENERATED_RESULT_ROOT", result_root), patch.object(
                user_generated_results,
                "ensure_user_file_root",
            ):
                result = user_recycle_bin.restore_failed_video_task("task-one")

            restored_key = result["restoredVideos"][0]["userGeneratedKey"]
            self.assertEqual(result["restoredCount"], 1)
            self.assertEqual(restored_key, f"video/{Path(restored_key).name}")
            self.assertFalse(task_folder.exists())
            self.assertFalse(source_video.exists())
            self.assertTrue((result_root / restored_key).is_file())
            preview_key = user_recycle_bin.preview_key_for_video(restored_key)
            self.assertTrue((result_root / preview_key).is_file())
            metadata = user_recycle_bin.load_restored_result_metadata(result_root, restored_key)
            self.assertEqual(metadata["episodeTitle"], "恢复测试")
            self.assertEqual(
                metadata["generationMeta"]["segmentRecords"][0]["narrationText"],
                "第一句台词。第二句台词。",
            )
            updated_metadata = user_recycle_bin.save_restored_result_html_motion_overlay(
                result_root,
                restored_key,
                {"status": "degraded", "reason": "动效降级"},
            )
            self.assertEqual(
                updated_metadata["htmlMotionOverlay"]["status"],
                "degraded",
            )

    @staticmethod
    def _create_task_folder(recycle_root: Path, folder_name: str) -> Path:
        task_folder = recycle_root / folder_name
        task_folder.mkdir(parents=True)
        (task_folder / user_recycle_bin.RECYCLE_MANIFEST_NAME).write_text(
            json.dumps({"videos": []}),
            encoding="utf-8",
        )
        return task_folder


if __name__ == "__main__":
    unittest.main()
