"""HyperFrames Node Worker 的 JSONL/stdio transport。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import time
from threading import Event
from typing import Any, Callable
from uuid import uuid4

from ai8video.core.paths import PROJECT_ROOT

WORKER_SCRIPT = Path(__file__).with_name("hyperframes_worker.cjs")


class HyperFramesWorkerError(RuntimeError):
    def __init__(self, message: str, *, code: str = "WORKER_FAILED") -> None:
        super().__init__(message)
        self.code = code


class HyperFramesWorkerCancelled(HyperFramesWorkerError):
    def __init__(self) -> None:
        super().__init__("HyperFrames Worker 已取消", code="CANCELLED")


class HyperFramesWorkerTimeout(HyperFramesWorkerError):
    def __init__(self, timeout_ms: int) -> None:
        super().__init__(f"HyperFrames Worker 超时（>{timeout_ms}ms）", code="TIMEOUT")


@dataclass(frozen=True)
class HyperFramesWorkerResult:
    task_id: str
    output: Path
    warnings: list[str]
    events: list[dict[str, Any]]


def render_with_hyperframes_worker(
    composition_dir: Path,
    output: Path,
    *,
    cli_path: Path,
    timeout_ms: int = 300_000,
    cancel_event: Event | None = None,
    stage_callback: Callable[[str, dict[str, Any] | None], None] | None = None,
    worker_script: Path = WORKER_SCRIPT,
) -> HyperFramesWorkerResult:
    task_id = uuid4().hex
    command = [_resolve_node_bin(), str(worker_script)]
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        bufsize=0,
        start_new_session=os.name != "nt",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    selector = selectors.DefaultSelector()
    events: list[dict[str, Any]] = []
    stderr_tail: list[str] = []
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + max(1, timeout_ms) / 1000
    try:
        _register_streams(selector, process)
        _send_task(process, {
            "type": "render",
            "taskId": task_id,
            "compositionDir": str(composition_dir.resolve()),
            "output": str(output.resolve()),
            "cliPath": str(cli_path.resolve()),
            "timeoutMs": int(timeout_ms),
        })
        terminal = _read_events(
            process,
            selector,
            task_id,
            deadline,
            events,
            stderr_tail,
            buffers,
            cancel_event,
            stage_callback,
            timeout_ms,
        )
        if terminal.get("status") != "succeeded":
            raise HyperFramesWorkerError(
                _event_message(terminal, stderr_tail),
                code=str(terminal.get("code") or "WORKER_FAILED"),
            )
        return HyperFramesWorkerResult(
            task_id=task_id,
            output=output,
            warnings=_warnings_from_events(events, terminal),
            events=events,
        )
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_process_group(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _terminate_process_group(process, force=True)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def _read_events(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    task_id: str,
    deadline: float,
    events: list[dict[str, Any]],
    stderr_tail: list[str],
    buffers: dict[str, bytearray],
    cancel_event: Event | None,
    stage_callback: Callable[[str, dict[str, Any] | None], None] | None,
    timeout_ms: int,
) -> dict[str, Any]:
    terminal: dict[str, Any] | None = None
    cancel_sent = False
    while terminal is None:
        if cancel_event is not None and cancel_event.is_set() and not cancel_sent:
            _send_task(process, {"type": "cancel", "taskId": task_id})
            cancel_sent = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HyperFramesWorkerTimeout(timeout_ms)
        for key, _ in selector.select(timeout=min(0.25, remaining)):
            stream_name = str(key.data)
            try:
                chunk = os.read(key.fd, 65536)
            except BlockingIOError:
                continue
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            buffers[stream_name].extend(chunk)
            while b"\n" in buffers[stream_name]:
                raw_line, _, remainder = buffers[stream_name].partition(b"\n")
                buffers[stream_name][:] = remainder
                line = raw_line.decode("utf-8", errors="replace")
                if stream_name == "stderr":
                    stderr_tail.append(line.strip())
                    del stderr_tail[:-8]
                    continue
                event = _parse_event(line)
                if not event or event.get("taskId") not in {task_id, ""}:
                    continue
                events.append(event)
                if stage_callback is not None and event.get("phase"):
                    stage_callback(str(event["phase"]), event)
                if event.get("phase") in {"completed", "failed", "cancelled"}:
                    terminal = event
                    break
            if terminal is not None:
                break
        if process.poll() is not None and terminal is None:
            raise HyperFramesWorkerError(
                "HyperFrames Worker 提前退出：" + " | ".join(stderr_tail[-4:]),
                code="WORKER_EXITED",
            )
    if cancel_sent and terminal.get("status") != "succeeded":
        raise HyperFramesWorkerCancelled()
    return terminal


def _register_streams(selector: selectors.BaseSelector, process: subprocess.Popen[bytes]) -> None:
    if process.stdout is not None:
        selector.register(process.stdout, selectors.EVENT_READ, data="stdout")
    if process.stderr is not None:
        selector.register(process.stderr, selectors.EVENT_READ, data="stderr")


def _send_task(process: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise HyperFramesWorkerError("HyperFrames Worker stdin 未就绪", code="WORKER_IO")
    process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    process.stdin.flush()


def _parse_event(line: str | bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(line.decode("utf-8") if isinstance(line, bytes) else line)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _event_message(event: dict[str, Any], stderr_tail: list[str]) -> str:
    message = str(event.get("message") or "").strip()
    if message:
        return message
    return " | ".join(stderr_tail[-4:]) or "HyperFrames Worker 失败"


def _warnings_from_events(events: list[dict[str, Any]], terminal: dict[str, Any]) -> list[str]:
    for event in [terminal, *reversed(events)]:
        warnings = event.get("warnings")
        if isinstance(warnings, list):
            return [str(item) for item in warnings]
    return []


def _resolve_node_bin() -> str:
    configured = str(os.getenv("AI8VIDEO_NODE_BIN") or "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path(shutil.which("node") or ""),
        Path.home() / ".local" / "bin" / "node",
        Path("/opt/homebrew/bin/node"),
        Path("/usr/local/bin/node"),
        PROJECT_ROOT / ".node" / "bin" / "node",
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise HyperFramesWorkerError("未找到 Node.js 运行时", code="NODE_MISSING")


def _terminate_process_group(process: subprocess.Popen[bytes], *, force: bool = False) -> None:
    if process.poll() is not None:
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    if os.name == "nt":
        command = ["taskkill", "/PID", str(process.pid), "/T", "/F"]
        try:
            subprocess.run(command, check=False, capture_output=True)
        except OSError:
            pass
        return
    try:
        os.killpg(process.pid, sig)
    except (OSError, ProcessLookupError):
        try:
            process.kill() if force else process.terminate()
        except OSError:
            pass
