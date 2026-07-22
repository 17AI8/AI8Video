from __future__ import annotations

import sys
import tempfile
import types
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from ai8video.core.config import AI8VideoConfig
from ai8video.core.models import EpisodePrompt, ParsedRequest, QuickVideoJob, GenerationOutcome
from ai8video.assets import user_generated_results as generated_results
from ai8video.assets import user_recycle_bin
from ai8video.assets.video_asset_archiver import (
    VideoAssetArchiver,
    _local_tts_narration_text,
    trim_video_start,
)


class AI8VideoVideoAssetArchiverTest(unittest.TestCase):
    def test_local_tts_uses_dialogue_instead_of_internal_source_summary(self) -> None:
        episode = EpisodePrompt(
            index=1,
            title="稀缺机会",
            prompt='画外音（坚定）：“一个城市只开放一个名额。”\n镜头：人物转身。',
            source_summary="基于脚本71与脚本75集中营造紧迫感。",
        )

        text = _local_tts_narration_text(None, episode)

        self.assertEqual(text, "一个城市只开放一个名额")
        self.assertNotIn("基于脚本", text)

    def test_local_tts_does_not_fall_back_to_source_summary_without_dialogue(self) -> None:
        episode = EpisodePrompt(
            index=1,
            title="纯画面",
            prompt="镜头：人物从远处走近。",
            source_summary="内部选材说明，不应成为配音。",
        )

        self.assertEqual(_local_tts_narration_text(None, episode), "")

    def test_local_tts_does_not_read_visual_prompt_when_source_summary_is_empty(self) -> None:
        episode = EpisodePrompt(
            index=1,
            title="纯画面延长",
            prompt="【0-10秒，中景】人物抬手触碰光点。情绪：坚定。音效：电子律动。",
        )

        self.assertEqual(_local_tts_narration_text(None, episode), "")

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _build_config(self, **overrides) -> AI8VideoConfig:
        base = dict(
            dry_run=False,
            archive_backend="auto",
            archive_local_dir=str(self.root / "archive"),
            archive_s3_endpoint="https://oss.example.com",
            archive_s3_bucket="ai8video-bucket",
            archive_s3_region="oss-cn-hangzhou",
            archive_s3_access_key="ak",
            archive_s3_secret_key="sk",
            archive_s3_prefix="AI8video",
            archive_public_base_url="https://cdn.example.com/ai8video",
        )
        base.update(overrides)
        return AI8VideoConfig(**base)

    def _sample_request(self) -> ParsedRequest:
        return ParsedRequest(raw_text="老板在会议室讲封号风险", mode="single_prompt")

    def _sample_episode(self) -> EpisodePrompt:
        return EpisodePrompt(index=1, title="单条视频", prompt="老板在会议室讲封号风险")

    def _sample_job(self) -> QuickVideoJob:
        return QuickVideoJob(
            episode_index=1,
            job_id="job-001",
            status="succeeded",
            prompt="老板在会议室讲封号风险",
            video_url="https://example.com/video.mp4",
            cover_image_url="https://example.com/cover.jpg",
            storage_key="mobile:job-001",
        )

    def _sample_generation_outcome(self) -> GenerationOutcome:
        return GenerationOutcome(
            episode_index=1,
            job_id="job-001",
            status="completed",
            decision="generated",
            reasons=[],
        )

    def test_resolve_backend_prefers_s3_when_auto_and_credentials_present(self) -> None:
        archiver = VideoAssetArchiver(self._build_config())
        self.assertEqual(archiver._resolve_backend(), "s3")

    def test_humanize_failed_video_reason_hides_remote_disconnect_detail(self) -> None:
        reason = (
            "('Connection aborted.', "
            "RemoteDisconnected('Remote end closed connection without response'))"
        )

        display = user_recycle_bin.humanize_failed_video_reason(reason)

        self.assertIn("生成服务连接中断", display)
        self.assertIn("结果区/回收站", display)
        self.assertNotIn("RemoteDisconnected", display)
        self.assertNotIn("Connection aborted", display)

    def test_resolve_backend_falls_back_to_local_when_s3_credentials_missing(self) -> None:
        archiver = VideoAssetArchiver(
            self._build_config(
                archive_s3_access_key=None,
                archive_s3_secret_key=None,
            )
        )
        self.assertEqual(archiver._resolve_backend(), "local")

    def test_archive_s3_returns_public_urls_and_uploads_manifest(self) -> None:
        config = self._build_config(archive_backend="s3")
        archiver = VideoAssetArchiver(config)

        video_temp = self.root / "downloaded-video.mp4"
        video_temp.write_bytes(b"mp4")
        cover_temp = self.root / "downloaded-cover.jpg"
        cover_temp.write_bytes(b"jpg")
        uploads: list[tuple[str, str, str | None]] = []

        class _FakeSession:
            def client(self, *args, **kwargs):  # noqa: ANN002, ANN003
                return "fake-client"

        fake_boto3 = types.SimpleNamespace(
            session=types.SimpleNamespace(Session=lambda: _FakeSession())
        )

        with patch.dict(sys.modules, {"boto3": fake_boto3}):
            with patch.object(
                VideoAssetArchiver,
                "_download_to_tempfile",
                side_effect=[
                    (str(video_temp), {"sha256": "video-sha", "size_bytes": 3}),
                    (str(cover_temp), {"sha256": "cover-sha", "size_bytes": 3}),
                ],
            ), patch.object(
                VideoAssetArchiver,
                "_upload_file_to_s3",
                side_effect=lambda client, file_path, key, content_type=None: uploads.append((client, key, content_type)),
            ), patch(
                "ai8video.assets.video_asset_archiver.mix_background_music",
                return_value={"enabled": False, "status": "skipped", "reason": "no background music"},
            ), patch(
                "ai8video.assets.video_asset_archiver.trim_video_start",
                return_value={"enabled": True, "status": "skipped", "reason": "test bypass"},
            ), patch(
                "ai8video.assets.video_asset_archiver.apply_video_text_overlay",
                return_value={"enabled": False, "status": "skipped", "reason": "test bypass"},
            ):
                archived = archiver.archive(
                    self._sample_request(),
                    self._sample_episode(),
                    self._sample_job(),
                    self._sample_generation_outcome(),
                )

        self.assertEqual(archived.backend, "s3")
        self.assertEqual(archived.status, "archived")
        self.assertTrue(archived.archive_url.startswith("https://cdn.example.com/ai8video/"))
        self.assertTrue(archived.archive_cover_url.startswith("https://cdn.example.com/ai8video/"))
        self.assertEqual(archived.meta["bucket"], "ai8video-bucket")
        self.assertEqual(archived.meta["endpoint"], "https://oss.example.com")
        self.assertTrue(Path(archived.manifest_path).exists())
        self.assertEqual(len(uploads), 3)
        self.assertEqual(uploads[0][0], "fake-client")
        self.assertEqual(uploads[0][2], "video/mp4")
        self.assertEqual(uploads[2][2], "application/json")
        self.assertEqual(archived.meta["backgroundMusic"]["status"], "skipped")

    def test_archive_local_writes_only_user_generated_video_and_cover(self) -> None:
        config = self._build_config(
            archive_backend="local",
            archive_s3_access_key=None,
            archive_s3_secret_key=None,
        )
        archiver = VideoAssetArchiver(config)
        video_temp = self.root / "downloaded-video.mp4"
        video_temp.write_bytes(b"mp4")
        cover_temp = self.root / "downloaded-cover.jpg"
        cover_temp.write_bytes(b"jpg")
        generated_root = self.root / "用户生成结果"

        with patch.object(generated_results, "USER_GENERATED_RESULT_ROOT", generated_root), patch.object(
            VideoAssetArchiver,
            "_download_to_tempfile",
            side_effect=[
                (str(video_temp), {"sha256": "video-sha", "size_bytes": 3}),
                (str(cover_temp), {"sha256": "cover-sha", "size_bytes": 3}),
            ],
        ), patch(
            "ai8video.assets.video_asset_archiver.mix_background_music",
            return_value={"enabled": False, "status": "skipped", "reason": "no background music"},
        ), patch(
            "ai8video.assets.video_asset_archiver.trim_video_start",
            return_value={"enabled": True, "status": "skipped", "reason": "test bypass"},
        ), patch(
            "ai8video.assets.video_asset_archiver.apply_video_text_overlay",
            return_value={"enabled": False, "status": "skipped", "reason": "test bypass"},
        ):
            archived = archiver.archive(
                self._sample_request(),
                self._sample_episode(),
                self._sample_job(),
                self._sample_generation_outcome(),
            )

        result_video = generated_root / archived.archive_key
        result_cover = generated_root / archived.archive_cover_key
        self.assertTrue(result_video.exists())
        self.assertTrue(result_cover.exists())
        self.assertEqual(result_video.read_bytes(), b"mp4")
        self.assertEqual(result_cover.read_bytes(), b"jpg")
        self.assertEqual(Path(archived.archive_key).parent, Path("video"))
        self.assertFalse((Path(config.archive_local_dir) / archived.archive_key).exists())
        self.assertFalse((Path(config.archive_local_dir) / archived.archive_cover_key).exists())
        self.assertEqual(Path(archived.local_path), result_video)
        self.assertEqual(archived.meta["backgroundMusic"]["status"], "skipped")

    def test_archive_local_mixes_background_music_before_mirroring(self) -> None:
        config = self._build_config(
            archive_backend="local",
            archive_s3_access_key=None,
            archive_s3_secret_key=None,
        )
        archiver = VideoAssetArchiver(config)
        video_temp = self.root / "downloaded-video.mp4"
        video_temp.write_bytes(b"mp4")
        generated_root = self.root / "用户生成结果"

        def _mix_video(path: Path, **kwargs) -> dict:
            self.assertIsNone(kwargs.get("preserved_audio_volume_override"))
            path.write_bytes(b"mixed-mp4")
            return {"enabled": True, "status": "mixed", "musicName": "theme.mp3"}

        with patch.object(generated_results, "USER_GENERATED_RESULT_ROOT", generated_root), patch.object(
            VideoAssetArchiver,
            "_download_to_tempfile",
            return_value=(str(video_temp), {"sha256": "video-sha", "size_bytes": 3}),
        ), patch(
            "ai8video.assets.video_asset_archiver.mix_background_music",
            side_effect=_mix_video,
        ) as mix_music, patch(
            "ai8video.assets.video_asset_archiver.trim_video_start",
            return_value={"enabled": True, "status": "skipped", "reason": "test bypass"},
        ), patch(
            "ai8video.assets.video_asset_archiver.apply_video_text_overlay",
            return_value={"enabled": False, "status": "skipped", "reason": "test bypass"},
        ), patch(
            "ai8video.assets.video_asset_archiver.file_meta",
            return_value={"sha256": "mixed-sha", "size_bytes": 9},
        ):
            job = self._sample_job()
            job.cover_image_url = None
            archived = archiver.archive(
                self._sample_request(),
                self._sample_episode(),
                job,
                self._sample_generation_outcome(),
            )

        mix_music.assert_called_once()
        result_video = generated_root / archived.archive_key
        self.assertEqual(result_video.read_bytes(), b"mixed-mp4")
        self.assertFalse((Path(config.archive_local_dir) / archived.archive_key).exists())
        self.assertEqual(archived.sha256, "mixed-sha")
        self.assertEqual(archived.size_bytes, 9)
        self.assertEqual(archived.meta["backgroundMusic"]["status"], "mixed")
        manifest = json.loads(Path(archived.manifest_path).read_text(encoding="utf-8"))
        self.assertEqual(manifest["backgroundMusic"]["musicName"], "theme.mp3")

    def test_archive_local_file_applies_postprocess_for_merged_video(self) -> None:
        config = self._build_config(
            archive_backend="local",
            archive_s3_access_key=None,
            archive_s3_secret_key=None,
        )
        archiver = VideoAssetArchiver(config)
        source_video = self.root / "merged.mp4"
        source_video.write_bytes(b"merged-raw")
        generated_root = self.root / "用户生成结果"
        calls: list[str] = []

        def _trim_video(path: Path) -> dict:
            calls.append("trim")
            self.assertEqual(path.read_bytes(), b"merged-raw")
            path.write_bytes(b"merged-trimmed")
            return {"enabled": True, "status": "trimmed", "trimStartSeconds": 0.1}

        def _burn_overlay(path: Path) -> dict:
            calls.append("overlay")
            self.assertEqual(path.read_bytes(), b"merged-trimmed")
            path.write_bytes(b"merged-overlay")
            return {"enabled": True, "status": "burned", "text": "花字"}

        def _mix_music(path: Path, **kwargs) -> dict:
            self.assertIsNone(kwargs.get("preserved_audio_volume_override"))
            calls.append("music")
            self.assertEqual(path.read_bytes(), b"merged-overlay")
            path.write_bytes(b"merged-music")
            return {"enabled": True, "status": "mixed", "musicName": "theme.mp3"}

        def _write_cover(video_path: Path, cover_path: Path) -> dict:
            self.assertEqual(video_path.read_bytes(), b"merged-music")
            cover_path.write_bytes(b"jpg")
            return {"status": "generated", "source": "test"}

        with patch.object(generated_results, "USER_GENERATED_RESULT_ROOT", generated_root), patch(
            "ai8video.assets.video_asset_archiver.trim_video_start",
            side_effect=_trim_video,
        ), patch(
            "ai8video.assets.video_asset_archiver.apply_video_text_overlay",
            side_effect=_burn_overlay,
        ), patch(
            "ai8video.assets.video_asset_archiver.mix_background_music",
            side_effect=_mix_music,
        ), patch(
            "ai8video.assets.video_asset_archiver.file_meta",
            return_value={"sha256": "merged-music-sha", "size_bytes": 12},
        ), patch.object(VideoAssetArchiver, "_extract_cover_frame", side_effect=_write_cover):
            archived = archiver.archive_local_file(
                source_video,
                self._sample_request(),
                self._sample_episode(),
                self._sample_job(),
                self._sample_generation_outcome(),
                extra_meta={"mergeMode": "merge2"},
            )

        self.assertEqual(calls, ["trim", "overlay", "music"])
        self.assertFalse(source_video.exists())
        result_video = generated_root / archived.archive_key
        self.assertEqual(result_video.read_bytes(), b"merged-music")
        self.assertEqual(archived.sha256, "merged-music-sha")
        self.assertEqual(archived.meta["source"], "merged-local-file")
        self.assertEqual(archived.meta["mergeMode"], "merge2")
        self.assertEqual(archived.meta["startTrim"]["status"], "trimmed")
        self.assertEqual(archived.meta["textOverlay"]["status"], "burned")
        self.assertEqual(archived.meta["backgroundMusic"]["status"], "mixed")
        manifest = json.loads(Path(archived.manifest_path).read_text(encoding="utf-8"))
        self.assertEqual(manifest["startTrim"]["status"], "trimmed")
        self.assertEqual(manifest["textOverlay"]["text"], "花字")
        self.assertEqual(manifest["backgroundMusic"]["musicName"], "theme.mp3")
        self.assertEqual(manifest["postprocess"]["mergeMode"], "merge2")

    def test_archive_local_file_skips_html_motion_before_text_tts_and_music(self) -> None:
        config = self._build_config(
            archive_backend="local",
            archive_s3_access_key=None,
            archive_s3_secret_key=None,
        )
        archiver = VideoAssetArchiver(config)
        source_video = self.root / "merged-html-motion.mp4"
        source_video.write_bytes(b"raw")
        request = self._sample_request()
        request.html_motion_overlay_enabled = True
        generated_root = self.root / "用户生成结果"
        calls: list[str] = []

        def transform(label: str, output: bytes):
            def _transform(path: Path, *_args, **_kwargs) -> dict:
                calls.append(label)
                path.write_bytes(output)
                return {"enabled": True, "status": "mixed"}
            return _transform

        with patch.object(generated_results, "USER_GENERATED_RESULT_ROOT", generated_root), patch(
            "ai8video.assets.video_asset_archiver.trim_video_start",
            side_effect=transform("trim", b"trim"),
        ), patch(
            "ai8video.assets.video_asset_archiver.apply_video_text_overlay",
            side_effect=transform("text", b"text"),
        ), patch(
            "ai8video.assets.video_asset_archiver.attach_local_tts_to_video",
            side_effect=transform("tts", b"tts"),
        ), patch(
            "ai8video.assets.video_asset_archiver.mix_background_music",
            side_effect=transform("music", b"music"),
        ), patch(
            "ai8video.assets.video_asset_archiver.file_meta",
            return_value={"sha256": "html-motion-sha", "size_bytes": 5},
        ), patch.object(
            VideoAssetArchiver,
            "_extract_cover_frame",
            return_value={"status": "generated", "source": "test"},
        ):
            archived = archiver.archive_local_file(
                source_video,
                request,
                EpisodePrompt(index=1, title="单条视频", prompt="旁白：老板在会议室讲封号风险"),
                self._sample_job(),
                self._sample_generation_outcome(),
            )

        self.assertEqual(calls, ["trim", "text", "tts", "music"])
        self.assertEqual(archived.meta["htmlMotionOverlay"]["status"], "skipped")
        self.assertEqual(
            archived.meta["localTts"]["narrationText"],
            "老板在会议室讲封号风险",
        )
        self.assertTrue(archived.meta["htmlMotionOverlay"]["manualOnly"])
        self.assertIn("视频播放界面", archived.meta["htmlMotionOverlay"]["reason"])
        manifest = json.loads(Path(archived.manifest_path).read_text(encoding="utf-8"))
        self.assertEqual(manifest["htmlMotionOverlay"]["status"], "skipped")

    def test_html_motion_enabled_request_still_archives_base_video_without_auto_burn(self) -> None:
        config = self._build_config(
            archive_backend="local",
            archive_s3_access_key=None,
            archive_s3_secret_key=None,
        )
        archiver = VideoAssetArchiver(config)
        source_video = self.root / "merged-degraded.mp4"
        source_video.write_bytes(b"raw")
        request = self._sample_request()
        request.html_motion_overlay_enabled = True
        generated_root = self.root / "用户生成结果"

        with patch.object(generated_results, "USER_GENERATED_RESULT_ROOT", generated_root), patch(
            "ai8video.assets.video_asset_archiver.trim_video_start",
            return_value={"enabled": True, "status": "trimmed"},
        ), patch(
            "ai8video.assets.video_asset_archiver.apply_video_text_overlay",
            return_value={"enabled": False, "status": "skipped"},
        ), patch(
            "ai8video.assets.video_asset_archiver.attach_local_tts_to_video",
            return_value={"enabled": False, "status": "skipped"},
        ), patch(
            "ai8video.assets.video_asset_archiver.mix_background_music",
            return_value={"enabled": False, "status": "skipped"},
        ) as mix_music, patch(
            "ai8video.assets.video_asset_archiver.file_meta",
            return_value={"sha256": "base-sha", "size_bytes": 3},
        ), patch.object(
            VideoAssetArchiver,
            "_extract_cover_frame",
            return_value={"status": "generated", "source": "test"},
        ):
            archived = archiver.archive_local_file(
                source_video,
                request,
                self._sample_episode(),
                self._sample_job(),
                self._sample_generation_outcome(),
            )

        mix_music.assert_called_once()
        self.assertEqual(archived.status, "archived")
        self.assertEqual(archived.meta["htmlMotionOverlay"]["status"], "skipped")
        self.assertTrue(archived.meta["htmlMotionOverlay"]["manualOnly"])

    def test_archive_local_file_boosts_tts_audio_above_background_music(self) -> None:
        config = self._build_config(
            archive_backend="local",
            archive_s3_access_key=None,
            archive_s3_secret_key=None,
        )
        archiver = VideoAssetArchiver(config)
        source_video = self.root / "merged.mp4"
        source_video.write_bytes(b"merged-raw")
        generated_root = self.root / "用户生成结果"
        mix_kwargs: list[dict] = []

        def _mix_music(path: Path, **kwargs) -> dict:
            mix_kwargs.append(kwargs)
            path.write_bytes(b"merged-music")
            return {
                "enabled": True,
                "status": "mixed",
                "musicName": "theme.mp3",
                "preservedAudioVolume": kwargs.get("preserved_audio_volume_override"),
            }

        with patch.object(generated_results, "USER_GENERATED_RESULT_ROOT", generated_root), patch(
            "ai8video.assets.video_asset_archiver.trim_video_start",
            return_value={"enabled": True, "status": "trimmed", "trimStartSeconds": 0.1},
        ), patch(
            "ai8video.assets.video_asset_archiver.apply_video_text_overlay",
            return_value={"enabled": False, "status": "skipped", "reason": "no text overlay"},
        ), patch(
            "ai8video.assets.video_asset_archiver.attach_local_tts_to_video",
            return_value={"enabled": True, "status": "mixed", "originalAudio": "replaced"},
        ), patch(
            "ai8video.assets.video_asset_archiver.background_music_volume",
            return_value=0.31,
        ), patch(
            "ai8video.assets.video_asset_archiver.mix_background_music",
            side_effect=_mix_music,
        ), patch(
            "ai8video.assets.video_asset_archiver.file_meta",
            return_value={"sha256": "merged-music-sha", "size_bytes": 12},
        ), patch.object(
            VideoAssetArchiver,
            "_extract_cover_frame",
            return_value={"status": "generated", "source": "test"},
        ):
            archived = archiver.archive_local_file(
                source_video,
                self._sample_request(),
                self._sample_episode(),
                self._sample_job(),
                self._sample_generation_outcome(),
            )

        self.assertEqual(mix_kwargs[0]["preserve_original_audio_override"], True)
        self.assertEqual(mix_kwargs[0]["preserved_audio_volume_override"], 1.0)
        self.assertEqual(archived.meta["backgroundMusic"]["preservedAudioVolume"], 1.0)

    def test_archive_local_file_fails_before_result_when_required_text_overlay_fails(self) -> None:
        config = self._build_config(
            archive_backend="local",
            archive_s3_access_key=None,
            archive_s3_secret_key=None,
        )
        archiver = VideoAssetArchiver(config)
        source_video = self.root / "merged.mp4"
        source_video.write_bytes(b"merged-raw")
        generated_root = self.root / "用户生成结果"
        recycle_root = self.root / "回收站"

        with patch.object(generated_results, "USER_GENERATED_RESULT_ROOT", generated_root), patch(
            "ai8video.assets.user_recycle_bin.USER_RECYCLE_BIN_ROOT",
            recycle_root,
        ), patch(
            "ai8video.assets.video_asset_archiver.trim_video_start",
            return_value={"enabled": True, "status": "trimmed", "trimStartSeconds": 0.1},
        ), patch(
            "ai8video.assets.video_asset_archiver.apply_video_text_overlay",
            return_value={"enabled": True, "status": "failed", "reason": "No module named 'PIL'"},
        ), patch(
            "ai8video.assets.video_asset_archiver.mix_background_music",
        ) as mix_music:
            with self.assertRaisesRegex(RuntimeError, "花字烧录失败"):
                archiver.archive_local_file(
                    source_video,
                    self._sample_request(),
                    self._sample_episode(),
                    self._sample_job(),
                    self._sample_generation_outcome(),
                    extra_meta={"mergeMode": "merge2"},
                )

        mix_music.assert_not_called()
        self.assertTrue(source_video.exists())
        self.assertEqual(list((generated_root / "video").glob("*.mp4")), [])
        recycled_videos = list((recycle_root).rglob("*.mp4"))
        self.assertEqual(len(recycled_videos), 1)
        self.assertEqual(recycled_videos[0].read_bytes(), b"merged-raw")
        manifest = json.loads(next(recycle_root.rglob("manifest.json")).read_text(encoding="utf-8"))
        self.assertIn("No module named 'PIL'", manifest["reason"])

    def test_archive_local_generates_cover_when_upstream_has_no_cover(self) -> None:
        config = self._build_config(
            archive_backend="local",
            archive_s3_access_key=None,
            archive_s3_secret_key=None,
        )
        archiver = VideoAssetArchiver(config)
        video_temp = self.root / "downloaded-video.mp4"
        video_temp.write_bytes(b"mp4")
        generated_root = self.root / "用户生成结果"

        def _write_cover(video_path: Path, cover_path: Path) -> dict:
            cover_path.write_bytes(b"jpg")
            return {"status": "generated", "source": "test"}

        job = self._sample_job()
        job.cover_image_url = None

        with patch.object(generated_results, "USER_GENERATED_RESULT_ROOT", generated_root), patch.object(
            VideoAssetArchiver,
            "_download_to_tempfile",
            return_value=(str(video_temp), {"sha256": "video-sha", "size_bytes": 3}),
        ), patch(
            "ai8video.assets.video_asset_archiver.mix_background_music",
            return_value={"enabled": False, "status": "skipped", "reason": "no background music"},
        ), patch(
            "ai8video.assets.video_asset_archiver.trim_video_start",
            return_value={"enabled": True, "status": "skipped", "reason": "test bypass"},
        ), patch(
            "ai8video.assets.video_asset_archiver.apply_video_text_overlay",
            return_value={"enabled": False, "status": "skipped", "reason": "test bypass"},
        ), patch.object(VideoAssetArchiver, "_extract_cover_frame", side_effect=_write_cover):
            archived = archiver.archive(
                self._sample_request(),
                self._sample_episode(),
                job,
                self._sample_generation_outcome(),
            )

        self.assertIsNotNone(archived.archive_cover_key)
        self.assertIsNotNone(archived.local_cover_path)
        result_cover = generated_root / archived.archive_cover_key
        self.assertTrue(result_cover.exists())
        self.assertEqual(result_cover.read_bytes(), b"jpg")
        self.assertFalse((Path(config.archive_local_dir) / archived.archive_cover_key).exists())
        self.assertEqual(archived.meta["coverGeneration"]["status"], "generated")

    def test_archive_local_trims_video_before_mirroring(self) -> None:
        config = self._build_config(
            archive_backend="local",
            archive_s3_access_key=None,
            archive_s3_secret_key=None,
        )
        archiver = VideoAssetArchiver(config)
        video_temp = self.root / "downloaded-video.mp4"
        video_temp.write_bytes(b"raw-mp4")
        cover_temp = self.root / "downloaded-cover.jpg"
        cover_temp.write_bytes(b"jpg")
        generated_root = self.root / "用户生成结果"

        def _trim_video(path: Path) -> dict:
            path.write_bytes(b"trimmed-mp4")
            return {"enabled": True, "status": "trimmed", "trimStartSeconds": 0.1}

        with patch.object(generated_results, "USER_GENERATED_RESULT_ROOT", generated_root), patch.object(
            VideoAssetArchiver,
            "_download_to_tempfile",
            side_effect=[
                (str(video_temp), {"sha256": "raw-sha", "size_bytes": 7}),
                (str(cover_temp), {"sha256": "cover-sha", "size_bytes": 3}),
            ],
        ), patch(
            "ai8video.assets.video_asset_archiver.trim_video_start",
            side_effect=_trim_video,
        ) as trim_start, patch(
            "ai8video.assets.video_asset_archiver.apply_video_text_overlay",
            return_value={"enabled": False, "status": "skipped", "reason": "no text overlay"},
        ), patch(
            "ai8video.assets.video_asset_archiver.mix_background_music",
            return_value={"enabled": False, "status": "skipped", "reason": "no background music"},
        ), patch(
            "ai8video.assets.video_asset_archiver.file_meta",
            return_value={"sha256": "trimmed-sha", "size_bytes": 11},
        ):
            archived = archiver.archive(
                self._sample_request(),
                self._sample_episode(),
                self._sample_job(),
                self._sample_generation_outcome(),
            )

        trim_start.assert_called_once()
        result_video = generated_root / archived.archive_key
        self.assertEqual(result_video.read_bytes(), b"trimmed-mp4")
        self.assertFalse((Path(config.archive_local_dir) / archived.archive_key).exists())
        self.assertEqual(archived.sha256, "trimmed-sha")
        self.assertEqual(archived.size_bytes, 11)
        self.assertEqual(archived.meta["startTrim"]["status"], "trimmed")
        manifest = json.loads(Path(archived.manifest_path).read_text(encoding="utf-8"))
        self.assertEqual(manifest["startTrim"]["trimStartSeconds"], 0.1)

    def test_archive_s3_trims_video_before_upload(self) -> None:
        config = self._build_config(archive_backend="s3")
        archiver = VideoAssetArchiver(config)
        video_temp = self.root / "downloaded-video.mp4"
        video_temp.write_bytes(b"raw-mp4")
        cover_temp = self.root / "downloaded-cover.jpg"
        cover_temp.write_bytes(b"jpg")
        uploaded_video_bytes: list[bytes] = []

        class _FakeSession:
            def client(self, *args, **kwargs):  # noqa: ANN002, ANN003
                return "fake-client"

        fake_boto3 = types.SimpleNamespace(
            session=types.SimpleNamespace(Session=lambda: _FakeSession())
        )

        def _trim_video(path: str) -> dict:
            Path(path).write_bytes(b"trimmed-mp4")
            return {"enabled": True, "status": "trimmed", "trimStartSeconds": 0.1}

        def _upload(client, file_path, key, content_type=None):  # noqa: ANN001, ANN002
            if content_type == "video/mp4":
                uploaded_video_bytes.append(Path(file_path).read_bytes())

        with patch.dict(sys.modules, {"boto3": fake_boto3}):
            with patch.object(
                VideoAssetArchiver,
                "_download_to_tempfile",
                side_effect=[
                    (str(video_temp), {"sha256": "raw-sha", "size_bytes": 7}),
                    (str(cover_temp), {"sha256": "cover-sha", "size_bytes": 3}),
                ],
            ), patch.object(
                VideoAssetArchiver,
                "_upload_file_to_s3",
                side_effect=_upload,
            ), patch(
                "ai8video.assets.video_asset_archiver.trim_video_start",
                side_effect=_trim_video,
            ), patch(
                "ai8video.assets.video_asset_archiver.apply_video_text_overlay",
                return_value={"enabled": False, "status": "skipped", "reason": "no text overlay"},
            ), patch(
                "ai8video.assets.video_asset_archiver.mix_background_music",
                return_value={"enabled": False, "status": "skipped", "reason": "no background music"},
            ), patch(
                "ai8video.assets.video_asset_archiver.file_meta",
                return_value={"sha256": "trimmed-sha", "size_bytes": 11},
            ):
                archived = archiver.archive(
                    self._sample_request(),
                    self._sample_episode(),
                    self._sample_job(),
                    self._sample_generation_outcome(),
                )

        self.assertEqual(uploaded_video_bytes, [b"trimmed-mp4"])
        self.assertEqual(archived.sha256, "trimmed-sha")
        self.assertEqual(archived.size_bytes, 11)
        self.assertEqual(archived.meta["startTrim"]["status"], "trimmed")

    def test_trim_video_start_uses_resolved_ffmpeg_command(self) -> None:
        video = self.root / "video.mp4"
        video.write_bytes(b"raw-mp4")

        def _run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
            self.assertTrue(check)
            self.assertTrue(capture_output)
            self.assertTrue(text)
            self.assertEqual(timeout, 180)
            Path(cmd[-1]).write_bytes(b"trimmed-mp4")

        with patch(
            "ai8video.assets.video_asset_archiver.subprocess.run",
            side_effect=_run,
        ) as run:
            result = trim_video_start(video, ffmpeg_bin="ffmpeg-test")

        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "ffmpeg-test")
        self.assertIn("-ss", cmd)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "0.100")
        self.assertIn("0:v:0", cmd)
        self.assertIn("0:a?", cmd)
        self.assertIn("libx264", cmd)
        self.assertEqual(cmd[cmd.index("-preset") + 1], "veryfast")
        self.assertEqual(cmd[cmd.index("-crf") + 1], "16")
        self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "yuv420p")
        self.assertIn("+faststart", cmd)
        self.assertEqual(video.read_bytes(), b"trimmed-mp4")
        self.assertEqual(result["status"], "trimmed")
        self.assertEqual(result["videoEncoding"]["crf"], "16")


if __name__ == "__main__":
    unittest.main()
