from __future__ import annotations

from typing import Any, Callable

from ai8video.application import ai8video_chat_service
from ai8video.core.config import AI8VideoConfig
from ai8video.generation.generation_task_runner import GenerationTask
from ai8video.application.runtime import (
    CHAT_BACKEND,
    build_batch_seed_file_payload,
    get_assets_payload,
    get_batch_alerts_payload,
    get_batch_reports_payload,
    get_health_payload,
    get_supervisor_admin_result_path,
    run_batch_payload,
    write_supervisor_admin_result_payload,
)
from ai8video.integrations.video_model_settings import load_video_model_settings


def get_config_status_payload() -> dict:
    config = AI8VideoConfig.from_env()
    video_settings = load_video_model_settings(
        llm_base_url=config.llm_base_url,
        llm_api_key=config.llm_api_key,
    )
    return {
        "chatBackend": CHAT_BACKEND,
        "dryRun": config.dry_run,
        "hasLLM": config.has_llm(),
        "llmSource": config.llm_source,
        "hasImageModel": config.has_image_model(),
        "imageModelSource": config.image_source,
        "hasVideoModel": video_settings.configured(),
    }


def handle_chat(
    session_id: str,
    message: str,
    refresh: bool = False,
    timeout_seconds: int | None = None,
) -> dict:
    options: dict[str, object] = {
        "session_id": session_id,
        "message": message,
        "timeout_seconds": timeout_seconds,
    }
    if refresh:
        options["refresh"] = True
    return ai8video_chat_service.handle_chat_via_ai8video(**options)


def get_chat_status(session_id: str, generation_batch_id: str | None = None) -> dict:
    return ai8video_chat_service.get_chat_status_via_ai8video(
        session_id=session_id,
        generation_batch_id=generation_batch_id,
    )


def start_external_generation_task(
    session_id: str,
    generation_batch_id: str,
    target: Callable[..., object],
    *,
    args: tuple[Any, ...] = (),
) -> GenerationTask:
    return ai8video_chat_service.start_external_generation_task(
        session_id=session_id,
        generation_batch_id=generation_batch_id,
        target=target,
        args=args,
    )


def cancel_chat(
    session_id: str,
    reason: str | None = None,
    generation_batch_id: str | None = None,
) -> dict:
    return ai8video_chat_service.cancel_chat_via_ai8video(
        session_id=session_id,
        reason=reason,
        generation_batch_id=generation_batch_id,
    )


def cancel_smart_split_confirmation(session_id: str) -> dict:
    return ai8video_chat_service.cancel_smart_split_confirmation_via_ai8video(session_id=session_id)


def restore_smart_split_plan(session_id: str, videos: list[dict]) -> bool:
    return ai8video_chat_service.restore_smart_split_plan_via_ai8video(
        session_id=session_id,
        videos=videos,
    )


__all__ = [
    "CHAT_BACKEND",
    "build_batch_seed_file_payload",
    "cancel_chat",
    "cancel_smart_split_confirmation",
    "get_assets_payload",
    "get_batch_alerts_payload",
    "get_batch_reports_payload",
    "get_chat_status",
    "get_config_status_payload",
    "get_health_payload",
    "get_supervisor_admin_result_path",
    "handle_chat",
    "run_batch_payload",
    "restore_smart_split_plan",
    "start_external_generation_task",
    "write_supervisor_admin_result_payload",
]
