from __future__ import annotations

from contextvars import ContextVar, Token


_CURRENT_GENERATION_BATCH_ID: ContextVar[str | None] = ContextVar(
    "ai8video_current_generation_batch_id",
    default=None,
)
_CURRENT_GENERATION_SESSION_ID: ContextVar[str | None] = ContextVar(
    "ai8video_current_generation_session_id",
    default=None,
)


def get_current_generation_batch_id() -> str | None:
    return _CURRENT_GENERATION_BATCH_ID.get()


def get_current_generation_session_id() -> str | None:
    return _CURRENT_GENERATION_SESSION_ID.get()


def set_current_generation_batch_id(generation_batch_id: str | None) -> Token:
    normalized_generation_batch_id = str(generation_batch_id or "").strip() or None
    return _CURRENT_GENERATION_BATCH_ID.set(normalized_generation_batch_id)


def set_current_generation_session_id(session_id: str | None) -> Token:
    normalized_session_id = str(session_id or "").strip() or None
    return _CURRENT_GENERATION_SESSION_ID.set(normalized_session_id)


def reset_current_generation_batch_id(token: Token) -> None:
    _CURRENT_GENERATION_BATCH_ID.reset(token)


def reset_current_generation_session_id(token: Token) -> None:
    _CURRENT_GENERATION_SESSION_ID.reset(token)
