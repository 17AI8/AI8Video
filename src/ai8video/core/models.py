from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedRequest:
    raw_text: str
    mode: str
    video_count: int | None = None
    reference_image: str | None = None
    reference_image_custom_prompt: str | None = None
    style_hint: str | None = None
    core_keywords: str | None = None
    duration_seconds: int | None = 10
    ratio: str = "9:16"
    resolution: str = "480p"
    preset: str = "custom"
    concurrent_generation: bool = False
    iterative_generation: bool = False
    html_motion_overlay_enabled: bool = False
    reference_image_transform_options: dict[str, bool] | None = None


@dataclass
class ConversationDraft:
    raw_text: str | None = None
    mode: str | None = None
    video_count: int | None = None
    reference_image: str | None = None
    reference_image_custom_prompt: str | None = None
    reference_image_enabled: bool | None = None
    content_completion_mode: str | None = None
    style_hint: str | None = None
    core_keywords: str | None = None
    duration_seconds: int | None = 10
    ratio: str = "9:16"
    resolution: str = "480p"
    preset: str = "custom"
    concurrent_generation: bool | None = None
    iterative_generation: bool | None = None
    html_motion_overlay_enabled: bool | None = None
    reference_image_transform_options: dict[str, bool] | None = None

    def to_request(self) -> ParsedRequest:
        if not self.raw_text:
            raise ValueError("draft.raw_text is required")
        mode = self.mode or ("batch_videos" if (self.video_count or 0) > 1 else "single_video")
        return ParsedRequest(
            raw_text=self.raw_text,
            mode=mode,
            video_count=self.video_count,
            reference_image=self.reference_image if self.reference_image_enabled else None,
            reference_image_custom_prompt=(
                self.reference_image_custom_prompt.strip()
                if self.reference_image_enabled and self.reference_image and str(self.reference_image_custom_prompt or "").strip()
                else None
            ),
            style_hint=self.style_hint,
            core_keywords=self.core_keywords,
            duration_seconds=self.duration_seconds,
            ratio=self.ratio,
            resolution=self.resolution,
            preset=self.preset,
            concurrent_generation=bool(self.concurrent_generation),
            iterative_generation=bool(self.iterative_generation),
            html_motion_overlay_enabled=bool(self.html_motion_overlay_enabled),
            reference_image_transform_options=(
                self.reference_image_transform_options
                if self.reference_image_enabled and self.reference_image and self.reference_image_transform_options
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rawText": self.raw_text,
            "mode": self.mode,
            "videoCount": self.video_count,
            "referenceImage": self.reference_image,
            "referenceImageCustomPrompt": self.reference_image_custom_prompt,
            "referenceImageEnabled": self.reference_image_enabled,
            "contentCompletionMode": self.content_completion_mode,
            "styleHint": self.style_hint,
            "coreKeywords": self.core_keywords,
            "durationSeconds": self.duration_seconds,
            "ratio": self.ratio,
            "resolution": self.resolution,
            "preset": self.preset,
            "concurrentGeneration": self.concurrent_generation,
            "iterativeGeneration": self.iterative_generation,
            "htmlMotionOverlayEnabled": self.html_motion_overlay_enabled,
            "referenceImageTransformOptions": self.reference_image_transform_options,
        }


@dataclass
class VideoPrompt:
    index: int
    title: str
    prompt: str
    source_summary: str = ""
    keyword_guidance: dict[str, Any] = field(default_factory=dict)
    archive_subdir: str = "video"


@dataclass
class FirstFrameAsset:
    first_frame_storage_key: str | None = None
    first_frame_image_url: str | None = None
    first_frame_token: str | None = None
    source: str | None = None


@dataclass
class QuickVideoJob:
    video_index: int
    job_id: str
    status: str = "pending"
    prompt: str = ""
    video_url: str | None = None
    storage_key: str | None = None
    cover_image_url: str | None = None
    cover_image_storage_key: str | None = None
    final_frame_storage_key: str | None = None
    local_video_path: str | None = None
    local_cover_path: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    retryable: bool | None = None
    provider_status: str | None = None
    provider_progress: int | None = None
    stage_label: str | None = None
    segment_index: int | None = None
    segment_label: str | None = None


@dataclass
class ArchivedAsset:
    video_index: int
    job_id: str
    backend: str
    status: str
    archive_key: str | None = None
    archive_url: str | None = None
    archive_cover_key: str | None = None
    archive_cover_url: str | None = None
    local_path: str | None = None
    local_cover_path: str | None = None
    manifest_path: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    request: ParsedRequest
    videos: list[VideoPrompt]
    first_frame: FirstFrameAsset | None
    jobs: list[QuickVideoJob]
    dry_run: bool
    outcomes: list["GenerationOutcome"] = field(default_factory=list)
    archives: list[ArchivedAsset] = field(default_factory=list)
    asset_records: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.__dict__,
            "videos": [item.__dict__ for item in self.videos],
            "firstFrame": None if self.first_frame is None else self.first_frame.__dict__,
            "jobs": [item.__dict__ for item in self.jobs],
            "outcomes": [item.__dict__ for item in self.outcomes],
            "archives": [item.__dict__ for item in self.archives],
            "assetRecords": self.asset_records,
            "dryRun": self.dry_run,
        }


@dataclass
class ConversationState:
    session_id: str
    draft: ConversationDraft = field(default_factory=ConversationDraft)
    awaiting: str | None = None
    completed_runs: int = 0
    last_result: dict[str, Any] | None = None
    batch_request: dict[str, Any] | None = None


@dataclass
class ChatReply:
    text: str
    stage: str
    awaiting: str | None = None
    draft: ConversationDraft | None = None
    result: PipelineResult | None = None
    result_payload: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "stage": self.stage,
            "awaiting": self.awaiting,
            "draft": None if self.draft is None else self.draft.to_dict(),
            "meta": self.meta or {},
            "result": self.result_payload if self.result_payload is not None else (None if self.result is None else self.result.to_dict()),
        }


@dataclass
class GenerationOutcome:
    video_index: int
    job_id: str
    status: str
    decision: str
    reasons: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
