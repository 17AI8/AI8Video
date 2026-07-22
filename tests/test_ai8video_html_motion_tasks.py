from __future__ import annotations

import threading
import time
import unittest

from ai8video.media.motion.html_motion_tasks import HtmlMotionTaskService


class HtmlMotionTaskServiceTest(unittest.TestCase):
    def test_preview_ready_task_is_not_rewritten_by_late_cancel(self) -> None:
        service = HtmlMotionTaskService()
        task = service.submit(
            "video/demo.mp4",
            lambda **_kwargs: {
                "htmlMotionOverlay": {
                    "status": "preview_ready",
                    "reason": "ready",
                },
            },
        )

        snapshot = _wait_for_terminal(service, task["taskId"])
        self.assertEqual(snapshot["status"], "preview_ready")
        after_cancel = service.cancel(task["taskId"])
        self.assertEqual(after_cancel["status"], "preview_ready")

    def test_get_active_returns_in_flight_task_until_terminal(self) -> None:
        service = HtmlMotionTaskService()
        started = threading.Event()
        release = threading.Event()

        def target(**_kwargs):
            started.set()
            release.wait(2)
            return {
                "htmlMotionOverlay": {
                    "status": "preview_ready",
                    "reason": "ready",
                },
            }

        task = service.submit("video/active.mp4", target)
        self.assertTrue(started.wait(1.0))
        active = service.get_active("video/active.mp4")
        self.assertIsNotNone(active)
        self.assertEqual(active["taskId"], task["taskId"])
        self.assertFalse(
            active["status"] in {"preview_ready", "preview_failed", "failed", "cancelled"},
        )
        release.set()
        snapshot = _wait_for_terminal(service, task["taskId"])
        self.assertEqual(snapshot["status"], "preview_ready")
        self.assertIsNone(service.get_active("video/active.mp4"))

    def test_cancelled_task_reports_cancelled_without_running_target_to_completion(self) -> None:
        service = HtmlMotionTaskService()

        def target(*, cancel_event, **_kwargs):
            cancel_event.wait(2)
            raise RuntimeError("cancelled")

        task = service.submit("video/cancel.mp4", target)
        time.sleep(0.03)
        cancelled = service.cancel(task["taskId"])

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(cancelled["cancelRequested"])

    def test_snapshot_includes_elapsed_and_phase_timings(self) -> None:
        service = HtmlMotionTaskService()

        def target(*, stage_callback, **_kwargs):
            time.sleep(0.12)
            stage_callback("generating", {"message": "正在生成动效方案"})
            time.sleep(0.12)
            stage_callback("rendering", {"message": "正在渲染透明动画"})
            time.sleep(0.12)
            stage_callback("compositing", {"message": "正在合成预览画面"})
            time.sleep(0.12)
            return {
                "htmlMotionOverlay": {
                    "status": "preview_ready",
                    "reason": "ready",
                },
            }

        task = service.submit("video/timing.mp4", target)
        snapshot = _wait_for_terminal(service, task["taskId"], timeout=5.0)

        self.assertIn("elapsedSeconds", snapshot)
        self.assertIn("phaseElapsedSeconds", snapshot)
        self.assertIn("phaseTimings", snapshot)
        self.assertGreaterEqual(snapshot["elapsedSeconds"], 0.4)
        self.assertGreater(snapshot["phaseTimings"].get("generating", 0), 0)
        self.assertGreater(snapshot["phaseTimings"].get("rendering", 0), 0)
        self.assertGreater(snapshot["phaseTimings"].get("compositing", 0), 0)
        self.assertNotIn("preview_ready", snapshot["phaseTimings"])

    def test_retry_status_exposes_ai_audit_result(self) -> None:
        service = HtmlMotionTaskService()
        release = threading.Event()

        def target(*, stage_callback, **_kwargs):
            stage_callback("generating", {
                "retryCount": 1,
                "retryLimit": 5,
                "auditResult": "问句缺少真实痛点",
                "retryReason": "不应展示的详细规则",
                "attemptTrace": {"attempt": 1, "responseJson": {"audit": {"passed": True}}},
            })
            release.wait(1)
            return {"htmlMotionOverlay": {"status": "preview_ready"}}

        task = service.submit("video/audit.mp4", target)
        deadline = time.monotonic() + 1
        snapshot = service.get(task["taskId"])
        while not snapshot["auditResult"] and time.monotonic() < deadline:
            time.sleep(0.01)
            snapshot = service.get(task["taskId"])
        release.set()
        self.assertEqual(snapshot["auditResult"], "问句缺少真实痛点")
        self.assertIn("审核结果：问句缺少真实痛点", snapshot["message"])
        self.assertEqual(snapshot["attemptTraces"][0]["attempt"], 1)


def _wait_for_terminal(service: HtmlMotionTaskService, task_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = service.get(task_id)
        if snapshot and snapshot["status"] in {"preview_ready", "preview_failed", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("HTML 动效任务未在测试超时内结束")


if __name__ == "__main__":
    unittest.main()
