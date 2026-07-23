from __future__ import annotations

import unittest

from ai8video.application.message_parser import (
    detect_concurrent_generation_decision,
    detect_batch_mode,
    extract_style_hint,
    extract_core_keywords,
    extract_batch_seed_messages,
    extract_batch_target_count,
    extract_duration_seconds,
    extract_video_count,
)


class AI8VideoMessageParserTest(unittest.TestCase):
    def test_extract_batch_target_count_supports_chinese_numerals(self) -> None:
        text = "今天先跑两条商务风，先看看效果。"

        self.assertTrue(detect_batch_mode(text))
        self.assertEqual(extract_batch_target_count(text), 2)

    def test_extract_batch_seed_messages_supports_inline_candidate_list(self) -> None:
        text = "今天先跑两条商务风，候选：老板在会议室讲封号风险；老板在办公室讲AI8video 承接私域。"

        items = extract_batch_seed_messages(text)

        self.assertEqual(len(items), 2)
        self.assertIn("老板在会议室讲封号风险", items[0])
        self.assertIn("AI8video 承接私域", items[1])

    def test_extract_batch_seed_messages_supports_single_candidate_after_colon(self) -> None:
        text = "今天先跑一条商务风：老板在会议室讲封号风险。"

        items = extract_batch_seed_messages(text)

        self.assertEqual(items, ["老板在会议室讲封号风险。"])

    def test_extract_video_count_supports_real_generation_wording(self) -> None:
        text = "根据这个剧本生成 2 个 10s 短视频，老板商务风。"

        self.assertEqual(extract_video_count(text), 2)

    def test_extract_video_count_supports_leading_count_with_topic(self) -> None:
        text = "10 个，重大消息"

        self.assertEqual(extract_video_count(text), 10)

    def test_extract_video_count_supports_real_generation_without_video_suffix(self) -> None:
        text = "@2.docx @612.png 生成 2 个 10s"

        self.assertEqual(extract_video_count(text), 2)

    def test_extract_video_count_supports_chinese_numerals_for_video_count(self) -> None:
        text = "请把这段内容拆成两条短视频，风格更真实。"

        self.assertEqual(extract_video_count(text), 2)

    def test_extract_duration_seconds_supports_s_suffix(self) -> None:
        text = "根据这个剧本生成 2 个 15s 短视频。"

        self.assertEqual(extract_duration_seconds(text), 15)

    def test_extract_style_hint_keeps_poster_text_request(self) -> None:
        text = "营销号风格，视频中大量“大字报式文字”。"

        hint = extract_style_hint(text)
        self.assertIn("营销号风格", hint)
        self.assertIn("视频中大量“大字报式文字”", hint)

    def test_extract_style_hint_keeps_freeform_visual_text_request(self) -> None:
        text = "老板口播，屏幕大标题冲屏，关键词要加粗高亮。"

        hint = extract_style_hint(text)

        self.assertIn("屏幕大标题冲屏", hint)
        self.assertIn("关键词要加粗高亮", hint)

    def test_extract_core_keywords_from_explicit_theme(self) -> None:
        text = "核心主题：6月18日全球发布倒计时、私域资产、AI8video 替代传统聊天工具"

        self.assertEqual(
            extract_core_keywords(text),
            "6月18日全球发布倒计时、私域资产、AI8video 替代传统聊天工具",
        )

    def test_detect_concurrent_generation_decision_accepts_fast_mode(self) -> None:
        self.assertIs(detect_concurrent_generation_decision("并发模式"), True)
        self.assertIs(detect_concurrent_generation_decision("一起提交，开快速模式"), True)

    def test_detect_concurrent_generation_decision_accepts_normal_mode(self) -> None:
        self.assertIs(detect_concurrent_generation_decision("普通模式"), False)
        self.assertIs(detect_concurrent_generation_decision("一条一条生成，更稳妥"), False)


if __name__ == "__main__":
    unittest.main()
