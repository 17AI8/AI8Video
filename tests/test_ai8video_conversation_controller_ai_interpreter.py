from __future__ import annotations

import unittest
from unittest.mock import Mock
from unittest.mock import patch

from ai8video.application.conversation_controller import AI8VideoConversationController
from ai8video.core.models import ConversationState, VideoPrompt, ParsedRequest, PipelineResult, QuickVideoJob


class _PipelineWithInterpreter:
    config = object()

    def __init__(self, response: str | None = None):
        self.llm_prompts: list[str] = []
        self.response = response
        self.run_request = Mock()
        self.rewrite_video = Mock()

    def llm(self, prompt: str) -> str:
        self.llm_prompts.append(prompt)
        if self.response is not None:
            return self.response
        return """
        {
          "intent": "generation",
          "mode": "batch_videos",
          "video_count": 10,
          "duration_seconds": null,
          "concurrent_generation": null,
          "reference_image_decision": null,
          "core_keywords": "重大消息",
          "style_hint": "发布会悬念感",
          "confidence": 0.93
        }
        """


class _PipelineWithFailingInterpreter:
    def request_interpreter_llm(self, prompt: str) -> str:
        raise TimeoutError("request interpretation timed out")


class AI8VideoConversationControllerAiInterpreterTest(unittest.TestCase):
    @staticmethod
    def _build_result() -> PipelineResult:
        request = ParsedRequest(raw_text="商务风三个10s", mode="batch_videos")
        video = VideoPrompt(index=1, title="单条视频", prompt="商务风三个10s")
        job = QuickVideoJob(
            video_index=1,
            job_id="job-ai-interpreter",
            status="succeeded",
            prompt=video.prompt,
            storage_key="mobile:job-ai-interpreter",
        )
        return PipelineResult(
            request=request,
            videos=[video],
            first_frame=None,
            jobs=[job],
            outcomes=[],
            archives=[],
            asset_records=[],
            dry_run=True,
        )

    def test_merge_message_prefers_ai_interpretation_for_freeform_count_and_topic(self) -> None:
        pipeline = _PipelineWithInterpreter()
        agent = AI8VideoConversationController(pipeline)  # type: ignore[arg-type]
        state = ConversationState(session_id="ai-interpret")

        agent._merge_message(state, "来一组重大消息，做成矩阵")

        self.assertEqual(state.draft.mode, "batch_videos")
        self.assertEqual(state.draft.video_count, 10)
        self.assertEqual(state.draft.core_keywords, "重大消息")
        self.assertIn("发布会悬念感", state.draft.style_hint)
        self.assertEqual(len(pipeline.llm_prompts), 1)

    def test_merge_message_falls_back_to_local_count_when_ai_interpreter_times_out(self) -> None:
        agent = AI8VideoConversationController(_PipelineWithFailingInterpreter())  # type: ignore[arg-type]
        state = ConversationState(session_id="ai-timeout-fallback")

        agent._merge_message(state, "10 个，重大消息")

        self.assertEqual(state.draft.mode, "batch_videos")
        self.assertEqual(state.draft.video_count, 10)

    def test_batch_request_uses_ai_intent_before_local_patterns(self) -> None:
        pipeline = _PipelineWithInterpreter(
            """
            {
              "intent": "batch_run",
              "mode": "batch_videos",
              "video_count": null,
              "duration_seconds": null,
              "concurrent_generation": null,
              "reference_image_decision": null,
              "core_keywords": null,
              "style_hint": "商务",
              "batch_target_count": 4,
              "batch_seed_messages": ["老板讲封号风险", "老板讲私域承接"],
              "rewrite_video_index": null,
              "rewrite_instruction": null,
              "needs_content_completion": false,
              "needs_core_keywords": false,
              "confidence": 0.91
            }
            """
        )
        fake_runner = Mock()
        fake_runner.run.return_value = {
            "targetPassCount": 4,
            "seedMessages": 2,
            "passCount": 2,
            "goalMet": False,
            "dryRun": True,
        }
        with patch("ai8video.application.conversation_controller.DailyBatchRunner", return_value=fake_runner):
            reply = AI8VideoConversationController(pipeline).handle_message(
                "ai-batch",
                "这一轮做个素材池，小范围先测：老板讲封号风险；老板讲私域承接",
            )

        self.assertEqual(reply.meta["operation"], "batch_run")
        self.assertEqual(reply.meta["targetPassCount"], 4)
        fake_runner.run.assert_called_once_with(
            ["老板讲封号风险", "老板讲私域承接"],
            style_hint="商务",
            trigger="conversation_controller",
            source="chat",
            session_id="ai-batch",
        )

    def test_rewrite_request_uses_ai_intent_and_instruction(self) -> None:
        pipeline = _PipelineWithInterpreter(
            """
            {
              "intent": "rewrite",
              "mode": "single_video",
              "video_count": 1,
              "duration_seconds": null,
              "concurrent_generation": null,
              "reference_image_decision": null,
              "core_keywords": null,
              "style_hint": "更坚定",
              "batch_target_count": null,
              "batch_seed_messages": [],
              "rewrite_video_index": 2,
              "rewrite_instruction": "老板表情更坚定，语气更像真实开会",
              "needs_content_completion": false,
              "needs_core_keywords": false,
              "confidence": 0.94
            }
            """
        )
        pipeline.rewrite_video.return_value.to_dict.return_value = {
            "videos": [{"index": 2, "title": "第 2 集", "prompt": "新版"}],
            "jobs": [],
            "outcomes": [],
            "archives": [],
            "assetRecords": [],
            "dryRun": True,
        }
        agent = AI8VideoConversationController(pipeline)  # type: ignore[arg-type]
        state = ConversationState(session_id="ai-rewrite", completed_runs=1)
        state.last_result = {
            "request": {},
            "videos": [
                {"index": 1, "title": "第 1 集", "prompt": "旧版一"},
                {"index": 2, "title": "第 2 集", "prompt": "旧版二"},
            ],
            "jobs": [],
            "outcomes": [],
            "archives": [],
            "assetRecords": [],
        }
        agent.sessions["ai-rewrite"] = state

        reply = agent.handle_message("ai-rewrite", "第二条那版老板不够稳，重新来一下")

        self.assertEqual(reply.meta["operation"], "rewrite")
        self.assertEqual(reply.meta["rewrittenVideoIndex"], 2)
        self.assertEqual(reply.meta["rewriteInstruction"], "老板表情更坚定，语气更像真实开会")
        pipeline.rewrite_video.assert_called_once()

    def test_missing_content_and_core_keywords_can_be_decided_by_ai(self) -> None:
        pipeline = _PipelineWithInterpreter(
            """
            {
              "intent": "generation",
              "mode": "batch_videos",
              "video_count": 3,
              "duration_seconds": 10,
              "concurrent_generation": null,
              "reference_image_decision": false,
              "core_keywords": null,
              "style_hint": "商务",
              "batch_target_count": null,
              "batch_seed_messages": [],
              "rewrite_video_index": null,
              "rewrite_instruction": null,
              "needs_content_completion": true,
              "needs_core_keywords": true,
              "confidence": 0.9
            }
            """
        )
        pipeline.run_request.return_value = self._build_result()
        reply = AI8VideoConversationController(pipeline, merge_mode_loader=lambda: "none").handle_message(
            "ai-missing",
            "商务风三个10s，先按默认图走",
        )

        self.assertEqual(reply.stage, "completed")
        self.assertIsNone(reply.awaiting)
        self.assertNotIn("guide", reply.meta)
        pipeline.run_request.assert_called_once()
        request = pipeline.run_request.call_args.args[0]
        self.assertIn("口播文案补全要求", request.raw_text)


if __name__ == "__main__":
    unittest.main()
