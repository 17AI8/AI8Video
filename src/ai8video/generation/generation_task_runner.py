"""AI8video 任务执行边界。

第一阶段保持单进程运行，但把任务生命周期从聊天会话中抽出来。后续
如果需要独立 worker，只需替换这个边界的 transport，不需要改业务流水线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable
from uuid import uuid4


TaskTarget = Callable[..., Any]


@dataclass
class GenerationTask:
    """一个可观察、可协作取消的生成任务。"""

    generation_batch_id: str
    result_queue: Any
    cancel_event: threading.Event = field(default_factory=threading.Event)
    worker_id: str = ""
    thread: threading.Thread | None = None
    state: str = "created"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: BaseException | None = None
    state_callback: Callable[["GenerationTask"], None] | None = None
    slot_acquired: bool = False
    slot_released: bool = False

    def is_alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    @property
    def cancel_requested(self) -> bool:
        return self.cancel_event.is_set()


class GenerationTaskRunner:
    """单进程任务执行器，提供稳定的任务句柄和生命周期状态。"""

    def __init__(
        self,
        *,
        worker_prefix: str = "ai8video-runtime",
        max_concurrency: int | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, GenerationTask] = {}
        self._worker_prefix = str(worker_prefix or "ai8video-runtime").strip()
        self._semaphore = (
            threading.Semaphore(max(1, int(max_concurrency)))
            if max_concurrency is not None
            else None
        )

    def start(
        self,
        generation_batch_id: str,
        target: TaskTarget,
        *,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        result_queue: Any = None,
        state_callback: Callable[[GenerationTask], None] | None = None,
    ) -> GenerationTask:
        batch_id = self._require_batch_id(generation_batch_id)
        if not callable(target):
            raise TypeError("generation task target must be callable")
        task = GenerationTask(
            generation_batch_id=batch_id,
            result_queue=result_queue,
            worker_id=f"{self._worker_prefix}-{uuid4().hex[:10]}",
            state_callback=state_callback,
        )
        with self._lock:
            previous = self._tasks.get(batch_id)
            if previous and previous.is_alive():
                previous.cancel_event.set()
                self._set_state(previous, "cancel_requested")
            thread = threading.Thread(
                target=self._run,
                args=(task, target, args, kwargs or {}),
                daemon=True,
            )
            task.thread = thread
            self._tasks[batch_id] = task
        thread.start()
        return task

    def cancel(self, generation_batch_id: str | None) -> bool:
        batch_id = str(generation_batch_id or "").strip()
        if not batch_id:
            return False
        with self._lock:
            task = self._tasks.get(batch_id)
            if task is None or not task.is_alive():
                return False
            task.cancel_event.set()
            self._set_state(task, "cancel_requested")
            return True

    def cancel_active(self, *, except_batch_id: str | None = None) -> list[str]:
        excluded = str(except_batch_id or "").strip()
        cancelled: list[str] = []
        with self._lock:
            for batch_id, task in self._tasks.items():
                if batch_id == excluded or not task.is_alive():
                    continue
                task.cancel_event.set()
                self._set_state(task, "cancel_requested")
                cancelled.append(batch_id)
        return cancelled

    def get(self, generation_batch_id: str | None) -> GenerationTask | None:
        batch_id = str(generation_batch_id or "").strip()
        if not batch_id:
            return None
        with self._lock:
            return self._tasks.get(batch_id)

    def is_running(self, generation_batch_id: str | None) -> bool:
        task = self.get(generation_batch_id)
        return bool(task and task.is_alive())

    def join(self, generation_batch_id: str | None, timeout: float | None = None) -> bool:
        task = self.get(generation_batch_id)
        if task is None or task.thread is None:
            return True
        task.thread.join(timeout=timeout)
        return not task.thread.is_alive()

    def _run(
        self,
        task: GenerationTask,
        target: TaskTarget,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        acquired = False
        try:
            if self._semaphore is not None:
                self._set_state(task, "queued")
                self._semaphore.acquire()
                acquired = True
                with self._lock:
                    task.slot_acquired = True
            if task.cancel_requested:
                self._set_state(task, "cancelled")
                return
            with self._lock:
                task.started_at = time.time()
            self._set_state(task, "running")
            target(task, *args, **kwargs)
        except BaseException as exc:
            task.error = exc
            self._set_state(task, "failed")
            self._emit_uncaught_error(task, exc)
        else:
            self._set_state(task, "cancelled" if task.cancel_requested else "completed")
        finally:
            task.finished_at = time.time()
            if acquired:
                self._release_slot_if_owned(task)

    def _release_slot_if_owned(self, task: GenerationTask) -> bool:
        if self._semaphore is None:
            return False
        with self._lock:
            if not task.slot_acquired or task.slot_released:
                return False
            task.slot_released = True
        self._semaphore.release()
        return True

    def _set_state(self, task: GenerationTask, state: str) -> None:
        callback = None
        with self._lock:
            task.state = state
            callback = task.state_callback
        if callback is not None:
            callback(task)

    @staticmethod
    def _emit_uncaught_error(task: GenerationTask, error: BaseException) -> None:
        queue_obj = task.result_queue
        if queue_obj is None or not hasattr(queue_obj, "put"):
            return
        queue_obj.put(
            {
                "done": True,
                "generationBatchId": task.generation_batch_id,
                "error": error,
                "runnerError": True,
            }
        )

    @staticmethod
    def _require_batch_id(value: str) -> str:
        batch_id = str(value or "").strip()
        if not batch_id:
            raise ValueError("generation_batch_id is required")
        return batch_id
