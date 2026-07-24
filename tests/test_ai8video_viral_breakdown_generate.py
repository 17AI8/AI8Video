from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai8video.breakdown import viral_breakdown as vb


class ViralBreakdownGenerateTests(unittest.TestCase):
    def test_assess_readiness_requires_grid_transcript_script(self) -> None:
        ready = vb.assess_viral_breakdown_generate_readiness(
            has_grid=True,
            transcript_text="台词",
            script_text="骨架",
        )
        self.assertTrue(ready["ready"])
        missing = vb.assess_viral_breakdown_generate_readiness(
            has_grid=False,
            transcript_text="",
            script_text="",
        )
        self.assertFalse(missing["ready"])
        self.assertEqual(missing["missing"], ["grid", "transcript", "script"])

    def test_build_generate_message_includes_material_and_script(self) -> None:
        message = vb.build_viral_breakdown_generate_message(
            script_text="开场冲突",
            transcript_text="机会来了",
            leaves=[{"title": "钩子", "content": "三秒抓住注意力"}],
            material_name="viral-bd-demo-grid.jpg",
            target_ratio="9:16",
            video_name="demo.mp4",
        )
        self.assertIn("@viral-bd-demo-grid.jpg", message)
        self.assertIn("开场冲突", message)
        self.assertIn("机会来了", message)
        self.assertIn("三秒抓住注意力", message)
        self.assertIn("直接生成 1 条 9:16", message)

    def test_save_and_load_generate_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source_dir = root / "原视频"
            source_dir.mkdir(parents=True)
            video = source_dir / "demo.mp4"
            video.write_bytes(b"fake")
            with mock.patch.object(vb, "VIRAL_BREAKDOWN_ROOT", root), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_SOURCE_VIDEO_DIR", source_dir), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_GENERATE_SESSION_DIR", root / "生成会话"), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_SCRIPT_DRAFT_DIR", root / "剧本草稿"), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_GENERATED_VIDEO_DIR", root / "用户生成视频"), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_FRAME_DIR", root / "截图"), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_GRID_DIR", root / "宫格图"), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_TRANSCRIPT_DIR", root / "台词"), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_WHISPER_CACHE_DIR", root / ".cache"):
                saved = vb.save_viral_breakdown_generate_session(
                    "原视频/demo.mp4",
                    session_id="viral-breakdown:demo",
                    status="running",
                    messages=[
                        {"role": "user", "text": "开始生成"},
                        {"role": "assistant", "text": "正在规划…", "kind": "progress"},
                    ],
                    started_at="2026-07-24T07:00:00+00:00",
                )
                self.assertTrue(saved["ok"])
                loaded = vb.load_viral_breakdown_generate_session("demo")
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertEqual(loaded["status"], "running")
                self.assertEqual(loaded["sessionId"], "viral-breakdown:demo")
                self.assertEqual(len(loaded["messages"]), 2)

    def test_attach_generated_video_copies_into_breakdown_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source_dir = root / "原视频"
            generated_dir = root / "用户生成视频"
            result_root = root / "用户生成结果"
            result_video_dir = result_root / "video"
            source_dir.mkdir(parents=True)
            generated_dir.mkdir(parents=True)
            result_video_dir.mkdir(parents=True)
            video = source_dir / "demo.mp4"
            video.write_bytes(b"source")
            source_result = result_video_dir / "out.mp4"
            source_result.write_bytes(b"generated-bytes")
            with mock.patch.object(vb, "VIRAL_BREAKDOWN_ROOT", root), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_SOURCE_VIDEO_DIR", source_dir), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_GENERATED_VIDEO_DIR", generated_dir), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_GENERATE_SESSION_DIR", root / "生成会话"), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_SCRIPT_DRAFT_DIR", root / "剧本草稿"), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_FRAME_DIR", root / "截图"), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_GRID_DIR", root / "宫格图"), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_TRANSCRIPT_DIR", root / "台词"), \
                 mock.patch.object(vb, "VIRAL_BREAKDOWN_WHISPER_CACHE_DIR", root / ".cache"), \
                 mock.patch("ai8video.assets.user_generated_results.ensure_user_generated_result_dir", return_value=result_root), \
                 mock.patch("ai8video.assets.user_files.USER_GENERATED_RESULT_ROOT", result_root):
                payload = vb.attach_viral_breakdown_generated_video(
                    "原视频/demo.mp4",
                    user_generated_key="video/out.mp4",
                )
                self.assertTrue(payload["ok"])
                target = generated_dir / "demo.mp4"
                self.assertTrue(target.is_file())
                self.assertEqual(target.read_bytes(), b"generated-bytes")


if __name__ == "__main__":
    unittest.main()
