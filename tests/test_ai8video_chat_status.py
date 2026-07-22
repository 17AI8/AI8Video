from __future__ import annotations

import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai8video.application import ai8video_chat_service
from ai8video.generation import generation_batch_context, generation_progress
from ai8video.assets.asset_store import JsonlAssetStore
from ai8video.core.config import AI8VideoConfig
from ai8video.generation.generation_progress import (
    claim_generation_batch,
    cancel_generation_progress,
    clear_generation_progress,
    create_generation_batch_id,
    fail_generation_progress,
    stop_unsubmitted_generation_progress,
    get_generation_progress,
    settle_stale_first_frame_progress,
    start_generation_progress,
    mark_job_failed,
    mark_job_preparing_first_frame,
    mark_job_submitted,
    mark_job_polling,
)
from ai8video.core.models import EpisodePrompt, QuickVideoJob
from ai8video.batch.task_ledger import TaskLedger


class AI8VideoAI8VideoChatStatusTest(unittest.TestCase):
    def test_start_generation_progress_assigns_generation_batch_id(self) -> None:
        session_id = "session status batch id"
        episodes = [EpisodePrompt(index=1, title="第一条", prompt="ep1")]
        custom_batch_id = create_generation_batch_id(session_id)

        try:
            start_generation_progress(session_id, episodes, generation_batch_id=custom_batch_id)

            progress = get_generation_progress(session_id)
            self.assertIsNotNone(progress)
            self.assertEqual(progress["sessionId"], session_id)
            self.assertEqual(progress["generationBatchId"], custom_batch_id)
            self.assertTrue(custom_batch_id.startswith("gb-session-status-batch-id-"))
        finally:
            clear_generation_progress(session_id)

    def test_generation_progress_records_real_item_execution_events(self) -> None:
        session_id = "session-real-execution-events"
        episode = EpisodePrompt(index=1, title="第一条", prompt="ep1")
        job = QuickVideoJob(episode_index=1, job_id="job-events", provider_progress=42)

        try:
            start_generation_progress(session_id, [episode])
            mark_job_submitted(session_id, episode, job)
            mark_job_polling(session_id, job)
            progress = get_generation_progress(session_id)
        finally:
            clear_generation_progress(session_id)

        self.assertIsNotNone(progress)
        messages = [event["message"] for event in progress["events"]]
        self.assertIn("已创建 1 个视频任务，正在生成视频方案。", messages)
        self.assertIn("生成任务已提交", messages)
        self.assertIn("视频生成中", messages)
        self.assertNotIn("job-events", str(progress["events"]))

    def test_generation_progress_coalesces_polling_event_updates(self) -> None:
        session_id = "session-coalesced-polling-events"
        episode = EpisodePrompt(index=1, title="第一条", prompt="ep1")
        job = QuickVideoJob(episode_index=1, job_id="job-events", provider_progress=33)

        try:
            start_generation_progress(session_id, [episode])
            mark_job_submitted(session_id, episode, job)
            mark_job_polling(session_id, job)
            job.provider_progress = 36
            mark_job_polling(session_id, job)
            progress = get_generation_progress(session_id)
        finally:
            clear_generation_progress(session_id)

        self.assertIsNotNone(progress)
        polling_events = [event for event in progress["events"] if event.get("status") == "polling"]
        self.assertEqual(1, len(polling_events))
        self.assertEqual(36, polling_events[0]["providerProgress"])

    def test_stale_batch_cannot_recreate_current_session_progress(self) -> None:
        session_id = "session-stale-batch-guard"
        episodes = [EpisodePrompt(index=1, title="第一条", prompt="ep1")]
        current_batch_id = create_generation_batch_id(session_id)
        stale_batch_id = create_generation_batch_id(session_id)

        try:
            claim_generation_batch(session_id, current_batch_id)
            start_generation_progress(
                session_id,
                episodes,
                generation_batch_id=current_batch_id,
            )
            token = generation_batch_context.set_current_generation_batch_id(stale_batch_id)
            try:
                start_generation_progress(
                    session_id,
                    episodes,
                    generation_batch_id=stale_batch_id,
                )
                mark_job_submitted(
                    session_id,
                    episodes[0],
                    QuickVideoJob(episode_index=1, job_id="stale-job"),
                )
            finally:
                generation_batch_context.reset_current_generation_batch_id(token)

            progress = get_generation_progress(session_id)
        finally:
            clear_generation_progress(session_id)

        self.assertIsNotNone(progress)
        self.assertEqual(progress["generationBatchId"], current_batch_id)
        self.assertIsNone(progress["items"][0].get("jobId"))

    def test_stale_batch_cannot_finish_current_session_progress(self) -> None:
        session_id = "session-stale-batch-finish-guard"
        episodes = [EpisodePrompt(index=1, title="第一条", prompt="ep1")]
        current_batch_id = create_generation_batch_id(session_id)
        stale_batch_id = create_generation_batch_id(session_id)

        try:
            claim_generation_batch(session_id, current_batch_id)
            start_generation_progress(
                session_id,
                episodes,
                generation_batch_id=current_batch_id,
            )
            token = generation_batch_context.set_current_generation_batch_id(stale_batch_id)
            try:
                generation_progress.finish_generation_progress(session_id)
                generation_progress.fail_generation_progress(session_id, "旧批次错误")
            finally:
                generation_batch_context.reset_current_generation_batch_id(token)

            progress = get_generation_progress(session_id)
        finally:
            clear_generation_progress(session_id)

        self.assertIsNotNone(progress)
        self.assertEqual(progress["generationBatchId"], current_batch_id)
        self.assertEqual(progress["status"], "active")

    def test_generation_progress_writes_item_updates_to_task_ledger(self) -> None:
        session_id = "session status task ledger"
        episodes = [EpisodePrompt(index=1, title="第一条", prompt="ep1")]
        generation_batch_id = create_generation_batch_id(session_id)

        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            with patch.object(generation_progress, "_TASK_LEDGER", ledger):
                try:
                    start_generation_progress(session_id, episodes, generation_batch_id=generation_batch_id)
                    mark_job_submitted(
                        session_id,
                        episodes[0],
                        QuickVideoJob(episode_index=1, job_id="job-ledger-001"),
                    )

                    record = ledger.get_generation_batch(generation_batch_id)
                finally:
                    clear_generation_progress(session_id)

        self.assertIsNotNone(record)
        self.assertEqual(record["generationBatchId"], generation_batch_id)
        self.assertEqual(record["sessionId"], session_id)
        self.assertEqual(record["status"], "active")
        self.assertEqual(record["progress"]["items"][0]["status"], "submitted")
        self.assertEqual(record["progress"]["items"][0]["jobId"], "job-ledger-001")

    def test_stale_first_frame_timeout_persists_terminal_ledger_snapshot(self) -> None:
        session_id = "session first frame timeout ledger"
        generation_batch_id = create_generation_batch_id(session_id)
        episode = EpisodePrompt(index=1, title="第一条", prompt="ep1")

        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            with patch.object(generation_progress, "_TASK_LEDGER", ledger):
                try:
                    start_generation_progress(
                        session_id,
                        [episode],
                        generation_batch_id=generation_batch_id,
                    )
                    mark_job_preparing_first_frame(session_id, episode)
                    with patch.object(generation_progress.time, "time", return_value=time.time() + 400):
                        result = settle_stale_first_frame_progress(session_id)
                    record = ledger.get_generation_batch(generation_batch_id)
                finally:
                    clear_generation_progress(session_id)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "failed")
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "failed")
        self.assertIsNotNone(record["completedAt"])
        self.assertEqual(record["progress"]["items"][0]["providerStatus"], "first_frame_timeout")

    def test_stop_unsubmitted_progress_persists_terminal_ledger_snapshot(self) -> None:
        session_id = "session unsubmitted timeout ledger"
        generation_batch_id = create_generation_batch_id(session_id)
        progress_snapshot = {
            "sessionId": session_id,
            "generationBatchId": generation_batch_id,
            "status": "planning",
            "totalRequested": 2,
            "items": [
                {"episodeIndex": 1, "title": "第一条", "status": "planning"},
                {"episodeIndex": 2, "title": "第二条", "status": "planning"},
            ],
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            with patch.object(generation_progress, "_TASK_LEDGER", ledger):
                try:
                    result = stop_unsubmitted_generation_progress(
                        session_id,
                        progress_snapshot,
                        "本地任务超时",
                    )
                    record = ledger.get_generation_batch(generation_batch_id)
                finally:
                    clear_generation_progress(session_id)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "failed")
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["progress"]["failedCount"], 2)
        self.assertTrue(
            all(item["providerStatus"] == "local_timeout" for item in record["progress"]["items"])
        )

    def test_finish_generation_progress_persists_terminal_ledger_snapshot(self) -> None:
        session_id = "session finish ledger"
        generation_batch_id = create_generation_batch_id(session_id)
        episode = EpisodePrompt(index=1, title="第一条", prompt="ep1")

        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            with patch.object(generation_progress, "_TASK_LEDGER", ledger):
                try:
                    start_generation_progress(
                        session_id,
                        [episode],
                        generation_batch_id=generation_batch_id,
                    )
                    generation_progress.mark_job_succeeded(
                        session_id,
                        QuickVideoJob(episode_index=1, job_id="job-finish-ledger-001"),
                    )
                    generation_progress.finish_generation_progress(session_id)
                    record = ledger.get_generation_batch(generation_batch_id)
                finally:
                    clear_generation_progress(session_id)

        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "completed")
        self.assertIsNotNone(record["completedAt"])
        self.assertEqual(record["progress"]["succeededCount"], 1)

    def test_cancel_generation_progress_persists_terminal_ledger_snapshot(self) -> None:
        session_id = "session cancel ledger"
        generation_batch_id = create_generation_batch_id(session_id)
        episode = EpisodePrompt(index=1, title="第一条", prompt="ep1")

        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            with patch.object(generation_progress, "_TASK_LEDGER", ledger):
                try:
                    start_generation_progress(
                        session_id,
                        [episode],
                        generation_batch_id=generation_batch_id,
                    )
                    result = cancel_generation_progress(session_id, "用户取消")
                    record = ledger.get_generation_batch(generation_batch_id)
                finally:
                    clear_generation_progress(session_id)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "cancelled")
        self.assertIsNotNone(record["completedAt"])
        self.assertEqual(record["progress"]["items"][0]["status"], "skipped")

    def _build_session(self) -> ai8video_chat_service._AI8VideoSession:
        session = ai8video_chat_service._AI8VideoSession.__new__(
            ai8video_chat_service._AI8VideoSession
        )
        session.session_id = "session-status"
        session.lock = threading.Lock()
        session.config = None
        session.worker_thread = SimpleNamespace(is_alive=lambda: True)
        session.latest_ai8video_payload = None
        session.latest_error = None
        session.current_display_queue = queue.Queue()
        session.current_message = "老板在会议室讲封号风险"
        session.current_started_at = time.time() - 8
        session.current_generation_batch_id = None
        session.background_delivery_pending = True
        session.background_final_payload = None
        session.background_completed_at = None
        return session

    def test_runtime_worker_sets_and_resets_generation_identity_context(self) -> None:
        session = self._build_session()
        session.config = SimpleNamespace(max_poll_attempts=1, poll_interval_seconds=1)
        display_queue = queue.Queue()
        observed_context = {}

        def fake_handle_chat_message(**_kwargs):
            observed_context["generationBatchId"] = (
                generation_batch_context.get_current_generation_batch_id()
            )
            observed_context["sessionId"] = (
                generation_batch_context.get_current_generation_session_id()
            )
            return {"stage": "completed"}

        with patch.object(
            ai8video_chat_service,
            "handle_chat_message",
            side_effect=fake_handle_chat_message,
        ):
            session._run_runtime_chat(
                "生成一条视频",
                display_queue,
                "gb-worker-context-001",
            )

        self.assertEqual(observed_context["generationBatchId"], "gb-worker-context-001")
        self.assertEqual(observed_context["sessionId"], "session-status")
        self.assertIsNone(generation_batch_context.get_current_generation_batch_id())
        self.assertIsNone(generation_batch_context.get_current_generation_session_id())
        self.assertTrue(display_queue.get_nowait()["done"])

    def test_snapshot_status_reports_planning_when_agent_still_running_after_timeout(self) -> None:
        session = self._build_session()

        status = session.snapshot_status()

        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["phase"], "planning")
        self.assertIn("分析文档", status["statusLabel"])
        self.assertEqual(status["sessionId"], "session-status")
        self.assertGreaterEqual(status["elapsedSeconds"], 7)

    def test_snapshot_status_reports_stale_when_timed_out_agent_stopped_without_payload(self) -> None:
        session = self._build_session()
        session.worker_thread = SimpleNamespace(is_alive=lambda: False)
        session.current_display_queue = None

        status = session.snapshot_status()

        self.assertEqual(status["status"], "idle")
        self.assertTrue(status["stalePending"])

    def test_handle_message_timeout_without_generation_keeps_running_planning_pending(self) -> None:
        session = self._build_session()
        session._start_task = lambda message: None
        session.current_display_queue = queue.Queue()
        session.current_started_at = time.time() - 20

        with patch.object(ai8video_chat_service.time, "time", side_effect=[100.0, 111.0, 111.0, 111.0, 111.0]):
            with self.assertRaises(TimeoutError):
                session.handle_message("2集", timeout_seconds=1)

        status = session.snapshot_status()
        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["phase"], "planning")
        self.assertTrue(session.background_delivery_pending)

    def test_handle_message_timeout_without_running_worker_marks_failed_payload(self) -> None:
        session = self._build_session()
        session._start_task = lambda message: None
        session.worker_thread = SimpleNamespace(is_alive=lambda: False)
        session.current_display_queue = queue.Queue()
        session.current_started_at = time.time() - 20

        with patch.object(ai8video_chat_service.time, "time", side_effect=[100.0, 111.0, 111.0, 111.0, 111.0]):
            with self.assertRaises(TimeoutError):
                session.handle_message("2集", timeout_seconds=1)

        status = session.snapshot_status()
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["reply"]["stage"], "error")
        self.assertEqual(status["reply"]["meta"]["operation"], "timeout")
        self.assertIn("没有进入真实视频提交阶段", status["reply"]["text"])

    def test_local_timeout_terminal_progress_cannot_be_reactivated(self) -> None:
        session_id = "session-status-local-timeout-terminal"
        episodes = [
            EpisodePrompt(index=1, title="第一条", prompt="ep1"),
            EpisodePrompt(index=2, title="第二条", prompt="ep2"),
        ]
        start_generation_progress(session_id, episodes, concurrent=True)
        try:
            snapshot = stop_unsubmitted_generation_progress(session_id, get_generation_progress(session_id), "本地任务超时")
            self.assertEqual(snapshot["status"], "failed")

            mark_job_preparing_first_frame(session_id, episodes[0])
            start_generation_progress(session_id, episodes, concurrent=True)
            mark_job_submitted(session_id, episodes[0], QuickVideoJob(episode_index=1, job_id="job-should-not-appear"))

            progress = get_generation_progress(session_id)
            self.assertEqual(progress["status"], "failed")
            self.assertEqual(progress["failedCount"], 2)
            self.assertEqual(progress["submittedCount"], 0)
            self.assertEqual([item["status"] for item in progress["items"]], ["failed", "failed"])
            self.assertTrue(all(item.get("jobId") is None for item in progress["items"]))
        finally:
            clear_generation_progress(session_id)

    def test_trace_recovered_timeout_blocks_late_generation_start(self) -> None:
        session_id = "session-status-trace-timeout-terminal"
        episodes = [
            EpisodePrompt(index=1, title="第一条", prompt="ep1"),
            EpisodePrompt(index=2, title="第二条", prompt="ep2"),
        ]
        trace_progress = {
            "sessionId": session_id,
            "status": "planning",
            "totalRequested": 2,
            "items": [
                {"episodeIndex": 1, "title": "视频 1", "status": "planning"},
                {"episodeIndex": 2, "title": "视频 2", "status": "planning"},
            ],
        }
        try:
            stop_unsubmitted_generation_progress(session_id, trace_progress, "本地任务超时")
            start_generation_progress(session_id, episodes, concurrent=True)
            mark_job_preparing_first_frame(session_id, episodes[0])

            progress = get_generation_progress(session_id)
            self.assertEqual(progress["status"], "failed")
            self.assertEqual(progress["totalRequested"], 2)
            self.assertEqual([item["providerStatus"] for item in progress["items"]], ["local_timeout", "local_timeout"])
        finally:
            clear_generation_progress(session_id)

    def test_snapshot_status_not_blocked_while_handle_message_waits_for_worker(self) -> None:
        session = ai8video_chat_service._AI8VideoSession("live-status")
        started = threading.Event()
        release = threading.Event()
        episodes = [
            EpisodePrompt(index=1, title="第一集", prompt="ep1"),
            EpisodePrompt(index=2, title="第二集", prompt="ep2"),
        ]

        def fake_handle_chat_message(*, session_id: str, message: str, refresh: bool) -> dict:
            run_id = generation_batch_context.get_current_generation_batch_id()
            start_generation_progress(
                session_id,
                episodes,
                concurrent=True,
                generation_batch_id=run_id,
            )
            started.set()
            release.wait(timeout=2)
            return {
                "reply": {
                    "text": "已完成",
                    "stage": "completed",
                    "meta": {"operation": "generate"},
                    "result": {"episodes": [{}, {}], "jobs": []},
                },
                "result": {"episodes": [{}, {}], "jobs": []},
            }

        result_holder: dict[str, object] = {}

        def run_handle_message() -> None:
            try:
                result_holder["payload"] = session.handle_message("2集", timeout_seconds=5)
            except BaseException as exc:
                result_holder["error"] = exc

        with patch.object(ai8video_chat_service, "handle_chat_message", fake_handle_chat_message):
            worker = threading.Thread(target=run_handle_message)
            worker.start()
            self.assertTrue(started.wait(timeout=1))

            status_holder: dict[str, object] = {}
            status_thread = threading.Thread(target=lambda: status_holder.update(status=session.snapshot_status()))
            status_thread.start()
            status_thread.join(timeout=0.2)
            release.set()
            worker.join(timeout=1)

        try:
            self.assertFalse(status_thread.is_alive())
            status = status_holder["status"]
            self.assertEqual(status["status"], "pending")
            generation_batch_id = status.get("generationBatchId")
            self.assertIsInstance(generation_batch_id, str)
            self.assertTrue(str(generation_batch_id).startswith("gb-live-status-"))
            self.assertEqual(status["generationProgress"]["totalRequested"], 2)
            self.assertEqual(status["generationProgress"]["generationBatchId"], generation_batch_id)
            self.assertTrue(status["generationProgress"]["concurrent"])
            self.assertNotIn("error", result_holder)
            self.assertEqual(result_holder["payload"]["generationBatchId"], generation_batch_id)
        finally:
            clear_generation_progress("live-status")

    def test_get_chat_status_rejects_unknown_generation_batch_id(self) -> None:
        session_id = "session-status-known-batch"
        session = ai8video_chat_service._AI8VideoSession(session_id)
        session.current_generation_batch_id = "gb-known-batch"
        with ai8video_chat_service._SESSIONS_LOCK:
            ai8video_chat_service._SESSIONS[session_id] = session
        try:
            status = ai8video_chat_service.get_chat_status_via_ai8video(
                session_id,
                generation_batch_id="gb-missing-batch",
            )

            self.assertEqual(status["status"], "not_found")
            self.assertEqual(status["phase"], "unknown_generation_batch")
            self.assertEqual(status["generationBatchId"], "gb-missing-batch")
            self.assertEqual(status["currentGenerationBatchId"], "gb-known-batch")
        finally:
            with ai8video_chat_service._SESSIONS_LOCK:
                ai8video_chat_service._SESSIONS.pop(session_id, None)

    def test_get_chat_status_recovers_ledger_snapshot_without_memory_session(self) -> None:
        session_id = "session-status-ledger-recovery"
        generation_batch_id = "gb-session-status-ledger-recovery"

        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.upsert_generation_batch(
                session_id=session_id,
                generation_batch_id=generation_batch_id,
                status="active",
                phase="generating",
                progress={
                    "sessionId": session_id,
                    "generationBatchId": generation_batch_id,
                    "status": "active",
                    "totalRequested": 1,
                    "runningCount": 1,
                    "items": [
                        {
                            "episodeIndex": 1,
                            "status": "submitted",
                            "jobId": "job-ledger-recovery-001",
                        }
                    ],
                },
            )
            with patch.object(generation_progress, "_TASK_LEDGER", ledger):
                with ai8video_chat_service._SESSIONS_LOCK:
                    ai8video_chat_service._SESSIONS.pop(session_id, None)

                status = ai8video_chat_service.get_chat_status_via_ai8video(session_id)

                with ai8video_chat_service._SESSIONS_LOCK:
                    memory_session_created = session_id in ai8video_chat_service._SESSIONS

        self.assertEqual(status["status"], "recovered")
        self.assertEqual(status["phase"], "read_only_recovery")
        self.assertEqual(status["generationBatchId"], generation_batch_id)
        self.assertTrue(status["readOnlyRecovery"])
        self.assertFalse(status["willResumeGeneration"])
        self.assertTrue(status["statelessProgress"])
        self.assertTrue(status["stalePending"])
        self.assertEqual(status["ledgerSnapshot"]["status"], "active")
        self.assertEqual(status["generationProgress"]["items"][0]["jobId"], "job-ledger-recovery-001")
        self.assertTrue(status["generationProgress"]["readOnlyRecovery"])
        self.assertFalse(status["generationProgress"]["willResumeGeneration"])
        self.assertFalse(memory_session_created)

    def test_get_chat_status_rejects_unknown_ledger_batch_without_memory_session(self) -> None:
        session_id = "session-status-ledger-unknown-batch"

        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.upsert_generation_batch(
                session_id=session_id,
                generation_batch_id="gb-session-status-ledger-known",
                status="active",
            )
            with patch.object(generation_progress, "_TASK_LEDGER", ledger):
                with ai8video_chat_service._SESSIONS_LOCK:
                    ai8video_chat_service._SESSIONS.pop(session_id, None)

                status = ai8video_chat_service.get_chat_status_via_ai8video(
                    session_id,
                    generation_batch_id="gb-session-status-ledger-missing",
                )

        self.assertEqual(status["status"], "not_found")
        self.assertEqual(status["phase"], "unknown_generation_batch")
        self.assertEqual(status["generationBatchId"], "gb-session-status-ledger-missing")
        self.assertIsNone(status["currentGenerationBatchId"])
        self.assertNotIn("ledgerSnapshot", status)

    def test_stale_worker_result_does_not_overwrite_current_batch_payload(self) -> None:
        session = ai8video_chat_service._AI8VideoSession("late-worker-session")
        session.current_generation_batch_id = "gb-current-batch"
        display_queue = queue.Queue()
        payload = {
            "status": "completed",
            "reply": {"stage": "completed", "text": "old result"},
        }

        with patch.object(ai8video_chat_service, "handle_chat_message", return_value=payload):
            session._run_runtime_chat("旧任务", display_queue, "gb-old-batch")

        queue_item = display_queue.get_nowait()
        self.assertEqual(queue_item["generationBatchId"], "gb-old-batch")
        self.assertEqual(queue_item["payload"]["generationBatchId"], "gb-old-batch")
        self.assertIsNone(session.latest_ai8video_payload)
        self.assertIsNone(session.latest_error)

    def test_snapshot_status_reports_worker_error_as_completed_error_payload(self) -> None:
        session = self._build_session()
        session.worker_thread = SimpleNamespace(is_alive=lambda: False)
        session.latest_error = RuntimeError("图片图生图提交失败")
        session.current_display_queue.put({"done": True})

        status = session.snapshot_status()

        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["reply"]["stage"], "error")
        self.assertEqual(status["reply"]["meta"]["errorType"], "RuntimeError")
        self.assertIn("图片图生图提交失败", status["reply"]["text"])

    def test_snapshot_status_humanizes_remote_disconnected_worker_error(self) -> None:
        session = self._build_session()
        session.worker_thread = SimpleNamespace(is_alive=lambda: False)
        session.latest_error = ConnectionError(
            "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
        )
        session.current_display_queue.put({"done": True})

        status = session.snapshot_status()

        self.assertEqual(status["reply"]["stage"], "error")
        self.assertIn("生成服务连接中断", status["reply"]["text"])
        self.assertIn("结果区/回收站", status["reply"]["text"])
        self.assertNotIn("RemoteDisconnected", status["reply"]["text"])
        self.assertNotIn("Connection aborted", status["reply"]["text"])
        self.assertIn("RemoteDisconnected", status["error"]["rawMessage"])

    def test_snapshot_status_includes_backend_generation_progress(self) -> None:
        session = self._build_session()
        episodes = [
            EpisodePrompt(index=1, title="第一集", prompt="ep1"),
            EpisodePrompt(index=2, title="第二集", prompt="ep2"),
        ]
        start_generation_progress("session-status", episodes, concurrent=True)
        mark_job_submitted(
            "session-status",
            episodes[0],
            QuickVideoJob(episode_index=1, job_id="job-1"),
        )
        try:
            status = session.snapshot_status()
        finally:
            clear_generation_progress("session-status")

        progress = status["generationProgress"]
        self.assertEqual(progress["totalRequested"], 2)
        self.assertEqual(progress["submittedCount"], 1)
        self.assertEqual(progress["waitingCount"], 1)
        self.assertEqual(progress["items"][0]["jobId"], "job-1")

    def test_snapshot_status_includes_task_ledger_snapshot_for_current_batch(self) -> None:
        session = self._build_session()
        episodes = [EpisodePrompt(index=1, title="第一集", prompt="ep1")]
        generation_batch_id = create_generation_batch_id("session-status")
        session.current_generation_batch_id = generation_batch_id

        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            with patch.object(generation_progress, "_TASK_LEDGER", ledger):
                try:
                    start_generation_progress("session-status", episodes, generation_batch_id=generation_batch_id)
                    mark_job_submitted(
                        "session-status",
                        episodes[0],
                        QuickVideoJob(episode_index=1, job_id="job-ledger-status-001"),
                    )
                    status = session.snapshot_status()
                finally:
                    clear_generation_progress("session-status")

        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["ledgerSnapshot"]["generationBatchId"], generation_batch_id)
        self.assertEqual(status["ledgerSnapshot"]["sessionId"], "session-status")
        self.assertEqual(status["ledgerSnapshot"]["progress"]["items"][0]["jobId"], "job-ledger-status-001")
        self.assertEqual(status["generationProgress"]["generationBatchId"], generation_batch_id)

    def test_snapshot_status_attaches_result_reconciliation(self) -> None:
        session = self._build_session()
        episodes = [EpisodePrompt(index=1, title="第一集", prompt="ep1")]
        expected_reconciliation = {
            "generationBatchId": "gb-reconciliation-current",
            "summary": {"conflicts": 0},
        }
        start_generation_progress(
            "session-status",
            episodes,
            generation_batch_id="gb-reconciliation-current",
        )
        try:
            with patch.object(
                ai8video_chat_service,
                "_build_result_reconciliation",
                return_value=expected_reconciliation,
            ):
                status = session.snapshot_status()
        finally:
            clear_generation_progress("session-status")

        self.assertEqual(status["resultReconciliation"], expected_reconciliation)
        self.assertEqual(status["status"], "pending")

    def test_read_only_recovery_attaches_result_reconciliation(self) -> None:
        ledger_snapshot = {
            "sessionId": "session-reconciliation-recovery",
            "generationBatchId": "gb-reconciliation-recovery",
            "status": "completed",
            "progress": {
                "generationBatchId": "gb-reconciliation-recovery",
                "status": "completed",
                "items": [{"episodeIndex": 1, "jobId": "job-1", "status": "succeeded"}],
            },
        }
        expected_reconciliation = {
            "generationBatchId": "gb-reconciliation-recovery",
            "summary": {"conflicts": 0},
        }

        with patch.object(
            ai8video_chat_service,
            "get_generation_ledger_snapshot",
            return_value=ledger_snapshot,
        ), patch.object(
            ai8video_chat_service,
            "_build_result_reconciliation",
            return_value=expected_reconciliation,
        ):
            status = ai8video_chat_service._recover_chat_status_from_ledger(
                "session-reconciliation-recovery",
                "gb-reconciliation-recovery",
            )

        self.assertEqual(status["status"], "recovered")
        self.assertTrue(status["readOnlyRecovery"])
        self.assertFalse(status["willResumeGeneration"])
        self.assertEqual(status["resultReconciliation"], expected_reconciliation)

    def test_result_reconciliation_exposes_asset_store_error(self) -> None:
        config = SimpleNamespace(asset_store_path="/missing/assets.jsonl")
        progress = {"generationBatchId": "gb-reconciliation-error", "items": []}

        with patch.object(JsonlAssetStore, "read_all", side_effect=ValueError("资产记录损坏")):
            result = ai8video_chat_service._build_result_reconciliation(progress, config)

        self.assertEqual(result["generationBatchId"], "gb-reconciliation-error")
        self.assertEqual(result["error"], "资产记录损坏")

    def test_snapshot_status_reads_latest_task_ledger_snapshot_without_memory_progress(self) -> None:
        session = self._build_session()
        session.current_generation_batch_id = None
        session.worker_thread = SimpleNamespace(is_alive=lambda: False)
        session.current_display_queue = None
        session.background_delivery_pending = False

        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.upsert_generation_batch(
                session_id="session-status",
                generation_batch_id="gb-session-status-ledger-only",
                status="active",
                phase="generating",
                progress={"items": [{"status": "submitted", "jobId": "job-ledger-only"}]},
            )
            with patch.object(generation_progress, "_TASK_LEDGER", ledger):
                status = session.snapshot_status()

        self.assertEqual(status["status"], "idle")
        self.assertNotIn("generationProgress", status)
        self.assertEqual(status["ledgerSnapshot"]["generationBatchId"], "gb-session-status-ledger-only")
        self.assertEqual(status["ledgerSnapshot"]["progress"]["items"][0]["jobId"], "job-ledger-only")

    def test_snapshot_status_keeps_pre_submit_progress_as_planning(self) -> None:
        session = self._build_session()
        episodes = [
            EpisodePrompt(index=1, title="第一集", prompt="ep1"),
            EpisodePrompt(index=2, title="第二集", prompt="ep2"),
        ]
        start_generation_progress("session-status", episodes, concurrent=True)
        try:
            status = session.snapshot_status()
        finally:
            clear_generation_progress("session-status")

        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["phase"], "planning")
        self.assertIn("规划剧本", status["statusLabel"])
        self.assertEqual(status["generationProgress"]["submittedCount"], 0)
        self.assertTrue(all(item["jobId"] is None for item in status["generationProgress"]["items"]))

    def test_snapshot_status_keeps_failed_progress_pending_while_jobs_are_active(self) -> None:
        session = self._build_session()
        episodes = [
            EpisodePrompt(index=1, title="第一集", prompt="ep1"),
            EpisodePrompt(index=2, title="第二集", prompt="ep2"),
        ]
        start_generation_progress("session-status", episodes, concurrent=True)
        mark_job_failed("session-status", 1, "官方负载大")
        mark_job_submitted(
            "session-status",
            episodes[1],
            QuickVideoJob(episode_index=2, job_id="job-2"),
        )
        fail_generation_progress("session-status", "部分任务失败", skip_pending=False)
        try:
            status = session.snapshot_status()
        finally:
            clear_generation_progress("session-status")

        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["phase"], "generating")
        self.assertEqual(status["statusLabel"], "真实视频生成中，部分任务已失败")
        self.assertEqual(status["generationProgress"]["status"], "active")
        self.assertEqual(status["generationProgress"]["failedCount"], 1)
        self.assertEqual(status["generationProgress"]["runningCount"], 1)
        self.assertIsNone(status["generationProgress"].get("completedAt"))
        self.assertNotIn("error", status["generationProgress"])

    def test_generation_progress_does_not_keep_global_error_while_first_frame_is_active(self) -> None:
        episodes = [
            EpisodePrompt(index=1, title="第一集", prompt="ep1"),
            EpisodePrompt(index=2, title="第二集", prompt="ep2"),
        ]
        start_generation_progress("session-first-frame-active", episodes, concurrent=True)
        try:
            mark_job_preparing_first_frame("session-first-frame-active", episodes[0])
            fail_generation_progress(
                "session-first-frame-active",
                "图生图阶段超过 240 秒没有任何视频任务提交。本轮已判定为后台卡死。",
                skip_pending=False,
            )
            progress = get_generation_progress("session-first-frame-active")
        finally:
            clear_generation_progress("session-first-frame-active")

        self.assertIsNotNone(progress)
        self.assertEqual(progress["status"], "active")
        self.assertEqual(progress["runningCount"], 1)
        self.assertEqual(progress["waitingCount"], 1)
        self.assertNotIn("error", progress)

    def test_stale_first_frame_timeout_scales_with_requested_count(self) -> None:
        episodes = [EpisodePrompt(index=index, title=f"第 {index} 集", prompt=f"ep{index}") for index in range(1, 11)]
        start_generation_progress("session-first-frame-scaled-timeout", episodes, concurrent=True)
        for episode in episodes:
            mark_job_preparing_first_frame("session-first-frame-scaled-timeout", episode)
        try:
            with patch("ai8video.generation.generation_progress.time.time", return_value=time.time() + 300):
                early = settle_stale_first_frame_progress("session-first-frame-scaled-timeout")
            progress = get_generation_progress("session-first-frame-scaled-timeout")
        finally:
            clear_generation_progress("session-first-frame-scaled-timeout")

        self.assertIsNone(early)
        self.assertIsNotNone(progress)
        self.assertEqual(progress["status"], "active")
        self.assertEqual(progress["runningCount"], 10)

    def test_generation_progress_humanizes_first_frame_response_lost(self) -> None:
        episodes = [EpisodePrompt(index=1, title="第一集", prompt="ep1")]
        start_generation_progress("session-first-frame-lost", episodes, concurrent=True)
        mark_job_preparing_first_frame("session-first-frame-lost", episodes[0])
        mark_job_failed(
            "session-first-frame-lost",
            1,
            "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))",
        )
        fail_generation_progress("session-first-frame-lost", "首帧失败", skip_pending=False)
        try:
            progress = get_generation_progress("session-first-frame-lost")
        finally:
            clear_generation_progress("session-first-frame-lost")

        self.assertIsNotNone(progress)
        self.assertEqual(progress["submittedCount"], 0)
        item = progress["items"][0]
        self.assertEqual(item["statusLabel"], "首帧图未回填")
        self.assertEqual(item["providerStatus"], "first_frame_response_lost")
        self.assertIn("首帧图接口在等待图生图结果时断开", item["error"])
        self.assertIn("本地没有拿到图片 URL", item["error"])
        self.assertIn("不会用原图冒充成功", item["error"])
        self.assertIn("可能仍会完成并扣费", item["error"])
        self.assertNotIn("真实结果回填为准", item["error"])
        self.assertNotIn("视频任务没有提交", item["error"])
        self.assertNotIn("RemoteDisconnected", item["error"])

    def test_generation_progress_treats_ssleof_during_first_frame_as_response_lost(self) -> None:
        episodes = [EpisodePrompt(index=1, title="第一集", prompt="ep1")]
        start_generation_progress("session-first-frame-ssleof", episodes, concurrent=True)
        mark_job_preparing_first_frame("session-first-frame-ssleof", episodes[0])
        mark_job_failed(
            "session-first-frame-ssleof",
            1,
            "HTTPSConnectionPool(host='api.example.com', port=443): Max retries exceeded with url: /v1/chat/completions "
            "(Caused by SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1000)')))",
        )
        fail_generation_progress("session-first-frame-ssleof", "首帧失败", skip_pending=False)
        try:
            progress = get_generation_progress("session-first-frame-ssleof")
        finally:
            clear_generation_progress("session-first-frame-ssleof")

        self.assertIsNotNone(progress)
        item = progress["items"][0]
        self.assertEqual(item["statusLabel"], "首帧图未回填")
        self.assertEqual(item["providerStatus"], "first_frame_response_lost")
        self.assertIn("本地没有拿到图片 URL", item["error"])

    def test_generation_progress_keeps_real_job_id_when_merge_failure_uses_local_placeholder(self) -> None:
        episodes = [EpisodePrompt(index=1, title="第一集", prompt="ep1")]
        start_generation_progress("session-real-job-preserve", episodes, concurrent=True)
        mark_job_submitted(
            "session-real-job-preserve",
            episodes[0],
            QuickVideoJob(episode_index=1, job_id="job-real-1"),
        )
        mark_job_failed(
            "session-real-job-preserve",
            1,
            "内容审核未通过",
            job_id="merge2-failed-1",
        )
        try:
            progress = get_generation_progress("session-real-job-preserve")
        finally:
            clear_generation_progress("session-real-job-preserve")

        self.assertIsNotNone(progress)
        item = progress["items"][0]
        self.assertEqual(item["jobId"], "job-real-1")
        self.assertEqual(progress["submittedCount"], 1)

    def test_snapshot_status_prefers_live_progress_over_background_final_payload_while_jobs_still_archiving(self) -> None:
        session = self._build_session()
        session.worker_thread = SimpleNamespace(is_alive=lambda: False)
        session.current_display_queue = None
        session.background_final_payload = {
            "reply": {
                "text": "本轮失败 2/2 条",
                "stage": "completed",
                "result": {"assetRecords": []},
            },
            "status": "completed",
        }
        session.background_completed_at = time.time()
        episodes = [
            EpisodePrompt(index=1, title="第一集", prompt="ep1"),
            EpisodePrompt(index=2, title="第二集", prompt="ep2"),
        ]
        start_generation_progress("session-status", episodes, concurrent=True)
        mark_job_failed("session-status", 1, "官方负载大")
        mark_job_polling(
            "session-status",
            QuickVideoJob(
                episode_index=2,
                job_id="job-2",
                status="succeeded",
                provider_status="completed",
                provider_progress=99,
                video_url="https://example.test/video.mp4",
            ),
        )
        fail_generation_progress("session-status", "部分任务失败", skip_pending=False)
        try:
            status = session.snapshot_status()
        finally:
            clear_generation_progress("session-status")

        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["phase"], "postprocessing")
        self.assertEqual(status["statusLabel"], "后台处理中")
        progress = status["generationProgress"]
        self.assertEqual(progress["status"], "active")
        self.assertEqual(progress["runningCount"], 1)
        self.assertEqual(progress["postProcessingCount"], 1)
        self.assertEqual(progress["failedCount"], 1)
        self.assertEqual(progress["items"][1]["status"], "archiving")
        self.assertEqual(progress["items"][1]["statusLabel"], "后台处理中")
        self.assertIsNone(progress.get("completedAt"))

    def test_generation_progress_keeps_provider_progress_like_aimanju(self) -> None:
        episodes = [EpisodePrompt(index=1, title="第一集", prompt="ep1")]
        start_generation_progress("session-status", episodes, concurrent=False)
        try:
            mark_job_polling(
                "session-status",
                QuickVideoJob(
                    episode_index=1,
                    job_id="job-1",
                    provider_status="processing",
                    provider_progress=12,
                ),
            )
            mark_job_polling(
                "session-status",
                QuickVideoJob(
                    episode_index=1,
                    job_id="job-1",
                    provider_status="processing",
                    provider_progress=48,
                ),
            )
            mark_job_polling(
                "session-status",
                QuickVideoJob(
                    episode_index=1,
                    job_id="job-1",
                    provider_status="processing",
                    provider_progress=41,
                ),
            )
            progress = self._build_session().snapshot_status()["generationProgress"]
        finally:
            clear_generation_progress("session-status")

        item = progress["items"][0]
        self.assertEqual(item["providerStatus"], "processing")
        self.assertEqual(item["providerProgress"], 48)
        self.assertEqual(item["statusLabel"], "真实生成进度 48%")

    def test_generation_progress_marks_provider_completed_as_post_processing_until_archived(self) -> None:
        episodes = [EpisodePrompt(index=1, title="第一集", prompt="ep1")]
        start_generation_progress("session-status", episodes, concurrent=False)
        try:
            mark_job_polling(
                "session-status",
                QuickVideoJob(
                    episode_index=1,
                    job_id="job-1",
                    status="succeeded",
                    video_url="https://example.com/video.mp4",
                    provider_status="completed",
                    provider_progress=100,
                ),
            )
            progress = get_generation_progress("session-status")
        finally:
            clear_generation_progress("session-status")

        item = progress["items"][0]
        self.assertEqual(item["status"], "archiving")
        self.assertEqual(item["statusLabel"], "后台处理中")
        self.assertEqual(item["videoUrl"], "https://example.com/video.mp4")
        self.assertEqual(progress["succeededCount"], 0)
        self.assertEqual(progress["runningCount"], 1)
        self.assertEqual(progress["postProcessingCount"], 1)

    def test_snapshot_status_refreshes_active_provider_jobs(self) -> None:
        class FakeClient:
            def __init__(self, config):
                self.config = config

            def get_job(self, job_id, episode_index=1, prompt=""):
                return QuickVideoJob(
                    episode_index=episode_index,
                    job_id=job_id,
                    status="succeeded",
                    video_url="https://example.com/provider.mp4",
                    provider_status="completed",
                    provider_progress=100,
                )

        session = self._build_session()
        session.config = AI8VideoConfig(dry_run=True)
        episodes = [EpisodePrompt(index=1, title="第一集", prompt="ep1")]
        start_generation_progress("session-status", episodes, concurrent=False)
        mark_job_submitted(
            "session-status",
            episodes[0],
            QuickVideoJob(episode_index=1, job_id="job-1"),
        )
        try:
            with patch.object(ai8video_chat_service, "AI8VideoModelClient", FakeClient):
                progress = session.snapshot_status()["generationProgress"]
        finally:
            clear_generation_progress("session-status")

        item = progress["items"][0]
        self.assertEqual(item["status"], "archiving")
        self.assertEqual(item["statusLabel"], "后台处理中")
        self.assertEqual(item["videoUrl"], "https://example.com/provider.mp4")
        self.assertEqual(progress["succeededCount"], 0)
        self.assertEqual(progress["runningCount"], 1)
        self.assertEqual(progress["postProcessingCount"], 1)

    def test_cancel_generation_progress_marks_unfinished_items_skipped(self) -> None:
        episodes = [
            EpisodePrompt(index=1, title="第一集", prompt="ep1"),
            EpisodePrompt(index=2, title="第二集", prompt="ep2"),
        ]
        start_generation_progress("session-status", episodes, concurrent=True)
        mark_job_submitted(
            "session-status",
            episodes[0],
            QuickVideoJob(episode_index=1, job_id="job-1"),
        )
        try:
            progress = cancel_generation_progress("session-status")
        finally:
            clear_generation_progress("session-status")

        self.assertEqual(progress["status"], "cancelled")
        self.assertEqual(progress["runningCount"], 0)
        self.assertEqual(progress["waitingCount"], 0)
        self.assertEqual(progress["skippedCount"], 2)
        self.assertEqual([item["status"] for item in progress["items"]], ["skipped", "skipped"])

    def test_cancel_current_returns_cancelled_snapshot(self) -> None:
        session = self._build_session()
        episodes = [EpisodePrompt(index=1, title="第一集", prompt="ep1")]
        start_generation_progress("session-status", episodes, concurrent=False)
        mark_job_submitted(
            "session-status",
            episodes[0],
            QuickVideoJob(episode_index=1, job_id="job-1"),
        )
        try:
            status = session.cancel_current()
        finally:
            clear_generation_progress("session-status")

        self.assertEqual(status["status"], "cancelled")
        self.assertEqual(status["statusLabel"], "已强行终止")
        self.assertEqual(status["generationProgress"]["status"], "cancelled")
        self.assertEqual(status["generationProgress"]["items"][0]["statusLabel"], "已取消")

    def test_snapshot_status_reports_completed_after_background_finish(self) -> None:
        session = self._build_session()
        session.worker_thread = SimpleNamespace(is_alive=lambda: False)
        session.latest_ai8video_payload = {
            "reply": {
                "text": "已生成完成",
                "stage": "completed",
                "meta": {"operation": "generate"},
                "result": {"jobs": []},
            },
            "summary": {"episodeCount": 1},
            "result": {"episodes": []},
        }
        session.current_display_queue.put({"done": "ok"})

        status = session.snapshot_status()

        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["reply"]["stage"], "completed")
        self.assertEqual(status["summary"]["episodeCount"], 1)
        self.assertIsNotNone(status["completedAt"])

    def test_snapshot_status_reports_synchronous_collecting_payload_as_completed(self) -> None:
        session = self._build_session()
        session.background_delivery_pending = False
        session.current_display_queue = None
        session.worker_thread = SimpleNamespace(is_alive=lambda: False)
        session.background_final_payload = {
            "reply": {
                "text": "已识别要生成 10 条视频。",
                "stage": "collecting",
                "awaiting": "core_keywords",
                "meta": {"operation": "collect"},
                "result": None,
            },
            "chatBackend": "ai8video-runtime",
        }
        session.background_completed_at = time.time()

        status = session.snapshot_status()

        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["reply"]["stage"], "collecting")
        self.assertEqual(status["reply"]["awaiting"], "core_keywords")
        self.assertEqual(status["chatBackend"], "ai8video-runtime")
        self.assertIsNotNone(status["completedAt"])

    def test_snapshot_status_uses_runtime_snapshot_when_agent_tail_blocks(self) -> None:
        session = self._build_session()
        cached = {
            "reply": {
                "text": "已生成完成",
                "stage": "completed",
                "meta": {"operation": "generate"},
                "result": {"jobs": []},
            },
            "summary": {"episodeCount": 2},
            "result": {"episodes": [{}, {}]},
        }

        with patch.object(ai8video_chat_service, "get_chat_snapshot", return_value=cached):
            status = session.snapshot_status()

        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["reply"]["stage"], "completed")
        self.assertEqual(status["summary"]["episodeCount"], 2)
        self.assertEqual(status["chatBackend"], "ai8video-runtime")

    def test_start_task_clears_stale_runtime_snapshot(self) -> None:
        session = self._build_session()

        class ImmediateThread:
            def __init__(self, target, args=(), daemon=None):
                self.target = target
                self.args = args
                self.daemon = daemon

            def start(self):
                return None

            def is_alive(self):
                return False

        with patch.object(ai8video_chat_service, "clear_chat_snapshot") as clear_snapshot, \
                patch.object(ai8video_chat_service.threading, "Thread", ImmediateThread):
            session._start_task("2")

        clear_snapshot.assert_called_once_with("session-status")
        self.assertEqual(session.current_message, "2")

    def test_get_chat_status_does_not_create_session(self) -> None:
        sessions_backup = ai8video_chat_service._SESSIONS
        lock_backup = ai8video_chat_service._SESSIONS_LOCK
        ai8video_chat_service._SESSIONS = {}
        ai8video_chat_service._SESSIONS_LOCK = threading.Lock()
        try:
            status = ai8video_chat_service.get_chat_status_via_ai8video("missing-session")
        finally:
            ai8video_chat_service._SESSIONS = sessions_backup
            ai8video_chat_service._SESSIONS_LOCK = lock_backup

        self.assertEqual(status, {"status": "idle", "sessionId": "missing-session", "stalePending": True})


if __name__ == "__main__":
    unittest.main()
