from __future__ import annotations

import unittest

from ai8video.core.identity import (
    bridge_legacy_environment,
    normalize_product_env_key,
)


class AI8VideoIdentityTests(unittest.TestCase):
    def test_legacy_environment_is_migrated_without_overwriting_new_value(self) -> None:
        environ = {
            "AI8MINIVIDEO_LLM_MODEL": "legacy-model",
            "AI8VIDEO_IMAGE_MODEL": "current-image",
            "AI8MINIVIDEO_IMAGE_MODEL": "legacy-image",
        }

        migrated = bridge_legacy_environment(environ)

        self.assertEqual(environ["AI8VIDEO_LLM_MODEL"], "legacy-model")
        self.assertEqual(environ["AI8VIDEO_IMAGE_MODEL"], "current-image")
        self.assertEqual(migrated, {"AI8VIDEO_LLM_MODEL": "AI8MINIVIDEO_LLM_MODEL"})

    def test_saved_model_key_is_normalized_to_current_prefix(self) -> None:
        self.assertEqual(
            normalize_product_env_key("AI8MINIVIDEO_VIDEO_MODEL"),
            "AI8VIDEO_VIDEO_MODEL",
        )
        self.assertEqual(normalize_product_env_key("mykey.py model"), "mykey.py model")


if __name__ == "__main__":
    unittest.main()

