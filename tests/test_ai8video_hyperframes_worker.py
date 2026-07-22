from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ai8video.media.motion.hyperframes_worker import (
    HyperFramesWorkerCancelled,
    HyperFramesWorkerError,
    HyperFramesWorkerTimeout,
    render_with_hyperframes_worker,
)


FAKE_CLI = """
const fs = require('fs');
const args = process.argv.slice(2);
const mode = process.env.FAKE_HF_MODE || 'success';
if (args[0] === 'check') {
  const payload = mode === 'fatal'
    ? {lint: {findings: [{severity: 'error', message: 'bad lint'}]}}
    : mode === 'warning'
      ? {layout: {findings: [{severity: 'error', message: 'soft overflow'}]}}
      : {lint: {findings: []}, runtime: {findings: []}};
  process.stdout.write(JSON.stringify(payload));
  process.exit(mode === 'fatal' ? 1 : 0);
}
if (args[0] === 'render') {
  if (mode === 'timeout' || mode === 'cancel') {
    setTimeout(() => {}, 10000);
  } else {
    const output = args[args.indexOf('--output') + 1];
    fs.writeFileSync(output, 'fake-webm');
    process.stdout.write('Capturing frame 1/1\\n');
  }
}
"""


class HyperFramesWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.composition = self.root / "composition"
        self.composition.mkdir()
        (self.composition / "index.html").write_text("<!doctype html>", encoding="utf-8")
        self.cli = self.root / "fake-cli.cjs"
        self.cli.write_text(FAKE_CLI, encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _render(self, mode: str, *, timeout_ms: int = 5000):
        output = self.composition / "overlay.webm"
        env = {"FAKE_HF_MODE": mode}
        with _temporary_environment(env):
            return render_with_hyperframes_worker(
                self.composition,
                output,
                cli_path=self.cli,
                timeout_ms=timeout_ms,
            )

    def test_success_emits_render_events_and_output(self) -> None:
        result = self._render("success")

        self.assertTrue(result.output.is_file())
        self.assertGreater(result.output.stat().st_size, 0)
        self.assertIn("completed", [event.get("phase") for event in result.events])

    def test_check_fatal_error_does_not_render(self) -> None:
        with self.assertRaises(HyperFramesWorkerError) as context:
            self._render("fatal")

        self.assertEqual(context.exception.code, "CHECK_FAILED")
        self.assertFalse((self.composition / "overlay.webm").exists())

    def test_layout_error_is_warning_and_render_continues(self) -> None:
        result = self._render("warning")

        self.assertTrue(result.warnings)
        self.assertIn("soft overflow", result.warnings[0])
        self.assertTrue(result.output.is_file())

    def test_timeout_kills_fake_cli(self) -> None:
        with self.assertRaises(HyperFramesWorkerTimeout):
            self._render("timeout", timeout_ms=1200)

    def test_cancel_kills_fake_cli(self) -> None:
        output = self.composition / "overlay.webm"
        cancel_event = threading.Event()
        captured: list[BaseException] = []

        def run() -> None:
            try:
                with _temporary_environment({"FAKE_HF_MODE": "cancel"}):
                    render_with_hyperframes_worker(
                        self.composition,
                        output,
                        cli_path=self.cli,
                        timeout_ms=5000,
                        cancel_event=cancel_event,
                    )
            except BaseException as exc:  # noqa: BLE001
                captured.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.2)
        cancel_event.set()
        thread.join(timeout=4)

        self.assertFalse(thread.is_alive())
        self.assertTrue(captured)
        self.assertIsInstance(captured[0], HyperFramesWorkerCancelled)


class _temporary_environment:
    def __init__(self, values: dict[str, str]) -> None:
        import os

        self.os = os
        self.values = values
        self.previous: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.previous[key] = self.os.environ.get(key)
            self.os.environ[key] = value

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.previous.items():
            if value is None:
                self.os.environ.pop(key, None)
            else:
                self.os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
