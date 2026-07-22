from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.media import narration_review


class NarrationReviewTest(unittest.TestCase):
    def test_review_accepts_clean_narration(self) -> None:
        result = narration_review.review_narration_text(
            lambda _prompt: '{"passes":true,"issues":[],"approved_text":"连接全球，从沟通开始。"}',
            video_prompt="镜头说明",
            candidate_text="连接全球，从沟通开始。",
            business_prompt="禁止 618",
            review_count=2,
        )

        self.assertTrue(result["passes"])
        self.assertEqual(result["text"], "连接全球，从沟通开始")
        self.assertEqual(len(result["attempts"]), 1)

    def test_review_repairs_then_rechecks_polluted_narration(self) -> None:
        outputs = iter([
            '{"passes":false,"issues":[{"type":"production_instruction_leak","text":"人物服装符合夏季","reason":"制作说明"}],"approved_text":"连接全球，从沟通开始。"}',
            '{"passes":true,"issues":[],"approved_text":"连接全球，从沟通开始。"}',
        ])

        result = narration_review.review_narration_text(
            lambda _prompt: next(outputs),
            video_prompt="人物服装符合夏季。台词：连接全球，从沟通开始。",
            candidate_text="连接全球，从沟通开始。人物服装符合夏季。",
            business_prompt="所有人穿着必须符合夏季",
            review_count=2,
        )

        self.assertTrue(result["passes"])
        self.assertEqual(result["text"], "连接全球，从沟通开始")
        self.assertEqual(len(result["attempts"]), 2)

    def test_review_exhaustion_returns_empty_text(self) -> None:
        result = narration_review.review_narration_text(
            lambda _prompt: '{"passes":false,"issues":[],"approved_text":"全片无任何618内容。"}',
            video_prompt="全片无任何618内容",
            candidate_text="全片无任何618内容",
            business_prompt="禁止 618",
            review_count=2,
        )

        self.assertFalse(result["passes"])
        self.assertEqual(result["text"], "")

    def test_review_count_persists_and_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings_path = Path(tempdir) / "settings.json"
            with patch.object(narration_review, "NARRATION_REVIEW_DIR", settings_path.parent), patch.object(
                narration_review,
                "NARRATION_REVIEW_SETTINGS_PATH",
                settings_path,
            ):
                result = narration_review.update_narration_review_count(99)

                self.assertEqual(result["reviewCount"], 10)
                self.assertEqual(narration_review.narration_review_status()["reviewCount"], 10)


if __name__ == "__main__":
    unittest.main()
