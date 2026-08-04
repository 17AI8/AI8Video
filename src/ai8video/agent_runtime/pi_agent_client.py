"""Long-lived JSONL bridge to the Pi Agent Core sidecar."""

from __future__ import annotations

import atexit
from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
from threading import Lock, Thread
import time
from typing import Any, Callable
from uuid import uuid4


ToolHandler = Callable[[str, dict[str, Any], str], dict[str, Any]]


@dataclass(frozen=True)
class PiAgentDecision:
    text: str
    action: dict[str, Any] | None
    stop_reason: str | None
    usage: dict[str, Any] | None


class PiAgentClientError(RuntimeError):
    pass


class PiAgentClient:
    def __init__(
        self,
        *,
        node_binary: str | None = None,
        sidecar_path: str | Path | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.node_binary = str(
            node_binary or os.getenv("AI8VIDEO_PI_AGENT_NODE") or shutil.which("node") or "node"
        )
        self.sidecar_path = Path(
            sidecar_path or Path(__file__).with_name("pi_agent_sidecar.mjs")
        ).resolve()
        self.timeout_seconds = float(
            timeout_seconds or os.getenv("AI8VIDEO_PI_AGENT_TIMEOUT_SECONDS") or 600
        )
        self._process: subprocess.Popen[str] | None = None
        self._lock = Lock()
        self._stderr_lines: deque[str] = deque(maxlen=20)

    def health(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_started()
            request_id = f"health-{uuid4().hex[:12]}"
            self._send({"type": "health", "requestId": request_id})
            while True:
                payload = self._read_payload(deadline=time.monotonic() + 10)
                if payload.get("type") == "health" and payload.get("requestId") == request_id:
                    return payload

    def decide(
        self,
        *,
        session_id: str,
        model_config: dict[str, Any],
        system_prompt: str,
        messages: list[dict[str, Any]],
        prompt: str,
        tool_handler: ToolHandler,
    ) -> PiAgentDecision:
        request_id = f"decision-{uuid4().hex}"
        with self._lock:
            self._ensure_started()
            self._send({
                "type": "decide",
                "requestId": request_id,
                "sessionId": str(session_id or request_id),
                "modelConfig": model_config,
                "systemPrompt": str(system_prompt or ""),
                "messages": messages,
                "prompt": str(prompt or ""),
            })
            deadline = time.monotonic() + max(10, self.timeout_seconds)
            tool_called = False
            tool_error: Exception | None = None
            while True:
                payload = self._read_payload(deadline=deadline)
                if payload.get("requestId") != request_id:
                    continue
                event_type = payload.get("type")
                if event_type == "tool_call":
                    tool_called = True
                    tool_error = self._handle_tool_call(payload, tool_handler)
                    continue
                if event_type == "decision_error":
                    if tool_error is not None:
                        raise tool_error
                    raise PiAgentClientError(str(payload.get("error") or "Pi Agent decision failed"))
                if event_type == "decision_result":
                    if tool_error is not None:
                        raise tool_error
                    action = payload.get("action") if isinstance(payload.get("action"), dict) else None
                    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
                    return PiAgentDecision(
                        text=str(payload.get("text") or "").strip(),
                        action=action,
                        stop_reason=str(payload.get("stopReason") or "").strip() or None,
                        usage=usage,
                    )
                if event_type == "protocol_error":
                    raise PiAgentClientError(str(payload.get("error") or "Pi Agent protocol error"))
                if self._process is None or self._process.poll() is not None:
                    suffix = " after tool execution" if tool_called else ""
                    raise PiAgentClientError(f"Pi Agent sidecar exited{suffix}: {self._stderr_summary()}")

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process is None or process.poll() is not None:
                self._close_process_streams(process)
                return
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            self._close_process_streams(process)

    def _handle_tool_call(
        self,
        payload: dict[str, Any],
        tool_handler: ToolHandler,
    ) -> Exception | None:
        tool_call_id = str(payload.get("toolCallId") or "")
        name = str(payload.get("name") or "")
        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        try:
            result = tool_handler(name, arguments, tool_call_id)
            response = {"ok": True, "result": result}
            error = None
        except Exception as exc:
            response = {"ok": False, "error": str(exc)[:2000]}
            error = exc
        self._send({
            "type": "tool_result",
            "requestId": payload.get("requestId"),
            "toolCallId": tool_call_id,
            **response,
        })
        return error

    def _ensure_started(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if not self.sidecar_path.is_file():
            raise PiAgentClientError(f"Pi Agent sidecar not found: {self.sidecar_path}")
        try:
            self._process = subprocess.Popen(
                [self.node_binary, str(self.sidecar_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                cwd=str(self.sidecar_path.parents[3]),
            )
        except OSError as exc:
            self._process = None
            raise PiAgentClientError(f"Pi Agent sidecar failed to start: {exc}") from exc
        self._stderr_lines.clear()
        Thread(target=self._capture_stderr, args=(self._process,), daemon=True).start()
        payload = self._read_payload(deadline=time.monotonic() + 15)
        if payload.get("type") != "ready" or int(payload.get("protocol") or 0) != 1:
            self.close()
            raise PiAgentClientError("Pi Agent sidecar returned an invalid startup handshake")

    def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise PiAgentClientError(f"Pi Agent sidecar is not running: {self._stderr_summary()}")
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise PiAgentClientError(f"Pi Agent sidecar write failed: {self._stderr_summary()}") from exc

    def _read_payload(self, *, deadline: float) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            raise PiAgentClientError("Pi Agent sidecar stdout is unavailable")
        timeout = max(0.0, deadline - time.monotonic())
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if not ready:
            raise PiAgentClientError("Pi Agent sidecar timed out")
        line = process.stdout.readline()
        if not line:
            raise PiAgentClientError(f"Pi Agent sidecar closed stdout: {self._stderr_summary()}")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PiAgentClientError("Pi Agent sidecar returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise PiAgentClientError("Pi Agent sidecar returned a non-object payload")
        return payload

    def _capture_stderr(self, process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            clean = line.strip()
            if clean:
                self._stderr_lines.append(clean[:1000])

    def _stderr_summary(self) -> str:
        return " | ".join(self._stderr_lines) or "no stderr output"

    @staticmethod
    def _close_process_streams(process: subprocess.Popen[str] | None) -> None:
        if process is None:
            return
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


_CLIENT = PiAgentClient()
atexit.register(_CLIENT.close)


def get_pi_agent_client() -> PiAgentClient:
    return _CLIENT
