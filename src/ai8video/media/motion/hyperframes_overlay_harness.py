from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ai8video.core.models import EpisodePrompt
from ai8video.media.motion.hyperframes_overlay_agent import run_semantic_agent
from ai8video.media.motion.hyperframes_overlay_renderer import build_composition_html, build_motion_manifest
from ai8video.media.motion.hyperframes_overlay_prompts import (
    build_critique_prompt,
    build_generation_prompt,
    build_validation_repair_prompt,
)
from ai8video.media.motion.hyperframes_overlay_semantic import compile_semantic_artifact
from ai8video.media.motion.hyperframes_overlay_legacy import (
    MIN_COVERAGE_RATIO,
    _artifact_summary,
    _normalize_artifact,
    _parse_critique,
    _parse_json_object,
)


class _LegacyArtifactPath(Exception):
    def __init__(self, value: dict[str, Any]) -> None:
        super().__init__("legacy-artifact")
        self.value = value


@dataclass(frozen=True)
class HarnessResult:
    artifact: dict[str, Any]
    composition_html: str
    motion_manifest: dict[str, Any]
    summary: dict[str, Any]
    semantic_spec: dict[str, Any] | None = None


def build_hyperframes_overlay(
    llm: Callable[[str], str],
    episode: EpisodePrompt,
    media: dict[str, Any],
    dialogue_text: str = "",
    font_family: str = "",
    critique_enabled: bool = True,
    quality_retry_count: int = 5,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> HarnessResult:
    """AI 一次返回自审结果与语义 JSON；遗留 scenes artifact 仍可走旧路径。"""

    def gated_llm(prompt: str) -> str:
        text = str(llm(prompt) or "")
        try:
            value = _parse_json_object(text)
        except Exception:
            return text
        if isinstance(value, dict) and "scenes" in value and not _looks_semantic(value):
            raise _LegacyArtifactPath(value)
        return text

    try:
        agent = run_semantic_agent(
            gated_llm,
            episode,
            media,
            dialogue_text,
            max_turns=max(1, int(quality_retry_count) + 1),
            retry_callback=progress_callback,
        )
    except _LegacyArtifactPath as legacy:
        return _finish_legacy_artifact(
            legacy.value, llm, episode, media, dialogue_text,
            font_family=font_family, critique_enabled=critique_enabled,
        )

    semantic_spec = agent.semantic
    artifact = compile_semantic_artifact(semantic_spec, media)
    summary = _artifact_summary(
        artifact,
        media,
        {"scores": {}, "notes": [], "converged": None, "skipped": True},
    )
    summary["harness"] = "hyperframes-overlay-v5-reviewed-json"
    summary["semanticContract"] = True
    summary["copyQualityMode"] = "ai-audit-only"
    summary["agentTurns"] = agent.turns
    summary["aiAudit"] = agent.audit
    summary["qualityRetryCount"] = max(0, int(quality_retry_count))
    summary["beatIntervalSeconds"] = semantic_spec["beatIntervalSeconds"]
    summary["smartBeatInterval"] = bool(media.get("smartBeatInterval"))
    summary["layoutRecipe"] = semantic_spec["layoutRecipe"]
    summary["motionRecipe"] = semantic_spec["motionRecipe"]
    summary["componentRecipes"] = semantic_spec["componentRecipes"]
    return HarnessResult(
        artifact=artifact,
        composition_html=build_composition_html(artifact, media, font_family=font_family),
        motion_manifest=build_motion_manifest(artifact, media),
        summary=summary,
        semantic_spec=semantic_spec,
    )


def revise_hyperframes_overlay(
    llm: Callable[[str], str],
    artifact: dict[str, Any],
    episode: EpisodePrompt,
    media: dict[str, Any],
    *,
    dialogue_text: str,
    validation_error: str,
    font_family: str,
    semantic_spec: dict[str, Any] | None = None,
) -> HarnessResult:
    if semantic_spec is not None:
        revised = compile_semantic_artifact(semantic_spec, media)
        summary = _artifact_summary(
            revised,
            media,
            {"scores": {}, "notes": [], "converged": None, "skipped": True},
        )
        summary.update({
            "harness": "hyperframes-overlay-v5-reviewed-json",
            "semanticContract": True,
            "validationRevision": True,
            "layoutRecipe": semantic_spec["layoutRecipe"],
            "motionRecipe": semantic_spec["motionRecipe"],
            "componentRecipes": semantic_spec["componentRecipes"],
        })
        return HarnessResult(
            artifact=revised,
            composition_html=build_composition_html(revised, media, font_family=font_family),
            motion_manifest=build_motion_manifest(revised, media),
            summary=summary,
            semantic_spec=semantic_spec,
        )
    revised = _normalize_artifact(
        _parse_json_object(
            llm(build_validation_repair_prompt(
                artifact,
                episode,
                media,
                dialogue_text=dialogue_text,
                validation_error=validation_error,
            ))
        ),
        media,
        dialogue_text,
    )
    summary = _artifact_summary(revised, media, {"scores": {}, "notes": [], "converged": None, "skipped": True})
    summary["validationRevision"] = True
    return HarnessResult(
        artifact=revised,
        composition_html=build_composition_html(revised, media, font_family=font_family),
        motion_manifest=build_motion_manifest(revised, media),
        summary=summary,
    )


def _finish_legacy_artifact(
    model_value: dict[str, Any],
    llm: Callable[[str], str],
    episode: EpisodePrompt,
    media: dict[str, Any],
    dialogue_text: str,
    *,
    font_family: str,
    critique_enabled: bool,
) -> HarnessResult:
    artifact = _normalize_artifact(model_value, media, dialogue_text)
    critique = _converge_critique(llm, artifact, episode, media, dialogue_text) if critique_enabled else {
        "artifact": artifact,
        "scores": {},
        "notes": [],
        "converged": None,
        "skipped": True,
    }
    artifact = critique.pop("artifact")
    return HarnessResult(
        artifact=artifact,
        composition_html=build_composition_html(artifact, media, font_family=font_family),
        motion_manifest=build_motion_manifest(artifact, media),
        summary=_artifact_summary(artifact, media, critique),
    )


def _converge_critique(
    llm: Callable[[str], str],
    artifact: dict[str, Any],
    episode: EpisodePrompt,
    media: dict[str, Any],
    dialogue_text: str,
) -> dict[str, Any]:
    for _ in range(3):
        critique = _parse_critique(llm(_build_critique_prompt(artifact, episode, media, dialogue_text)))
        if min(critique["scores"].values()) >= 4:
            return {**critique, "artifact": artifact, "converged": True}
        revised = critique.get("revisedArtifact")
        if not isinstance(revised, dict):
            return {**critique, "artifact": artifact, "converged": False}
        artifact = _normalize_artifact(revised, media, dialogue_text)
    return {**critique, "artifact": artifact, "converged": False}


def _looks_semantic(value: dict[str, Any]) -> bool:
    if "scenes" in value or "design" in value:
        return False
    return any(key in value for key in (
        "designDirection", "layoutRecipe", "motionRecipe", "motionRecipes", "density",
        "theme", "anchor", "motion", "decorations", "palette", "beats",
    ))


def _build_generation_prompt(
    episode: EpisodePrompt,
    media: dict[str, Any],
    dialogue_text: str = "",
) -> str:
    return build_generation_prompt(
        episode,
        media,
        minimum_coverage_ratio=MIN_COVERAGE_RATIO,
        dialogue_text=dialogue_text,
    )


def _build_critique_prompt(
    artifact: dict[str, Any],
    episode: EpisodePrompt,
    media: dict[str, Any],
    dialogue_text: str = "",
) -> str:
    return build_critique_prompt(artifact, episode, media, dialogue_text)
