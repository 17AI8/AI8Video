from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

from ai8video.knowledge import default_script_reference
from ai8video.assets import user_materials
from ai8video.application.conversation_controller import AI8VideoConversationController
from ai8video.core.models import ConversationState, VideoPrompt, ParsedRequest, PipelineResult, QuickVideoJob


class AI8VideoDefaultScriptReferenceTest(unittest.TestCase):
    def test_temporary_script_knowledge_is_bounded_and_can_add_default_reference(self) -> None:
        payload = {
            "title": "猜剧本临时知识库 · TEMU 教程",
            "summary": "根据宫格、台词和剧本骨架生成。",
            "tags": ["临时知识库", "猜剧本"],
            "leaves": [
                {
                    "path": ["开场", "冲突"],
                    "heading": "开场 / 冲突",
                    "content": "前三秒先展示新手最容易踩的坑。" + ("细节" * 5000),
                },
                {"heading": "收束", "content": "最后给出可执行步骤。"},
            ],
        }

        enriched = default_script_reference.apply_temporary_script_knowledge(
            "生成 2 条 10 秒视频",
            payload,
            include_default_reference=True,
        )

        self.assertIn("[临时知识库｜猜剧本临时知识库 · TEMU 教程]", enriched)
        self.assertIn("[叶节点 1｜开场 / 冲突]", enriched)
        self.assertIn("发送后自动解绑，不会写入正式知识库", enriched)
        self.assertIn("同时使用当前已选知识库参考", enriched)
        self.assertLessEqual(len(enriched), default_script_reference.TEMPORARY_SCRIPT_REFERENCE_MAX_CHARS + 100)
        control_text, context = default_script_reference.split_temporary_script_knowledge(enriched)
        self.assertIn("生成 2 条 10 秒视频", control_text)
        self.assertIn("同时使用当前已选知识库参考", control_text)
        self.assertNotIn("前三秒先展示", control_text)
        self.assertIn("前三秒先展示", context)

    def test_temporary_script_knowledge_requires_leaf_content(self) -> None:
        with self.assertRaisesRegex(ValueError, "leaves is required"):
            default_script_reference.apply_temporary_script_knowledge(
                "生成视频",
                {"title": "空临时库", "leaves": []},
            )

    def test_docx_preview_falls_back_to_word_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            docx_path = Path(tempdir) / "表格剧本.docx"
            xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>第一段：老板讲私域承接。</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格台词：客户资料不能只留平台。</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
  </w:body>
