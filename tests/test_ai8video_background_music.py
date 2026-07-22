from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.media import background_music
from ai8video.media import ffmpeg_utils
from ai8video.media.ffmpeg_utils import resolve_ffmpeg_bin


class AI8VideoBackgroundMusicTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.env_backup = os.environ.get("AI8VIDEO_BACKGROUND_MUSIC_DIR")
        os.environ["AI8VIDEO_BACKGROUND_MUSIC_DIR"] = str(self.root / "music")

    def tearDown(self) -> None:
        if self.env_backup is None:
            os.environ.pop("AI8VIDEO_BACKGROUND_MUSIC_DIR", None)
        else:
            os.environ["AI8VIDEO_BACKGROUND_MUSIC_DIR"] = self.env_backup
        self.tempdir.cleanup()

    def test_mix_background_music_loops_and_trims_audio_with_ffmpeg(self) -> None:
        video = self.root / "video.mp4"
        video.write_bytes(b"video")
        music = background_music.background_music_path()
        music.parent.mkdir(parents=True, exist_ok=True)
        music.write_bytes(b"mp3")
        commands: list[list[str]] = []

        def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool) -> None:
            commands.append(cmd)
            Path(cmd[-1]).write_bytes(b"mixed")

        with patch.object(background_music, "probe_media_duration_seconds", return_value=20.0), \
                patch.object(background_music.subprocess, "run", side_effect=fake_run):
            result = background_music.mix_background_music(video, music, ffmpeg_bin="ffmpeg-test")

        self.assertEqual(result["status"], "mixed")
        self.assertEqual(video.read_bytes(), b"mixed")
        self.assertEqual(commands[0][0], "ffmpeg-test")
        self.assertIn("-stream_loop", commands[0])
        self.assertIn("-1", commands[0])
        self.assertNotIn("-shortest", commands[0])
        self.assertIn("-t", commands[0])
        self.assertEqual(commands[0][commands[0].index("-t") + 1], "20.000")
        self.assertIn("+faststart", commands[0])
        self.assertEqual(commands[0][commands[0].index("-map") + 1], "0:v:0")
        self.assertEqual(commands[0][commands[0].index("-c:v") + 1], "copy")
        self.assertIn("-filter_complex", commands[0])
        filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
        self.assertIn("[1:a:0]volume=0.28[bgm]", filter_complex)
        self.assertIn("[0:a:0]apad[orig]", filter_complex)
        self.assertIn("[orig][bgm]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0", filter_complex)
        self.assertIn("[aout]", commands[0])
        self.assertEqual(result["originalAudio"], "preserved")
        self.assertEqual(result["backgroundMusicVolume"], 0.28)

    def test_mix_background_music_can_mute_original_audio_with_music(self) -> None:
        video = self.root / "video.mp4"
        video.write_bytes(b"video")
        music = background_music.background_music_path()
        music.parent.mkdir(parents=True, exist_ok=True)
        music.write_bytes(b"mp3")
        background_music.update_preserve_original_audio(False)
        commands: list[list[str]] = []

        def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool) -> None:
            commands.append(cmd)
            Path(cmd[-1]).write_bytes(b"mixed")

        with patch.object(background_music, "probe_media_duration_seconds", return_value=20.0), \
                patch.object(background_music.subprocess, "run", side_effect=fake_run):
            result = background_music.mix_background_music(video, music, ffmpeg_bin="ffmpeg-test")

        self.assertEqual(result["status"], "mixed")
        self.assertEqual(result["originalAudio"], "muted")
        filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
        self.assertIn("[1:a:0]volume=0.28[aout]", filter_complex)
        self.assertNotIn("[0:a:0]", filter_complex)
        self.assertEqual(commands[0][commands[0].index("-t") + 1], "20.000")
        self.assertEqual(video.read_bytes(), b"mixed")

    def test_mix_background_music_can_preserve_tts_replaced_audio_with_override(self) -> None:
        video = self.root / "video.mp4"
        video.write_bytes(b"video")
        music = background_music.background_music_path()
        music.parent.mkdir(parents=True, exist_ok=True)
        music.write_bytes(b"mp3")
        background_music.update_preserve_original_audio(False)
        commands: list[list[str]] = []

        def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool) -> None:
            commands.append(cmd)
            Path(cmd[-1]).write_bytes(b"mixed")

        with patch.object(background_music, "probe_media_duration_seconds", return_value=20.0), \
                patch.object(background_music.subprocess, "run", side_effect=fake_run):
            result = background_music.mix_background_music(
                video,
                music,
                ffmpeg_bin="ffmpeg-test",
                preserve_original_audio_override=True,
                preserved_audio_volume_override=0.78,
            )

        self.assertEqual(result["status"], "mixed")
        self.assertEqual(result["originalAudio"], "preserved")
        self.assertEqual(result["preservedAudioVolume"], 0.78)
        filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
        self.assertIn("[0:a:0]volume=0.78,apad[orig]", filter_complex)
        self.assertIn("[orig][bgm]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0", filter_complex)

    def test_mix_background_music_mutes_original_audio_without_music_when_disabled(self) -> None:
        video = self.root / "video.mp4"
        video.write_bytes(b"video")
        background_music.update_preserve_original_audio(False)
        commands: list[list[str]] = []

        def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool) -> None:
            commands.append(cmd)
            Path(cmd[-1]).write_bytes(b"muted")

        with patch.object(background_music.subprocess, "run", side_effect=fake_run):
            result = background_music.mix_background_music(video, ffmpeg_bin="ffmpeg-test")

        self.assertEqual(result["status"], "muted")
        self.assertEqual(result["originalAudio"], "muted")
        self.assertIn("-an", commands[0])
        self.assertNotIn("-filter_complex", commands[0])
        self.assertEqual(video.read_bytes(), b"muted")

    def test_mix_background_music_uses_saved_volume(self) -> None:
        video = self.root / "video.mp4"
        video.write_bytes(b"video")
        music = background_music.background_music_path()
        music.parent.mkdir(parents=True, exist_ok=True)
        music.write_bytes(b"mp3")
        background_music.update_background_music_volume(45)
        commands: list[list[str]] = []

        def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool) -> None:
            commands.append(cmd)
            Path(cmd[-1]).write_bytes(b"mixed")

        with patch.object(background_music, "probe_media_duration_seconds", return_value=20.0), \
                patch.object(background_music.subprocess, "run", side_effect=fake_run):
            result = background_music.mix_background_music(video, music, ffmpeg_bin="ffmpeg-test")

        filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
        self.assertIn("[1:a:0]volume=0.45[bgm]", filter_complex)
        self.assertEqual(result["backgroundMusicVolume"], 0.45)

    def test_mix_background_music_falls_back_when_video_has_no_original_audio(self) -> None:
        video = self.root / "silent.mp4"
        video.write_bytes(b"video")
        music = background_music.background_music_path()
        music.parent.mkdir(parents=True, exist_ok=True)
        music.write_bytes(b"mp3")
        commands: list[list[str]] = []

        def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool) -> None:
            commands.append(cmd)
            if len(commands) == 1:
                raise background_music.subprocess.CalledProcessError(
                    1,
                    cmd,
                    stderr="Stream specifier ':a:0' matches no streams.",
                )
            Path(cmd[-1]).write_bytes(b"fallback-mixed")

        with patch.object(background_music, "probe_media_duration_seconds", return_value=20.0), \
                patch.object(background_music.subprocess, "run", side_effect=fake_run):
            result = background_music.mix_background_music(video, music, ffmpeg_bin="ffmpeg-test")

        self.assertEqual(result["status"], "mixed")
        self.assertEqual(result["originalAudio"], "missing")
        self.assertEqual(result["fallback"], "background_music_only")
        self.assertEqual(video.read_bytes(), b"fallback-mixed")
        self.assertEqual(len(commands), 2)
        self.assertIn("-filter_complex", commands[0])
        self.assertIn("-filter_complex", commands[1])
        self.assertIn("[1:a:0]volume=0.28[aout]", commands[1])
        self.assertIn("1:a:0", " ".join(commands[1]))
        self.assertEqual(commands[1][commands[1].index("-t") + 1], "20.000")

    def test_mix_background_music_skips_when_no_music_uploaded(self) -> None:
        video = self.root / "video.mp4"
        video.write_bytes(b"video")

        with patch.object(background_music.subprocess, "run") as run_ffmpeg:
            result = background_music.mix_background_music(video)

        run_ffmpeg.assert_not_called()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(video.read_bytes(), b"video")

    def test_background_music_status_defaults_to_preserve_original_audio(self) -> None:
        result = background_music.background_music_status()

        self.assertTrue(result["ok"])
        self.assertTrue(result["preserveOriginalAudio"])

    def test_extract_background_music_from_video_writes_current_mp3(self) -> None:
        video = self.root / "source.mp4"
        video.write_bytes(b"video")
        target = self.root / "current.mp3"
        commands: list[list[str]] = []

        def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool) -> None:
            commands.append(cmd)
            Path(cmd[-1]).write_bytes(b"mp3")

        with patch.object(background_music.subprocess, "run", side_effect=fake_run):
            result = background_music.extract_background_music_from_video(video, target, ffmpeg_bin="ffmpeg-test")

        self.assertTrue(result["ok"])
        self.assertEqual(target.read_bytes(), b"mp3")
        self.assertEqual(commands[0][0], "ffmpeg-test")
        self.assertIn("-vn", commands[0])
        self.assertIn("0:a:0", commands[0])
        self.assertIn("libmp3lame", commands[0])

    def test_select_background_music_does_not_copy_item_to_current(self) -> None:
        music_root = background_music.ensure_background_music_dir()
        library = background_music.background_music_library_dir()
        library.mkdir(parents=True, exist_ok=True)
        first = library / "first.mp3"
        second = library / "second.mp3"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        background_music._upsert_background_music_item({
            "id": "first",
            "name": "first.mp3",
            "sourceName": "first.mp3",
            "sourceType": "audio",
            "path": str(first),
        })
        background_music._upsert_background_music_item({
            "id": "second",
            "name": "second.mp3",
            "sourceName": "second.mp3",
            "sourceType": "audio",
            "path": str(second),
        })

        result = background_music.select_background_music("second")

        self.assertTrue(result["ok"])
        self.assertEqual(result["selectedId"], "second")
        self.assertFalse((music_root / "current.mp3").exists())
        self.assertEqual([item["id"] for item in result["items"] if item["selected"]], ["second"])
        self.assertEqual(background_music._selected_background_music_path(), second)

    def test_clear_background_music_selection_keeps_items_but_removes_current(self) -> None:
        music_root = background_music.ensure_background_music_dir()
        library = background_music.background_music_library_dir()
        library.mkdir(parents=True, exist_ok=True)
        music = library / "theme.mp3"
        music.write_bytes(b"theme")
        background_music._upsert_background_music_item({
            "id": "theme",
            "name": "theme.mp3",
            "sourceName": "theme.mp3",
            "sourceType": "audio",
            "path": str(music),
        })
        background_music.select_background_music("theme")

        result = background_music.clear_background_music_selection()

        self.assertTrue(result["ok"])
        self.assertFalse(result["enabled"])
        self.assertEqual(result["selectedId"], "")
        self.assertFalse((music_root / "current.mp3").exists())
        self.assertEqual([item["name"] for item in result["items"]], ["theme.mp3"])
        self.assertEqual([item["selected"] for item in result["items"]], [False])

    def test_status_discovers_manual_mp3_in_library_with_original_name(self) -> None:
        music_root = background_music.ensure_background_music_dir()
        library = background_music.background_music_library_dir()
        library.mkdir(parents=True, exist_ok=True)
        (music_root / "current.mp3").write_bytes(b"current")
        (music_root / "source.mp4").write_bytes(b"legacy-source")
        manual = library / "马斯克BGM.mp3"
        manual.write_bytes(b"manual")

        result = background_music.background_music_status()

        names = [item["name"] for item in result["items"]]
        self.assertIn("马斯克BGM.mp3", names)
        self.assertNotIn("source.mp3", names)
        self.assertNotIn("current.mp3", names)
        self.assertFalse(result["enabled"])
        self.assertEqual([item["name"] for item in result["items"] if item["selected"]], [])
        self.assertEqual(background_music.background_music_path().read_bytes(), b"current")

    def test_status_dedupes_same_bgm_after_project_path_changes(self) -> None:
        music_root = background_music.ensure_background_music_dir()
        library = background_music.background_music_library_dir()
        library.mkdir(parents=True, exist_ok=True)
        current = music_root / "current.mp3"
        current.write_bytes(b"manual")
        actual = library / "马斯克BGM.mp3"
        actual.write_bytes(b"manual")
        old_id = "manual-BGM-old"
        new_id = "manual-BGM-new"
        (music_root / "items.json").write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "id": old_id,
                            "name": "马斯克BGM.mp3",
                            "sourceName": "马斯克BGM.mp3",
                            "sourceType": "audio",
                            "path": str(self.root / "old-machine" / "马斯克BGM.mp3"),
                            "sizeBytes": 6,
                            "createdAt": "2026-06-14T00:00:00+00:00",
                            "updatedAt": "2026-06-14T00:00:00+00:00",
                        },
                        {
                            "id": new_id,
                            "name": "马斯克BGM.mp3",
                            "sourceName": "马斯克BGM.mp3",
                            "sourceType": "audio",
                            "path": str(actual),
                            "sizeBytes": 6,
                            "createdAt": "2026-06-15T00:00:00+00:00",
                            "updatedAt": "2026-06-15T00:00:00+00:00",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (music_root / "current.json").write_text(
            json.dumps({"selectedId": old_id, "volume": 0.49}, ensure_ascii=False),
            encoding="utf-8",
        )

        result = background_music.background_music_status()

        self.assertEqual([item["name"] for item in result["items"]], ["马斯克BGM.mp3"])
        self.assertEqual(result["selectedId"], new_id)
        self.assertEqual([item["selected"] for item in result["items"]], [True])
        saved_items = json.loads((music_root / "items.json").read_text(encoding="utf-8"))["items"]
        self.assertEqual([item["id"] for item in saved_items], [new_id])
        saved_meta = json.loads((music_root / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(saved_meta["selectedId"], new_id)

    def test_resolve_ffmpeg_prefers_explicit_value(self) -> None:
        self.assertEqual(resolve_ffmpeg_bin("ffmpeg-test"), "ffmpeg-test")

    def test_resolve_ffmpeg_uses_external_local_runtime(self) -> None:
        local = self.root / ".local" / "bin" / "ffmpeg"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(b"ffmpeg")
        local.chmod(0o755)

        with patch.dict(os.environ, {}, clear=True), patch.object(
            ffmpeg_utils.shutil, "which", return_value=None
        ), patch.object(ffmpeg_utils, "local_ffmpeg_candidates", return_value=[local]):
            self.assertEqual(resolve_ffmpeg_bin(), str(local))


if __name__ == "__main__":
    unittest.main()
