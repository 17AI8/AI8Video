from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.media import ffmpeg_utils
from ai8video.media.motion import html_motion_overlay, html_motion_review
from ai8video.core.config import AI8VideoConfig
from ai8video.media.motion.html_motion_overlay import (
    _composite_transparent_layer,
    _validate_transparent_layer,
    apply_html_motion_overlay,
    html_motion_safe_zone_for_media,
    html_motion_safe_zone_status,
    html_motion_overlay_status,
    update_html_motion_safe_zone,
    update_html_motion_overlay,
    update_html_motion_beat_interval_seconds,
    update_html_motion_smart_beat_interval,
    update_html_motion_quality_retry_count,
)
from ai8video.media.motion.hyperframes_overlay_harness import HarnessResult
from ai8video.core.models import EpisodePrompt, ParsedRequest, QuickVideoJob


class AI8VideoHtmlMotionOverlayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.settings_dir = self.root / "HTML动效"
        self.settings_path = self.settings_dir / "settings.json"
        self.dir_patcher = patch.object(html_motion_overlay, "HTML_MOTION_DIR", self.settings_dir)
        self.path_patcher = patch.object(html_motion_overlay, "HTML_MOTION_SETTINGS_PATH", self.settings_path)
        self.dir_patcher.start()
        self.path_patcher.start()

    def tearDown(self) -> None:
        self.path_patcher.stop()
        self.dir_patcher.stop()
        self.tempdir.cleanup()

    @staticmethod
    def _request(enabled: bool) -> ParsedRequest:
        return ParsedRequest(raw_text="生成视频", mode="single_prompt", html_motion_overlay_enabled=enabled)

    @staticmethod
    def _episode() -> EpisodePrompt:
        return EpisodePrompt(index=1, title="第一集", prompt="商务团队在办公室讨论客户承接")

    @staticmethod
    def _job() -> QuickVideoJob:
        return QuickVideoJob(episode_index=1, job_id="html-motion-job", status="succeeded")

    def test_setting_is_persisted_and_exposed(self) -> None:
        with patch.object(html_motion_overlay, "ensure_user_file_root", return_value=self.root), patch.object(
            html_motion_overlay,
            "html_motion_runtime_status",
            return_value={"ready": False, "renderer": "hyperframes", "rendererVersion": "0.7.59", "reason": "未安装"},
        ):
            payload = update_html_motion_overlay(enabled=True)
            status = html_motion_overlay_status()

        self.assertTrue(payload["enabled"])
        self.assertTrue(status["enabled"])
        self.assertEqual(self.settings_path.read_text(encoding="utf-8"), '{\n  "enabled": true\n}')

    def test_safe_zone_is_persisted_per_aspect_ratio(self) -> None:
        with patch.object(html_motion_overlay, "ensure_user_file_root", return_value=self.root):
            saved = update_html_motion_safe_zone(
                "9:16",
                {"x": 12, "y": 18, "width": 64, "height": 30},
            )
            status = html_motion_safe_zone_status("9:16")

        self.assertEqual(saved["safeZone"], {"x": 12.0, "y": 18.0, "width": 64.0, "height": 30.0})
        self.assertEqual(status["safeZone"], saved["safeZone"])
        self.assertEqual(
            html_motion_safe_zone_for_media({"width": 720, "height": 1280}),
            saved["safeZone"],
        )

    def test_quality_retry_count_defaults_to_five_and_is_persisted(self) -> None:
        self.assertEqual(html_motion_overlay_status()["qualityRetryCount"], 5)
        with patch.object(html_motion_overlay, "ensure_user_file_root", return_value=self.root):
            saved = update_html_motion_quality_retry_count(7)
        self.assertEqual(saved["qualityRetryCount"], 7)
        self.assertEqual(html_motion_overlay_status()["qualityRetryCount"], 7)

    def test_beat_interval_defaults_to_five_and_is_persisted(self) -> None:
        self.assertEqual(html_motion_overlay_status()["beatIntervalSeconds"], 5)
        with patch.object(html_motion_overlay, "ensure_user_file_root", return_value=self.root):
            saved = update_html_motion_beat_interval_seconds(8)
        self.assertEqual(saved["beatIntervalSeconds"], 8)
        self.assertEqual(html_motion_overlay_status()["beatIntervalSeconds"], 8)
        with patch.object(html_motion_overlay, "ensure_user_file_root", return_value=self.root):
            minimum = update_html_motion_beat_interval_seconds(1)
        self.assertEqual(minimum["beatIntervalSeconds"], 1)
        with patch.object(html_motion_overlay, "ensure_user_file_root", return_value=self.root):
            decimal = update_html_motion_beat_interval_seconds(2.2)
        self.assertEqual(decimal["beatIntervalSeconds"], 2.2)

    def test_smart_beat_interval_defaults_off_and_is_persisted(self) -> None:
        self.assertFalse(html_motion_overlay_status()["smartBeatInterval"])
        with patch.object(html_motion_overlay, "ensure_user_file_root", return_value=self.root):
            saved = update_html_motion_smart_beat_interval(True)
        self.assertTrue(saved["smartBeatInterval"])
        self.assertTrue(html_motion_overlay_status()["smartBeatInterval"])

    def test_safe_zone_is_clamped_inside_canvas(self) -> None:
        with patch.object(html_motion_overlay, "ensure_user_file_root", return_value=self.root):
            saved = update_html_motion_safe_zone(
                "16:9",
                {"x": 95, "y": -10, "width": 40, "height": 120},
            )

        self.assertEqual(saved["safeZone"], {"x": 60.0, "y": 0.0, "width": 40.0, "height": 96.0})

    def test_html_motion_llm_uses_single_streaming_request(self) -> None:
        config = AI8VideoConfig(llm_base_url="https://example.invalid", llm_api_key="key", llm_model="model")
        with patch.object(html_motion_overlay, "build_openai_compat_splitter", return_value=lambda _: "{}") as builder:
            html_motion_overlay.build_html_motion_llm(config)

        self.assertTrue(builder.call_args.kwargs["stream"])
        self.assertEqual(builder.call_args.kwargs["transport_retry_count"], 0)
        self.assertEqual(builder.call_args.kwargs["timeout_seconds"], 20)

    def test_html_motion_llm_race_returns_first_successful_result(self) -> None:
        calls: list[str] = []

        def llm(_prompt: str) -> str:
            calls.append("called")
            return "{}"

        self.assertEqual(html_motion_overlay._run_llm_race(llm, "prompt"), "{}")
        self.assertEqual(len(calls), html_motion_overlay.HTML_MOTION_LLM_CONCURRENCY)

    def test_disabled_request_skips_without_model_call(self) -> None:
        source = self.root / "base.mp4"
        source.write_bytes(b"video")
        result = apply_html_motion_overlay(
            source,
            self._request(False),
            self._episode(),
            self._job(),
            llm=lambda _: self.fail("disabled request must not call model"),
            trigger="video_playback",
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(source.read_bytes(), b"video")

    def test_missing_runtime_degrades_and_preserves_base_video(self) -> None:
        source = self.root / "base.mp4"
        source.write_bytes(b"video")
        with patch.object(
            html_motion_overlay,
            "html_motion_runtime_status",
            return_value={"ready": False, "renderer": "hyperframes", "rendererVersion": "0.7.59", "reason": "依赖未安装"},
        ):
            result = apply_html_motion_overlay(
                source,
                self._request(True),
                self._episode(),
                self._job(),
                llm=lambda _: self.fail("missing runtime must not call model"),
                trigger="video_playback",
            )
        self.assertEqual(result["status"], "degraded")
        self.assertIn("依赖未安装", result["reason"])
        self.assertEqual(source.read_bytes(), b"video")

    def test_successful_adapter_cleans_work_directory(self) -> None:
        source = self.root / "base.mp4"
        source.write_bytes(b"base")
        layer = self.root / "overlay.webm"
        events: list[str] = []
        with patch.object(
            html_motion_overlay,
            "html_motion_runtime_status",
            return_value={"ready": True, "renderer": "hyperframes", "rendererVersion": "0.7.59", "reason": ""},
        ), patch.object(
            html_motion_overlay,
            "_probe_video_info",
            return_value={"width": 720, "height": 1280, "durationSeconds": 2.0},
        ), patch.object(
            html_motion_overlay,
            "_render_transparent_layer",
            return_value=layer,
        ), patch.object(
            html_motion_overlay,
            "build_hyperframes_overlay",
            return_value=HarnessResult(
                artifact={"design": {}, "scenes": []},
                composition_html="<!doctype html><div></div>",
                motion_manifest={"duration": 2.0, "assertions": []},
                summary={
                    "harness": "hyperframes-overlay-v1",
                    "sceneCount": 2,
                    "elementCount": 6,
                    "animationCount": 8,
                    "coveredDurationSeconds": 2.0,
                    "coverageRatio": 1.0,
                },
            ),
        ), patch.object(html_motion_overlay, "_validate_transparent_layer"), patch.object(
            html_motion_overlay, "_validate_composited_video"), patch.object(
            html_motion_overlay,
            "_composite_transparent_layer",
            side_effect=lambda path, *_args: path.write_bytes(b"composited"),
        ):
            result = apply_html_motion_overlay(
                source,
                self._request(True),
                self._episode(),
                self._job(),
                llm=lambda _: "{}",
                stage_callback=lambda stage, _result: events.append(stage),
                trigger="video_playback",
            )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(events, ["preparing", "generating", "validating", "compositing", "validating"])
        self.assertEqual(source.read_bytes(), b"composited")
        self.assertEqual(
            result["timeline"],
            {
                "harness": "hyperframes-overlay-v1",
                "sceneCount": 2,
                "elementCount": 6,
                "animationCount": 8,
                "coveredDurationSeconds": 2.0,
                "coverageRatio": 1.0,
            },
        )
        self.assertEqual(list(self.settings_dir.glob("render-*")), [])

    def test_automatic_trigger_skips_even_when_request_is_enabled(self) -> None:
        source = self.root / "base.mp4"
        source.write_bytes(b"video")
        result = apply_html_motion_overlay(
            source,
            self._request(True),
            self._episode(),
            self._job(),
            llm=lambda _: self.fail("automatic trigger must not call model"),
        )

        self.assertEqual(result["status"], "skipped")
        self.assertTrue(result["manualOnly"])
        self.assertEqual(result["entrypoint"], "video_playback")
        self.assertIn("视频播放界面", result["reason"])
        self.assertEqual(source.read_bytes(), b"video")

    def test_review_regeneration_uses_fixed_base_and_confirm_is_atomic(self) -> None:
        source = self.root / "result.mp4"
        source.write_bytes(b"base")
        review_root = self.root / "reviews"
        counter = {"value": 0}

        def render(candidate: Path) -> dict:
            counter["value"] += 1
            candidate.write_bytes(candidate.read_bytes() + f"+overlay-{counter['value']}".encode())
            return {"status": "applied", "reason": "rendered"}

        with patch.object(html_motion_review, "HTML_MOTION_REVIEW_ROOT", review_root):
            first = html_motion_review.prepare_html_motion_review(source, "video/result.mp4", render)
            second = html_motion_review.prepare_html_motion_review(source, "video/result.mp4", render)
            preview = html_motion_review.resolve_html_motion_review_video(second["reviewId"])
            confirmed = html_motion_review.confirm_html_motion_review(source, "video/result.mp4")
            status = html_motion_review.html_motion_review_status("video/result.mp4")

        self.assertEqual(first["status"], "preview_ready")
        self.assertEqual(source.read_bytes(), b"base+overlay-2")
        self.assertEqual(preview.read_bytes(), b"base+overlay-2")
        self.assertEqual(confirmed["status"], "applied")
        self.assertFalse(status["reviewReady"])

    def test_failed_review_preserves_official_video(self) -> None:
        source = self.root / "result.mp4"
        source.write_bytes(b"base")
        with patch.object(html_motion_review, "HTML_MOTION_REVIEW_ROOT", self.root / "reviews"):
            result = html_motion_review.prepare_html_motion_review(
                source,
                "video/result.mp4",
                lambda candidate: {"status": "degraded", "reason": "failed"},
            )

        self.assertEqual(result["status"], "preview_failed")
        self.assertEqual(source.read_bytes(), b"base")

    def test_review_audio_sync_keeps_candidate_video_and_uses_official_audio(self) -> None:
        source = self.root / "result.mp4"
        source.write_bytes(b"official")
        review_root = self.root / "reviews"

        def render(candidate: Path) -> dict:
            candidate.write_bytes(b"candidate")
            return {"status": "applied"}

        def run_ffmpeg(cmd, **_kwargs):  # noqa: ANN001
            Path(cmd[-1]).write_bytes(b"candidate-with-official-audio")

        with patch.object(html_motion_review, "HTML_MOTION_REVIEW_ROOT", review_root), patch.object(
            html_motion_review,
            "resolve_ffmpeg_bin",
            return_value="ffmpeg",
        ), patch.object(html_motion_review.subprocess, "run", side_effect=run_ffmpeg) as run:
            prepared = html_motion_review.prepare_html_motion_review(source, "video/result.mp4", render)
            synced = html_motion_review.sync_html_motion_review_audio(source, "video/result.mp4")
            candidate = html_motion_review.resolve_html_motion_review_video(prepared["reviewId"])

        self.assertEqual(synced["status"], "synced")
        self.assertEqual(synced["syncedTargets"], 2)
        self.assertEqual(candidate.read_bytes(), b"candidate-with-official-audio")
        self.assertEqual((review_root / prepared["reviewId"] / "base.mp4").read_bytes(), b"candidate-with-official-audio")
        self.assertEqual(run.call_count, 2)
        command = run.call_args_list[-1].args[0]
        first_map = command.index("-map")
        second_map = command.index("-map", first_map + 1)
        self.assertEqual(command[first_map + 1], "0:v:0")
        self.assertEqual(command[second_map + 1], "1:a:0")

    def test_alpha_validation_rejects_nontransparent_layer(self) -> None:
        with patch.object(html_motion_overlay, "probe_media_video_info", return_value={"pixelFormat": "yuv420p"}):
            with self.assertRaisesRegex(RuntimeError, "透明"):
                _validate_transparent_layer(self.root / "opaque.webm")

    def test_alpha_validation_accepts_alpha_metadata(self) -> None:
        with patch.object(html_motion_overlay, "probe_media_video_info", return_value={"hasAlpha": True}):
            _validate_transparent_layer(self.root / "transparent.webm")

    def test_render_check_skips_snapshot_capture_for_fast_regeneration(self) -> None:
        work_dir = self.root / "render-work"
        work_dir.mkdir()
        cli = self.root / "hyperframes-cli.js"
        cli.write_bytes(b"cli")

        def fake_render(render_dir, **_kwargs):  # noqa: ANN001
            output = render_dir / "overlay.webm"
            output.write_bytes(b"webm")
            return output, types.SimpleNamespace()

        with patch.object(
            html_motion_overlay, "_hyperframes_cli_path", return_value=cli
        ), patch.object(
            html_motion_overlay, "render_prepared_hyperframes", side_effect=fake_render
        ) as render:
            output = html_motion_overlay._render_transparent_layer(
                work_dir, "<!doctype html><html></html>", {"duration": 1, "assertions": []}
            )

        self.assertTrue(output.is_file())
        self.assertTrue((work_dir / "waapi-timeline-runtime.js").is_file())
        render.assert_called_once()
        self.assertEqual(render.call_args.kwargs["timeout_ms"], 300_000)

    def test_check_error_ignores_font_fetch_info(self) -> None:
        result = types.SimpleNamespace(
            stdout='[INFO] [Compiler] Fetched 11 font face(s) for "Inter" from Google Fonts',
            stderr="",
        )

        self.assertEqual(
            html_motion_overlay._hyperframes_check_error(result),
            "HyperFrames 版式或时间线校验未通过",
        )

    def test_check_error_uses_structured_validation_finding(self) -> None:
        result = types.SimpleNamespace(
            stdout='[INFO] font cache\\n{"layout":{"findings":[{"severity":"error","message":"元素超出安全区"}]}}',
            stderr="",
        )

        self.assertEqual(
            html_motion_overlay._hyperframes_check_error(result),
            "HyperFrames 版式或时间线校验未通过：元素超出安全区",
        )

    def test_check_error_keeps_finding_fields_when_message_is_missing(self) -> None:
        result = types.SimpleNamespace(
            stdout='{"layout":{"findings":[{"severity":"error","selector":"#title","box":"x=740"}]}}',
            stderr="",
        )

        self.assertIn("#title", html_motion_overlay._hyperframes_check_error(result))

    def test_selected_flower_font_is_copied_without_extension(self) -> None:
        font = self.root / "selected.otf"
        font.write_bytes(b"font")
        work_dir = self.root / "render-work"
        work_dir.mkdir()

        with patch.object(html_motion_overlay, "selected_video_text_overlay_font_path", return_value=font):
            family = html_motion_overlay._copy_selected_flower_font(work_dir)

        self.assertEqual(family, "AI8VideoFlower")
        self.assertEqual((work_dir / "flower-font.otf").read_bytes(), b"font")

    def test_motion_font_prefers_current_flower_font(self) -> None:
        font = self.root / "selected.otf"
        font.write_bytes(b"font")

        with patch.object(html_motion_overlay, "selected_video_text_overlay_font_path", return_value=font):
            family = html_motion_overlay._resolve_motion_font_family()

        self.assertEqual(family, "AI8VideoFlower")

    def test_ffmpeg_fallback_reads_video_info_and_alpha_without_ffprobe(self) -> None:
        source = self.root / "base.mp4"
        source.write_bytes(b"video")

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[0] == "ffprobe-missing":
                raise FileNotFoundError("ffprobe missing")
            self.assertEqual(cmd[0], "ffmpeg-test")
            return types.SimpleNamespace(
                stdout="",
                stderr=(
                    "Duration: 00:00:02.50, start: 0.000000, bitrate: 1000 kb/s\n"
                    "Stream #0:0: Video: vp9, yuv420p(progressive), 720x1280, 30 fps\n"
                    "    ALPHA_MODE      : 1"
                ),
            )

        with patch.object(ffmpeg_utils, "resolve_ffprobe_bin", return_value="ffprobe-missing"), patch.object(
            ffmpeg_utils,
            "resolve_ffmpeg_bin",
            return_value="ffmpeg-test",
        ), patch.object(ffmpeg_utils.subprocess, "run", side_effect=fake_run):
            media = ffmpeg_utils.probe_media_video_info(source)

        self.assertEqual(media["width"], 720)
        self.assertEqual(media["height"], 1280)
        self.assertEqual(media["durationSeconds"], 2.5)
        self.assertTrue(media["hasAlpha"])

    def test_ffprobe_alpha_metadata_accepts_uppercase_tag(self) -> None:
        source = self.root / "overlay.webm"
        source.write_bytes(b"video")
        payload = (
            '{"streams":[{"codec_type":"video","width":720,"height":1280,'
            '"pix_fmt":"yuv420p","tags":{"ALPHA_MODE":"1"}}],'
            '"format":{"duration":"2.5"}}'
        )
        with patch.object(
            ffmpeg_utils.subprocess,
            "run",
            return_value=types.SimpleNamespace(stdout=payload),
        ):
            media = ffmpeg_utils.probe_media_video_info(source, ffprobe_bin="ffprobe-test")

        self.assertTrue(media["hasAlpha"])

    def test_ffmpeg_composite_uses_alpha_overlay_and_preserves_audio(self) -> None:
        source = self.root / "base.mp4"
        layer = self.root / "overlay.webm"
        source.write_bytes(b"base")
        layer.write_bytes(b"overlay")

        def run(cmd, **_kwargs):  # noqa: ANN001
            Path(cmd[-1]).write_bytes(b"mixed")

        with patch.object(html_motion_overlay.subprocess, "run", side_effect=run) as runner:
            _composite_transparent_layer(
                source,
                layer,
                {"width": 720, "height": 1280, "durationSeconds": 2.0},
                "ffmpeg-test",
            )

        command = runner.call_args.args[0]
        self.assertEqual(command[0], "ffmpeg-test")
        layer_input_index = command.index(str(layer))
        self.assertEqual(command[layer_input_index - 3:layer_input_index - 1], ["-c:v", "libvpx-vp9"])
        self.assertIn("overlay=eof_action=pass:shortest=0", command[command.index("-filter_complex") + 1])
        self.assertEqual(command[command.index("-c:a") + 1], "copy")
        self.assertEqual(source.read_bytes(), b"mixed")

if __name__ == "__main__":
    unittest.main()
