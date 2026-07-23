from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.assets.asset_store import JsonlAssetStore
from ai8video.generation.generation_batch_context import (
    reset_current_generation_batch_id,
    reset_current_generation_session_id,
    set_current_generation_batch_id,
    set_current_generation_session_id,
)
from ai8video.core.models import VideoPrompt, ParsedRequest, QuickVideoJob, GenerationOutcome


class AI8VideoAssetStoreTest(unittest.TestCase):
    def test_append_records_generation_outcome_without_scoring_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = JsonlAssetStore(f"{tempdir}/assets.jsonl")
            request = ParsedRequest(raw_text="生成一条视频", mode="single_video")
            video = VideoPrompt(index=1, title="单条视频", prompt="视频提示词")
            job = QuickVideoJob(
                video_index=1,
                job_id="job-1",
                status="succeeded",
                video_url="https://example.test/video.mp4",
            )
            outcome = GenerationOutcome(
                video_index=1,
                job_id="job-1",
                status="succeeded",
                decision="generated",
                reasons=[],
                meta={"kind": "generation_outcome"},
            )

            record = store.append(request, video, job, outcome)

            self.assertEqual(record["generationStatus"], "generated")
            self.assertEqual(record["generationReasons"], [])
            self.assertEqual(set(record).intersection({"score", "decision"}), set())

    def test_append_records_generation_batch_and_session_context(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = JsonlAssetStore(f"{tempdir}/assets.jsonl")
            request = ParsedRequest(raw_text="生成一条视频", mode="single_video")
            video = VideoPrompt(index=1, title="单条视频", prompt="视频提示词")
            job = QuickVideoJob(video_index=1, job_id="job-context", status="succeeded")
            outcome = GenerationOutcome(
                video_index=1,
                job_id="job-context",
                status="succeeded",
                decision="generated",
                reasons=[],
                meta={},
            )
            batch_token = set_current_generation_batch_id("gb-context-001")
            session_token = set_current_generation_session_id("session-context-001")
            try:
                record = store.append(request, video, job, outcome)
            finally:
                reset_current_generation_session_id(session_token)
                reset_current_generation_batch_id(batch_token)

            record_without_context = store.append(request, video, job, outcome)

        self.assertEqual(record["generationBatchId"], "gb-context-001")
        self.assertEqual(record["sessionId"], "session-context-001")
        self.assertIsNone(record_without_context["generationBatchId"])
        self.assertIsNone(record_without_context["sessionId"])

    def test_read_all_keeps_generation_records_available(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = JsonlAssetStore(f"{tempdir}/assets.jsonl")
            store.rewrite_all([
                {
                    "jobId": "old-job",
                    "generationStatus": "generated",
                }
            ])

            payload = store.read_all()

            self.assertEqual(payload[0]["jobId"], "old-job")
            self.assertNotIn("generationBatchId", payload[0])
            self.assertNotIn("sessionId", payload[0])

    def test_mutate_records_preserves_concurrent_append(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            asset_path = Path(tempdir) / "assets.jsonl"
            mutation_store = JsonlAssetStore(asset_path)
            append_store = JsonlAssetStore(asset_path)
            mutation_store.rewrite_all([{"jobId": "existing-job", "generationMeta": {}}])
            mutation_started = threading.Event()
            allow_mutation_to_finish = threading.Event()
            append_finished = threading.Event()
            thread_errors: list[BaseException] = []

            def mutate_existing_record(records: list[dict]) -> None:
                mutation_started.set()
                if not allow_mutation_to_finish.wait(timeout=2):
                    raise TimeoutError("测试未允许资产变更完成")
                records[0]["generationMeta"] = {"edited": True}

            def run_mutation() -> None:
                try:
                    mutation_store.mutate_records(mutate_existing_record)
                except BaseException as error:
                    thread_errors.append(error)

            request = ParsedRequest(raw_text="生成一条视频", mode="single_video")
            video = VideoPrompt(index=2, title="并发视频", prompt="并发提示词")
            job = QuickVideoJob(video_index=2, job_id="concurrent-job", status="succeeded")
            outcome = GenerationOutcome(
                video_index=2,
                job_id="concurrent-job",
                status="succeeded",
                decision="generated",
                reasons=[],
                meta={},
            )

            def run_append() -> None:
                try:
                    append_store.append(request, video, job, outcome)
                except BaseException as error:
                    thread_errors.append(error)
                finally:
                    append_finished.set()

            mutation_thread = threading.Thread(target=run_mutation)
            append_thread = threading.Thread(target=run_append)
            mutation_thread.start()
            self.assertTrue(mutation_started.wait(timeout=1))
            append_thread.start()
            self.assertFalse(append_finished.wait(timeout=0.05))
            allow_mutation_to_finish.set()
            mutation_thread.join(timeout=2)
            append_thread.join(timeout=2)

            self.assertFalse(mutation_thread.is_alive())
            self.assertFalse(append_thread.is_alive())
            self.assertEqual(thread_errors, [])
            records = mutation_store.read_all()

        self.assertEqual([record["jobId"] for record in records], ["existing-job", "concurrent-job"])
        self.assertEqual(records[0]["generationMeta"], {"edited": True})

    def test_rewrite_all_keeps_original_file_when_atomic_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            asset_path = Path(tempdir) / "assets.jsonl"
            store = JsonlAssetStore(asset_path)
            store.rewrite_all([{"jobId": "original-job"}])

            with patch.object(Path, "replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    store.rewrite_all([{"jobId": "replacement-job"}])

            records = store.read_all()
            remaining_paths = list(asset_path.parent.iterdir())

        self.assertEqual(records, [{"jobId": "original-job"}])
        self.assertEqual(remaining_paths, [asset_path])


if __name__ == "__main__":
    unittest.main()
