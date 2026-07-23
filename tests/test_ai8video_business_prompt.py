from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai8video.generation import business_prompt
from ai8video.core.config import AI8VideoConfig
from ai8video.core.models import VideoPrompt, ParsedRequest, QuickVideoJob, ArchivedAsset
from ai8video.generation.pipeline import AI8VideoPipeline


class _FakeClient:
    def __init__(self) -> None:
        self.guard = SimpleNamespace(forced_duration_seconds=0, assert_can_create_count=lambda _count: None)
        self.created_text = ""
        self.created_texts = []

    def create_job(self, *, text, video_index, first_frame, duration_seconds, ratio, resolution, preset):
        self.created_text = text
        self.created_texts.append(text)
        return QuickVideoJob(
            video_index=video_index,
            job_id=f"job-{video_index}",
            status="succeeded",
            prompt=text,
            video_url=f"https://example.test/video-{video_index}.mp4",
        )

    def poll_job(self, job: QuickVideoJob) -> QuickVideoJob:
        return job


class _FakeArchiver:
    def archive(self, request, video, job, outcome) -> ArchivedAsset:
        return ArchivedAsset(
            video_index=video.index,
            job_id=job.job_id,
            backend="local",
            status="archived",
            archive_key=f"video/{job.job_id}.mp4",
        )


class _FakeAssetStore:
    def __init__(self) -> None:
        self.records = []

    def append(self, request, video, job, outcome, first_frame, archive):
        record = {
            "videoIndex": video.index,
            "prompt": video.prompt,
            "jobPrompt": job.prompt,
        }
        self.records.append(record)
        return record


