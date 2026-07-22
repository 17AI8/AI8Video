from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from ai8video.batch.batch_report_store import BatchReportStore
from ai8video.batch.daily_batch_runner import DailyBatchRunner
from ai8video.core.models import EpisodePrompt, ParsedRequest, PipelineResult, GenerationOutcome


class _FakePipeline:
    def __init__(self):
        self.run_calls: list[str] = []
        self.rewrite_calls: list[dict] = []
        self.expand_calls: list[dict] = []

    def run_from_message(self, message: str) -> PipelineResult:
        self.run_calls.append(message)
        request = ParsedRequest(raw_text=message, mode="multi_episode_script", episode_count=2)
        episodes = [
            EpisodePrompt(index=1, title="第一集", prompt="ep1 prompt"),
            EpisodePrompt(index=2, title="第二集", prompt="ep2 prompt"),
        ]
        outcomes = [
            GenerationOutcome(episode_index=1, job_id="job-1", status="succeeded", decision="generated"),
            GenerationOutcome(
                episode_index=2,
                job_id="job-2",
                status="failed",
                decision="failed",
                reasons=["生成失败"],
            ),
        ]
        assets = [
            {"episodeIndex": 1, "generationStatus": "generated", "usage": {}},
            {"episodeIndex": 2, "generationStatus": "failed", "usage": {}},
        ]
        return PipelineResult(
            request=request,
            episodes=episodes,
            first_frame=None,
            jobs=[],
            dry_run=True,
            outcomes=outcomes,
            archives=[],
            asset_records=assets,
        )

    def rewrite_episode(
        self,
        request: ParsedRequest,
        episode: EpisodePrompt,
        rewrite_instruction: str,
    ) -> PipelineResult:
        self.rewrite_calls.append(
            {
                "request_mode": request.mode,
                "request_episode_count": request.episode_count,
                "episode_index": episode.index,
                "episode_title": episode.title,
                "rewrite_instruction": rewrite_instruction,
            }
        )
        outcomes = [
            GenerationOutcome(episode_index=episode.index, job_id="job-2b", status="succeeded", decision="generated")
        ]
        assets = [{"episodeIndex": episode.index, "generationStatus": "generated", "usage": {}}]
        return PipelineResult(
            request=request,
            episodes=[episode],
            first_frame=None,
            jobs=[],
            dry_run=True,
            outcomes=outcomes,
            archives=[],
            asset_records=assets,
        )

    def expand_seed_messages(
        self,
        seed_messages: list[str],
        target_count: int,
        *,
        style_hint: str | None = None,
        failure_reasons: list[str] | None = None,
    ) -> list[str]:
        self.expand_calls.append(
            {
                "seed_messages": list(seed_messages),
                "target_count": target_count,
                "style_hint": style_hint,
                "failure_reasons": list(failure_reasons or []),
            }
        )
        return [f"扩写候选 {idx + 1}" for idx in range(target_count)]


class _FakeExpansionPipeline:
    def __init__(self):
        self.run_calls: list[str] = []
        self.expand_calls: list[dict] = []

    def run_from_message(self, message: str) -> PipelineResult:
        self.run_calls.append(message)
        index = len(self.run_calls)
        request = ParsedRequest(raw_text=message, mode="single_prompt", episode_count=1)
        episode = EpisodePrompt(index=1, title=f"第 {index} 条", prompt=message)
        outcome = GenerationOutcome(
            episode_index=1,
            job_id=f"job-{index}",
            status="succeeded",
            decision="generated",
        )
        asset = {"episodeIndex": 1, "generationStatus": "generated", "usage": {}}
        return PipelineResult(
            request=request,
            episodes=[episode],
            first_frame=None,
            jobs=[],
            dry_run=True,
            outcomes=[outcome],
            archives=[],
            asset_records=[asset],
        )

    def expand_seed_messages(
        self,
        seed_messages: list[str],
        target_count: int,
        *,
        style_hint: str | None = None,
        failure_reasons: list[str] | None = None,
    ) -> list[str]:
        self.expand_calls.append(
            {
                "seed_messages": list(seed_messages),
                "target_count": target_count,
                "style_hint": style_hint,
                "failure_reasons": list(failure_reasons or []),
            }
        )
        return [f"老板在办公室讲AI8video 跟进效率补量版本 {idx + 1}" for idx in range(target_count)]


