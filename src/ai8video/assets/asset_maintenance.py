from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai8video.assets.asset_store import JsonlAssetStore


AssetRecord = dict[str, object]
AssetRecordPredicate = Callable[[AssetRecord], bool]


class AssetMaintenanceService:
    def __init__(self, asset_store: JsonlAssetStore, project_root: Path):
        self.asset_store = asset_store
        self.project_root = project_root.resolve()

    def find_user_generated_record(
        self,
        relative_key: str,
        video_path: Path,
    ) -> AssetRecord:
        for record in reversed(self.asset_store.read_all()):
            if self._record_matches_user_generated_video(record, relative_key, video_path):
                return record
        return {}

    def save_tts_narration_text(
        self,
        relative_key: str,
        video_path: Path,
        text: str,
    ) -> None:
        def update_matching_record(records: list[AssetRecord]) -> None:
            for index in range(len(records) - 1, -1, -1):
                existing_record = records[index]
                if not self._record_matches_user_generated_video(
                    existing_record,
                    relative_key,
                    video_path,
                ):
                    continue
                records[index] = self._with_tts_narration_text(existing_record, text)
                return
            raise LookupError("台词已删除")

        self.asset_store.mutate_records(update_matching_record)

    def save_extension_video_prompt(self, relative_key: str, video_path: Path, text: str) -> dict[str, Any]:
        def update_matching_record(records: list[AssetRecord]) -> dict[str, Any]:
            for index in range(len(records) - 1, -1, -1):
                existing_record = records[index]
                if not self._record_matches_user_generated_video(existing_record, relative_key, video_path):
                    continue
                updated_record = dict(existing_record)
                generation_meta = updated_record.get("generationMeta")
                updated_meta = dict(generation_meta) if isinstance(generation_meta, dict) else {}
                updated_meta.pop("userVideoPrompt", None)
                updated_meta["extensionVideoPrompt"] = text
                updated_meta["extensionVideoPromptUpdatedAt"] = datetime.now(timezone.utc).isoformat()
                updated_record["generationMeta"] = updated_meta
                records[index] = updated_record
                return updated_record
            raise LookupError("视频提示词已删除")

        return self.asset_store.mutate_records(update_matching_record)

    def save_html_motion_overlay_result(
        self,
        relative_key: str,
        video_path: Path,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        def update_matching_record(records: list[AssetRecord]) -> dict[str, Any]:
            for index in range(len(records) - 1, -1, -1):
                existing_record = records[index]
                if not self._record_matches_user_generated_video(
                    existing_record,
                    relative_key,
                    video_path,
                ):
                    continue
                updated_record = self._with_html_motion_overlay_result(existing_record, result)
                records[index] = updated_record
                return updated_record
            raise LookupError("视频提示词已删除")

        return self.asset_store.mutate_records(update_matching_record)

    def save_extension_frame_result(
        self,
        relative_key: str,
        video_path: Path,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        def update_matching_record(records: list[AssetRecord]) -> dict[str, Any]:
            for index in range(len(records) - 1, -1, -1):
                existing_record = records[index]
                if not self._record_matches_user_generated_video(existing_record, relative_key, video_path):
                    continue
                updated_record = dict(existing_record)
                archive_meta = updated_record.get("archiveMeta")
                updated_meta = dict(archive_meta) if isinstance(archive_meta, dict) else {}
                updated_meta["extensionFrame"] = dict(result)
                updated_record["archiveMeta"] = updated_meta
                records[index] = updated_record
                return updated_record
            raise LookupError("原视频归档记录不存在")

        return self.asset_store.mutate_records(update_matching_record)

    def clear_extension_frame_result(self, relative_key: str, video_path: Path) -> dict[str, Any]:
        def update_matching_record(records: list[AssetRecord]) -> dict[str, Any]:
            for index in range(len(records) - 1, -1, -1):
                existing_record = records[index]
                if not self._record_matches_user_generated_video(existing_record, relative_key, video_path):
                    continue
                updated_record = dict(existing_record)
                archive_meta = updated_record.get("archiveMeta")
                updated_meta = dict(archive_meta) if isinstance(archive_meta, dict) else {}
                updated_meta.pop("extensionFrame", None)
                updated_meta.pop("extensionFrameVariants", None)
                updated_record["archiveMeta"] = updated_meta
                records[index] = updated_record
                return updated_record
            raise LookupError("原视频归档记录不存在")

        return self.asset_store.mutate_records(update_matching_record)

    def save_extension_frame_variant_result(
        self,
        relative_key: str,
        video_path: Path,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        def update_matching_record(records: list[AssetRecord]) -> dict[str, Any]:
            for index in range(len(records) - 1, -1, -1):
                existing_record = records[index]
                if not self._record_matches_user_generated_video(existing_record, relative_key, video_path):
                    continue
                updated_record = dict(existing_record)
                archive_meta = updated_record.get("archiveMeta")
                updated_meta = dict(archive_meta) if isinstance(archive_meta, dict) else {}
                key = str(result.get("frameKey") or "")
                variants = [item for item in updated_meta.get("extensionFrameVariants", []) if item.get("frameKey") != key]
                updated_meta["extensionFrameVariants"] = [*variants, dict(result)]
                updated_record["archiveMeta"] = updated_meta
                records[index] = updated_record
                return updated_record
            raise LookupError("原视频归档记录不存在")

        return self.asset_store.mutate_records(update_matching_record)

    def remove_records(self, should_remove: AssetRecordPredicate) -> tuple[int, int]:
        def remove_matching_records(records: list[AssetRecord]) -> tuple[int, int]:
            original_count = len(records)
            records[:] = [record for record in records if not should_remove(record)]
            return original_count - len(records), len(records)

        return self.asset_store.mutate_records(remove_matching_records)

    def _record_matches_user_generated_video(
        self,
        record: AssetRecord,
        relative_key: str,
        video_path: Path,
    ) -> bool:
        normalized_key = str(relative_key or "").strip()
        filename = Path(normalized_key).name
        for field_name in (
            "userGeneratedKey",
            "archiveKey",
            "archiveUrl",
            "storageKey",
            "archiveLocalPath",
            "userGeneratedLocalPath",
            "localVideoPath",
        ):
            candidate = str(record.get(field_name) or "").strip()
            if not candidate:
                continue
            if candidate == normalized_key or Path(candidate).name == filename:
                return True
            try:
                if self._resolve_asset_path(candidate) == video_path.resolve():
                    return True
            except (OSError, ValueError):
                continue
        return False

    def _resolve_asset_path(self, value: str) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        return candidate.resolve()

    @staticmethod
    def _with_tts_narration_text(record: AssetRecord, text: str) -> AssetRecord:
        updated_record = dict(record)
        generation_meta = updated_record.get("generationMeta")
        updated_generation_meta = (
            dict(generation_meta) if isinstance(generation_meta, dict) else {}
        )
        updated_generation_meta["userTtsNarrationText"] = text
        updated_generation_meta["userTtsNarrationUpdatedAt"] = datetime.now(
            timezone.utc
        ).isoformat()
        updated_record["generationMeta"] = updated_generation_meta
        return updated_record

    @staticmethod
    def _with_html_motion_overlay_result(
        record: AssetRecord,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        updated_record = dict(record)
        overlay_result = dict(result)
        updated_at = datetime.now(timezone.utc).isoformat()
        archive_meta = updated_record.get("archiveMeta")
        updated_archive_meta = dict(archive_meta) if isinstance(archive_meta, dict) else {}
        generation_meta = updated_record.get("generationMeta")
        updated_generation_meta = dict(generation_meta) if isinstance(generation_meta, dict) else {}
        updated_generation_meta["htmlMotionOverlayRegeneration"] = {
            **overlay_result,
            "updatedAt": updated_at,
        }
        if overlay_result.get("status") in {"applied", "degraded"}:
            updated_record["htmlMotionOverlay"] = overlay_result
            updated_archive_meta["htmlMotionOverlay"] = overlay_result
        updated_record["archiveMeta"] = updated_archive_meta
        updated_record["generationMeta"] = updated_generation_meta
        return updated_record