class AI8VideoBusinessPromptTest(unittest.TestCase):
    def test_fallback_only_removes_internal_metadata_without_local_prompt_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            prompt_path = Path(tempdir) / "ai8video_business_model_prompt.txt"
            with patch.object(business_prompt, "BUSINESS_PROMPT_PATH", prompt_path):
                business_prompt.write_business_prompt("所有AI8VIDEO自动过滤，不允许出现任何logo。系统规则：需要模型理解语义约束。")

                result = business_prompt.finalize_prompt_text(
                    "候选提示词包含禁用内容甲，AI8videoAI8VIDEO，画面出现logo。\n"
                    "信息保真：这是内部补丁说明，不应进入最终提示词。"
                )

        self.assertIn("禁用内容甲", result)
        self.assertNotIn("用户可编辑业务模型系统提示词", result)
        self.assertIn("AI8VIDEO", result)
        self.assertIn("logo", result.lower())
        self.assertNotIn("信息保真", result)

    def test_model_rewrite_is_primary_for_final_prompt_quality(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            return '{"final_prompt":"模型改写后的最终提示词：镜头、动作、情绪和口播完整。","notes":"已理解系统提示词"}'

        with tempfile.TemporaryDirectory() as tempdir:
            prompt_path = Path(tempdir) / "ai8video_business_model_prompt.txt"
            with patch.object(business_prompt, "BUSINESS_PROMPT_PATH", prompt_path):
                business_prompt.write_business_prompt("系统规则：按语义理解候选提示词。")
                result = business_prompt.finalize_video_prompt_with_ai(
                    "候选提示词：请交给模型判断。",
                    llm=fake_llm,
                )

        self.assertEqual(result, "模型改写后的最终提示词：镜头、动作、情绪和口播完整。")
        self.assertEqual(len(model_inputs), 2)
        self.assertIn("用户可编辑业务模型系统提示词", model_inputs[0])
        self.assertIn("不要机械套词表", model_inputs[0])
        self.assertIn("输出前自检一次", model_inputs[0])
        self.assertIn("最终出站审校模型", model_inputs[1])

    def test_model_rewrite_carries_task_constraints_into_rewrite_and_validation(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            if "最终出站审校模型" in prompt:
                return '{"passes": true, "final_prompt": "沙滩场景最终提示词。", "notes": "已审校"}'
            return '{"final_prompt":"沙滩场景最终提示词。","notes":"已理解任务约束"}'

        result = business_prompt.finalize_video_prompt_with_ai(
            "候选提示词：办公室开场。",
            llm=fake_llm,
            task_constraints="参考图设定：背景必须和原参考图完全不同；补充要求：必须泳装和沙滩。",
        )

        self.assertEqual(result, "沙滩场景最终提示词。")
        self.assertEqual(len(model_inputs), 2)
        self.assertIn("当前任务补充约束", model_inputs[0])
        self.assertIn("本轮用户原文、核心主题、明确风格和业务模型系统提示词都是用户输入", model_inputs[0])
        self.assertIn("必须泳装和沙滩", model_inputs[0])
        self.assertIn("任何一方都不能换掉另一方", model_inputs[0])
        self.assertIn("必须泳装和沙滩", model_inputs[1])

    def test_explicit_core_topic_guard_rejects_cross_task_template_replacement(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            return (
                '{"passes": true, "final_prompt": "美女介绍翻译软件。", "notes": "错误换题"}'
                if "最终出站审校模型" in prompt
                else '{"final_prompt": "美女介绍翻译软件。", "notes": "错误换题"}'
            )

        source = "生成一条小动物视频。\n核心主题 / 关键词：小动物。"
        with tempfile.TemporaryDirectory() as tempdir:
            prompt_path = Path(tempdir) / "business-prompt.txt"
            with patch.object(business_prompt, "BUSINESS_PROMPT_PATH", prompt_path):
                business_prompt.write_business_prompt("第一人称自拍，美女介绍翻译软件。")
                result = business_prompt.finalize_video_prompt_with_ai(
                    source,
                    llm=fake_llm,
                    keyword_guidance={"explicit_core_keywords": ["小动物"]},
                )

        self.assertEqual(result, source)
        self.assertEqual(len(model_inputs), 2)
        self.assertIn("本轮原始候选", model_inputs[1])
        self.assertIn("小动物", model_inputs[1])

    def test_current_topic_and_saved_business_prompt_can_coexist(self) -> None:
        merged = "自拍镜头中，美女抱着小动物演示翻译软件。"

        def fake_llm(prompt: str) -> str:
            if "最终出站审校模型" in prompt:
                return f'{{"passes": true, "final_prompt": "{merged}", "notes": "约束共存"}}'
            return f'{{"final_prompt": "{merged}", "notes": "约束共存"}}'

        with tempfile.TemporaryDirectory() as tempdir:
            prompt_path = Path(tempdir) / "business-prompt.txt"
            with patch.object(business_prompt, "BUSINESS_PROMPT_PATH", prompt_path):
                business_prompt.write_business_prompt("第一人称自拍，美女介绍翻译软件。")
                result = business_prompt.finalize_video_prompt_with_ai(
                    "生成一条小动物视频。",
                    llm=fake_llm,
                    keyword_guidance={"explicit_core_keywords": ["小动物"]},
                )

        self.assertEqual(result, merged)

    def test_explicit_core_topic_guard_requires_every_declared_keyword(self) -> None:
        source = "小动物在草地上玩耍，并展示翻译软件。"
        candidate = "小动物在草地上玩耍。"

        guarded, preserved = business_prompt.enforce_explicit_core_keywords(
            source,
            candidate,
            keyword_guidance={"explicit_core_keywords": ["小动物", "翻译软件"]},
        )

        self.assertFalse(preserved)
        self.assertEqual(guarded, source)

    def test_local_safety_guard_requires_explicit_toolbar_override(self) -> None:
        candidate = "美女身材特写。结尾：邀请好友，立享返佣！"

        unchanged = business_prompt._apply_custom_safety_guard(
            candidate,
            "本次用户自定义输入中的安全过滤：删除外貌化和营销内容。",
        )
        overridden = business_prompt._apply_custom_safety_guard(
            candidate,
            "本次明确覆盖工具栏用户设置。安全过滤：删除外貌化和营销内容。",
        )

        self.assertEqual(unchanged, candidate)
        self.assertNotIn("美女", overridden)
        self.assertNotIn("返佣", overridden)

    def test_business_prompt_generation_policy_only_filters_model_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            prompt_path = Path(tempdir) / "business-prompt.txt"
            with patch.object(business_prompt, "BUSINESS_PROMPT_PATH", prompt_path):
                business_prompt.write_business_prompt("不允许出现任何文字，不允许出现任何logo。")
                policy = business_prompt.business_prompt_generation_policy()
                overridden = business_prompt.business_prompt_generation_policy(
                    task_constraints="本次明确覆盖工具栏用户设置",
                )

        self.assertEqual(
            policy,
            {"filteredTerms": []},
        )
        self.assertEqual(
            overridden,
            {"filteredTerms": []},
        )

    def test_model_output_guard_only_removes_internal_metadata(self) -> None:
        def fake_llm(_prompt: str) -> str:
            return """
            {
              "final_prompt": "画面硬性约束：旧补丁行不应保留。\\n最终提示词正文保留模型自己的语义选择。\\n品牌保真：内部说明",
              "notes": "模型已处理"
            }
            """

        result = business_prompt.finalize_video_prompt_with_ai("候选提示词", llm=fake_llm)

        self.assertEqual(result, "最终提示词正文保留模型自己的语义选择。")
        self.assertNotIn("画面硬性约束", result)
        self.assertNotIn("品牌保真", result)

    def test_model_rewrite_repairs_unescaped_quotes_inside_prompt_json(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            if "最终出站审校模型" in prompt:
                return '{"passes": true, "final_prompt": "人物说："来了"，镜头推进。", "notes": "已审校"}'
            return '{"final_prompt": "人物说："来了"，镜头推进。", "notes": "模型输出含未转义引号"}'

        result = business_prompt.finalize_video_prompt_with_ai("候选提示词", llm=fake_llm)

        self.assertEqual(result, '人物说："来了"，镜头推进。')
        self.assertEqual(len(model_inputs), 2)

    def test_batch_rewrite_repairs_missing_comma_between_json_fields(self) -> None:
        def fake_llm(_prompt: str) -> str:
            return """
            [
              {
                "index": 1,
                "title": "消息到来"
                "final_prompt": "人物看向镜头，说：消息来了。",
                "notes": "模型漏了逗号"
              },
              {
                "index": 2,
                "title": "继续推进",
                "final_prompt": "人物转身走向发射场。",
                "notes": "正常"
              }
            ]
            """

        videos = [
            VideoPrompt(index=1, title="旧标题一", prompt="候选一"),
            VideoPrompt(index=2, title="旧标题二", prompt="候选二"),
        ]

        finalized = business_prompt.finalize_video_prompts(videos, llm=fake_llm)

        self.assertEqual([video.title for video in finalized], ["消息到来", "继续推进"])
        self.assertEqual(finalized[0].prompt, "人物看向镜头，说：消息来了。")
        self.assertEqual(finalized[1].prompt, "人物转身走向发射场。")

    def test_model_validation_repairs_business_prompt_violations_semantically(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            if "最终出站审校模型" in prompt:
                return """
                {
                  "passes": false,
                  "final_prompt": "镜头一：人物口播AI8video 。台词/口播：新的发布节奏已经开始。",
                  "notes": "系统提示词禁止活动词、logo 和指定倒计时表述，已语义改写"
                }
                """
            return """
            {
              "final_prompt": "镜头一：人物口播AI8videoAI8VIDEO。画面角落出现logo。台词/口播：不要再说倒计时多少天，也不要提618活动。",
              "notes": "第一轮模型漏掉了系统提示词"
            }
            """

        with tempfile.TemporaryDirectory() as tempdir:
            prompt_path = Path(tempdir) / "ai8video_business_model_prompt.txt"
            with patch.object(business_prompt, "BUSINESS_PROMPT_PATH", prompt_path):
                business_prompt.write_business_prompt(
                    "所有AI8VIDEO自动过滤，不允许出现任何logo，"
                    "台词不要出现“倒计时多少天”这种表述，禁止出现 618 相关内容"
                )
                result = business_prompt.finalize_video_prompt_with_ai("候选提示词", llm=fake_llm)

        self.assertEqual(len(model_inputs), 2)
        self.assertIn("最终出站审校模型", model_inputs[1])
        self.assertNotIn("AI8VIDEO", result)
        self.assertNotIn("logo", result.lower())
        self.assertNotIn("倒计时多少天", result)
        self.assertNotIn("618", result)
        self.assertIn("AI8video", result)

    def test_title_guard_applies_shared_business_prompt_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            prompt_path = Path(tempdir) / "ai8video_business_model_prompt.txt"
            with patch.object(business_prompt, "BUSINESS_PROMPT_PATH", prompt_path):
                business_prompt.write_business_prompt("所有SAMPLEBRAND自动过滤，不允许出现任何logo。")
                title = business_prompt.finalize_title("AI8video SAMPLEBRAND：未来10年的logo连接器")

        self.assertEqual(title, "AI8video：未来10年的logo连接器")

    def test_pipeline_sends_model_rewritten_prompt_to_video_client(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            return '{"final_prompt":"模型最终提示词：第一条。","notes":"已处理"}'

        with tempfile.TemporaryDirectory() as tempdir:
            prompt_path = Path(tempdir) / "ai8video_business_model_prompt.txt"
            with patch.object(business_prompt, "BUSINESS_PROMPT_PATH", prompt_path):
                business_prompt.write_business_prompt("系统规则：最终提示词必须由模型语义改写。")
                pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
                pipeline.config = AI8VideoConfig(dry_run=False)
                pipeline.llm = fake_llm
                pipeline.client = _FakeClient()
                pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
                pipeline.archiver = _FakeArchiver()
                pipeline.asset_store = _FakeAssetStore()

                request = ParsedRequest(raw_text="测试", mode="single_video")
                videos = [VideoPrompt(index=1, title="测试", prompt="候选提示词：第一条。")]
                result = pipeline._run_videos(request, videos, progress_session_id=None)

        self.assertEqual(pipeline.client.created_text, "模型最终提示词：第一条。")
        self.assertEqual(result.videos[0].prompt, pipeline.client.created_text)
        self.assertEqual(pipeline.asset_store.records[0]["prompt"], pipeline.client.created_text)
        self.assertEqual(len(model_inputs), 3)
        self.assertIn("最终出站审校模型", model_inputs[1])

    def test_pipeline_persists_reference_constraints_into_video_prompt_chain(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            if "最终出站审校模型" in prompt:
                return '{"passes": true, "final_prompt": "模型最终提示词：沙滩版本。", "notes": "已审校"}'
            return '{"final_prompt":"模型最终提示词：沙滩版本。","notes":"已处理"}'

        with tempfile.TemporaryDirectory() as tempdir:
            prompt_path = Path(tempdir) / "ai8video_business_model_prompt.txt"
            with patch.object(business_prompt, "BUSINESS_PROMPT_PATH", prompt_path):
                business_prompt.write_business_prompt("系统规则：最终提示词必须由模型语义改写。")
                pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
                pipeline.config = AI8VideoConfig(dry_run=False)
                pipeline.llm = fake_llm
                pipeline.client = _FakeClient()
                pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
                pipeline.archiver = _FakeArchiver()
                pipeline.asset_store = _FakeAssetStore()

                request = ParsedRequest(
                    raw_text="测试",
                    mode="single_video",
                    reference_image="/tmp/reference.png",
                    reference_image_custom_prompt="必须泳装和沙滩",
                    reference_image_transform_options={
                        "autoChangeBackground": True,
                        "autoChangePose": True,
                        "autoChangeClothes": False,
                    },
                )
                videos = [VideoPrompt(index=1, title="测试", prompt="候选提示词：第一条。")]
                pipeline._run_videos(request, videos, progress_session_id=None)

        self.assertEqual(len(model_inputs), 3)
        self.assertIn("当前任务补充约束", model_inputs[0])
        self.assertIn("背景必须和原参考图完全不同", model_inputs[0])
        self.assertIn("人物姿势必须和原参考图完全不同", model_inputs[0])
        self.assertIn("必须泳装和沙滩", model_inputs[0])
        self.assertIn("必须泳装和沙滩", model_inputs[1])
        self.assertIn("最终输出后审核模型", model_inputs[2])

    def test_pipeline_uses_one_batch_rewrite_for_multiple_final_prompts(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            if "最终出站审校模型" in prompt:
                return '{"passes": true, "final_prompt": "模型最终提示词：第一条。", "notes": "已审校"}' \
                    if "第一条" in prompt else \
                    '{"passes": true, "final_prompt": "模型最终提示词：第二条。", "notes": "已审校"}'
            self.assertIn("候选提示词数组", prompt)
            self.assertIn("保留整批差异", prompt)
            self.assertIn("source_summary", prompt)
            self.assertIn("keyword_guidance", prompt)
            return """
            [
              {
                "index": 1,
                "title": "第一条",
                "final_prompt": "模型最终提示词：第一条。",
                "notes": "已处理第一条"
              },
              {
                "index": 2,
                "title": "第二条",
                "final_prompt": "模型最终提示词：第二条。",
                "notes": "已处理第二条"
              }
            ]
            """

        with tempfile.TemporaryDirectory() as tempdir:
            prompt_path = Path(tempdir) / "ai8video_business_model_prompt.txt"
            with patch.object(business_prompt, "BUSINESS_PROMPT_PATH", prompt_path):
                business_prompt.write_business_prompt("系统规则：批量理解，分别改写。")
                pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
                pipeline.config = AI8VideoConfig(dry_run=False)
                pipeline.llm = fake_llm
                pipeline.client = _FakeClient()
                pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
                pipeline.archiver = _FakeArchiver()
                pipeline.asset_store = _FakeAssetStore()

                request = ParsedRequest(raw_text="测试", mode="batch_videos", concurrent_generation=True)
                videos = [
                    VideoPrompt(
                        index=1,
                        title="第一条",
                        prompt="候选提示词：第一条。",
                        source_summary="来自脚本1",
                        keyword_guidance={"global_keywords": ["AI8video"], "preserved_keywords": ["AI8video"]},
                    ),
                    VideoPrompt(
                        index=2,
                        title="第二条",
                        prompt="候选提示词：第二条。",
                        source_summary="来自脚本2",
                        keyword_guidance={"global_keywords": ["AI8VIDEO"], "preserved_keywords": ["AI8VIDEO"]},
                    ),
                ]
                result = pipeline._run_videos(request, videos, progress_session_id=None)

        self.assertEqual(len(model_inputs), 2)
        self.assertIn("整批最终提示词质检与改写模型", model_inputs[0])
        self.assertIn("最终输出后审核模型", model_inputs[1])
        self.assertEqual(pipeline.client.created_texts, ["模型最终提示词：第一条。", "模型最终提示词：第二条。"])
        self.assertEqual([item.prompt for item in result.videos], pipeline.client.created_texts)
        self.assertEqual(result.videos[0].keyword_guidance["final_rewrite_notes"], "已处理第一条")

    def test_batch_rewrite_prompt_carries_ai_keyword_guidance(self) -> None:
        videos = [
            VideoPrompt(
                index=1,
                title="品牌发布",
                prompt="候选提示词：老板口播品牌发布。",
                source_summary="来自脚本12：品牌正式发布",
                keyword_guidance={
                    "global": {
                        "global_keywords": ["AI8video", "AI8VIDEO", "6月18日"],
                        "must_preserve_facts": ["AI8videoAI8VIDEO 6月18日发布"],
                    },
                    "preserved_keywords": ["AI8video", "AI8VIDEO"],
                    "omitted_keywords_reason": "",
                },
            )
        ]

        prompt = business_prompt.build_business_prompt_batch_rewrite_prompt(videos)

        self.assertIn("上游 AI 文本理解", prompt)
        self.assertIn("source_summary", prompt)
        self.assertIn("keyword_guidance", prompt)
        self.assertIn("来自脚本12", prompt)
        self.assertIn("AI8videoAI8VIDEO 6月18日发布", prompt)

    def test_batch_model_failure_falls_back_without_local_prompt_parsing(self) -> None:
        def broken_llm(_prompt: str) -> str:
            return "not json"

        with tempfile.TemporaryDirectory() as tempdir:
            prompt_path = Path(tempdir) / "ai8video_business_model_prompt.txt"
            with patch.object(business_prompt, "BUSINESS_PROMPT_PATH", prompt_path):
                business_prompt.write_business_prompt("所有AI8VIDEO自动过滤。系统规则：需要模型处理禁用内容。")
                videos = [
                    VideoPrompt(index=1, title="第一条", prompt="候选提示词包含禁用内容甲，AI8videoAI8VIDEO。"),
                    VideoPrompt(index=2, title="第二条", prompt="候选提示词包含禁用内容乙。"),
                ]
                result = business_prompt.finalize_video_prompts(videos, llm=broken_llm)

        self.assertIn("禁用内容甲", result[0].prompt)
        self.assertIn("禁用内容乙", result[1].prompt)
        self.assertIn("AI8VIDEO", result[0].prompt)
        self.assertNotIn("用户可编辑业务模型系统提示词", result[0].prompt)
        self.assertNotIn("用户可编辑业务模型系统提示词", result[1].prompt)


if __name__ == "__main__":
    unittest.main()
