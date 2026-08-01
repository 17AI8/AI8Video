from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from ai8video.integrations import model_profiles
from ai8video.core import config as core_config
from ai8video.integrations import video_model_settings


class ModelProfilesTest(unittest.TestCase):
    def test_migrates_defaults_and_supports_create_activate_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(model_profiles, "MODEL_PROFILES_DIR", root), patch.object(
                model_profiles, "MODEL_PROFILES_PATH", root / "model_profiles.json"
            ):
                store = model_profiles.ensure_model_profiles({
                    "llm": {"baseUrl": "https://one.example", "apiKey": "secret", "model": "one"},
                })
                default_id = store["categories"]["llm"]["activeId"]
                self.assertEqual(model_profiles.active_model_profile("llm")["model"], "one")

                store = model_profiles.create_model_profile("llm", {"name": "备用", "model": "two"})
                backup_id = store["categories"]["llm"]["profiles"][-1]["id"]
                model_profiles.activate_model_profile("llm", backup_id)
                self.assertEqual(model_profiles.active_model_profile("llm")["model"], "two")

                store = model_profiles.delete_model_profile("llm", default_id)
                self.assertEqual(len(store["categories"]["llm"]["profiles"]), 1)

    def test_blank_api_key_keeps_existing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(model_profiles, "MODEL_PROFILES_DIR", root), patch.object(
                model_profiles, "MODEL_PROFILES_PATH", root / "model_profiles.json"
            ):
                store = model_profiles.ensure_model_profiles({
                    "llm": {"apiKey": "keep-me", "model": "one"},
                })
                profile_id = store["categories"]["llm"]["activeId"]
                model_profiles.update_model_profile("llm", profile_id, {"apiKey": "", "model": "two"})
                self.assertEqual(model_profiles.active_model_profile("llm")["apiKey"], "keep-me")

    def test_duplicate_copies_secret_and_stays_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(model_profiles, "MODEL_PROFILES_DIR", root), patch.object(
                model_profiles, "MODEL_PROFILES_PATH", root / "model_profiles.json"
            ):
                store = model_profiles.ensure_model_profiles({
                    "video": {
                        "baseUrl": "https://video.example",
                        "apiKey": "copy-me",
                        "model": "video-model",
                        "template": "openai-compatible",
                    },
                })
                active_id = store["categories"]["video"]["activeId"]
                store = model_profiles.duplicate_model_profile("video", active_id)
                duplicate = store["categories"]["video"]["profiles"][-1]

                self.assertNotEqual(duplicate["id"], active_id)
                self.assertEqual(store["categories"]["video"]["activeId"], active_id)
                self.assertEqual(duplicate["name"], "默认配置 副本")
                self.assertEqual(duplicate["baseUrl"], "https://video.example")
                self.assertEqual(duplicate["apiKey"], "copy-me")
                self.assertEqual(duplicate["model"], "video-model")
                self.assertEqual(duplicate["template"], "openai-compatible")

                store = model_profiles.duplicate_model_profile("video", active_id)
                self.assertEqual(store["categories"]["video"]["profiles"][-1]["name"], "默认配置 副本 2")

    def test_public_payload_never_returns_api_key(self) -> None:
        store = {
            "categories": {
                "llm": {"activeId": "one", "profiles": [{"id": "one", "apiKey": "secret"}]},
            },
        }
        payload = model_profiles.public_model_profiles(store)
        self.assertEqual(payload["llm"]["profiles"][0]["apiKey"], "")
        self.assertTrue(payload["llm"]["profiles"][0]["hasApiKey"])

    def test_core_config_uses_active_profiles(self) -> None:
        profiles = {
            "llm": {"baseUrl": "https://llm.example", "apiKey": "llm-key", "model": "llm-model"},
            "multimodal": {"baseUrl": "https://mm.example", "apiKey": "mm-key", "model": "mm-model"},
            "image": {"baseUrl": "https://image.example", "apiKey": "image-key", "model": "image-model"},
        }
        with patch.dict(os.environ, {"AI8VIDEO_DRY_RUN": "0"}, clear=True), patch.object(
            core_config, "active_model_profile", side_effect=lambda category: profiles.get(category)
        ):
            config = core_config.AI8VideoConfig.from_env()
        self.assertEqual(config.llm_model, "llm-model")
        self.assertEqual(config.multimodal_model, "mm-model")
        self.assertEqual(config.image_model, "image-model")

    def test_video_settings_uses_active_profile(self) -> None:
        with patch.object(video_model_settings, "_load_from_env", return_value=None), patch.object(
            video_model_settings, "_load_from_file", return_value=None
        ), patch.object(
            video_model_settings,
            "active_model_profile",
            return_value={
                "baseUrl": "https://video.example",
                "apiKey": "video-key",
                "model": "video-model",
                "template": "openai-compatible",
            },
        ):
            settings = video_model_settings.load_video_model_settings()
        self.assertEqual(settings.base_url, "https://video.example")
        self.assertEqual(settings.model, "video-model")
        self.assertEqual(settings.template, "openai-compatible")

    def test_removed_video_templates_migrate_to_openai_compatible(self) -> None:
        for template in ("yunwu-grok", "yunwu-omni", "yunwu-veo"):
            settings = video_model_settings.normalize_video_model_settings({"template": template})
            self.assertEqual(settings.template, "openai-compatible")


if __name__ == "__main__":
    unittest.main()
