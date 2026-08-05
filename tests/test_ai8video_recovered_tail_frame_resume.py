from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ai8video.generation.recovered_tail_frame_resume import (
    get_recovered_tail_frame_resume,
    prepare_recovered_tail_frame_resume,
    refresh_recovered_tail_frame_resume,
    take_recovered_tail_frame_resume,
)
from ai8video.generation.pipeline import AI8VideoPipeline


class RecoveredTailFrameResumeTests(TestCase):
    def test_tail_frame_reference_without_extra_instruction_is_valid(self):
        request = self._asset_request()
        request.reference_image = "/tmp/tail-frame.png"

        self.assertIsNone(AI8VideoPipeline._reference_task_constraints(request))

    def test_reference_image_transform_settings_do_not_enter_planning_constraints(self):
        request = self._asset_request()
        request.reference_image = "/tmp/default-reference.png"
        request.reference_image_transform_options = {
            "autoChangeBackground": True,
            "autoChangePose": True,
        }
        request.reference_image_custom_prompt = "重新生成夏日天台场景"

        self.assertIsNone(AI8VideoPipeline._reference_task_constraints(request))

    def test_recovery_waits_with_latest_tail_frame_and_preserves_remaining_prompts(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_video = root / "first.mp4"
            regenerated_video = root / "first-regenerated.mp4"
            first_video.write_bytes(b"video-1")
            regenerated_video.write_bytes(b"video-1-new")
            progress = {
                "items": [
                    {"videoIndex": 1, "title": "第一条", "videoPrompt": "提示词一", "status": "archiving"},
                    {"videoIndex": 2, "title": "第二条", "videoPrompt": "提示词二", "status": "pending_submission"},
                    {"videoIndex": 3, "title": "第三条", "videoPrompt": "提示词三", "status": "pending_submission"},
                ]
            }
            records = [self._asset_record(first_video)]

            with patch(
                "ai8video.generation.recovered_tail_frame_resume.MANUAL_TAIL_FRAME_DIR",
                root / "previews",
            ), patch(
                "ai8video.generation.recovered_tail_frame_resume.extract_tail_frame",
                side_effect=self._write_preview,
            ):
                checkpoint = prepare_recovered_tail_frame_resume(
                    session_id="session-1",
                    source_batch_id="batch-1",
                    progress=progress,
                    asset_records=records,
                )

                self.assertIsNotNone(checkpoint)
                self.assertEqual([video.index for video in checkpoint.videos], [2, 3])
                self.assertEqual([video.prompt for video in checkpoint.videos], ["提示词二", "提示词三"])
                self.assertEqual(checkpoint.request.tail_frame_chaining_mode, "manual")
                self.assertTrue(checkpoint.request.smart_split_reason)
                self.assertTrue(Path(checkpoint.request.reference_image).is_file())

                refreshed = refresh_recovered_tail_frame_resume(
                    "session-1",
                    "batch-1",
                    2,
                    records + [self._asset_record(regenerated_video)],
                )
                self.assertTrue(refreshed["recoveredResume"])
                self.assertEqual(checkpoint.predecessor_path, regenerated_video)
                self.assertIs(take_recovered_tail_frame_resume("session-1", "batch-1", 2), checkpoint)
                with self.assertRaises(LookupError):
                    get_recovered_tail_frame_resume("session-1", "batch-1", 2)

    def test_recovery_does_not_replace_active_remote_job_with_manual_wait(self):
        progress = {
            "items": [
                {"videoIndex": 1, "status": "succeeded"},
                {
                    "videoIndex": 2,
                    "status": "polling",
                    "jobId": "remote-job-2",
                    "videoPrompt": "提示词二",
                },
            ]
        }

        checkpoint = prepare_recovered_tail_frame_resume(
            session_id="session-1",
            source_batch_id="batch-1",
            progress=progress,
            asset_records=[],
        )

        self.assertIsNone(checkpoint)

    @staticmethod
    def _write_preview(_source: Path, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"preview")

    @staticmethod
    def _asset_record(path: Path) -> dict:
        return {
            "sessionId": "session-1",
            "videoIndex": 1,
            "generationStatus": "generated",
            "archiveLocalPath": str(path),
            "request": {
                "durationSeconds": 10,
                "ratio": "9:16",
                "resolution": "720p",
                "preset": "custom",
                "htmlMotionOverlayEnabled": True,
            },
        }

    @staticmethod
    def _asset_request():
        from ai8video.core.models import ParsedRequest

        return ParsedRequest(
            raw_text="恢复被中断的传尾帧任务",
            mode="batch_videos",
            video_count=2,
            tail_frame_chaining=True,
            tail_frame_chaining_mode="manual",
        )