</w:document>
"""
            with zipfile.ZipFile(docx_path, "w") as archive:
                archive.writestr("word/document.xml", xml)

            text = user_materials.read_script_material_text(docx_path, limit=200)

        self.assertIn("第一段：老板讲私域承接", text)
        self.assertIn("表格台词：客户资料不能只留平台", text)

    def test_select_and_clear_default_script_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings_path = Path(tempdir) / "剧本参考" / "settings.json"
            script = Path(tempdir) / "老板话术.txt"
            script.write_text("老板讲私域承接。", encoding="utf-8")
            materials = {
                "scripts": [{
                    "name": "老板话术.txt",
                    "relativePath": "老板话术.txt",
                    "path": str(script),
                    "kind": "script",
                    "preview": "老板讲私域承接。",
                }]
            }
            with patch.object(default_script_reference, "DEFAULT_SCRIPT_REFERENCE_DIR", settings_path.parent), \
                    patch.object(default_script_reference, "DEFAULT_SCRIPT_REFERENCE_SETTINGS_PATH", settings_path), \
                    patch.object(default_script_reference, "list_user_materials", return_value=materials):
                selected = default_script_reference.select_default_script_reference("老板话术.txt")
                self.assertTrue(selected["enabled"])
                self.assertEqual(selected["item"]["path"], str(script))

                cleared = default_script_reference.clear_default_script_reference()
                self.assertFalse(cleared["enabled"])

    def test_conversation_controller_skips_default_script_reference_for_plain_request(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[VideoPrompt(index=1, title="第 1 条", prompt=request.raw_text)],
                    first_frame=None,
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        script_item = {
            "name": "老板话术.txt",
            "relativePath": "老板话术.txt",
            "path": "/tmp/老板话术.txt",
            "kind": "script",
            "preview": "老板讲私域承接。",
        }
        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "none")  # type: ignore[arg-type]
        message = "生成一条10秒短视频，不用参考图。开场老板提醒团队沉淀客户关系。"
        with patch("ai8video.knowledge.default_script_reference.load_default_script_reference", return_value=script_item), \
                patch("ai8video.knowledge.default_script_reference.read_script_material_text", return_value="参考剧本正文：客户资料要沉淀到AI8video 。"):
            reply = agent.handle_message("script-ref", message)

        self.assertEqual(reply.stage, "completed")
        self.assertNotIn("已读取剧本素材：剧本参考 老板话术.txt", reply.text)
        self.assertNotIn("剧本参考《老板话术.txt》内容", captured["request"].raw_text)
        self.assertNotIn("客户资料要沉淀到AI8video", captured["request"].raw_text)
        self.assertIsNone(captured["request"].reference_image)

    def test_default_script_reference_uses_full_script_text(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings_path = Path(tempdir) / "剧本参考" / "settings.json"
            script = Path(tempdir) / "长剧本.txt"
            head = "开头脚本：私域承接。\n"
            tail = "后段脚本：评论区裂变和用户真实反馈。"
            script.write_text(head + ("中段内容\n" * 1200) + tail, encoding="utf-8")
            script_item = {
                "name": "长剧本.txt",
                "relativePath": "长剧本.txt",
                "path": str(script),
                "kind": "script",
                "preview": "开头脚本：私域承接。",
            }

            with patch.object(default_script_reference, "DEFAULT_SCRIPT_REFERENCE_SETTINGS_PATH", settings_path), \
                    patch("ai8video.knowledge.default_script_reference.load_default_script_reference", return_value=script_item):
                enriched_text, context = default_script_reference.apply_default_script_reference("生成10个", None)

        self.assertIn("开头脚本：私域承接", enriched_text)
        self.assertIn("后段脚本：评论区裂变和用户真实反馈", enriched_text)
        self.assertGreater(context["scripts"][0]["contentCharCount"], 5000)

    def test_at_script_material_uses_full_script_text(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            script = Path(tempdir) / "长剧本.txt"
            tail = "后段脚本：上线首日体验和海外华人反馈。"
            script.write_text("开头脚本：发布倒计时。\n" + ("中段内容\n" * 1200) + tail, encoding="utf-8")
            materials = {
                "images": [],
                "scripts": [{
                    "name": "长剧本.txt",
                    "relativePath": "长剧本.txt",
                    "path": str(script),
                    "kind": "script",
                    "preview": "开头脚本：发布倒计时。",
                }],
            }

            with patch.object(user_materials, "list_user_materials", return_value=materials):
                enriched_text, context = user_materials.expand_material_mentions("@长剧本.txt 生成10个")

        self.assertIn("开头脚本：发布倒计时", enriched_text)
        self.assertIn("后段脚本：上线首日体验和海外华人反馈", enriched_text)
        self.assertGreater(context["scripts"][0]["contentCharCount"], 5000)

    def test_short_count_with_default_script_reference_generates_without_extra_prompts(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[
                        VideoPrompt(index=1, title="第一条视频", prompt="video1"),
                        VideoPrompt(index=2, title="第二条视频", prompt="video2"),
                    ],
                    first_frame=None,
                    jobs=[
                        QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded"),
                        QuickVideoJob(video_index=2, job_id="dry-2", status="succeeded"),
                    ],
                    dry_run=True,
                )

        script_item = {
            "name": "2.docx",
            "relativePath": "2.docx",
            "path": "/tmp/2.docx",
            "kind": "script",
            "preview": "AI8video 发布倒计时。",
        }
        retrieval = {
            "ok": True,
            "query": "AI8video 发布倒计时",
            "recallCount": 20,
            "topK": 1,
            "rerankApplied": True,
            "fallbackReason": "",
            "sections": [{"id": 1, "heading": "全球发布", "content": "全球发布倒计时。", "score": 5.0}],
            "contextText": "[知识段 1｜全球发布]\n全球发布倒计时。",
        }
        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "none")  # type: ignore[arg-type]
        with patch("ai8video.knowledge.default_script_reference.load_default_script_reference", return_value=script_item), \
                patch("ai8video.knowledge.default_script_reference.retrieve_reference_context", return_value=retrieval), \
                patch("ai8video.knowledge.default_script_reference.read_script_material_text") as read_full, \
                patch("ai8video.application.conversation_controller.default_reference_image_path", return_value="/tmp/default.png"), \
                patch("ai8video.application.conversation_controller.enabled_default_reference_image_options", return_value={}), \
                patch("ai8video.application.conversation_controller.default_concurrent_generation_enabled", return_value=False):
            reply = agent.handle_message("script-ref-count", "2个")

        self.assertEqual(reply.stage, "completed")
        self.assertEqual(captured["request"].video_count, 2)
        self.assertEqual(captured["request"].mode, "batch_videos")
        read_full.assert_not_called()
        self.assertIn("剧本参考《2.docx》相关知识段（Top 1）", captured["request"].raw_text)
        self.assertEqual(captured["request"].reference_image, "/tmp/default.png")
        self.assertIsNone(captured["request"].reference_image_transform_options)
        self.assertFalse(captured["request"].concurrent_generation)

    def test_short_count_with_default_script_reference_ignores_batch_misclassification(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def llm(self, prompt: str) -> str:
                return """
                {
                  "intent": "batch_run",
                  "mode": null,
                  "video_count": 5,
                  "duration_seconds": null,
                  "concurrent_generation": null,
                  "reference_image_decision": null,
                  "core_keywords": null,
                  "style_hint": null,
                  "batch_target_count": 5,
                  "batch_seed_messages": [],
                  "rewrite_video_index": null,
                  "rewrite_instruction": null,
                  "needs_content_completion": false,
                  "needs_core_keywords": false,
                  "confidence": 0.72
                }
                """

            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[VideoPrompt(index=i, title=f"第 {i} 集", prompt=f"ep{i}") for i in range(1, 6)],
                    first_frame=None,
                    jobs=[QuickVideoJob(video_index=i, job_id=f"dry-{i}", status="succeeded") for i in range(1, 6)],
                    dry_run=True,
                )

        script_item = {
            "name": "2.docx",
            "relativePath": "2.docx",
            "path": "/tmp/2.docx",
            "kind": "script",
            "preview": "AI8video 发布倒计时。",
        }
        retrieval = {
            "ok": True,
            "query": "AI8video 发布倒计时",
            "recallCount": 20,
            "topK": 1,
            "rerankApplied": True,
            "fallbackReason": "",
            "sections": [{"id": 1, "heading": "全球发布", "content": "全球发布倒计时。", "score": 5.0}],
            "contextText": "[知识段 1｜全球发布]\n全球发布倒计时。",
        }
        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "none")  # type: ignore[arg-type]
        with patch("ai8video.knowledge.default_script_reference.load_default_script_reference", return_value=script_item), \
                patch("ai8video.knowledge.default_script_reference.retrieve_reference_context", return_value=retrieval), \
                patch("ai8video.knowledge.default_script_reference.read_script_material_text") as read_full, \
                patch("ai8video.application.conversation_controller.default_reference_image_path", return_value="/tmp/default.png"), \
                patch("ai8video.application.conversation_controller.enabled_default_reference_image_options", return_value={}), \
                patch("ai8video.application.conversation_controller.default_concurrent_generation_enabled", return_value=True):
            reply = agent.handle_message("script-ref-count-batch-misread", "5个")

        self.assertEqual(reply.stage, "completed")
        self.assertNotEqual(reply.meta.get("operation"), "batch_run")
        self.assertEqual(captured["request"].video_count, 5)
        self.assertEqual(captured["request"].mode, "batch_videos")
        self.assertTrue(captured["request"].concurrent_generation)
        read_full.assert_not_called()
        self.assertIn("剧本参考《2.docx》相关知识段（Top 1）", captured["request"].raw_text)
        self.assertIn("全球发布倒计时", captured["request"].raw_text)

    def test_control_message_uses_selected_script_reference_and_existing_form_state(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[VideoPrompt(index=1, title="第一条视频", prompt="video1")],
                    first_frame=None,
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        script_item = {
            "name": "2.docx",
            "relativePath": "2.docx",
            "path": "/tmp/2.docx",
            "kind": "script",
            "preview": "AI8video 发布倒计时。",
        }
        session_id = "script-ref-form-state"
        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "none")  # type: ignore[arg-type]
        state = ConversationState(session_id=session_id)
        state.draft.video_count = 15
        state.draft.mode = "batch_videos"
        state.draft.reference_image_enabled = False
        state.draft.concurrent_generation = True
        agent.sessions[session_id] = state

        with patch("ai8video.knowledge.default_script_reference.load_default_script_reference", return_value=script_item), \
                patch("ai8video.knowledge.default_script_reference.read_script_material_text", return_value="参考剧本正文：AI8video 全球发布倒计时。"):
            reply = agent.handle_message(session_id, "开始生成")

        self.assertEqual(reply.stage, "completed")
        self.assertNotEqual(reply.awaiting, "raw_text")
        self.assertEqual(captured["request"].video_count, 15)
        self.assertTrue(captured["request"].concurrent_generation)
        self.assertIn("剧本参考《2.docx》内容", captured["request"].raw_text)
        self.assertIn("AI8video 全球发布倒计时", captured["request"].raw_text)

    def test_default_script_reference_skips_manual_keyword_confirmation_when_ai_requests_it(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def llm(self, prompt: str) -> str:
                return """
                {
                  "intent": "generation",
                  "mode": "batch_videos",
                  "video_count": 30,
                  "duration_seconds": null,
                  "concurrent_generation": false,
                  "reference_image_decision": null,
                  "core_keywords": null,
                  "style_hint": null,
                  "batch_target_count": null,
                  "batch_seed_messages": [],
                  "rewrite_video_index": null,
                  "rewrite_instruction": null,
                  "needs_content_completion": false,
                  "needs_core_keywords": true,
                  "confidence": 0.91
                }
                """

            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[
                        VideoPrompt(index=1, title="第一条视频", prompt="video1"),
                        VideoPrompt(index=2, title="第二条视频", prompt="video2"),
                    ],
                    first_frame=None,
                    jobs=[
                        QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded"),
                        QuickVideoJob(video_index=2, job_id="dry-2", status="succeeded"),
                    ],
                    dry_run=True,
                )

        script_item = {
            "name": "2.docx",
            "relativePath": "2.docx",
            "path": "/tmp/2.docx",
            "kind": "script",
            "preview": "AI8video 全球发布倒计时。",
        }
        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "none")  # type: ignore[arg-type]
        with patch("ai8video.knowledge.default_script_reference.load_default_script_reference", return_value=script_item), \
                patch("ai8video.knowledge.default_script_reference.read_script_material_text", return_value="参考剧本正文：AI8video 全球发布倒计时。"), \
                patch("ai8video.application.conversation_controller.default_reference_image_path", return_value="/tmp/default.png"), \
                patch("ai8video.application.conversation_controller.enabled_default_reference_image_options", return_value={}):
            reply = agent.handle_message("script-ref-ai-keywords", "30")

        self.assertEqual(reply.stage, "completed")
        self.assertIsNone(reply.awaiting)
        self.assertNotEqual(reply.awaiting, "core_keywords")
        self.assertIn("剧本参考《2.docx》内容", captured["request"].raw_text)
        self.assertIsNone(captured["request"].core_keywords)


if __name__ == "__main__":
    unittest.main()
