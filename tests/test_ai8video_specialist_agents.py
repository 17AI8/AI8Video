from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai8video.batch import specialist_agent_observer
from ai8video.batch.agent_task_models import (
    TASK_FAILED,
    AgentResult,
    AgentTaskSpec,
    AgentTaskTransition,
)
from ai8video.batch.task_ledger import TaskLedger
from ai8video.core.models import (
    ArchivedAsset,
    GenerationOutcome,
    ParsedRequest,
    QuickVideoJob,
    VideoPrompt,
)
from ai8video.generation import generation_batch_context, merged_video_pipeline, output_review
from ai8video.generation.merged_video_pipeline import AI8VideoMergedPipeline
from ai8video.generation.pipeline import AI8VideoPipeline


class SpecialistAgentObserverTest(unittest.TestCase):
    def tearDown(self) -> None:
        specialist_agent_observer.shutdown_specialist_agent_scheduler(grace_seconds=0.2)

    def test_planner_and_reviewer_form_depth_one_shadow_graph(self) -> None:
        secret_title = "内部保密标题"
        secret_prompt = "内部保密提示词，不应进入任务快照"
        videos = [
            VideoPrompt(
                index=1,
                title=secret_title,
                prompt=secret_prompt,
                source_summary="内部素材摘要",
                keyword_guidance={
                    "post_review": {
                        "passes": False,
                        "narrationText": "仅用于判断是否存在，不应入账",
                        "violations": ["问题一"],
                        "userAdvisories": ["提醒一"],
                    }
                },
            )
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory, "gb-shadow")
            with patch.object(specialist_agent_observer, "_TASK_LEDGER", ledger):
                self._observe_pair(videos, "gb-shadow", "session-shadow")
            specialist_agent_observer.shutdown_specialist_agent_scheduler(grace_seconds=0.2)
            tasks = {task.agent_role: task for task in ledger.agent_tasks.list_tasks("gb-shadow")}
            reviewer_dependencies = ledger.agent_tasks.list_dependencies(tasks["reviewer"].task_id)

        planner = tasks["planner"]
        reviewer = tasks["reviewer"]
        serialized = json.dumps(
            {
                "plannerInput": planner.input_snapshot,
                "plannerOutput": planner.output_snapshot,
                "reviewerInput": reviewer.input_snapshot,
                "reviewerOutput": reviewer.output_snapshot,
            },
            ensure_ascii=False,
        )
        self.assertEqual(planner.state, "succeeded")
        self.assertEqual(reviewer.state, "succeeded")
        self.assertEqual(planner.parent_task_id, "gb-shadow")
        self.assertEqual(reviewer.parent_task_id, "gb-shadow")
        self.assertEqual(reviewer_dependencies, [planner.task_id])
        self.assertEqual(reviewer.output_snapshot["violationCount"], 1)
        self.assertNotIn(secret_title, serialized)
        self.assertNotIn(secret_prompt, serialized)
        self.assertNotIn("内部素材摘要", serialized)
        self.assertNotIn("仅用于判断是否存在", serialized)

    def test_failed_planner_cancels_reviewer_instead_of_leaving_it_queued(self) -> None:
        videos = [VideoPrompt(index=1, title="标题", prompt="提示词")]
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory, "gb-missing-plan")
            planner = ledger.agent_tasks.create_task(
                AgentTaskSpec(
                    task_id="gb-missing-plan:planner",
                    generation_batch_id="gb-missing-plan",
                    session_id="session-shadow",
                    task_type="video_plan_shadow",
                    agent_role="planner",
                    parent_task_id="gb-missing-plan",
                    idempotency_key="shadow:planner:v1",
                )
            )
            ledger.agent_tasks.transition_task(
                AgentTaskTransition(
                    task_id=planner.task_id,
                    expected_version=planner.version,
                    target_state=TASK_FAILED,
                    result=AgentResult(
                        planner.task_id,
                        error_type="PlannerFailed",
                        error_message="planner failed",
                    ),
                )
            )
            with patch.object(specialist_agent_observer, "_TASK_LEDGER", ledger):
                self._with_context(
                    "gb-missing-plan",
                    "session-shadow",
                    lambda: specialist_agent_observer.observe_reviewer_shadow(
                        videos,
                        session_id="session-shadow",
                        review_source="deterministic_finalization",
                    ),
                )
            specialist_agent_observer.shutdown_specialist_agent_scheduler(grace_seconds=0.2)
            tasks = {task.agent_role: task for task in ledger.agent_tasks.list_tasks("gb-missing-plan")}
            reviewer_dependencies = ledger.agent_tasks.list_dependencies(tasks["reviewer"].task_id)

        self.assertEqual(tasks["planner"].state, "failed")
        self.assertEqual(tasks["reviewer"].state, "cancelled")
        self.assertEqual(reviewer_dependencies, [tasks["planner"].task_id])

    def test_generated_output_reviews_use_distinct_scoped_reviewer_tasks(self) -> None:
        videos = [
            VideoPrompt(
                index=index,
                title=f"第 {index} 条",
                prompt=f"提示词 {index}",
                keyword_guidance={
                    "generated_output_review": {
                        "status": "completed",
                        "passes": index == 1,
                        "issues": [] if index == 1 else ["主体闪动"],
                        "improvements": ["保持主体朝向稳定"],
                        "nextPromptConstraints": ["减少快速转身"],
                    }
                },
            )
            for index in (1, 2)
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory, "gb-generated-review")
            with patch.object(specialist_agent_observer, "_TASK_LEDGER", ledger):
                def observe() -> None:
                    specialist_agent_observer.observe_planner_shadow(
                        videos,
                        session_id="session-shadow",
                        source_stage="planning_output",
                    )
                    for video in videos:
                        specialist_agent_observer.observe_reviewer_shadow(
                            [video],
                            session_id="session-shadow",
                            review_source="multimodal_contact_sheet",
                            task_scope=f"generated-{video.index}",
                        )

                self._with_context("gb-generated-review", "session-shadow", observe)
            specialist_agent_observer.shutdown_specialist_agent_scheduler(grace_seconds=0.2)
            first = ledger.agent_tasks.get_task("gb-generated-review:reviewer:generated-1")
            second = ledger.agent_tasks.get_task("gb-generated-review:reviewer:generated-2")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.input_snapshot["taskScope"], "generated-1")
        self.assertEqual(second.input_snapshot["taskScope"], "generated-2")
        self.assertEqual(first.output_snapshot["improvementCount"], 1)
        self.assertEqual(second.output_snapshot["issueCount"], 1)

    def test_observer_storage_failure_is_fail_open(self) -> None:
        broken_ledger = SimpleNamespace(
            agent_tasks=SimpleNamespace(get_task=Mock(side_effect=OSError("database unavailable")))
        )
        video = VideoPrompt(index=1, title="标题", prompt="提示词")
        with patch.object(specialist_agent_observer, "_TASK_LEDGER", broken_ledger):
            with self.assertLogs(specialist_agent_observer.logger, level="WARNING"):
                self._with_context(
                    "gb-broken",
                    "session-broken",
                    lambda: specialist_agent_observer.observe_planner_shadow(
                        [video],
                        session_id="session-broken",
                        source_stage="planning_output",
                    ),
                )

    def test_observer_write_lock_wait_is_bounded(self) -> None:
        video = VideoPrompt(index=1, title="标题", prompt="提示词")
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "task_ledger.sqlite3"
            ledger = TaskLedger(path, timeout_seconds=0.05)
            ledger.ensure_generation_batch(
                session_id="session-locked",
                generation_batch_id="gb-locked",
            )
            lock_connection = sqlite3.connect(path)
            lock_connection.execute("BEGIN IMMEDIATE")
            try:
                started = time.monotonic()
                with patch.object(specialist_agent_observer, "_TASK_LEDGER", ledger):
                    with self.assertLogs(specialist_agent_observer.logger, level="WARNING"):
                        self._with_context(
                            "gb-locked",
                            "session-locked",
                            lambda: specialist_agent_observer.observe_planner_shadow(
                                [video],
                                session_id="session-locked",
                                source_stage="planning_output",
                            ),
                        )
                elapsed = time.monotonic() - started
            finally:
                lock_connection.rollback()
                lock_connection.close()
            planner = ledger.agent_tasks.get_task("gb-locked:planner")

        self.assertLess(elapsed, 0.5)
        self.assertIsNone(planner)

    def test_shutdown_failure_keeps_scheduler_reference_for_retry(self) -> None:
        scheduler = Mock()
        scheduler.shutdown.side_effect = RuntimeError("shutdown failed")
        with patch.object(specialist_agent_observer, "_SHADOW_SCHEDULER", scheduler), patch.object(
            specialist_agent_observer,
            "_SHADOW_SCHEDULER_LEDGER_ID",
            123,
        ):
            with self.assertRaisesRegex(RuntimeError, "shutdown failed"):
                specialist_agent_observer.shutdown_specialist_agent_scheduler()
            retained = specialist_agent_observer._SHADOW_SCHEDULER

        self.assertIs(retained, scheduler)

    @staticmethod
    def _ledger(temporary_directory: str, batch_id: str) -> TaskLedger:
        ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
        ledger.ensure_generation_batch(
            session_id="session-shadow",
            generation_batch_id=batch_id,
            request_snapshot={"message": "原始请求只保存在根任务"},
        )
        return ledger

    def _observe_pair(self, videos: list[VideoPrompt], batch_id: str, session_id: str) -> None:
        def observe() -> None:
            specialist_agent_observer.observe_planner_shadow(
                videos,
                session_id=session_id,
                source_stage="planning_output",
            )
            specialist_agent_observer.observe_reviewer_shadow(
                videos,
                session_id=session_id,
                review_source="post_review_model",
            )

        self._with_context(batch_id, session_id, observe)

    @staticmethod
    def _with_context(batch_id: str, session_id: str, callback) -> None:
        batch_token = generation_batch_context.set_current_generation_batch_id(batch_id)
        session_token = generation_batch_context.set_current_generation_session_id(session_id)
        try:
            callback()
        finally:
            generation_batch_context.reset_current_generation_session_id(session_token)
            generation_batch_context.reset_current_generation_batch_id(batch_token)


