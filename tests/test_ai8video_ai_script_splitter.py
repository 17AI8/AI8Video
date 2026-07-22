from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.generation import business_prompt
from ai8video.generation.ai_script_splitter import (
    build_rewrite_prompt,
    build_split_prompt,
    extract_script_keywords_with_ai,
    rewrite_episode_with_ai,
    single_prompt_to_episode,
    split_script_with_ai,
)
from ai8video.core.models import EpisodePrompt


class AI8VideoAiScriptSplitterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.prompt_path = Path(self.tempdir.name) / "ai8video_business_model_prompt.txt"
        self.prompt_patch = patch.object(business_prompt, "BUSINESS_PROMPT_PATH", self.prompt_path)
        self.prompt_patch.start()

    def tearDown(self) -> None:
        self.prompt_patch.stop()
        self.tempdir.cleanup()

    def test_split_prompt_requires_episode_count_planning_before_splitting(self) -> None:
        prompt = build_split_prompt("老板在会议室讲封号风险", 4, "商务真实感")

        self.assertIn("先根据目标集数 4 规划整组短视频的叙事节奏", prompt)
        self.assertIn("2 集，应形成“强痛点/冲突开场 -> 解决方案/结果收束”", prompt)
        self.assertIn("3 集，应形成“痛点引入 -> 能力展开 -> 结果转化”", prompt)
        self.assertIn("4 集，应形成“痛点引入 -> 第一卖点 -> 第二卖点/升级问题 -> 结果收束”", prompt)
        self.assertIn("不能丢掉原剧本的核心卖点、人物关系、场景信息和情绪递进", prompt)

    def test_split_prompt_requires_whole_script_coverage(self) -> None:
        prompt = build_split_prompt("脚本1 开头\n脚本10 后段", 10, "商务真实感")

        self.assertIn("全篇覆盖地图", prompt)
        self.assertIn("开头、中段、后段和结尾", prompt)
        self.assertIn("后半部分集数要优先使用尚未覆盖的中后段内容", prompt)
        self.assertIn("不能把 1-5 集换词后重复成 6-10 集", prompt)
        self.assertIn("source_summary 必须写清这一集来自原文哪个脚本编号", prompt)

    def test_split_prompt_prioritizes_collected_core_keywords(self) -> None:
        prompt = build_split_prompt(
            "老板在会议室讲封号风险",
            2,
            "营销号风格",
            "6月18日全球发布倒计时、私域资产",
        )

        self.assertIn("本轮必须优先围绕这些核心主题 / 关键词规划", prompt)
        self.assertIn("6月18日全球发布倒计时、私域资产", prompt)

    def test_keyword_extraction_uses_llm_structured_result(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            return """
            {
              "global_keywords": ["AI8video", "AI8VIDEO", "6月18日"],
              "must_preserve_facts": ["AI8videoAI8VIDEO全球发布"],
              "episode_keyword_guidance": [
                {"index": 1, "source_hint": "脚本1", "keywords": ["AI8video"], "facts": ["全球发布"], "usage_note": "放入口播"}
              ],
              "usage_policy": "根据系统提示词决定放入口播或画面"
            }
            """

        guidance = extract_script_keywords_with_ai(
            "AI8video AI8VIDEO 6月18日 全球发布",
            2,
            "商务真实",
            llm=fake_llm,
        )

        self.assertEqual(len(model_inputs), 1)
        self.assertIn("不允许用本地词频、正则或固定词表", model_inputs[0])
        self.assertEqual(guidance["global_keywords"], ["AI8video", "AI8VIDEO", "6月18日"])
        self.assertEqual(guidance["must_preserve_facts"], ["AI8videoAI8VIDEO全球发布"])
        self.assertEqual(guidance["episode_keyword_guidance"][0]["keywords"], ["AI8video"])

    def test_split_prompt_receives_model_keyword_guidance(self) -> None:
        guidance = {
            "global_keywords": ["AI8video", "AI8VIDEO", "6月18日"],
            "must_preserve_facts": ["AI8videoAI8VIDEO全球发布"],
            "episode_keyword_guidance": [
                {
                    "index": 2,
                    "source_hint": "脚本20",
                    "keywords": ["私域承接"],
                    "facts": ["客户关系沉淀"],
                    "usage_note": "放在第二集口播中自然承接",
                }
            ],
            "usage_policy": "按叙事功能自然使用，不硬塞。",
        }

        prompt = build_split_prompt("长剧本正文", 2, keyword_guidance=guidance)

        self.assertIn("文本模型提取的关键词指导", prompt)
        self.assertIn('"global_keywords"', prompt)
        self.assertIn("AI8video", prompt)
        self.assertIn("preserved_keywords", prompt)
        self.assertIn("omitted_keywords_reason", prompt)

    def test_split_prompt_receives_task_constraints(self) -> None:
        prompt = build_split_prompt(
            "老板在办公室讲私域痛点",
            2,
            task_constraints="参考图设定：背景必须和原参考图完全不同；补充要求：必须泳装和沙滩。",
        )

        self.assertIn("当前任务附加高优先级约束", prompt)
        self.assertIn("必须泳装和沙滩", prompt)
        self.assertIn("如果原素材里的默认办公室", prompt)

    def test_splitter_runs_keyword_model_before_split_model(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            if "剧本关键词理解模型" in prompt:
                return """
                {
                  "global_keywords": ["AI8video", "AI8VIDEO"],
                  "must_preserve_facts": ["6月18日发布"],
                  "episode_keyword_guidance": [],
                  "usage_policy": "尽可能放入口播。"
                }
                """
            return """
            [
              {
                "index": 1,
                "title": "发布预热",
                "prompt": "0-5 秒老板开场。台词/口播：AI8video AI8VIDEO 即将发布。5-10 秒收束。",
                "source_summary": "脚本1",
                "preserved_keywords": ["AI8video", "AI8VIDEO"],
                "omitted_keywords_reason": ""
              }
            ]
            """

        episodes = split_script_with_ai("AI8video AI8VIDEO 6月18日发布", 1, llm=fake_llm)

        self.assertEqual(len(model_inputs), 2)
        self.assertIn("剧本关键词理解模型", model_inputs[0])
        self.assertIn("文本模型提取的关键词指导", model_inputs[1])
        self.assertEqual(episodes[0].keyword_guidance["preserved_keywords"], ["AI8video", "AI8VIDEO"])
        self.assertIn("global", episodes[0].keyword_guidance)

    def test_splitter_repairs_invalid_json_array_once(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            if "剧本关键词理解模型" in prompt:
                return """
                {
                  "global_keywords": ["消息"],
                  "must_preserve_facts": ["两个消息"],
                  "episode_keyword_guidance": [],
                  "usage_policy": "用画面表达。"
                }
                """
            if "JSON 格式修复器" in prompt:
                return """
                [
                  {
                    "index": 1,
                    "title": "消息到来",
                    "prompt": "0-5 秒人物看向桌面。台词/口播：他说：\\"来了\\"。5-10 秒收束。",
                    "source_summary": "原文",
                    "preserved_keywords": ["消息"],
                    "omitted_keywords_reason": ""
                  }
                ]
                """
            return """
            [
              {
                "index": 1,
                "title": "消息到来",
                "prompt": "0-5 秒人物看向桌面。台词/口播：他说："来了"。5-10 秒收束。",
                "source_summary": "原文",
                "preserved_keywords": ["消息"],
                "omitted_keywords_reason": ""
              }
            ]
            """

        episodes = split_script_with_ai("2个，消息", 1, llm=fake_llm)

        self.assertEqual(len(model_inputs), 3)
        self.assertIn("JSON 格式修复器", model_inputs[2])
        self.assertEqual(episodes[0].title, "消息到来")
        self.assertIn('他说："来了"', episodes[0].prompt)

    def test_split_prompt_requires_dialogue_and_shootable_segments(self) -> None:
        prompt = build_split_prompt("老板说客户丢了才知道私域沉淀重要", 2, "老板真实口播")

        self.assertIn("每集必须先规划可直接口播/对白的中文台词", prompt)
        self.assertIn("每条提示词必须包含“台词/口播：...”", prompt)
        self.assertIn("0-5 秒、5-10 秒", prompt)
        self.assertIn("语气状态", prompt)
        self.assertIn("不要添加用户原文没有要求的声线、性别或身份设定", prompt)
        self.assertIn("不要用固定词表机械判断", prompt)
        self.assertIn("不要为某个禁用项临时发明本地替换规则", prompt)
        self.assertIn("可见视觉内容和口播内容不能混淆", prompt)

    def test_split_prompt_can_plan_against_final_merged_duration(self) -> None:
        prompt = build_split_prompt(
            "按四个镜头讲一个完整热点事件",
            1,
            final_duration_seconds=20,
        )

        self.assertIn("最终成片约 20 秒", prompt)
        self.assertIn("不要退回默认 0-5 秒、5-10 秒结构", prompt)
        self.assertIn("不要指望后置 TTS 再压缩或重写正文", prompt)

    def test_rewrite_prompt_keeps_dialogue_requirement(self) -> None:
        episode = EpisodePrompt(index=1, title="封号危机", prompt="老板在办公室焦虑", source_summary="")
        prompt = build_rewrite_prompt(episode, "台词更狠一点", "商务真实")

        self.assertIn("必须保留或补齐可直接口播/对白的中文台词", prompt)
        self.assertIn("台词/口播：", prompt)
        self.assertIn("0-5 秒、5-10 秒", prompt)
        self.assertIn("可见视觉内容和人物台词/口播内容不能混淆", prompt)

    def test_rewrite_prompt_receives_task_constraints(self) -> None:
        episode = EpisodePrompt(index=1, title="封号危机", prompt="老板在办公室焦虑", source_summary="")
        prompt = build_rewrite_prompt(
            episode,
            "台词更狠一点",
            "商务真实",
            task_constraints="参考图设定：背景必须和原参考图完全不同；补充要求：必须泳装和沙滩。",
        )

        self.assertIn("当前任务附加高优先级约束", prompt)
        self.assertIn("必须泳装和沙滩", prompt)
        self.assertIn("持续生效", prompt)

    def test_split_prompt_allows_user_requested_poster_text_style(self) -> None:
        prompt = build_split_prompt("老板在会议室讲封号风险", 2, "老板真实口播、大量大字报式文字")

        self.assertIn("先理解用户原文、风格要求和用户可编辑业务模型系统提示词里的视觉要求", prompt)
        self.assertIn("不要用固定词表机械判断", prompt)
        self.assertIn("老板真实口播、大量大字报式文字", prompt)
        self.assertIn("如果用户要求画面呈现某类视觉表达", prompt)

    def test_single_prompt_preserves_user_requested_poster_text_style(self) -> None:
        episode = single_prompt_to_episode("视频中大量“大字报式文字”，讲AI8video 私域承接", None)[0]

        self.assertIn("视频中大量“大字报式文字”", episode.prompt)
        self.assertIn("请先理解用户原文、风格要求和用户可编辑业务模型系统提示词里的视觉要求", episode.prompt)
        self.assertIn("不要用本地固定词表替用户判断内容", episode.prompt)

    def test_visual_text_rule_is_intent_based_not_keyword_only(self) -> None:
        prompt = build_split_prompt("老板在会议室讲封号风险", 2, "屏幕大标题冲屏，关键词要加粗高亮")

        self.assertIn("屏幕大标题冲屏，关键词要加粗高亮", prompt)
        self.assertIn("不要用固定词表机械判断", prompt)

    def test_splitter_does_not_locally_normalize_brand_or_append_old_fidelity_notes(self) -> None:
        def fake_llm(_prompt: str) -> str:
            return """
            [
              {"index": 1, "title": "飞讯发布预热", "prompt": "台词/口播：飞讯要来了。0-5 秒老板开场，5-10 秒收束。", "source_summary": ""},
              {"index": 2, "title": "私域承接", "prompt": "台词/口播：客户要沉淀到自己的池子。0-5 秒痛点，5-10 秒方案。", "source_summary": ""}
            ]
            """

        episodes = split_script_with_ai(
            "@AI8video 脚本.docx @612.png AI8video 618 倒计时 5 天",
            2,
            llm=fake_llm,
        )

        self.assertIn("飞讯发布预热", episodes[0].title)
        self.assertIn("台词/口播：飞讯", episodes[0].prompt)
        self.assertNotIn("信息保真", episodes[0].prompt)
        self.assertNotIn("口播保真", episodes[0].prompt)
        self.assertNotIn("618 倒计时 5 天", episodes[1].prompt)

    def test_splitter_does_not_append_protection_notes_to_titles(self) -> None:
        def fake_llm(_prompt: str) -> str:
            return """
            [
              {"index": 1, "title": "沟通噩梦", "prompt": "台词/口播：客户资料不能再散落。0-5 秒痛点，5-10 秒方案。", "source_summary": ""},
              {"index": 2, "title": "私域承接", "prompt": "台词/口播：AI8video 承接客户关系。0-5 秒痛点，5-10 秒方案。", "source_summary": ""}
            ]
            """

        episodes = split_script_with_ai(
            "AI8video AI8VIDEO 618 倒计时 5 天",
            2,
            llm=fake_llm,
        )

        self.assertEqual(episodes[0].title, "沟通噩梦")
        self.assertNotIn("品牌保真", episodes[0].title)
        self.assertNotIn("信息保真", episodes[0].title)
        self.assertNotIn("品牌保真", episodes[0].prompt)
        self.assertNotIn("信息保真", episodes[0].prompt)

    def test_splitter_does_not_parse_business_prompt_as_local_forbidden_terms(self) -> None:
        business_prompt.write_business_prompt("系统规则：由核心文本模型理解并执行禁用要求。")

        def fake_llm(_prompt: str) -> str:
            return """
            [
              {"index": 1, "title": "发布预热", "prompt": "台词/口播：全球发布倒计时。0-5 秒老板开场，5-10 秒收束。", "source_summary": ""},
              {"index": 2, "title": "私域承接", "prompt": "台词/口播：客户要沉淀到自己的池子。0-5 秒痛点，5-10 秒方案。", "source_summary": ""}
            ]
            """

        episodes = split_script_with_ai(
            "AI8video AI8VIDEO 618 倒计时 5 天",
            2,
            llm=fake_llm,
        )

        self.assertIn("全球发布倒计时", episodes[0].prompt)
        self.assertNotIn("最终硬性约束", episodes[0].prompt)
        self.assertNotIn("用户可编辑业务模型系统提示词", episodes[0].prompt)

    def test_split_prompt_keeps_dates_as_spoken_by_default_not_visible_text(self) -> None:
        prompt = build_split_prompt(
            "老板口播 6月18日全球发布倒计时",
            2,
            None,
            "6月18日全球发布倒计时",
        )

        self.assertIn("品牌、专名、日期和核心信息必须服从", prompt)
        self.assertIn("只能依据本轮用户输入、参考剧本和系统提示词", prompt)

    def test_single_prompt_preserves_brand_and_ai8video_terms(self) -> None:
        episode = single_prompt_to_episode("AI8video AI8VIDEO 618 倒计时 5 天，老板口播私域承接", None)[0]

        self.assertIn("AI8video", episode.prompt)
        self.assertIn("AI8VIDEO", episode.prompt)
        self.assertIn("618 倒计时 5 天", episode.prompt)

    def test_rewrite_does_not_locally_normalize_brand_or_restore_countdown(self) -> None:
        episode = EpisodePrompt(
            index=1,
            title="AI8video 倒计时",
            prompt="台词/口播：AI8video 618 倒计时 5 天，私域承接不能再等。",
            source_summary="",
        )

        def fake_llm(_prompt: str) -> str:
            return '{"title":"飞讯倒计时","prompt":"台词/口播：飞讯私域承接发布预热。0-5 秒痛点，5-10 秒方案。","source_summary":""}'

        rewritten = rewrite_episode_with_ai(episode, "语气更狠一点", llm=fake_llm)

        self.assertIn("飞讯", rewritten.title)
        self.assertIn("飞讯", rewritten.prompt)
        self.assertNotIn("618 倒计时 5 天", rewritten.prompt)
        self.assertNotIn("信息保真", rewritten.prompt)

    def test_split_prompt_tells_model_not_to_invent_unrequested_identity_details(self) -> None:
        prompt = build_split_prompt("一个人顶一个团队", 1)

        self.assertIn("不要添加用户原文没有要求的声线、性别或身份设定", prompt)
        self.assertIn("不要用固定词表机械判断", prompt)
        self.assertIn("必须理解限制的作用域", prompt)


if __name__ == "__main__":
    unittest.main()
