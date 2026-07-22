from __future__ import annotations

import os
import tempfile
import unittest

from ai8video.application.conversation_controller import AI8VideoConversationController
from ai8video.core.config import AI8VideoConfig
from ai8video.generation.pipeline import AI8VideoPipeline


class AI8VideoConversationControllerBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.env_backup = {key: os.environ.get(key) for key in self._env_keys()}
        os.environ["AI8VIDEO_DRY_RUN"] = "1"
        os.environ["AI8VIDEO_ASSET_STORE_PATH"] = os.path.join(self.tempdir.name, "assets.jsonl")
        os.environ["AI8VIDEO_ARCHIVE_LOCAL_DIR"] = os.path.join(self.tempdir.name, "archive")
        os.environ["AI8VIDEO_BATCH_REPORT_DIR"] = os.path.join(self.tempdir.name, "batch_reports")

    def tearDown(self) -> None:
        for key, value in self.env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tempdir.cleanup()

    @staticmethod
    def _env_keys() -> list[str]:
        return [
            "AI8VIDEO_DRY_RUN",
            "AI8VIDEO_ASSET_STORE_PATH",
            "AI8VIDEO_ARCHIVE_LOCAL_DIR",
            "AI8VIDEO_BATCH_REPORT_DIR",
        ]

    def test_batch_run_collects_seed_lines_and_returns_report(self) -> None:
        config = AI8VideoConfig.from_env()
        agent = AI8VideoConversationController(AI8VideoPipeline(config=config))

        first = agent.handle_message("batch-user", "今天帮我批量跑 2 条，风格更商务一点。")
        self.assertEqual(first.awaiting, "batch_seed_messages")
        self.assertEqual(first.meta["operation"], "batch_collect")
        self.assertEqual(first.meta["targetPassCount"], 2)

        second = agent.handle_message(
            "batch-user",
            "1. 老板在会议室讲封号风险\n2. 老板在办公室讲AI8video 承接私域",
        )
        payload = second.to_dict()

        self.assertEqual(second.meta["operation"], "batch_run")
        self.assertEqual(payload["result"]["targetPassCount"], 2)
        self.assertEqual(payload["result"]["passCount"], 2)
        self.assertTrue(payload["result"]["goalMet"])
        self.assertEqual(payload["result"]["dryRun"], True)
        self.assertTrue(os.path.exists(payload["result"]["reportPath"]))
        self.assertEqual(payload["result"]["reportSource"], "chat")

    def test_batch_run_can_start_from_single_message_with_inline_candidates(self) -> None:
        config = AI8VideoConfig.from_env()
        agent = AI8VideoConversationController(AI8VideoPipeline(config=config))

        reply = agent.handle_message(
            "batch-user-inline",
            "今天先跑两条商务风，候选：老板在会议室讲封号风险；老板在办公室讲AI8video 承接私域。",
        )
        payload = reply.to_dict()

        self.assertEqual(reply.meta["operation"], "batch_run")
        self.assertEqual(payload["result"]["targetPassCount"], 2)
        self.assertEqual(payload["result"]["seedMessages"], 2)
        self.assertEqual(payload["result"]["passCount"], 2)
        self.assertTrue(payload["result"]["goalMet"])

    def test_batch_run_allows_one_target_one_inline_candidate(self) -> None:
        config = AI8VideoConfig.from_env()
        agent = AI8VideoConversationController(AI8VideoPipeline(config=config))

        reply = agent.handle_message(
            "batch-user-single",
            "今天先跑一条商务风：老板在会议室讲封号风险。",
        )
        payload = reply.to_dict()

        self.assertEqual(reply.meta["operation"], "batch_run")
        self.assertEqual(payload["result"]["targetPassCount"], 1)
        self.assertEqual(payload["result"]["seedMessages"], 1)
        self.assertEqual(payload["result"]["passCount"], 1)
        self.assertTrue(payload["result"]["goalMet"])


if __name__ == "__main__":
    unittest.main()
