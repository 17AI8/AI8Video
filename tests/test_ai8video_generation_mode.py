from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.generation import generation_mode
from ai8video.application.conversation_controller import AI8VideoConversationController
from ai8video.core.models import EpisodePrompt, ParsedRequest, PipelineResult, QuickVideoJob


class AI8VideoGenerationModeTest(unittest.TestCase):
    def test_generation_mode_defaults_to_normal_and_saves_concurrent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings_path = Path(tempdir) / "生成模式" / "settings.json"
            with patch.object(generation_mode, "GENERATION_MODE_DIR", settings_path.parent), \
                    patch.object(generation_mode, "GENERATION_MODE_SETTINGS_PATH", settings_path):
                self.assertFalse(generation_mode.default_concurrent_generation_enabled())

                status = generation_mode.update_generation_mode(concurrent_generation=True)

                self.assertTrue(status["ok"])
                self.assertTrue(status["concurrentGeneration"])
                self.assertTrue(generation_mode.default_concurrent_generation_enabled())

    def test_conversation_controller_uses_default_concurrent_mode_when_user_does_not_choose(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    episodes=[EpisodePrompt(index=1, title="第 1 条", prompt=request.raw_text)],
                    first_frame=None,
                    jobs=[QuickVideoJob(episode_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "normal")  # type: ignore[arg-type]
        message = (
            "根据这个剧本生成 2 个 10s 短视频，老板商务风。"
            "核心主题：私域资产。参考图：/tmp/612.png"
        )
        with patch("ai8video.application.conversation_controller.default_concurrent_generation_enabled", return_value=True):
            reply = agent.handle_message("generation-default-concurrent", message)

        self.assertEqual(reply.stage, "completed")
        self.assertTrue(captured["request"].concurrent_generation)

    def test_conversation_controller_explicit_normal_mode_overrides_default_concurrent_mode(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    episodes=[EpisodePrompt(index=1, title="第 1 条", prompt=request.raw_text)],
                    first_frame=None,
                    jobs=[QuickVideoJob(episode_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "normal")  # type: ignore[arg-type]
        message = (
            "根据这个剧本生成 2 个 10s 短视频，老板商务风。"
            "核心主题：私域资产。参考图：/tmp/612.png，普通模式"
        )
        with patch("ai8video.application.conversation_controller.default_concurrent_generation_enabled", return_value=True):
            reply = agent.handle_message("generation-explicit-normal", message)

        self.assertEqual(reply.stage, "completed")
        self.assertFalse(captured["request"].concurrent_generation)


if __name__ == "__main__":
    unittest.main()
