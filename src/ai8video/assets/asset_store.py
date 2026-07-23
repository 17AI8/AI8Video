from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from ai8video.generation.business_prompt import sanitize_internal_fidelity_notes
from ai8video.generation.generation_batch_context import (
    get_current_generation_batch_id,
    get_current_generation_session_id,
)
from ai8video.core.legacy_payload import normalize_legacy_video_payload
from ai8video.core.models import ArchivedAsset, VideoPrompt, FirstFrameAsset, ParsedRequest, QuickVideoJob, GenerationOutcome


MutationResult = TypeVar("MutationResult")

_PATH_LOCKS: dict[Path, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _get_path_lock(path: Path) -> threading.RLock:
    normalized_path = path.expanduser().resolve(strict=False)
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(normalized_path, threading.RLock())


class JsonlAssetStore:
    def __init__(self, path: str | Path = "temp/ai8video/assets.jsonl"):
        self.path = Path(path)
        self._lock = _get_path_lock(self.path)

    def append(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        job: QuickVideoJob,
        outcome: GenerationOutcome,
        first_frame: FirstFrameAsset | None = None,
        archive: ArchivedAsset | None = None,
    ) -> dict[str, Any]:
        record = {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "sessionId": get_current_generation_session_id(),
            "generationBatchId": get_current_generation_batch_id(),
            "videoIndex": video.index,
            "videoTitle": sanitize_internal_fidelity_notes(video.title),
            "jobId": job.job_id,
            "status": job.status,
            "generationStatus": "generated" if outcome.decision == "generated" else "failed",
            "generationReasons": outcome.reasons,
            "generationMeta": outcome.meta,
            "storageKey": job.storage_key,
            "coverImageStorageKey": job.cover_image_storage_key,
            "finalFrameStorageKey": job.final_frame_storage_key,
            "videoUrl": job.video_url,
            "coverImageUrl": job.cover_image_url,
            "archiveStatus": None if archive is None else archive.status,
            "archiveBackend": None if archive is None else archive.backend,
            "archiveKey": None if archive is None else archive.archive_key,
            "archiveUrl": None if archive is None else archive.archive_url,
            "archiveCoverKey": None if archive is None else archive.archive_cover_key,
            "archiveCoverUrl": None if archive is None else archive.archive_cover_url,
            "archiveLocalPath": None if archive is None else archive.local_path,
            "archiveLocalCoverPath": None if archive is None else archive.local_cover_path,
            "archiveManifestPath": None if archive is None else archive.manifest_path,
            "archiveSizeBytes": None if archive is None else archive.size_bytes,
            "archiveSha256": None if archive is None else archive.sha256,
            "archiveError": None if archive is None else archive.error,
            "archiveMeta": None if archive is None else archive.meta,
            "htmlMotionOverlay": None if archive is None else archive.meta.get("htmlMotionOverlay"),
            "usage": job.usage,
            "prompt": sanitize_internal_fidelity_notes(video.prompt),
            "keywordGuidance": video.keyword_guidance,
            "generatedOutputReview": (video.keyword_guidance or {}).get("generated_output_review"),
            "request": {
                "mode": request.mode,
                "videoCount": request.video_count,
                "styleHint": request.style_hint,
                "durationSeconds": request.duration_seconds,
                "ratio": request.ratio,
                "resolution": request.resolution,
                "preset": request.preset,
                "iterativeGeneration": request.iterative_generation,
                "htmlMotionOverlayEnabled": request.html_motion_overlay_enabled,
            },
            "firstFrame": None if first_frame is None else first_frame.__dict__,
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file_handle:
                file_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def read_all(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_all_unlocked()

    def mutate_records(
        self,
        mutation: Callable[[list[dict[str, Any]]], MutationResult],
    ) -> MutationResult:
        with self._lock:
            records = self._read_all_unlocked()
            result = mutation(records)
            self._rewrite_all_unlocked(records)
            return result

    def rewrite_all(self, records: list[dict[str, Any]]) -> None:
        with self._lock:
            self._rewrite_all_unlocked(records)

    def _read_all_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as file_handle:
            for line in file_handle:
                line = line.strip()
                if line:
                    records.append(_sanitize_asset_record(json.loads(line)))
        return records

    def _rewrite_all_unlocked(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_path_value = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_path_value)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as file_handle:
                file_descriptor = -1
                for record in records:
                    file_handle.write(
                        json.dumps(_sanitize_asset_record(record), ensure_ascii=False) + "\n"
                    )
                file_handle.flush()
                os.fsync(file_handle.fileno())
            temporary_path.replace(self.path)
        except Exception:
            if file_descriptor >= 0:
                os.close(file_descriptor)
            temporary_path.unlink(missing_ok=True)
            raise


def _sanitize_asset_record(record: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(normalize_legacy_video_payload(record))
    for key in ("videoTitle", "prompt"):
        if cleaned.get(key) is not None:
            cleaned[key] = sanitize_internal_fidelity_notes(str(cleaned[key]))
    return cleaned
