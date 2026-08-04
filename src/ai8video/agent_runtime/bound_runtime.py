"""Resolve a conversation's non-secret model binding into a pinned pipeline."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ai8video.core.config import AI8VideoConfig
from ai8video.generation.pipeline import AI8VideoPipeline
from ai8video.integrations.direct_video_model_client import AI8VideoModelClient
from ai8video.integrations.model_profiles import resolve_model_profile_binding
from ai8video.integrations.video_model_settings import load_video_model_settings


def build_bound_pipeline(binding: dict[str, Any]) -> AI8VideoPipeline:
    profiles = resolve_model_profile_binding(binding)
    config = _bound_config(AI8VideoConfig.from_env(), profiles)
    video_settings = load_video_model_settings(
        llm_base_url=config.llm_base_url,
        llm_api_key=config.llm_api_key,
        profile=profiles.get("video"),
    )
    pipeline = AI8VideoPipeline(config=config)
    pipeline.client = AI8VideoModelClient(config=config, settings=video_settings)
    return pipeline


def bound_llm_config(binding: dict[str, Any]) -> dict[str, str]:
    profile = resolve_model_profile_binding(binding).get("llm")
    if not profile:
        raise ValueError("当前对话没有绑定可用的文本模型配置，请重置对话后重试。")
    config = {
        "baseUrl": str(profile.get("baseUrl") or "").rstrip("/"),
        "apiKey": str(profile.get("apiKey") or ""),
        "model": str(profile.get("model") or ""),
    }
    if not all(config.values()):
        raise ValueError("当前对话绑定的文本模型配置不完整，请重置对话后重试。")
    return config


def _bound_config(
    base: AI8VideoConfig,
    profiles: dict[str, dict[str, Any] | None],
) -> AI8VideoConfig:
    llm = profiles.get("llm")
    if not llm:
        raise ValueError("当前对话没有绑定可用的文本模型配置，请重置对话后重试。")
    multimodal = profiles.get("multimodal") or llm
    image = profiles.get("image") or llm
    return replace(
        base,
        llm_base_url=str(llm.get("baseUrl") or "").rstrip("/"),
        llm_api_key=str(llm.get("apiKey") or ""),
        llm_model=str(llm.get("model") or ""),
        multimodal_base_url=str(multimodal.get("baseUrl") or "").rstrip("/"),
        multimodal_api_key=str(multimodal.get("apiKey") or ""),
        multimodal_model=str(multimodal.get("model") or ""),
        image_base_url=str(image.get("baseUrl") or "").rstrip("/"),
        image_api_key=str(image.get("apiKey") or ""),
        image_model=str(image.get("model") or ""),
        llm_source="conversation_binding",
        multimodal_source="conversation_binding",
        image_source="conversation_binding",
    )
