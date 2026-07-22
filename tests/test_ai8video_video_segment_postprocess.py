from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.media import video_segment_postprocess


class AI8VideoVideoSegmentPostprocessTests(unittest.TestCase):
    def test_extract_frame_at_time_writes_png_at_requested_time(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "source.mp4"
            output = root / "extension-frame.png"
            source.write_bytes(b"video")
            commands: list[list[str]] = []

            def fake_run(cmd: list[str], _message: str) -> None:
                commands.append(cmd)
                output.write_bytes(b"png")

            with patch.object(video_segment_postprocess, "_run_ffmpeg", side_effect=fake_run):
                result = video_segment_postprocess.extract_frame_at_time(
                    source,
                    output,
                    time_seconds=3.75,
                    ffmpeg_bin="ffmpeg-test",
                )

            command = commands[0]
            self.assertEqual(command[command.index("-ss") + 1], "3.750")
            self.assertEqual(result, output)

    def test_trim_video_end_keeps_audio_and_uses_requested_frame_time(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "source.mp4"
            output = root / "trimmed.mp4"
            source.write_bytes(b"video")
            commands: list[list[str]] = []

            def fake_run(cmd: list[str], _message: str) -> None:
                commands.append(cmd)
                output.write_bytes(b"trimmed")

            with patch.object(video_segment_postprocess, "_run_ffmpeg", side_effect=fake_run):
                result = video_segment_postprocess.trim_video_end(
                    source,
                    output,
                    end_seconds=4.25,
                    ffmpeg_bin="ffmpeg-test",
                )

            command = commands[0]
            self.assertEqual(command[command.index("-t") + 1], "4.250")
            self.assertEqual(command[command.index("-c:a") + 1], "aac")
            self.assertEqual(result["endSeconds"], 4.25)

    def test_concat_videos_uses_quality_encoding_only_when_copy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            segment1 = root / "segment1.mp4"
            segment2 = root / "segment2.mp4"
            output = root / "merged.mp4"
            segment1.write_bytes(b"segment1")
            segment2.write_bytes(b"segment2")
            commands: list[list[str]] = []

            def fake_run(cmd: list[str], message: str) -> None:
                commands.append(cmd)
                if len(commands) == 1:
                    raise RuntimeError(message)
                output.write_bytes(b"merged")

            with patch.object(video_segment_postprocess, "_run_ffmpeg", side_effect=fake_run):
                result = video_segment_postprocess.concat_videos(
                    [segment1, segment2],
                    output,
                    ffmpeg_bin="ffmpeg-test",
                )

            self.assertEqual(result["method"], "reencode")
            self.assertEqual(commands[0][commands[0].index("-c") + 1], "copy")
            self.assertEqual(commands[1][commands[1].index("-c:v") + 1], "libx264")
            self.assertEqual(commands[1][commands[1].index("-preset") + 1], "veryfast")
            self.assertEqual(commands[1][commands[1].index("-crf") + 1], "16")
            self.assertEqual(commands[1][commands[1].index("-pix_fmt") + 1], "yuv420p")
            self.assertEqual(result["videoEncoding"]["crf"], "16")


if __name__ == "__main__":
    unittest.main()
