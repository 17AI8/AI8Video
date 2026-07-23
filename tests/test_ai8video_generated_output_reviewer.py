from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai8video.core.config import AI8VideoConfig
from ai8video.core.models import VideoPrompt
from ai8video.generation.generated_output_reviewer import GeneratedOutputReviewer


class AI8VideoGeneratedOutputReviewerTest(unittest.TestCase):
    def test_dry_run_is_explicitly_simulated_not_fake_passed(self) -> None:
        reviewer = GeneratedOutputReviewer(AI8VideoConfig(dry_run=True))

        result = reviewer.review(None, VideoPrompt(1, "第一条", "提示词"))

        self.assertEqual(result["status"], "simulated")
        self.assertIsNone(result["passes"])
        self.assertEqual(result["reviewSource"], "dry_run")

    def test_missing_local_video_reports_review_unavailable(self) -> None:
        reviewer = GeneratedOutputReviewer(AI8VideoConfig(dry_run=False))

        result = reviewer.review("/tmp/not-created-ai8video.mp4", VideoPrompt(1, "第一条", "提示词"))

        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["passes"])
        self.assertIn("未落到可审查", result["issues"][0])

    def test_multimodal_review_merges_technical_findings_and_next_constraints(self) -> None:
        calls: list[tuple[Path, int, list[str]]] = []

        def multimodal(contact_sheet, video, expected_duration_seconds, technical_issues):
            calls.append((contact_sheet, video.index, technical_issues))
            return {
                "passes": True,
                "issues": ["主体边缘有轻微闪动"],
                "improvements": ["下一条减少快速转身，保持主体轮廓稳定"],
                "next_prompt_constraints": ["镜头切换前后保持人物朝向一致"],
            }

        config = AI8VideoConfig(
            dry_run=False,
            multimodal_base_url="https://example.test/v1",
            multimodal_api_key="test-key",
            multimodal_model="test-model",
        )
        reviewer = GeneratedOutputReviewer(config, multimodal_call=multimodal)

        with tempfile.TemporaryDirectory() as tempdir:
            video_path = Path(tempdir) / "video.mp4"
            video_path.write_bytes(b"video")
            contact_sheet = Path(tempdir) / "contact-sheet.jpg"
            contact_sheet.write_bytes(b"image")
            with patch(
                "ai8video.generation.generated_output_reviewer._technical_review",
                return_value=(7.0, ["成片时长为 7.0 秒，与目标 10 秒偏差过大"]),
            ), patch(
                "ai8video.generation.generated_output_reviewer._build_contact_sheet",
                return_value=contact_sheet,
            ):
                result = reviewer.review(video_path, VideoPrompt(2, "第二条", "提示词"))

        self.assertEqual(calls, [(contact_sheet, 2, ["成片时长为 7.0 秒，与目标 10 秒偏差过大"])])
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["passes"])
        self.assertEqual(result["durationSeconds"], 7.0)
        self.assertIn("主体边缘有轻微闪动", result["issues"])
        self.assertIn("严格按 10 秒时间线", result["nextPromptConstraints"][0])
        self.assertIn("镜头切换前后", result["nextPromptConstraints"][1])


if __name__ == "__main__":
    unittest.main()
