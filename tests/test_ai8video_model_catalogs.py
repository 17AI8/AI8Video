from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai8video.integrations import model_catalogs


class AI8VideoModelCatalogsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_dir = model_catalogs.MODEL_CATALOG_DIR
        self.original_path = model_catalogs.MODEL_CATALOG_PATH
        root = Path(self.tempdir.name)
        model_catalogs.MODEL_CATALOG_DIR = root / "模型设置"
        model_catalogs.MODEL_CATALOG_PATH = model_catalogs.MODEL_CATALOG_DIR / "model_catalogs.json"

    def tearDown(self) -> None:
        model_catalogs.MODEL_CATALOG_DIR = self.original_dir
        model_catalogs.MODEL_CATALOG_PATH = self.original_path
        self.tempdir.cleanup()

    def test_save_model_catalog_persists_until_next_save(self) -> None:
        saved = model_catalogs.save_model_catalog(
            "AI8VIDEO_VIDEO_MODEL",
            [
                {"modelId": "doubao-seedance-1-5-pro-251215", "name": "doubao-seedance-1-5-pro-251215", "type": "video"},
                {"modelId": "wan2.6-i2v-flash", "name": "wan2.6-i2v-flash", "type": "video"},
            ],
        )

        self.assertEqual([item["modelId"] for item in saved], ["doubao-seedance-1-5-pro-251215", "wan2.6-i2v-flash"])
        self.assertEqual(
            [item["modelId"] for item in model_catalogs.load_model_catalog("AI8VIDEO_VIDEO_MODEL")],
            ["doubao-seedance-1-5-pro-251215", "wan2.6-i2v-flash"],
        )

        model_catalogs.save_model_catalog(
            "AI8VIDEO_VIDEO_MODEL",
            [{"modelId": "new-video-model", "name": "new-video-model", "type": "video"}],
        )

        self.assertEqual(
            [item["modelId"] for item in model_catalogs.load_model_catalog("AI8VIDEO_VIDEO_MODEL")],
            ["new-video-model"],
        )

    def test_image_model_catalog_is_persisted(self) -> None:
        saved = model_catalogs.save_model_catalog(
            "AI8VIDEO_IMAGE_MODEL",
            [
                {"modelId": "GPT-image2", "name": "GPT-image2", "type": "image"},
                {"modelId": "seedream", "name": "seedream", "type": "image"},
            ],
        )

        self.assertEqual([item["modelId"] for item in saved], ["GPT-image2", "seedream"])
        self.assertEqual(
            [item["modelId"] for item in model_catalogs.load_model_catalog("AI8VIDEO_IMAGE_MODEL")],
            ["GPT-image2", "seedream"],
        )


if __name__ == "__main__":
    unittest.main()
