"""Pi 风格的低层能力边界：显式类型、事件、取消和副作用串行化。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Generic, TypeVar


InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
EventSink = Callable[[dict[str, Any]], None]
CapabilityHandler = Callable[["AgentRunContext", Any], Any]


@dataclass(frozen=True)
class AgentRunContext:
    session_id: str = ""
    batch_id: str = ""
    trace_id: str = ""
    cancel_check: Callable[[], bool] | None = None
    event_sink: EventSink | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def raise_if_cancelled(self) -> None:
        if self.cancel_check is not None and self.cancel_check():
            raise RuntimeError("Agent 能力执行已取消")

    def emit(self, event: str, **payload: Any) -> None:
        if self.event_sink is None:
            return
        self.event_sink({
            "event": event,
            "sessionId": self.session_id,
            "batchId": self.batch_id,
            "traceId": self.trace_id,
            **payload,
        })


@dataclass(frozen=True)
class CapabilitySpec(Generic[InputT, OutputT]):
    name: str
    agent_id: str
    description: str
    handler: CapabilityHandler
    input_type: type | tuple[type, ...]
    output_type: type | tuple[type, ...]
    policy_skills: tuple[str, ...] = ()
    side_effects: bool = False
    replay_safe: bool = True
    execution_mode: str = "sequential"


@dataclass(frozen=True)
class CapabilityResult(Generic[OutputT]):
    capability: str
    status: str
    value: OutputT
    started_at: str
    finished_at: str


class CapabilityRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, CapabilitySpec[Any, Any]] = {}
        self._sequential_lock = threading.RLock()

    def register(self, spec: CapabilitySpec[Any, Any]) -> None:
        name = str(spec.name or "").strip()
        if not name or name in self._specs:
            raise ValueError(f"Capability 名称重复或为空：{name}")
        if spec.execution_mode not in {"sequential", "parallel"}:
            raise ValueError(f"Capability execution_mode 不合法：{name}")
        if spec.side_effects and spec.execution_mode != "sequential":
            raise ValueError(f"有副作用的 Capability 必须串行执行：{name}")
        self._specs[name] = spec

    def get(self, name: str) -> CapabilitySpec[Any, Any]:
        try:
            return self._specs[str(name)]
        except KeyError as exc:
            raise KeyError(f"Capability 不存在：{name}") from exc

    def list_specs(self) -> tuple[CapabilitySpec[Any, Any], ...]:
        return tuple(self._specs[name] for name in sorted(self._specs))

    def execute(
        self,
        name: str,
        context: AgentRunContext,
        payload: InputT,
    ) -> CapabilityResult[OutputT]:
        spec = self.get(name)
        if not isinstance(payload, spec.input_type):
            raise TypeError(f"{name} 输入类型不合法：{type(payload).__name__}")
        lock = self._sequential_lock if spec.execution_mode == "sequential" else _NullLock()
        with lock:
            return self._execute_spec(spec, context, payload)

    @staticmethod
    def _execute_spec(
        spec: CapabilitySpec[Any, Any],
        context: AgentRunContext,
        payload: Any,
    ) -> CapabilityResult[Any]:
        started_at = _utc_now()
        context.raise_if_cancelled()
        context.emit(
            "capability_start",
            capability=spec.name,
            agentId=spec.agent_id,
            replaySafe=spec.replay_safe,
            sideEffects=spec.side_effects,
        )
        try:
            value = spec.handler(context, payload)
            context.raise_if_cancelled()
            if not isinstance(value, spec.output_type):
                raise TypeError(f"{spec.name} 输出类型不合法：{type(value).__name__}")
        except Exception as exc:
            context.emit(
                "capability_error",
                capability=spec.name,
                errorType=exc.__class__.__name__,
                error=str(exc)[:300],
            )
            raise
        finished_at = _utc_now()
        context.emit("capability_end", capability=spec.name, status="completed")
        return CapabilityResult(spec.name, "completed", value, started_at, finished_at)


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
