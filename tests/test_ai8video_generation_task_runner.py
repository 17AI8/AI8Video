from __future__ import annotations

import queue
import threading
import unittest

from ai8video.generation.generation_task_runner import GenerationTaskRunner


class GenerationTaskRunnerTest(unittest.TestCase):
    def test_start_exposes_task_identity_and_completion_state(self) -> None:
        runner = GenerationTaskRunner(worker_prefix="test-runtime")
        result_queue: queue.Queue = queue.Queue()
        observed: dict[str, str] = {}

        def target(task) -> None:
            observed["batch"] = task.generation_batch_id
            observed["worker"] = task.worker_id
            result_queue.put({"done": True, "generationBatchId": task.generation_batch_id})

        task = runner.start(
            "gb-runner-complete",
            target,
            result_queue=result_queue,
        )

        self.assertTrue(runner.join(task.generation_batch_id, timeout=1))
        self.assertEqual(task.state, "completed")
        self.assertEqual(observed["batch"], "gb-runner-complete")
        self.assertTrue(observed["worker"].startswith("test-runtime-"))
        self.assertEqual(result_queue.get_nowait()["generationBatchId"], "gb-runner-complete")

    def test_cancel_is_cooperative_and_visible_on_task(self) -> None:
        runner = GenerationTaskRunner()
        started = threading.Event()

        def target(task) -> None:
            started.set()
            task.cancel_event.wait(timeout=1)

        task = runner.start("gb-runner-cancel", target)
        self.assertTrue(started.wait(timeout=1))
        self.assertTrue(runner.cancel(task.generation_batch_id))
        self.assertTrue(runner.join(task.generation_batch_id, timeout=1))
        self.assertTrue(task.cancel_requested)
        self.assertEqual(task.state, "cancelled")

    def test_starting_a_new_batch_can_cancel_other_active_batches(self) -> None:
        runner = GenerationTaskRunner()
        started = threading.Event()

        def target(task) -> None:
            started.set()
            task.cancel_event.wait(timeout=1)

        old_task = runner.start("gb-runner-old", target)
        self.assertTrue(started.wait(timeout=1))
        cancelled = runner.cancel_active()
        self.assertEqual(cancelled, ["gb-runner-old"])
        self.assertTrue(runner.join(old_task.generation_batch_id, timeout=1))
        self.assertEqual(old_task.state, "cancelled")

    def test_cancel_releases_single_concurrency_slot_before_target_returns(self) -> None:
        runner = GenerationTaskRunner(max_concurrency=1)
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()

        def first_target(_task) -> None:
            first_started.set()
            release_first.wait(timeout=2)

        def second_target(_task) -> None:
            second_started.set()

        first = runner.start("gb-runner-blocking", first_target)
        self.assertTrue(first_started.wait(timeout=1))
        second = runner.start("gb-runner-next", second_target)
        self.assertFalse(second_started.wait(timeout=0.1))

        self.assertTrue(runner.cancel(first.generation_batch_id))
        self.assertTrue(second_started.wait(timeout=1))
        self.assertTrue(runner.join(second.generation_batch_id, timeout=1))

        release_first.set()
        self.assertTrue(runner.join(first.generation_batch_id, timeout=1))
        self.assertTrue(first.slot_released)
        self.assertEqual(first.state, "cancelled")

    def test_uncaught_target_error_is_delivered_once(self) -> None:
        runner = GenerationTaskRunner()
        result_queue: queue.Queue = queue.Queue()

        def target(_task) -> None:
            raise RuntimeError("runner boom")

        task = runner.start(
            "gb-runner-error",
            target,
            result_queue=result_queue,
        )
        self.assertTrue(runner.join(task.generation_batch_id, timeout=1))
        item = result_queue.get_nowait()
        self.assertTrue(item["runnerError"])
        self.assertIsInstance(item["error"], RuntimeError)
        with self.assertRaises(queue.Empty):
            result_queue.get_nowait()

    def test_start_rejects_missing_batch_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "generation_batch_id is required"):
            GenerationTaskRunner().start(" ", lambda _task: None)


if __name__ == "__main__":
    unittest.main()