class _RequestSnapshotPipeline(_FakePipeline):
    def __init__(self):
        super().__init__()
        self.requests: list[ParsedRequest] = []

    def run_request(self, request: ParsedRequest) -> PipelineResult:
        self.requests.append(request)
        return self.run_from_message(request.raw_text)


class DailyBatchRunnerTest(unittest.TestCase):
    def test_batch_reads_html_motion_setting_once_and_passes_request_snapshot(self) -> None:
        pipeline = _RequestSnapshotPipeline()
        with tempfile.TemporaryDirectory() as tempdir, patch(
            "ai8video.batch.daily_batch_runner.default_html_motion_overlay_enabled",
            return_value=True,
        ) as read_setting:
            runner = DailyBatchRunner(
                pipeline=pipeline,
                target_pass_count=1,
                initial_candidate_budget=1,
                max_candidate_budget=1,
                report_store=BatchReportStore(Path(tempdir) / "batch_reports"),
            )
            report = runner.run(["老板在办公室讲封号风险。"], trigger="unit_test", source="tests")

        read_setting.assert_called_once_with()
        self.assertTrue(pipeline.requests[0].html_motion_overlay_enabled)
        self.assertTrue(report["htmlMotionOverlayEnabled"])

    def test_multi_episode_failure_does_not_schedule_retry_after_generation_failed(self) -> None:
        pipeline = _FakePipeline()
        with tempfile.TemporaryDirectory() as tempdir:
            runner = DailyBatchRunner(
                pipeline=pipeline,
                target_pass_count=2,
                initial_candidate_budget=2,
                max_candidate_budget=2,
                max_retries_per_video=1,
                report_store=BatchReportStore(Path(tempdir) / "batch_reports"),
            )

            report = runner.run(
                ["我贴一段 2 集剧本，你帮我拆成 2 条短视频。"],
                style_hint="商务",
                trigger="unit_test",
                source="tests",
                session_id="batch-test",
            )

            self.assertEqual(pipeline.run_calls, ["我贴一段 2 集剧本，你帮我拆成 2 条短视频。风格更偏商务。"])
            self.assertEqual(pipeline.rewrite_calls, [])
            self.assertEqual(report["successCount"], 1)
            self.assertEqual(report["failedCount"], 1)
            self.assertEqual(report["retryScheduledCount"], 0)
            self.assertFalse(report["goalMet"])
            self.assertTrue(Path(report["reportPath"]).exists())
            self.assertEqual(report["reportSource"], "tests")

    def test_queue_exhausted_auto_expands_seed_messages_until_goal_met(self) -> None:
        pipeline = _FakeExpansionPipeline()
        with tempfile.TemporaryDirectory() as tempdir:
            runner = DailyBatchRunner(
                pipeline=pipeline,
                target_pass_count=2,
                initial_candidate_budget=2,
                max_candidate_budget=3,
                max_retries_per_video=0,
                report_store=BatchReportStore(Path(tempdir) / "batch_reports"),
            )

            report = runner.run(
                ["老板在会议室讲封号风险。"],
                style_hint="商务",
                trigger="unit_test",
                source="tests",
                session_id="batch-expand",
            )

            self.assertEqual(len(pipeline.expand_calls), 1)
            expand_call = pipeline.expand_calls[0]
            self.assertEqual(expand_call["target_count"], 1)
            self.assertEqual(expand_call["style_hint"], "商务")
            self.assertEqual(report["successCount"], 2)
            self.assertEqual(report["expansionRoundCount"], 1)
            self.assertEqual(report["expandedSeedCount"], 1)
            self.assertEqual(report["topUpStrategies"], ["queue_exhausted_goal_gap_top_up"])
            self.assertEqual(len(report["expandedSeedSamples"]), 1)
            self.assertTrue(report["goalMet"])
            self.assertEqual(len(pipeline.run_calls), 2)


if __name__ == "__main__":
    unittest.main()
