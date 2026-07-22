from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.media import video_text_overlay


class AI8VideoVideoTextOverlayTest(unittest.TestCase):
    def test_video_text_overlay_defaults_and_saves_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings_path = Path(tempdir) / "花字" / "settings.json"
            font_dir = Path(tempdir) / "用户字体"
            font_dir.mkdir()
            (font_dir / "custom.ttf").write_bytes(b"not a real font")
            with patch.object(video_text_overlay, "VIDEO_TEXT_OVERLAY_DIR", settings_path.parent), \
                    patch.object(video_text_overlay, "VIDEO_TEXT_OVERLAY_SETTINGS_PATH", settings_path), \
                    patch.object(video_text_overlay, "USER_FONT_DIR", font_dir):
                initial_status = video_text_overlay.video_text_overlay_status()
                self.assertFalse(initial_status["enabled"])
                self.assertEqual(initial_status["watermarkOpacity"], 100)
                self.assertEqual(initial_status["animationDelaySeconds"], 0)
                available_font = initial_status["availableFonts"][0]
                self.assertEqual(available_font["id"], "custom.ttf")
                self.assertEqual(available_font["fontUrl"], "/user-fonts/custom.ttf")

                status = video_text_overlay.update_video_text_overlay(
                    enabled=True,
                    text="限时福利\n立即领取",
                    canvas_width=9,
                    canvas_height=16,
                    font_family="custom.ttf",
                    font_weight=900,
                    watermark_x=35,
                    watermark_y=66,
                    animation_delay_seconds=3,
                    preview_background_color="#445566",
                )

                self.assertTrue(status["ok"])
                self.assertTrue(status["enabled"])
                self.assertEqual(status["text"], "限时福利\n立即领取")
                self.assertEqual(status["canvasWidth"], 9)
                self.assertEqual(status["canvasHeight"], 16)
                self.assertEqual(status["fontFamily"], "custom.ttf")
                self.assertEqual(status["fontName"], "custom")
                self.assertEqual(status["fontWeight"], 900)
                self.assertEqual(status["watermarkX"], 35)
                self.assertEqual(status["watermarkY"], 66)
                self.assertEqual(status["previewBackgroundColor"], "#445566")
                self.assertEqual(video_text_overlay.video_text_overlay_status()["fontWeight"], 900)

                invalid = video_text_overlay.update_video_text_overlay(font_family="../bad.ttf")
                self.assertEqual(invalid["fontFamily"], "")

    def test_video_text_overlay_migrates_legacy_watermark_opacity(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings_path = Path(tempdir) / "花字" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text('{"watermarkOpacity": 42}', encoding="utf-8")
            with patch.object(video_text_overlay, "VIDEO_TEXT_OVERLAY_DIR", settings_path.parent), \
                    patch.object(video_text_overlay, "VIDEO_TEXT_OVERLAY_SETTINGS_PATH", settings_path):
                status = video_text_overlay.video_text_overlay_status()

            self.assertEqual(status["watermarkOpacity"], 100)

    def test_preview_background_upload_replaces_old_file_and_color_clears_image(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            settings_path = root / "花字" / "settings.json"
            background_dir = root / "花字" / "预览背景图"
            background_dir.mkdir(parents=True)
            (background_dir / "old.png").write_bytes(b"old")
            with patch.object(video_text_overlay, "VIDEO_TEXT_OVERLAY_DIR", settings_path.parent), \
                    patch.object(video_text_overlay, "VIDEO_TEXT_OVERLAY_SETTINGS_PATH", settings_path), \
                    patch.object(video_text_overlay, "VIDEO_TEXT_PREVIEW_BACKGROUND_DIR", background_dir):
                result = video_text_overlay.save_video_text_preview_background_upload("new.png", b"new")

                self.assertTrue(result["ok"])
                self.assertFalse((background_dir / "old.png").exists())
                self.assertTrue((background_dir / "new.png").is_file())
                self.assertEqual(video_text_overlay.video_text_overlay_status()["previewBackgroundImage"], "new.png")

                cleared = video_text_overlay.clear_video_text_preview_background_image()
                self.assertEqual(cleared["previewBackgroundImage"], "")
                self.assertFalse((background_dir / "new.png").exists())

    def test_preview_background_is_not_burned_into_overlay_image(self) -> None:
        image = video_text_overlay._render_overlay_image(
            {
                "enabled": True,
                "text": "",
                "canvasWidth": 9,
                "canvasHeight": 16,
                "previewBackgroundColor": "#ff0000",
                "previewBackgroundImage": "preview.png",
            },
            target_size=(32, 32),
        ).convert("RGBA")

        self.assertEqual(image.getchannel("A").getextrema(), (0, 0))
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))

    def test_render_video_text_overlay_preview_can_render_watermark_only(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            watermark_dir = Path(tempdir) / "花字水印库"
            watermark_dir.mkdir()
            from PIL import Image

            Image.new("RGBA", (20, 10), (255, 0, 0, 255)).save(watermark_dir / "logo.png")
            settings = {
                "enabled": True,
                "text": "",
                "canvasWidth": 9,
                "canvasHeight": 16,
                "watermarkEnabled": True,
                "watermarkImage": "logo.png",
                "watermarkSize": 50,
                "watermarkOpacity": 100,
                "watermarkPosition": "bottom-right",
            }
            with patch.object(video_text_overlay, "USER_FLOWER_WATERMARK_DIR", watermark_dir):
                image_bytes = video_text_overlay.render_video_text_overlay_preview(
                    settings,
                    target_width=90,
                    target_height=160,
                )

            self.assertTrue(image_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
            rendered = Image.open(video_text_overlay.BytesIO(image_bytes)).convert("RGBA")
            self.assertGreater(rendered.getchannel("A").getextrema()[1], 0)

    def test_resolve_watermark_image_returns_absolute_path_for_runtime_render(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            watermark_dir = Path(tempdir) / "花字水印库"
            watermark_dir.mkdir()
            from PIL import Image

            logo = watermark_dir / "logo.png"
            Image.new("RGBA", (12, 12), (123, 45, 67, 255)).save(logo)

            with patch.object(video_text_overlay, "USER_FLOWER_WATERMARK_DIR", watermark_dir):
                cleaned = video_text_overlay._clean_watermark_image("logo.png")
                resolved = video_text_overlay._resolve_watermark_image(cleaned)

            self.assertEqual(cleaned, "logo.png")
            self.assertEqual(resolved, logo.resolve())

    def test_apply_video_text_overlay_uses_scaled_overlay_and_preserves_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            video = root / "video.mp4"
            video.write_bytes(b"video")
            commands: list[list[str]] = []

            def fake_render(_settings, target_size=None, *, layer="all"):
                overlay = root / "overlay.png"
                overlay.write_bytes(b"png")
                return overlay

            def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool) -> None:
                commands.append(cmd)
                Path(cmd[-1]).write_bytes(b"burned")

            settings = {"enabled": True, "text": "大字报", "canvasWidth": 9, "canvasHeight": 16}
            with patch.object(video_text_overlay, "_probe_video_size", return_value=(416, 720)), \
                    patch.object(video_text_overlay, "_render_overlay_png", side_effect=fake_render), \
                    patch.object(video_text_overlay.subprocess, "run", side_effect=fake_run):
                result = video_text_overlay.apply_video_text_overlay(video, ffmpeg_bin="ffmpeg-test", settings=settings)

            self.assertEqual(result["status"], "burned")
            self.assertEqual(video.read_bytes(), b"burned")
            self.assertEqual(commands[0][0], "ffmpeg-test")
            self.assertIn("-filter_complex", commands[0])
            filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
            self.assertEqual(
                filter_complex,
                "[1:v]format=rgba[overlay1];[0:v][overlay1]overlay=0:0:shortest=1:format=auto[vout]",
            )
            self.assertIn("0:a?", commands[0])
            self.assertEqual(commands[0][commands[0].index("-c:v") + 1], "libx264")
            self.assertEqual(commands[0][commands[0].index("-preset") + 1], "veryfast")
            self.assertEqual(commands[0][commands[0].index("-crf") + 1], "16")
            self.assertEqual(commands[0][commands[0].index("-pix_fmt") + 1], "yuv420p")
            self.assertEqual(commands[0][commands[0].index("-c:a") + 1], "copy")
            self.assertEqual(result["videoEncoding"]["crf"], "16")

    def test_apply_video_text_overlay_burns_watermark_without_text(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            video = root / "video.mp4"
            video.write_bytes(b"video")
            watermark_dir = root / "花字水印库"
            watermark_dir.mkdir()
            (watermark_dir / "logo.png").write_bytes(b"png")
            commands: list[list[str]] = []

            def fake_render(_settings, target_size=None, *, layer="all"):
                overlay = root / "overlay.png"
                overlay.write_bytes(b"png")
                return overlay

            def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool) -> None:
                commands.append(cmd)
                Path(cmd[-1]).write_bytes(b"burned")

            settings = {
                "enabled": True,
                "text": "",
                "canvasWidth": 9,
                "canvasHeight": 16,
                "watermarkEnabled": True,
                "watermarkImage": "logo.png",
            }
            with patch.object(video_text_overlay, "USER_FLOWER_WATERMARK_DIR", watermark_dir), \
                    patch.object(video_text_overlay, "_probe_video_size", return_value=(416, 720)), \
                    patch.object(video_text_overlay, "_render_overlay_png", side_effect=fake_render), \
                    patch.object(video_text_overlay.subprocess, "run", side_effect=fake_run):
                result = video_text_overlay.apply_video_text_overlay(video, ffmpeg_bin="ffmpeg-test", settings=settings)

            self.assertEqual(result["status"], "burned")
            self.assertTrue(result["watermarkPresent"])
            self.assertEqual(result["textLength"], 0)
            self.assertEqual(video.read_bytes(), b"burned")
            self.assertEqual(commands[0][0], "ffmpeg-test")

    def test_apply_video_text_overlay_skips_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            video = Path(tempdir) / "video.mp4"
            video.write_bytes(b"video")
            with patch.object(video_text_overlay.subprocess, "run") as run_ffmpeg:
                result = video_text_overlay.apply_video_text_overlay(
                    video,
                    settings={"enabled": False, "text": "大字报", "canvasWidth": 9, "canvasHeight": 16},
                )

            run_ffmpeg.assert_not_called()
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(video.read_bytes(), b"video")

    def test_runtime_status_blocks_enabled_text_when_pillow_missing(self) -> None:
        settings = {"enabled": True, "text": "大字报", "canvasWidth": 9, "canvasHeight": 16}

        def fake_import(name: str):  # noqa: ANN001
            if name == "PIL":
                raise ModuleNotFoundError("No module named 'PIL'")
            raise AssertionError(name)

        with patch.object(video_text_overlay.importlib, "import_module", side_effect=fake_import):
            status = video_text_overlay.video_text_overlay_runtime_status(settings)

        self.assertFalse(status["ready"])
        self.assertTrue(status["enabled"])
        self.assertTrue(status["textPresent"])
        self.assertFalse(status["pillow"]["available"])
        self.assertIn("Pillow/PIL", status["blockingReason"])

    def test_runtime_status_allows_missing_pillow_when_overlay_disabled(self) -> None:
        settings = {"enabled": False, "text": "大字报", "canvasWidth": 9, "canvasHeight": 16}

        with patch.object(video_text_overlay.importlib, "import_module", side_effect=ModuleNotFoundError("PIL")):
            status = video_text_overlay.video_text_overlay_runtime_status(settings)

        self.assertTrue(status["ready"])
        self.assertEqual(status["blockingReason"], "")

    def test_render_video_text_overlay_preview_returns_png(self) -> None:
        settings = {
            "enabled": True,
            "text": "全球私域营销平台\nAI8videoAI8VIDEO",
            "canvasWidth": 9,
            "canvasHeight": 16,
            "textColor": "#ffee43",
            "strokeColor": "#121826",
            "fontSize": 6,
            "fontWeight": 900,
            "strokeWidth": 3,
            "textX": 51,
            "textY": 16,
        }

        image = video_text_overlay.render_video_text_overlay_preview(
            settings,
            target_width=405,
            target_height=720,
        )

        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(image), 1000)

    def test_render_video_text_overlay_reserves_stroke_inside_canvas(self) -> None:
        from PIL import Image

        settings = {
            "enabled": True,
            "text": "全球私域营销平台\nAI8video AI8VIDEO \n正式发布",
            "canvasWidth": 9,
            "canvasHeight": 16,
            "textColor": "#ffe062",
            "strokeColor": "#000000",
            "fontFamily": "内置字体/SourceHanSerifSC-Bold.otf",
            "fontSize": 10,
            "fontWeight": 600,
            "strokeWidth": 7,
            "textX": 51,
            "textY": 58,
        }

        image_bytes = video_text_overlay.render_video_text_overlay_preview(
            settings,
            target_width=416,
            target_height=720,
        )
        image = Image.open(video_text_overlay.BytesIO(image_bytes)).convert("RGBA")
        bbox = image.getbbox()

        self.assertIsNotNone(bbox)
        left, top, right, bottom = bbox
        self.assertGreater(left, 0)
        self.assertGreater(top, 0)
        self.assertLess(right, image.width)
        self.assertLess(bottom, image.height)

    def test_render_video_text_overlay_keeps_dragged_bottom_position(self) -> None:
        settings = {
            "enabled": True,
            "text": "全球私域营销平台\nAI8video    正式发布",
            "canvasWidth": 9,
            "canvasHeight": 16,
            "fontSize": 7,
            "fontWeight": 800,
            "strokeWidth": 8,
            "textX": 40,
            "textY": 95,
        }

        image = video_text_overlay._render_overlay_image(
            settings,
            target_size=(405, 720),
        ).convert("RGBA")
        bbox = image.getbbox()

        self.assertIsNotNone(bbox)
        self.assertGreaterEqual(bbox[3], 710)

    def test_probe_video_size_falls_back_to_ffmpeg_when_ffprobe_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            video = Path(tempdir) / "video.mp4"
            video.write_bytes(b"video")

            def fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
                if cmd[0] == "ffprobe-missing":
                    raise FileNotFoundError("ffprobe missing")
                self.assertEqual(cmd[0], "ffmpeg-test")
                return types.SimpleNamespace(
                    stdout="",
                    stderr="Input #0, mov, from 'video.mp4':\n  Stream #0:0: Video: h264, yuv420p, 540x960, 30 fps",
                )

            with patch.object(video_text_overlay, "resolve_ffprobe_bin", return_value="ffprobe-missing"), \
                    patch.object(video_text_overlay, "resolve_ffmpeg_bin", return_value="ffmpeg-test"), \
                    patch.object(video_text_overlay.subprocess, "run", side_effect=fake_run):
                self.assertEqual(video_text_overlay._probe_video_size(video), (540, 960))


if __name__ == "__main__":
    unittest.main()


    def test_apply_video_text_overlay_uses_animation_delay_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            video = root / "video.mp4"
            video.write_bytes(b"video")
            overlay = root / "overlay.png"
            overlay.write_bytes(b"png")
            commands: list[list[str]] = []

            def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool) -> None:
                commands.append(cmd)
                output = root / "video.with-text.tmp.mp4"
                output.write_bytes(b"burned")

            settings = {"enabled": True, "text": "限时福利", "animationDelaySeconds": 5}
            with patch.object(video_text_overlay, "_probe_video_size", return_value=(720, 1280)), \
                    patch.object(video_text_overlay, "_render_overlay_png", return_value=overlay), \
                    patch.object(video_text_overlay.subprocess, "run", side_effect=fake_run):
                result = video_text_overlay.apply_video_text_overlay(video, ffmpeg_bin="ffmpeg-test", settings=settings)

            self.assertEqual(result["status"], "burned")
            filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
            self.assertIn("fade=t=in:st=5:d=1:alpha=1", filter_complex)
            self.assertIn("overlay=0:0:shortest=1:format=auto[vout]", filter_complex)
