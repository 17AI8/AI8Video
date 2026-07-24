from __future__ import annotations

import unittest

from ai8video.media.ffmpeg_utils import _build_media_metadata


class MediaMetadataTests(unittest.TestCase):
    def test_build_media_metadata_from_ffprobe_payload(self) -> None:
        payload = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "avg_frame_rate": "30/1",
                    "pix_fmt": "yuv420p",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "channels": 2,
                    "sample_rate": "44100",
                },
            ],
            "format": {
                "duration": "125.4",
                "bit_rate": "2500000",
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            },
        }
        media = _build_media_metadata(payload)
        self.assertIsNotNone(media)
        assert media is not None
        self.assertEqual(media["resolution"], "1080×1920")
        self.assertEqual(media["aspectRatio"], "9:16")
        self.assertEqual(media["durationLabel"], "2:05")
        self.assertEqual(media["fpsLabel"], "30 fps")
        self.assertEqual(media["videoCodec"], "h264")
        self.assertEqual(media["audioCodec"], "aac")
        self.assertEqual(media["container"], "mov")
        self.assertEqual(media["bitrateLabel"], "2.50 Mbps")

    def test_rotation_swaps_display_resolution(self) -> None:
        payload = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "tags": {"rotate": "90"},
                    "avg_frame_rate": "24/1",
                }
            ],
            "format": {"duration": "10", "format_name": "mp4"},
        }
        media = _build_media_metadata(payload)
        self.assertIsNotNone(media)
        assert media is not None
        self.assertEqual(media["resolution"], "1080×1920")
        self.assertEqual(media["aspectRatio"], "9:16")


if __name__ == "__main__":
    unittest.main()