class SpecialistAgentPipelineHookTest(unittest.TestCase):
    def test_normal_pipeline_observes_existing_plan_without_extra_model_call(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = SimpleNamespace(dry_run=True)
        pipeline.llm = None
        request = ParsedRequest(raw_text="生成一条产品视频", mode="single_video")
        sentinel = object()

        with patch.object(pipeline, "_run_videos", return_value=sentinel), patch(
            "ai8video.generation.pipeline.observe_planner_shadow"
        ) as observer:
            result = pipeline.run_request(request, progress_session_id="session-hook")

        self.assertIs(result, sentinel)
        observer.assert_called_once()
        self.assertEqual(observer.call_args.kwargs["source_stage"], "planning_output")

    def test_output_review_calls_model_once_and_observes_its_result(self) -> None:
        calls: list[str] = []

        def llm(prompt: str) -> str:
            calls.append(prompt)
            return json.dumps(
                [{
                    "index": 1,
                    "passes": True,
                    "corrected_video_prompt": "修正后的提示词",
                    "narration_text": "旁白",
                    "violations": [],
                    "user_advisories": [],
                }],
                ensure_ascii=False,
            )

        with patch.object(output_review, "observe_reviewer_shadow") as observer:
            reviewed = output_review.review_final_outputs(
                [VideoPrompt(index=1, title="标题", prompt="原提示词")],
                llm=llm,
                trace_session_id="session-review",
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(reviewed[0].prompt, "修正后的提示词")
        observer.assert_called_once()
        self.assertEqual(observer.call_args.kwargs["review_source"], "post_review_model")

    def test_merged_pipeline_observes_plan_and_deterministic_review(self) -> None:
        pipeline = AI8VideoMergedPipeline.__new__(AI8VideoMergedPipeline)
        pipeline.config = SimpleNamespace(dry_run=True)
        pipeline.llm = None
        pipeline.segment_count = 2
        request = ParsedRequest(raw_text="生成合并视频", mode="single_video", duration_seconds=10)
        sentinel = object()

        with patch.object(pipeline, "_run_final_videos", return_value=sentinel), patch.object(
            merged_video_pipeline,
            "observe_planner_shadow",
        ) as planner_observer:
            result = pipeline.run_request(request, progress_session_id="session-merge")

        self.assertIs(result, sentinel)
        self.assertEqual(planner_observer.call_args.kwargs["merge_mode"], "merge2")

        video = VideoPrompt(index=1, title="标题", prompt="最终提示词")
        pipeline.client = SimpleNamespace(
            guard=SimpleNamespace(forced_duration_seconds=0, assert_can_create_count=lambda _count: None)
        )
        completed = (
            QuickVideoJob(video_index=1, job_id="merge-job", status="succeeded"),
            GenerationOutcome(1, "merge-job", "succeeded", "generated"),
            ArchivedAsset(1, "merge-job", "test", "stored"),
            {"videoIndex": 1},
        )
        with patch.object(merged_video_pipeline, "finalize_video_prompts", return_value=[video]), patch.object(
            merged_video_pipeline,
            "observe_reviewer_shadow",
        ) as reviewer_observer, patch.object(
            pipeline,
            "_trace_merged_final_video_prompt",
        ), patch.object(pipeline, "_run_one_final_video", return_value=completed):
            pipeline._run_final_videos(
                request,
                [video],
                segment_duration_seconds=10,
                progress_session_id=None,
            )

        reviewer_observer.assert_called_once()
        self.assertEqual(
            reviewer_observer.call_args.kwargs["review_source"],
            "deterministic_finalization",
        )


if __name__ == "__main__":
    unittest.main()
