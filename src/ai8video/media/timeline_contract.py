"""跨视频、配音与动效时间轴共享的非破坏性裁剪契约。"""

from __future__ import annotations

import math
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


TIMELINE_SCHEMA_VERSION = 2
TIMELINE_PRECISION_DIGITS = 3
_TIMELINE_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_TIMELINE_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class TimelineRestoreBounds:
    start_seconds: float
    end_seconds: float

    def as_payload(self) -> dict[str, float]:
        return {
            "originalSourceStartSeconds": self.start_seconds,
            "originalSourceEndSeconds": self.end_seconds,
        }


class TimelineRevisionConflict(LookupError):
    pass


@contextmanager
def timeline_review_lock(track: str, relative_key: str):
    identity = (str(track), str(relative_key))
    with _TIMELINE_LOCKS_GUARD:
        lock = _TIMELINE_LOCKS.setdefault(identity, threading.RLock())
    with lock:
        yield


def normalize_restore_bounds(
    item: dict[str, Any],
    *,
    visible_start_seconds: float,
    visible_end_seconds: float,
    source_duration_seconds: float,
) -> TimelineRestoreBounds:
    """保留可恢复上限，同时禁止客户端把边界扩展到真实素材之外。"""

    source_duration = max(0.0, finite_seconds(source_duration_seconds, "素材时长不合法"))
    visible_start = min(max(0.0, visible_start_seconds), source_duration)
    visible_end = min(max(visible_start, visible_end_seconds), source_duration)
    original_start = optional_seconds(
        item.get("originalSourceStartSeconds"),
        visible_start,
    )
    original_end = optional_seconds(
        item.get("originalSourceEndSeconds"),
        visible_end,
    )
    original_start = min(max(0.0, original_start), visible_start)
    original_end = max(min(source_duration, original_end), visible_end)
    return TimelineRestoreBounds(
        round(original_start, TIMELINE_PRECISION_DIGITS),
        round(original_end, TIMELINE_PRECISION_DIGITS),
    )


def next_timeline_revision(previous: dict[str, Any] | None) -> int:
    try:
        revision = int((previous or {}).get("revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    return max(0, revision) + 1


def ensure_expected_revision(previous: dict[str, Any] | None, expected_revision: Any) -> None:
    if expected_revision is None:
        return
    try:
        expected = int(expected_revision)
        current = int((previous or {}).get("revision") or 0)
    except (TypeError, ValueError) as exc:
        raise TimelineRevisionConflict("时间轴版本不合法，请刷新后重试") from exc
    if expected != current:
        raise TimelineRevisionConflict(
            f"时间轴已在其他操作中更新（当前版本 {current}），请刷新后重试"
        )


def finite_seconds(value: Any, message: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(message) from exc
    if not math.isfinite(number):
        raise ValueError(message)
    return number


def optional_seconds(value: Any, fallback: float) -> float:
    if value is None:
        return fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback
