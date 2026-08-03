from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.media import tts_mp3_export


class TtsMp3ExportTest(unittest.TestCase):
    def test_empty_settings_default_to_downloads_instead_of_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            downloads = root / "Downloads"
            downloads.mkdir()
            with patch.object(
                tts_mp3_export,
                "_load_settings",
                return_value={},
            ), patch.object(
                tts_mp3_export.Path,
                "home",
                return_value=root,
            ):
                result = tts_mp3_export.load_tts_mp3_export_directory()

            self.assertEqual(result, downloads.resolve())

    def test_selected_file_name_is_normalized_and_directory_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            selected = root / "exports"
            selected.mkdir()
            selected_path = selected / "自定义配音名称"
            settings = root / "TTS" / "export-settings.json"
            with patch.object(tts_mp3_export, "TTS_MP3_EXPORT_SETTINGS_PATH", settings), patch.object(
                tts_mp3_export,
                "ensure_user_file_root",
                return_value=root,
            ), patch.object(
                tts_mp3_export,
                "_pick_native_save_path",
                return_value=selected_path,
            ):
                result = tts_mp3_export.choose_tts_mp3_export_path("video/demo.mp4")
                reloaded = tts_mp3_export.load_tts_mp3_export_directory()

            self.assertEqual(result, (selected / "自定义配音名称.mp3").resolve())
            self.assertEqual(reloaded, selected.resolve())
            self.assertEqual(
                json.loads(settings.read_text(encoding="utf-8"))["exportDirectory"],
                str(selected.resolve()),
            )

    def test_cancel_keeps_previous_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            selected = root / "exports"
            selected.mkdir()
            settings = root / "export-settings.json"
            settings.write_text(
                json.dumps({"exportDirectory": str(selected)}),
                encoding="utf-8",
            )
            with patch.object(tts_mp3_export, "TTS_MP3_EXPORT_SETTINGS_PATH", settings), patch.object(
                tts_mp3_export,
                "_pick_native_save_path",
                return_value=None,
            ):
                result = tts_mp3_export.choose_tts_mp3_export_path("video/demo.mp4")

            self.assertIsNone(result)
            self.assertEqual(
                json.loads(settings.read_text(encoding="utf-8"))["exportDirectory"],
                str(selected),
            )

    def test_macos_picker_passes_default_directory_and_file_name(self) -> None:
        initial_path = Path('/tmp/folder with "quotes"/自定义配音.mp3')
        completed = subprocess.CompletedProcess(
            ["osascript"],
            0,
            "/tmp/selected/自定义结果.mp3\n",
            "",
        )
        with patch.object(tts_mp3_export.subprocess, "run", return_value=completed) as run:
            result = tts_mp3_export._pick_macos_save_path(initial_path)

        command = run.call_args.args[0]
        self.assertEqual(command[-2], str(initial_path.parent))
        self.assertEqual(command[-1], initial_path.name)
        self.assertIn("item 1 of argv", command[2])
        self.assertIn("choose file name", command[2])
        self.assertEqual(result, Path("/tmp/selected/自定义结果.mp3"))

    def test_export_uses_current_timeline_and_custom_file_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            audio = root / "voice.m4a"
            audio.write_bytes(b"audio")
            export_path = root / "我的自定义配音.mp3"
            commands: list[list[str]] = []

            def fake_run(command, **kwargs):
                commands.append(command)
                Path(command[-1]).write_bytes(b"mp3")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(
                tts_mp3_export,
                "probe_media_duration_seconds",
                return_value=10.0,
            ), patch.object(
                tts_mp3_export,
                "resolve_ffmpeg_bin",
                return_value="ffmpeg",
            ), patch.object(
                tts_mp3_export,
                "_ffmpeg_supports_libmp3lame",
                return_value=True,
            ), patch.object(
                tts_mp3_export.subprocess,
                "run",
                side_effect=fake_run,
            ):
                result = tts_mp3_export.export_tts_timeline_mp3(
                    audio,
                    [
                        {"sourceStartSeconds": 0, "sourceEndSeconds": 4, "startSeconds": 0},
                        {"sourceStartSeconds": 4, "sourceEndSeconds": 10, "startSeconds": 5},
                    ],
                    duration_seconds=12,
                    tts_volume=1.2,
                    export_path=export_path,
                )

            self.assertEqual(result["fileName"], "我的自定义配音.mp3")
            self.assertTrue(Path(result["outputPath"]).is_file())
            filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
            self.assertIn("[0:a:0]asplit=2[source0][source1]", filter_complex)
            self.assertIn("adelay=5000:all=1", filter_complex)
            self.assertIn("apad=whole_dur=12.000", filter_complex)
            self.assertIn("atrim=end=12.000", filter_complex)
            self.assertIn("libmp3lame", commands[0])

    def test_export_falls_back_to_installed_lame_encoder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            audio = root / "voice.wav"
            audio.write_bytes(b"audio")
            commands: list[list[str]] = []

            def fake_run(command, **kwargs):
                commands.append(command)
                Path(command[-1]).write_bytes(b"generated")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(
                tts_mp3_export,
                "probe_media_duration_seconds",
                return_value=2.0,
            ), patch.object(
                tts_mp3_export,
                "resolve_ffmpeg_bin",
                return_value="ffmpeg",
            ), patch.object(
                tts_mp3_export,
                "_ffmpeg_supports_libmp3lame",
                return_value=False,
            ), patch.object(
                tts_mp3_export,
                "_resolve_lame_bin",
                return_value="lame",
            ), patch.object(
                tts_mp3_export.subprocess,
                "run",
                side_effect=fake_run,
            ):
                result = tts_mp3_export.export_tts_timeline_mp3(
                    audio,
                    [{"sourceStartSeconds": 0, "sourceEndSeconds": 2, "startSeconds": 0}],
                    duration_seconds=3,
                    tts_volume=1.0,
                    export_path=root / "demo.mp3",
                )

            self.assertTrue(Path(result["outputPath"]).is_file())
            self.assertIn("pcm_s16le", commands[0])
            self.assertEqual(commands[1][:4], ["lame", "--silent", "-b", "192"])
            self.assertTrue(commands[1][4].endswith(".wav"))
            self.assertTrue(commands[1][5].endswith(".mp3"))
            self.assertFalse(Path(commands[0][-1]).exists())


if __name__ == "__main__":
    unittest.main()
