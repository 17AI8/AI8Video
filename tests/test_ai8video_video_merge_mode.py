from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai8video.interfaces.web import app as ai8video_web
from ai8video.media import video_merge_mode


class AI8VideoVideoMergeModeTest(unittest.TestCase):
    def test_defaults_to_none_and_persists_merge_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings_path = Path(tempdir) / "视频合并" / "settings.json"
            with patch.object(video_merge_mode, "VIDEO_MERGE_MODE_DIR", settings_path.parent), \
                    patch.object(video_merge_mode, "VIDEO_MERGE_MODE_SETTINGS_PATH", settings_path):
                self.assertEqual(video_merge_mode.load_video_merge_mode(), "none")

                saved = video_merge_mode.save_video_merge_mode("merge2")

                self.assertTrue(saved["ok"])
                self.assertEqual(saved["mergeMode"], "merge2")
                self.assertEqual(video_merge_mode.load_video_merge_mode(), "merge2")

                saved = video_merge_mode.save_video_merge_mode("merge4")

                self.assertTrue(saved["ok"])
                self.assertEqual(saved["mergeMode"], "merge4")
                self.assertEqual(video_merge_mode.load_video_merge_mode(), "merge4")

    def test_rejects_unknown_mode_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings_path = Path(tempdir) / "视频合并" / "settings.json"
            with patch.object(video_merge_mode, "VIDEO_MERGE_MODE_DIR", settings_path.parent), \
                    patch.object(video_merge_mode, "VIDEO_MERGE_MODE_SETTINGS_PATH", settings_path):
                saved = video_merge_mode.save_video_merge_mode("merge3")

                self.assertEqual(saved["mergeMode"], "none")
                self.assertEqual(video_merge_mode.load_video_merge_mode(), "none")

    def test_web_api_saves_merge_mode(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="POST", json={"mergeMode": "merge4"})
        try:
            with patch.object(
                ai8video_web,
                "save_video_merge_mode",
                return_value={"ok": True, "mergeMode": "merge4"},
            ) as save:
                body = ai8video_web.api_video_merge_mode()
        finally:
            ai8video_web.request = request_backup

        save.assert_called_once_with("merge4")
        self.assertTrue(body["ok"])
        self.assertEqual(body["mergeMode"], "merge4")


if __name__ == "__main__":
    unittest.main()
