from __future__ import annotations

import unittest

from ai8video.core.models import VideoPrompt
from ai8video.generation.output_review import review_final_outputs
from ai8video.assets.video_asset_archiver import _local_tts_narration_text


class AI8VideoOutputReviewTest(unittest.TestCase):
    def test_review_returns_corrected_prompt_and_narration(self) -> None:
        video = VideoPrompt(
            index=1,
            title="代理机会",
            prompt='画外音：“一个城市只开放一个名额。”',
            source_summary="基于脚本71与脚本75形成选材说明。",
        )
        raw = """[{
          "index": 1,
          "passes": false,
          "corrected_video_prompt": "成年虚构女性以远景站在城市露台。画外音：一个城市只开放一个名额。",
          "narration_text": "一个城市只开放一个名额。",
          "violations": ["内部选材说明已移除"],
          "user_advisories": ["S 型特写可能提高上游审核拒绝概率，已按用户设置保留"]
        }]"""

        prompts: list[str] = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return raw

        reviewed = review_final_outputs([video], llm=fake_llm)[0]

        self.assertIn("成年虚构女性", reviewed.prompt)
        self.assertEqual(_local_tts_narration_text(None, reviewed), "一个城市只开放一个名额")
        self.assertNotIn("基于脚本", _local_tts_narration_text(None, reviewed))
        self.assertFalse(reviewed.keyword_guidance["post_review"]["passes"])
        self.assertEqual(
            reviewed.keyword_guidance["post_review"]["userAdvisories"],
            ["S 型特写可能提高上游审核拒绝概率，已按用户设置保留"],
        )
        self.assertIn("不得擅自删除、弱化或替换", prompts[0])

    def test_review_failure_uses_dialogue_only_fallback(self) -> None:
        video = VideoPrompt(
            index=1,
            title="代理机会",
            prompt='画外音：“邀请好友，立享返佣。”',
            source_summary="内部选材说明。",
        )

        reviewed = review_final_outputs([video], llm=lambda _: "invalid")[0]

        self.assertEqual(_local_tts_narration_text(None, reviewed), "邀请好友，立享返佣")
        self.assertTrue(reviewed.keyword_guidance["post_review"]["fallback"])
        self.assertIsNone(reviewed.keyword_guidance["post_review"]["passes"])
        self.assertEqual(reviewed.keyword_guidance["post_review"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
