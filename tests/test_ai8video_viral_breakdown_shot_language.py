from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from ai8video.breakdown import viral_breakdown as viral_breakdown
from ai8video.breakdown import viral_breakdown_shot_language as shot_language


class _FakeResponse:
    status_code = 200
    text = ""

    @staticmethod
    def json() -> dict:
        content = {
            "overall": "近景主导，信息密度逐步提高",
            "hook": "开场直接给出结果画面",
            "rhythm": "前快后稳",
            "visualStyle": "高对比、主体居中",
            "camera": "固定机位与画面变化推断交替",
            "lighting": "明亮冷色",
            "reusable": "先结果、后解释",
            "avoid": "不要复制人物和品牌标识",
            "beats": [
                {
                    "time": "00:00-00:03",
                    "visual": "近景",
                    "technique": "快速建立主题",
                    "purpose": "钩子",
                }
            ],
            "confidence": 0.82,
        }
        return {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}


class ViralBreakdownShotLanguageTests(unittest.TestCase):
    def test_analysis_uses_representative_frames_and_persists_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            video = self._prepare_source(root, frame_count=12)
            request_mock = mock.Mock(return_value=_FakeResponse())
            with self._patched_environment(root, video), mock.patch.object(
                shot_language,
                "api_request",
                request_mock,
            ):
                result = shot_language.analyze_viral_breakdown_shot_language(
                    "原视频/demo.mp4",
                    config=self._config(),
                )

                self.assertTrue(result["ok"])
                self.assertEqual(len(result["selectedFrames"]), 12)
                self.assertEqual(result["inputFrameCount"], 12)
                self.assertEqual(result["imageBatchCount"], 3)
                self.assertEqual(result["selectedFrames"][0]["timestampSeconds"], 0.0)
                self.assertEqual(result["selectedFrames"][-1]["timestampSeconds"], 22.0)
                self.assertIn("整体策略", result["text"])
                persisted = root / "镜头语言" / "demo.json"
                self.assertTrue(persisted.is_file())
                self.assertNotIn("base64", persisted.read_text(encoding="utf-8"))

                request_payload = request_mock.call_args.kwargs["json"]
                content = request_payload["messages"][1]["content"]
                image_blocks = [item for item in content if item.get("type") == "image_url"]
                text_blocks = [item for item in content if item.get("type") == "text"]
                self.assertEqual(len(image_blocks), 3)
                batch_texts = [item["text"] for item in text_blocks if "全量截图第" in item["text"]]
                self.assertEqual(len(batch_texts), 3)
                self.assertIn("总视频时长", batch_texts[0])
                self.assertIn("截图间隔 2 秒", batch_texts[0])
                self.assertIn("第 1/3 批", batch_texts[0])
                self.assertIn("序号 9–12", batch_texts[-1])
                self.assertIn("右下角黑色标签内的白色数字", batch_texts[0])
                self.assertIn("序号1=0.0–2.0s", batch_texts[0])
                self.assertIn("序号4=6.0–8.0s", batch_texts[0])
                self.assertIn("禁止直接将本批整体作为一个节拍", batch_texts[0])
                self.assertNotIn("从左往右", batch_texts[0])
                self.assertNotIn("从上往下", batch_texts[0])
                self.assertIn("不猜测隐藏提示词", request_payload["messages"][0]["content"])
                self.assertIn("不可信参考数据", request_payload["messages"][0]["content"])

                loaded = shot_language.load_viral_breakdown_shot_language(video)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertFalse(loaded["stale"])

    def test_transcript_change_marks_analysis_stale_and_blocks_injection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            video = self._prepare_source(root, frame_count=3)
            with self._patched_environment(root, video), mock.patch.object(
                shot_language,
                "api_request",
                return_value=_FakeResponse(),
            ):
                shot_language.analyze_viral_breakdown_shot_language(
                    "原视频/demo.mp4",
                    config=self._config(),
                )
                transcript_path = root / "台词" / "demo.json"
                payload = json.loads(transcript_path.read_text(encoding="utf-8"))
                payload["text"] = "台词已经被用户修改"
                transcript_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

                loaded = shot_language.load_viral_breakdown_shot_language(video)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertTrue(loaded["stale"])
                self.assertEqual(shot_language.effective_viral_breakdown_shot_language_text(video), "")

    def test_timestamp_only_change_does_not_stale_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            video = self._prepare_source(root, frame_count=3)
            with self._patched_environment(root, video), mock.patch.object(
                shot_language,
                "api_request",
                return_value=_FakeResponse(),
            ):
                shot_language.analyze_viral_breakdown_shot_language(
                    "原视频/demo.mp4",
                    config=self._config(),
                )
                transcript_path = root / "台词" / "demo.json"
                payload = json.loads(transcript_path.read_text(encoding="utf-8"))
                payload["generatedAt"] = "2030-01-01T00:00:00+00:00"
                payload["updatedAt"] = "2030-01-02T00:00:00+00:00"
                transcript_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

                loaded = shot_language.load_viral_breakdown_shot_language(video)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertFalse(loaded["stale"])

    def test_method_version_change_marks_analysis_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            video = self._prepare_source(root, frame_count=3)
            with self._patched_environment(root, video), mock.patch.object(
                shot_language,
                "api_request",
                return_value=_FakeResponse(),
            ):
                shot_language.analyze_viral_breakdown_shot_language(
                    "原视频/demo.mp4",
                    config=self._config(),
                )
                analysis_path = root / "镜头语言" / "demo.json"
                payload = json.loads(analysis_path.read_text(encoding="utf-8"))
                payload["promptVersion"] = "old-method"
                analysis_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

                loaded = shot_language.load_viral_breakdown_shot_language(video)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertTrue(loaded["stale"])
                self.assertIn("方法", loaded["staleReason"])

    def test_empty_model_result_is_rejected_without_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            video = self._prepare_source(root, frame_count=3)
            response = mock.Mock(status_code=200, text="")
            response.json.return_value = {"choices": [{"message": {"content": "{}"}}]}
            with self._patched_environment(root, video), mock.patch.object(
                shot_language,
                "api_request",
                return_value=response,
            ):
                with self.assertRaisesRegex(RuntimeError, "没有返回可用分析结果"):
                    shot_language.analyze_viral_breakdown_shot_language(
                        "原视频/demo.mp4",
                        config=self._config(),
                    )
                self.assertFalse((root / "镜头语言" / "demo.json").exists())

    def test_manual_transcript_edit_discards_old_segment_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            video = self._prepare_source(root, frame_count=3)
            with mock.patch.multiple(
                viral_breakdown,
                VIRAL_BREAKDOWN_ROOT=root,
                VIRAL_BREAKDOWN_TRANSCRIPT_DIR=root / "台词",
                resolve_viral_breakdown_video_path=mock.Mock(
                    return_value=(video, "原视频/demo.mp4"),
                ),
            ):
                result = viral_breakdown.save_viral_breakdown_transcript(
                    "原视频/demo.mp4",
                    transcript_text="用户重写后的完整台词",
                )

                self.assertEqual(result["segments"], [])
                self.assertTrue(result["segmentsStale"])

    def test_missing_frames_and_multimodal_config_fail_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            video = self._prepare_source(root, frame_count=0)
            request_mock = mock.Mock()
            with self._patched_environment(root, video), mock.patch.object(
                shot_language,
                "api_request",
                request_mock,
            ):
                with self.assertRaisesRegex(RuntimeError, "多模态模型配置不完整"):
                    shot_language.analyze_viral_breakdown_shot_language(
                        "原视频/demo.mp4",
                        config=SimpleNamespace(
                            multimodal_base_url="",
                            multimodal_api_key="",
                            multimodal_model="",
                            timeout_seconds=30,
                        ),
                    )
                with self.assertRaisesRegex(RuntimeError, "拆解画面"):
                    shot_language.analyze_viral_breakdown_shot_language(
                        "原视频/demo.mp4",
                        config=self._config(),
                    )
                request_mock.assert_not_called()

    @staticmethod
    def _prepare_source(root: Path, *, frame_count: int) -> Path:
        video = root / "原视频" / "demo.mp4"
        frame_dir = root / "截图" / "demo"
        transcript_dir = root / "台词"
        grid = root / "宫格图" / "demo-16x9.jpg"
        video.parent.mkdir(parents=True)
        frame_dir.mkdir(parents=True)
        transcript_dir.mkdir(parents=True)
        grid.parent.mkdir(parents=True)
        video.write_bytes(b"video")
        Image.new("RGB", (1600, 900), color=(225, 230, 240)).save(grid, quality=90)
        (frame_dir / "meta.json").write_text(
            json.dumps(
                {
                    "intervalSeconds": 2.0,
                    "frameCount": frame_count,
                    "gridColumns": 4,
                    "gridRows": 3,
                    "gridImageKey": "宫格图/demo-16x9.jpg",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        for index in range(1, frame_count + 1):
            Image.new("RGB", (240, 160), color=(220 - index, 225, 235)).save(
                frame_dir / f"frame-{index:04d}.jpg",
                quality=90,
            )
        (transcript_dir / "demo.json").write_text(
            json.dumps(
                {
                    "text": "开场介绍，随后解释重点。",
                    "segments": [
                        {"start": 0.0, "end": 4.0, "text": "开场介绍"},
                        {"start": 12.0, "end": 24.0, "text": "随后解释重点"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return video

    @staticmethod
    def _config() -> SimpleNamespace:
        return SimpleNamespace(
            multimodal_base_url="https://example.com/v1",
            multimodal_api_key="test-key",
            multimodal_model="vision-test",
            timeout_seconds=30,
        )

    @staticmethod
    def _patched_environment(root: Path, video: Path):
        return mock.patch.multiple(
            shot_language,
            VIRAL_BREAKDOWN_ROOT=root,
            VIRAL_BREAKDOWN_FRAME_DIR=root / "截图",
            VIRAL_BREAKDOWN_TRANSCRIPT_DIR=root / "台词",
            VIRAL_BREAKDOWN_SHOT_LANGUAGE_DIR=root / "镜头语言",
            ensure_viral_breakdown_dirs=mock.Mock(return_value=root),
            resolve_viral_breakdown_video_path=mock.Mock(
                return_value=(video, "原视频/demo.mp4"),
            ),
        )


if __name__ == "__main__":
    unittest.main()
