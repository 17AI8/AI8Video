"""单进程 Agent 任务调度器：有界并发、租约心跳与安全恢复。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import json
import logging
from threading import Event, Lock, RLock, Thread
import time
from typing import Callable
from uuid import uuid4

from ai8video.batch.agent_task_ledger import AgentTaskLedger
from ai8video.batch.agent_task_models import (
    TERMINAL_TASK_STATES,
    AgentResult,
    AgentTask,
    AgentTaskSpec,
)


logger = logging.getLogger(__name__)
AgentTaskHandler = Callable[[AgentTask, Event], AgentResult]


@dataclass(frozen=True)
class AgentTaskHandlerSpec:
    handler: AgentTaskHandler
    replay_safe: bool = False
    retry_delay_seconds: float = 0


@dataclass
class _ActiveClaim:
    task: AgentTask
    handler_spec: AgentTaskHandlerSpec
    worker_id: str
    cancel_event: Event
    future: Future[AgentResult]
    next_heartbeat_at: float
    lease_lost: bool = False


class AgentTaskScheduler:
    """只自动恢复显式注册为 replay-safe（可重放）的任务类型。"""

    def __init__(
        self,
        ledger: AgentTaskLedger,
        *,
        max_concurrency: int = 2,
        lease_seconds: float = 30,
        heartbeat_interval: float | None = None,
        poll_interval: float = 0.05,
        worker_prefix: str = "ai8video-agent",
    ) -> None:
        self.ledger = ledger
        self.max_concurrency = max(1, int(max_concurrency))
        self.lease_seconds = max(0.05, float(lease_seconds))
        default_heartbeat = self.lease_seconds / 3
        requested_heartbeat = default_heartbeat if heartbeat_interval is None else float(heartbeat_interval)
        if requested_heartbeat <= 0 or requested_heartbeat >= self.lease_seconds:
            raise ValueError("heartbeat_interval must be positive and shorter than lease_seconds")
        self.heartbeat_interval = requested_heartbeat
        self.poll_interval = max(0.005, float(poll_interval))
        if self.poll_interval >= self.heartbeat_interval:
            raise ValueError("poll_interval must be shorter than heartbeat_interval")
        self.worker_prefix = str(worker_prefix or "ai8video-agent").strip()
        self._handlers: dict[str, AgentTaskHandlerSpec] = {}
        self._active: dict[str, _ActiveClaim] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_concurrency,
            thread_name_prefix=self.worker_prefix,
        )
        self._state_lock = RLock()
        self._cycle_lock = RLock()
        self._shutdown_lock = Lock()
        self._wake_event = Event()
        self._stop_event = Event()
        self._dispatcher: Thread | None = None
        self._accepting = True
        self._closed = False
        self._shutdown_complete = False

    def register_handler(self, task_type: str, spec: AgentTaskHandlerSpec) -> None:
        normalized_type = str(task_type or "").strip()
        if not normalized_type:
            raise ValueError("task_type is required")
        if not callable(spec.handler):
            raise TypeError("agent task handler must be callable")
        with self._state_lock:
            if self._closed:
                raise RuntimeError("agent task scheduler is closed")
            self._handlers[normalized_type] = spec
        self._wake_event.set()

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("agent task scheduler is closed")
            if self._dispatcher is not None:
                return
            self.ledger.initialize()
            self._stop_event.clear()
            dispatcher = Thread(
                target=self._run_loop,
                name=f"{self.worker_prefix}-dispatcher",
                daemon=True,
            )
            self._dispatcher = dispatcher
        try:
            self.run_once(allow_dispatch=False)
        except BaseException:
            with self._state_lock:
                self._dispatcher = None
            raise
        dispatcher.start()

    def enqueue(
        self,
        spec: AgentTaskSpec,
        *,
        dependency_task_id: str | None = None,
    ) -> AgentTask:
        with self._cycle_lock:
            with self._state_lock:
                if spec.task_type not in self._handlers:
                    raise ValueError(f"no handler registered for task type: {spec.task_type}")
                if not self._accepting or self._closed:
                    raise RuntimeError("agent task scheduler is shutting down")
            task = self.ledger.create_task_with_dependency(spec, dependency_task_id)
            self.start()
        self._wake_event.set()
        return task

    def wait_for_terminal(self, task_id: str, timeout_seconds: float = 1) -> AgentTask | None:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        with self._cycle_lock:
            with self._state_lock:
                should_start = not self._closed
            if should_start:
                self.start()
        while True:
            task = self.ledger.get_task(task_id)
            if (
                task is None
                or task.state in TERMINAL_TASK_STATES
                or self._stop_event.is_set()
            ):
                return task
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return task
            self._wake_event.set()
            time.sleep(min(self.poll_interval, remaining))

    def cancel(self, task_id: str) -> AgentTask:
        with self._cycle_lock:
            with self._state_lock:
                active = self._active.get(task_id)
            if active is not None and active.future.done():
                result = self._resolved_future_result(active)
                if not self._result_is_success(result):
                    task = self._request_cancel_active(task_id)
                    if self._finish_active_claim(active, result):
                        self._remove_active_claim(active)
                    self._wake_event.set()
                    return self.ledger.get_task(task_id) or task
                if self._finish_active_claim(active, result):
                    self._remove_active_claim(active)
            task = self._request_cancel_active(task_id)
        self._wake_event.set()
        return task

    def run_once(self, *, allow_dispatch: bool = True) -> dict[str, int]:
        with self._cycle_lock:
            completed = self._collect_finished()
            renewed = self._renew_active_leases()
            recovered, blocked = self._run_maintenance()
            with self._state_lock:
                can_dispatch = allow_dispatch and self._accepting and not self._stop_event.is_set()
            dispatched = self._dispatch_ready() if can_dispatch else 0
        return {
            "completed": completed,
            "renewed": renewed,
            "recovered": recovered,
            "blocked": blocked,
            "dispatched": dispatched,
        }

    def shutdown(self, grace_seconds: float = 1) -> None:
        grace = max(0.0, float(grace_seconds))
        with self._shutdown_lock:
            with self._cycle_lock:
                with self._state_lock:
                    if self._shutdown_complete:
                        return
                    self._accepting = False
                    self._closed = True
                    active_ids = list(self._active)
                self._stop_event.set()
                self._wake_event.set()
                self._cancel_active_for_shutdown(active_ids)
                self._collect_finished()
                try:
                    self._drain_active(grace)
                except Exception as exc:
                    logger.warning(
                        "agent task scheduler drain failed error_type=%s",
                        exc.__class__.__name__,
                    )
            self._close_runtime(grace)

    @property
    def active_count(self) -> int:
        with self._state_lock:
            return len(self._active)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once(allow_dispatch=True)
            except Exception as exc:
                logger.warning(
                    "agent task scheduler cycle failed error_type=%s",
                    exc.__class__.__name__,
                )
            self._wake_event.wait(self.poll_interval)
            self._wake_event.clear()

    def _collect_finished(self) -> int:
        with self._state_lock:
            finished = [item for item in self._active.values() if item.future.done()]
        completed = 0
        for active in finished:
            try:
                removable = self._finish_active_claim(active)
            except Exception as exc:
                logger.warning(
                    "agent task completion persistence failed error_type=%s",
                    exc.__class__.__name__,
                )
                continue
            if removable:
                self._remove_active_claim(active)
                completed += 1
        return completed

    def _finish_active_claim(
        self,
        active: _ActiveClaim,
        result: AgentResult | None = None,
    ) -> bool:
        if active.lease_lost:
            return True
        self._write_active_result(active, result or self._resolved_future_result(active))
        return True

    def _resolved_future_result(self, active: _ActiveClaim) -> AgentResult:
        try:
            result = active.future.result()
        except BaseException as exc:
            logger.warning(
                "agent task handler failed error_type=%s",
                exc.__class__.__name__,
            )
            return AgentResult(
                active.task.task_id,
                error_type=exc.__class__.__name__,
                error_message="agent task handler failed",
            )
        try:
            if not isinstance(result, AgentResult):
                raise TypeError("agent task handler must return AgentResult")
            if result.task_id != active.task.task_id:
                raise ValueError("result task_id does not match claimed task")
            output = self._copy_json_snapshot(result.output_snapshot)
            return AgentResult(
                active.task.task_id,
                output,
                None if result.error_type is None else str(result.error_type),
                None if result.error_message is None else str(result.error_message),
            )
        except BaseException as exc:
            logger.warning(
                "agent task result normalization failed error_type=%s",
                exc.__class__.__name__,
            )
            return AgentResult(
                active.task.task_id,
                error_type=exc.__class__.__name__,
                error_message="agent task result could not be persisted",
            )

    @staticmethod
    def _copy_json_snapshot(snapshot: dict | None) -> dict | None:
        if snapshot is None:
            return None
        parsed = json.loads(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
        if not isinstance(parsed, dict):
            raise TypeError("agent task output_snapshot must be a JSON object")
        return parsed

    @staticmethod
    def _result_is_success(result: AgentResult) -> bool:
        return not result.error_type and not result.error_message

    def _request_cancel_active(self, task_id: str) -> AgentTask:
        task = self.ledger.request_cancel(task_id)
        with self._state_lock:
            active = self._active.get(task_id)
            if active is not None:
                active.task = task
                active.cancel_event.set()
        return task

    def _write_active_result(self, active: _ActiveClaim, result: AgentResult) -> None:
        self.ledger.finish_claimed_task(
            active.task.task_id,
            active.worker_id,
            active.task.version,
            result,
            retry_delay_seconds=active.handler_spec.retry_delay_seconds,
        )

    def _remove_active_claim(self, active: _ActiveClaim) -> None:
        with self._state_lock:
            current = self._active.get(active.task.task_id)
            if current is active:
                self._active.pop(active.task.task_id, None)

    def _cancel_active_for_shutdown(self, task_ids: list[str]) -> None:
        for task_id in task_ids:
            try:
                self.cancel(task_id)
            except Exception as exc:
                logger.warning(
                    "agent task shutdown cancellation failed error_type=%s",
                    exc.__class__.__name__,
                )
                with self._state_lock:
                    active = self._active.get(task_id)
                    if active is not None:
                        active.cancel_event.set()

    def _close_runtime(self, grace_seconds: float) -> None:
        dispatcher = self._dispatcher
        if dispatcher is not None and dispatcher.is_alive():
            dispatcher.join(timeout=grace_seconds)
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        finally:
            with self._state_lock:
                self._dispatcher = None
                self._shutdown_complete = True

    def _renew_active_leases(self) -> int:
        now = time.time()
        with self._state_lock:
            active_items = list(self._active.values())
        renewed_count = 0
        for active in active_items:
            if active.future.done() or active.lease_lost or now < active.next_heartbeat_at:
                continue
            renewed = self.ledger.renew_lease(
                active.task.task_id,
                active.worker_id,
                active.task.version,
                lease_seconds=self.lease_seconds,
                now=now,
            )
            if renewed is None:
                active.lease_lost = True
                active.cancel_event.set()
                continue
            active.task = renewed
            active.next_heartbeat_at = now + self.heartbeat_interval
            renewed_count += 1
        return renewed_count

    def _run_maintenance(self) -> tuple[int, int]:
        self.ledger.mark_expired_leases_for_recovery()
        recovered = self.ledger.requeue_replay_safe_tasks(self._replay_safe_types())
        blocked = self.ledger.settle_blocked_tasks()
        return len(recovered), len(blocked)

    def _dispatch_ready(self) -> int:
        dispatched = 0
        while True:
            with self._state_lock:
                capacity = self.max_concurrency - len(self._active)
                task_types = tuple(self._handlers)
                excluded_task_ids = tuple(self._active)
                accepting = self._accepting and not self._stop_event.is_set()
            if capacity <= 0 or not task_types or not accepting:
                return dispatched
            worker_id = f"{self.worker_prefix}-{uuid4().hex}"
            task = self.ledger.claim_next_ready(
                task_types,
                worker_id,
                exclude_task_ids=excluded_task_ids,
                lease_seconds=self.lease_seconds,
            )
            if task is None:
                return dispatched
            if not self._submit_or_fail_claim(task, worker_id):
                return dispatched
            dispatched += 1

    def _submit_or_fail_claim(self, task: AgentTask, worker_id: str) -> bool:
        try:
            self._submit_claim(task, worker_id)
            return True
        except BaseException as exc:
            self.ledger.finish_claimed_task(
                task.task_id,
                worker_id,
                task.version,
                AgentResult(
                    task.task_id,
                    error_type=exc.__class__.__name__,
                    error_message="agent task handler could not start",
                ),
            )
            return False

    def _submit_claim(self, task: AgentTask, worker_id: str) -> None:
        with self._state_lock:
            handler_spec = self._handlers[task.task_type]
        cancel_event = Event()
        start_gate = Event()
        future = self._executor.submit(
            self._invoke_handler,
            handler_spec.handler,
            task,
            cancel_event,
            start_gate,
        )
        active = _ActiveClaim(
            task=task,
            handler_spec=handler_spec,
            worker_id=worker_id,
            cancel_event=cancel_event,
            future=future,
            next_heartbeat_at=time.time() + self.heartbeat_interval,
        )
        with self._state_lock:
            self._active[task.task_id] = active
        start_gate.set()

    @staticmethod
    def _invoke_handler(
        handler: AgentTaskHandler,
        task: AgentTask,
        cancel_event: Event,
        start_gate: Event,
    ) -> AgentResult:
        start_gate.wait()
        return handler(task, cancel_event)

    def _replay_safe_types(self) -> tuple[str, ...]:
        with self._state_lock:
            return tuple(
                task_type
                for task_type, spec in self._handlers.items()
                if spec.replay_safe
            )

    def _drain_active(self, grace_seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, float(grace_seconds))
        while self.active_count and time.monotonic() < deadline:
            self.run_once(allow_dispatch=False)
            self._wake_event.clear()
            self._wake_event.wait(min(self.poll_interval, max(0.0, deadline - time.monotonic())))
