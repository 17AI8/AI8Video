from __future__ import annotations

import tempfile
import unittest

from ai8video.generation.real_generation_guard import RealGenerationGuard


class RealGenerationGuardTest(unittest.TestCase):
    def test_default_guard_is_disabled(self):
        with tempfile.TemporaryDirectory() as tempdir:
            guard = RealGenerationGuard(path=f"{tempdir}/jobs.jsonl")
            self.assertFalse(guard.enabled())
            guard.assert_can_create_count(10)
            guard.record_job(job_id="job-1", video_index=1, prompt="first")
            self.assertEqual(guard.snapshot()["usedInWindow"], 0)

    def test_allows_two_and_blocks_third_within_window(self):
        with tempfile.TemporaryDirectory() as tempdir:
            guard = RealGenerationGuard(
                path=f"{tempdir}/jobs.jsonl",
                max_jobs_per_window=2,
                window_seconds=3600,
                forced_duration_seconds=10,
            )
            guard.assert_can_create()
            guard.record_job(job_id="job-1", video_index=1, prompt="first")
            guard.assert_can_create()
            guard.record_job(job_id="job-2", video_index=2, prompt="second")
            with self.assertRaisesRegex(RuntimeError, "最多生成 2 条 10 秒视频"):
                guard.assert_can_create()


if __name__ == "__main__":
    unittest.main()
