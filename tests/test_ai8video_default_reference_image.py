from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from ai8video.assets import default_reference_image
from ai8video.generation import business_prompt
from ai8video.application.conversation_controller import AI8VideoConversationController
from ai8video.core.config import AI8VideoConfig
from ai8video.core.models import (
    ConversationState,
    VideoPrompt,
    FirstFrameAsset,
    ParsedRequest,
    PipelineResult,
    QuickVideoJob,
)
from ai8video.generation.reference_image_preprocessor import (
    ReferenceImagePreprocessor,
    _image_generation_direct_connect_target,
    _image_generation_direct_connect_trace,
    _image_generation_max_concurrency,
    _image_generation_size,
    _lookup_public_dns_ipv4,
    _is_fake_dns_address,
    _image_lost_response_retries,
    _build_context_text,
    _reference_image_input,
    build_reference_image_transform_prompt,
)


def _write_test_image(path: Path, *, image_format: str = "PNG") -> None:
    from PIL import Image

    Image.new("RGB", (4, 4), (248, 64, 64)).save(path, format=image_format)


class _FakeRequestsSession:
    def __init__(self, *, post_response=None, get_response=None):
        self.post_response = post_response
        self.get_response = get_response
        self.trust_env = True
        self.proxies = {
            "http": "http://127.0.0.1:7890",
            "https": "http://127.0.0.1:7890",
        }
        self.post_calls = []
        self.get_calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        if isinstance(self.post_response, list):
            next_response = self.post_response.pop(0)
            if isinstance(next_response, BaseException):
                raise next_response
            return next_response
        return self.post_response

    def get(self, *args, **kwargs):
        self.get_calls.append((args, kwargs))
        return self.get_response


class AI8VideoDefaultReferenceImageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.business_prompt_tempdir = tempfile.TemporaryDirectory()
        self.business_prompt_path = Path(self.business_prompt_tempdir.name) / "ai8video_business_model_prompt.txt"
        self.business_prompt_patch = patch.object(business_prompt, "BUSINESS_PROMPT_PATH", self.business_prompt_path)
        self.business_prompt_patch.start()
        _lookup_public_dns_ipv4.cache_clear()
        self.reference_dns_patch = patch(
            "ai8video.generation.reference_image_preprocessor.socket.gethostbyname",
            return_value="203.0.113.10",
        )
        self.reference_dns_patch.start()

    def tearDown(self) -> None:
        self.reference_dns_patch.stop()
        _lookup_public_dns_ipv4.cache_clear()
        self.business_prompt_patch.stop()
        self.business_prompt_tempdir.cleanup()

    def test_select_and_clear_default_reference_image(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings_path = Path(tempdir) / "参考图" / "settings.json"
            image = Path(tempdir) / "老板.png"
            image.write_bytes(b"png")
            materials = {
                "images": [{
                    "name": "老板.png",
                    "relativePath": "老板.png",
                    "path": str(image),
                    "url": "/user-materials/images/%E8%80%81%E6%9D%BF.png",
                    "kind": "image",
                }]
            }
            with patch.object(default_reference_image, "DEFAULT_REFERENCE_IMAGE_DIR", settings_path.parent), \
                    patch.object(default_reference_image, "DEFAULT_REFERENCE_IMAGE_SETTINGS_PATH", settings_path), \
                    patch.object(default_reference_image, "list_user_materials", return_value=materials):
                selected = default_reference_image.select_default_reference_image("老板.png")
                self.assertTrue(selected["enabled"])
                self.assertEqual(selected["item"]["path"], str(image))
                self.assertEqual(default_reference_image.default_reference_image_path(), str(image))
                self.assertIn("effectDefinitions", selected)
                self.assertIn("autoChangeClothes", {item["key"] for item in selected["effectDefinitions"]})

                options = default_reference_image.update_default_reference_image_options({
                    "autoChangeClothes": True,
                    "autoChangeBackground": True,
                    "autoChangePose": False,
                }, custom_prompt="保留高级商务质感，人物穿深色西装。")
                self.assertTrue(options["options"]["autoChangeClothes"])
                self.assertTrue(options["options"]["autoChangeBackground"])
                self.assertFalse(options["options"]["autoChangePose"])
                self.assertIn("衣服必须和原参考图完全不同", default_reference_image.default_reference_image_instruction() or "")
                self.assertEqual(options["customPrompt"], "保留高级商务质感，人物穿深色西装。")
                self.assertIn("保留高级商务质感", default_reference_image.default_reference_image_instruction() or "")
                self.assertEqual(
                    default_reference_image.default_reference_image_custom_prompt(),
                    "保留高级商务质感，人物穿深色西装。",
                )

                cleared = default_reference_image.clear_default_reference_image()
                self.assertFalse(cleared["enabled"])
                self.assertTrue(cleared["options"]["autoChangeClothes"])
                self.assertEqual(cleared["customPrompt"], "保留高级商务质感，人物穿深色西装。")
                self.assertIsNone(default_reference_image.default_reference_image_path())

    def test_conversation_controller_uses_default_reference_when_user_does_not_opt_out(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[VideoPrompt(index=1, title="第 1 条", prompt=request.raw_text)],
                    first_frame=FirstFrameAsset(source=request.reference_image),
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "none")  # type: ignore[arg-type]
        message = (
            "生成一条10秒短视频，开场老板抬头看向镜头，说客户资料不能只留在平台，"
            "中段讲私域承接的重要性，结尾提醒团队今天就把线索沉淀到AI8video 。"
        )
        with patch("ai8video.application.conversation_controller.default_reference_image_path", return_value="/tmp/default.png"), \
                patch(
                    "ai8video.application.conversation_controller.enabled_default_reference_image_options",
                    return_value={"autoChangeClothes": True, "autoChangeBackground": False, "autoChangePose": False},
                ), patch(
                    "ai8video.application.conversation_controller.default_reference_image_custom_prompt",
                    return_value="保持高级商务棚拍感",
                ):
            reply = agent.handle_message("s1", message)

        self.assertEqual(reply.stage, "completed")
        self.assertEqual(captured["request"].reference_image, "/tmp/default.png")
        self.assertEqual(captured["request"].reference_image_transform_options, {
            "autoChangeClothes": True,
            "autoChangeBackground": False,
            "autoChangePose": False,
        })
        self.assertEqual(captured["request"].reference_image_custom_prompt, "保持高级商务棚拍感")
        self.assertNotIn("参考图设定", captured["request"].raw_text)

    def test_conversation_controller_uses_saved_reference_when_user_says_current_reference(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[VideoPrompt(index=1, title="第 1 条", prompt=request.raw_text)],
                    first_frame=FirstFrameAsset(source=request.reference_image),
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "none")  # type: ignore[arg-type]
        message = (
            "根据当前剧本参考生成1个10秒短视频，使用当前参考图。"
            "台词/口播：老板说客户资料不能只留在平台，今天就沉淀到AI8video 。"
        )
        with patch("ai8video.application.conversation_controller.default_reference_image_path", return_value="/tmp/current.png"), \
                patch("ai8video.application.conversation_controller.enabled_default_reference_image_options", return_value={}), \
                patch("ai8video.application.conversation_controller.default_reference_image_custom_prompt", return_value=None):
            reply = agent.handle_message("s-current-ref", message)

        self.assertEqual(reply.stage, "completed")
        self.assertEqual(captured["request"].reference_image, "/tmp/current.png")

    def test_conversation_controller_explicit_reference_inherits_enabled_default_effect_options(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[VideoPrompt(index=1, title="第 1 条", prompt=request.raw_text)],
                    first_frame=FirstFrameAsset(source=request.reference_image),
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "none")  # type: ignore[arg-type]
        message = (
            "生成1个10秒短视频，参考图 /tmp/user-ref.png。"
            "老板正对镜头讲客户资料沉淀到AI8video 。"
        )
        with patch(
                "ai8video.application.conversation_controller.enabled_default_reference_image_options",
                return_value={"autoChangeClothes": False, "autoChangeBackground": True, "autoChangePose": True},
            ), patch(
                "ai8video.application.conversation_controller.default_reference_image_custom_prompt",
                return_value="画面更偏中东商务广告质感",
            ):
            reply = agent.handle_message("s-explicit-ref", message)

        self.assertEqual(reply.stage, "completed")
        self.assertEqual(captured["request"].reference_image, "/tmp/user-ref.png")
        self.assertEqual(captured["request"].reference_image_transform_options, {
            "autoChangeClothes": False,
            "autoChangeBackground": True,
            "autoChangePose": True,
        })
        self.assertEqual(captured["request"].reference_image_custom_prompt, "画面更偏中东商务广告质感")

    def test_rewrite_request_keeps_reference_transform_options(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def rewrite_video(
                self,
                request: ParsedRequest,
                video: VideoPrompt,
                rewrite_instruction: str,
                *,
                progress_session_id: str | None = None,
            ) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[video],
                    first_frame=FirstFrameAsset(source=request.reference_image),
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "none")  # type: ignore[arg-type]
        agent.sessions["s-rewrite"] = ConversationState(session_id="s-rewrite")
        state = agent.sessions["s-rewrite"]
        state.completed_runs = 1
        state.last_result = {
            "videos": [{"index": 1, "title": "第 1 条", "prompt": "原始视频提示词"}],
            "jobs": [],
            "outcomes": [],
            "archives": [],
            "assetRecords": [],
        }
        state.draft.raw_text = "原始视频请求"
        state.draft.reference_image = "/tmp/user-ref.png"
        state.draft.reference_image_enabled = True
        state.draft.reference_image_custom_prompt = "保持高端商务灯光"
        state.draft.reference_image_transform_options = {
            "autoChangeClothes": True,
            "autoChangeBackground": False,
            "autoChangePose": True,
        }

        reply = agent.handle_message("s-rewrite", "重做第1条，老板表情更坚定")

        self.assertEqual(reply.stage, "completed")
        self.assertEqual(captured["request"].reference_image, "/tmp/user-ref.png")
        self.assertEqual(captured["request"].reference_image_transform_options, {
            "autoChangeClothes": True,
            "autoChangeBackground": False,
            "autoChangePose": True,
        })
        self.assertEqual(captured["request"].reference_image_custom_prompt, "保持高端商务灯光")

    def test_conversation_controller_respects_user_no_reference_decision(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[VideoPrompt(index=1, title="第 1 条", prompt=request.raw_text)],
                    first_frame=None,
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "none")  # type: ignore[arg-type]
        message = (
            "生成一条10秒短视频，不用参考图。开场老板抬头看向镜头，说客户资料不能只留在平台，"
            "中段讲私域承接的重要性，结尾提醒团队今天就把线索沉淀到AI8video 。"
        )
        with patch("ai8video.application.conversation_controller.default_reference_image_path", return_value="/tmp/default.png"):
            reply = agent.handle_message("s2", message)

        self.assertEqual(reply.stage, "completed")
        self.assertIsNone(captured["request"].reference_image)

    def test_reference_preprocessor_skips_image_model_without_enabled_options(self) -> None:
        config = AI8VideoConfig(dry_run=False, image_base_url="https://api.example.com", image_api_key="sk-test", image_model="GPT-image2")
        preprocessor = ReferenceImagePreprocessor(config)
        request = ParsedRequest(
            raw_text="生成一条视频",
            mode="single_video",
            reference_image="/tmp/default.png",
            reference_image_transform_options=None,
        )
        with patch("ai8video.generation.reference_image_preprocessor.requests.Session") as session_factory:
            first_frame = preprocessor.prepare_first_frame(request)
        self.assertEqual(first_frame.source, "/tmp/default.png")
        session_factory.assert_not_called()

    def test_reference_preprocessor_calls_image_model_when_effect_enabled(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"data": [{"b64_json": "cG5n"}]}

        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "default.png"
            _write_test_image(source)
            config = AI8VideoConfig(
                dry_run=False,
                image_base_url="https://api.example.com",
                image_api_key="sk-test",
                image_model="GPT-image2",
            )
            preprocessor = ReferenceImagePreprocessor(config)
            request = ParsedRequest(
                raw_text="生成一条老板讲私域的视频",
                mode="single_video",
                reference_image=str(source),
                reference_image_transform_options={
                    "autoChangeClothes": False,
                    "autoChangeBackground": True,
                    "autoChangePose": False,
                },
            )
            output_dir = Path(tempdir) / "i2i"
            session = _FakeRequestsSession(post_response=FakeResponse())
            with patch(
                "ai8video.generation.reference_image_preprocessor.requests.Session",
                return_value=session,
            ) as session_factory, patch(
                "ai8video.generation.reference_image_preprocessor.TRANSFORMED_REFERENCE_DIR",
                output_dir,
            ):
                first_frame = preprocessor.prepare_first_frame(request)

            self.assertTrue(first_frame.source.endswith(".png"))
            self.assertTrue(Path(first_frame.source).exists())
            self.assertEqual(Path(first_frame.source).read_bytes(), b"png")
            session_factory.assert_called_once()
            self.assertFalse(session.trust_env)
            self.assertEqual(session.proxies, {})
            self.assertTrue(session.closed)
            self.assertEqual(len(session.post_calls), 1)
            _, kwargs = session.post_calls[0]
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test")
            self.assertIn("Idempotency-Key", kwargs["headers"])
            self.assertEqual(kwargs["headers"]["Idempotency-Key"], kwargs["headers"]["X-Request-Id"])
            self.assertEqual(kwargs["json"]["model"], "GPT-image2")
            self.assertEqual(kwargs["json"]["response_format"], "url")
            self.assertEqual(kwargs["json"]["size"], "1024x1792")
            self.assertIn("image", kwargs["json"])
            self.assertEqual(len(kwargs["json"]["image"]), 1)
            self.assertTrue(kwargs["json"]["image"][0].startswith("data:image/png;base64,"))
            self.assertTrue(base64.b64decode(kwargs["json"]["image"][0].split(",", 1)[1]).startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertGreaterEqual(kwargs["timeout"], 300)
            self.assertIn("背景必须和原参考图完全不同", kwargs["json"]["prompt"])
            self.assertIn("正对着镜头", kwargs["json"]["prompt"])

    def test_reference_preprocessor_calls_image_model_when_only_custom_prompt_present(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"data": [{"b64_json": "cG5n"}]}

        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "default.png"
            _write_test_image(source)
            config = AI8VideoConfig(
                dry_run=False,
                image_base_url="https://api.example.com",
                image_api_key="sk-test",
                image_model="GPT-image2",
            )
            preprocessor = ReferenceImagePreprocessor(config)
            request = ParsedRequest(
                raw_text="生成一条老板讲私域的视频",
                mode="single_video",
                reference_image=str(source),
                reference_image_custom_prompt="人物穿深色西装，棚拍灯光更高级。",
                reference_image_transform_options=None,
            )
            session = _FakeRequestsSession(post_response=FakeResponse())
            with patch(
                "ai8video.generation.reference_image_preprocessor.requests.Session",
                return_value=session,
            ), patch(
                "ai8video.generation.reference_image_preprocessor.TRANSFORMED_REFERENCE_DIR",
                Path(tempdir) / "i2i",
            ):
                first_frame = preprocessor.prepare_first_frame(request)

            self.assertTrue(Path(first_frame.source).exists())
            self.assertEqual(len(session.post_calls), 1)
            prompt = session.post_calls[0][1]["json"]["prompt"]
            self.assertIn("人物穿深色西装，棚拍灯光更高级。", prompt)

    def test_reference_preprocessor_retries_lost_image_response_with_same_idempotency_key(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"data": [{"b64_json": "cG5n"}]}

        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "default.png"
            _write_test_image(source)
            config = AI8VideoConfig(
                dry_run=False,
                image_base_url="https://api.example.com",
                image_api_key="sk-test",
                image_model="GPT-image2",
            )
            preprocessor = ReferenceImagePreprocessor(config)
            request = ParsedRequest(
                raw_text="生成一条老板讲私域的视频",
                mode="single_video",
                reference_image=str(source),
                reference_image_transform_options={
                    "autoChangeClothes": True,
                    "autoChangeBackground": False,
                    "autoChangePose": False,
                },
            )
            output_dir = Path(tempdir) / "i2i"
            session = _FakeRequestsSession(post_response=[
                requests.ConnectionError("('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"),
                FakeResponse(),
            ])
            with patch(
                "ai8video.generation.reference_image_preprocessor.requests.Session",
                return_value=session,
            ), patch(
                "ai8video.generation.reference_image_preprocessor.TRANSFORMED_REFERENCE_DIR",
                output_dir,
            ), patch(
                "ai8video.generation.reference_image_preprocessor.time.sleep",
            ), patch.dict(os.environ, {"AI8VIDEO_IMAGE_LOST_RESPONSE_RETRIES": "1"}):
                first_frame = preprocessor.prepare_first_frame(request, video=VideoPrompt(index=1, title="第一条", prompt="测试"))

            self.assertTrue(Path(first_frame.source).exists())
            self.assertEqual(len(session.post_calls), 2)
            first_headers = session.post_calls[0][1]["headers"]
            second_headers = session.post_calls[1][1]["headers"]
            self.assertEqual(first_headers["Idempotency-Key"], second_headers["Idempotency-Key"])
            self.assertEqual(first_headers["X-Request-Id"], second_headers["X-Request-Id"])

    def test_reference_preprocessor_retries_transient_image_http_error_with_same_idempotency_key(self) -> None:
        class FakeResponse:
            def __init__(self, status_code: int = 200):
                self.status_code = status_code
                self.headers = {}

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise requests.HTTPError(f"{self.status_code} Server Error")

            def json(self) -> dict:
                return {"data": [{"b64_json": "cG5n"}]}

        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "default.png"
            _write_test_image(source)
            config = AI8VideoConfig(
                dry_run=False,
                image_base_url="https://api.example.com",
                image_api_key="sk-test",
                image_model="GPT-image2",
            )
            preprocessor = ReferenceImagePreprocessor(config)
            request = ParsedRequest(
                raw_text="生成一条老板讲私域的视频",
                mode="single_video",
                reference_image=str(source),
                reference_image_transform_options={
                    "autoChangeClothes": True,
                    "autoChangeBackground": False,
                    "autoChangePose": False,
                },
            )
            output_dir = Path(tempdir) / "i2i"
            session = _FakeRequestsSession(post_response=[FakeResponse(503), FakeResponse(200)])
            with patch(
                "ai8video.generation.reference_image_preprocessor.requests.Session",
                return_value=session,
            ), patch(
                "ai8video.generation.reference_image_preprocessor.TRANSFORMED_REFERENCE_DIR",
                output_dir,
            ), patch(
                "ai8video.generation.reference_image_preprocessor.time.sleep",
            ):
                first_frame = preprocessor.prepare_first_frame(request, video=VideoPrompt(index=1, title="第一条", prompt="测试"))

            self.assertTrue(Path(first_frame.source).exists())
            self.assertEqual(len(session.post_calls), 2)
            first_headers = session.post_calls[0][1]["headers"]
            second_headers = session.post_calls[1][1]["headers"]
            self.assertEqual(first_headers["Idempotency-Key"], second_headers["Idempotency-Key"])
            self.assertEqual(first_headers["X-Request-Id"], second_headers["X-Request-Id"])

    def test_reference_preprocessor_retries_image_rate_limit_with_same_idempotency_key(self) -> None:
        class FakeResponse:
            def __init__(self, status_code: int = 200):
                self.status_code = status_code
                self.headers = {"Retry-After": "1"} if status_code == 429 else {}

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise requests.HTTPError(f"{self.status_code} Client Error")

            def json(self) -> dict:
                return {"data": [{"b64_json": "cG5n"}]}

        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "default.png"
            _write_test_image(source)
            config = AI8VideoConfig(
                dry_run=False,
                image_base_url="https://api.example.com",
                image_api_key="sk-test",
                image_model="GPT-image2",
            )
            preprocessor = ReferenceImagePreprocessor(config)
            request = ParsedRequest(
                raw_text="生成一条老板讲私域的视频",
                mode="single_video",
                reference_image=str(source),
                reference_image_transform_options={
                    "autoChangeClothes": True,
                    "autoChangeBackground": False,
                    "autoChangePose": False,
                },
            )
            session = _FakeRequestsSession(post_response=[FakeResponse(429), FakeResponse(200)])
            with patch(
                "ai8video.generation.reference_image_preprocessor.requests.Session",
                return_value=session,
            ), patch(
                "ai8video.generation.reference_image_preprocessor.TRANSFORMED_REFERENCE_DIR",
                Path(tempdir) / "i2i",
            ), patch(
                "ai8video.generation.reference_image_preprocessor.time.sleep",
            ) as sleep_mock:
                first_frame = preprocessor.prepare_first_frame(request, video=VideoPrompt(index=1, title="第一条", prompt="测试"))

            self.assertTrue(Path(first_frame.source).exists())
            self.assertEqual(len(session.post_calls), 2)
            self.assertEqual(sleep_mock.call_args.args[0], 1.0)
            first_headers = session.post_calls[0][1]["headers"]
            second_headers = session.post_calls[1][1]["headers"]
            self.assertEqual(first_headers["Idempotency-Key"], second_headers["Idempotency-Key"])

    def test_image_generation_max_concurrency_defaults_to_one(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AI8VIDEO_IMAGE_MAX_CONCURRENCY", None)
            self.assertEqual(_image_generation_max_concurrency(), 1)

    def test_image_lost_response_retry_defaults_to_zero_to_avoid_duplicate_charges(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AI8VIDEO_IMAGE_LOST_RESPONSE_RETRIES", None)
            self.assertEqual(_image_lost_response_retries(), 0)

        with patch.dict(os.environ, {"AI8VIDEO_IMAGE_LOST_RESPONSE_RETRIES": "1"}):
            self.assertEqual(_image_lost_response_retries(), 1)

    def test_image_generation_bypasses_fake_dns_without_changing_public_url(self) -> None:
        self.assertTrue(_is_fake_dns_address("198.18.0.7"))
        with patch(
            "ai8video.generation.reference_image_preprocessor.socket.gethostbyname",
            return_value="198.18.0.7",
        ), patch.dict(os.environ, {}, clear=False), patch(
            "ai8video.generation.reference_image_preprocessor._lookup_public_dns_ipv4",
            return_value="203.0.113.10",
        ):
            os.environ.pop("AI8VIDEO_IMAGE_DIRECT_CONNECT_IP", None)
            target = _image_generation_direct_connect_target("https://api.example.com/v1/images/generations")

        self.assertEqual(target, ("api.example.com", "203.0.113.10"))

        with patch(
            "ai8video.generation.reference_image_preprocessor.socket.gethostbyname",
            return_value="198.18.0.7",
        ), patch.dict(os.environ, {"AI8VIDEO_IMAGE_DIRECT_CONNECT_IP_API_EXAMPLE_COM": "127.0.0.1:18443"}):
            target = _image_generation_direct_connect_target("https://api.example.com/v1/images/generations")

        self.assertEqual(target, ("api.example.com", "127.0.0.1:18443"))
        self.assertEqual(
            _image_generation_direct_connect_trace(target),
            {
                "directConnect": True,
                "directConnectHost": "api.example.com",
                "directConnectOrigin": "127.0.0.1",
                "directConnectPort": 18443,
            },
        )

    def test_image_system_proxy_disables_direct_connect(self) -> None:
        with patch.dict(os.environ, {"AI8VIDEO_IMAGE_USE_SYSTEM_PROXY": "1"}), patch(
            "ai8video.generation.reference_image_preprocessor.socket.gethostbyname",
            return_value="198.18.0.7",
        ):
            target = _image_generation_direct_connect_target(
                "https://api.example.com/v1/images/generations",
            )

        self.assertIsNone(target)

        with patch(
            "ai8video.generation.reference_image_preprocessor.socket.gethostbyname",
            return_value="203.0.113.10",
        ):
            self.assertIsNone(_image_generation_direct_connect_target("https://api.example.com/v1/images/generations"))

        with patch.dict(os.environ, {"AI8VIDEO_IMAGE_DIRECT_CONNECT_IP": "127.0.0.1:18443"}):
            self.assertEqual(
                _image_generation_direct_connect_trace(("api.example.com", "127.0.0.1:18443")),
                {
                    "directConnect": True,
                    "directConnectHost": "api.example.com",
                    "directConnectOrigin": "127.0.0.1",
                    "directConnectPort": 18443,
                },
            )

    def test_lookup_public_dns_ipv4_uses_doh_without_system_proxy(self) -> None:
        class FakeDnsResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {
                    "Status": 0,
                    "Answer": [{"name": "api.example.com.", "type": 1, "data": "203.0.113.10"}],
                }

        session = _FakeRequestsSession(get_response=FakeDnsResponse())
        with patch(
            "ai8video.generation.reference_image_preprocessor.requests.Session",
            return_value=session,
        ):
            resolved = _lookup_public_dns_ipv4("api.example.com")

        self.assertEqual(resolved, "203.0.113.10")
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies, {})
        self.assertEqual(session.get_calls[0][0][0], "https://dns.google/resolve?name=api.example.com&type=A")

    def test_seedream_image_generation_size_meets_minimum_pixels(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AI8VIDEO_IMAGE_SIZE", None)
            self.assertEqual(_image_generation_size("doubao-seedream-4-5-251128"), "1440x2560")
            self.assertEqual(_image_generation_size("GPT-image2"), "1024x1792")

    def test_image_generation_size_env_override_wins(self) -> None:
        with patch.dict(os.environ, {"AI8VIDEO_IMAGE_SIZE": "1536x2048"}):
            self.assertEqual(_image_generation_size("doubao-seedream-4-5-251128"), "1536x2048")
        with patch.dict(os.environ, {"AI8VIDEO_IMAGE_MAX_CONCURRENCY": "3"}):
            self.assertEqual(_image_generation_max_concurrency(), 3)
        with patch.dict(os.environ, {"AI8VIDEO_IMAGE_MAX_CONCURRENCY": "0"}):
            self.assertEqual(_image_generation_max_concurrency(), 1)
        with patch.dict(os.environ, {"AI8VIDEO_IMAGE_MAX_CONCURRENCY": "bad"}):
            self.assertEqual(_image_generation_max_concurrency(), 1)

    def test_reference_image_input_converts_rgb_webp_to_jpeg_base64(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "default.webp"
            _write_test_image(source, image_format="WEBP")

            image_input = _reference_image_input(str(source))

        self.assertTrue(image_input.startswith("data:image/jpeg;base64,"))
        self.assertTrue(base64.b64decode(image_input.split(",", 1)[1]).startswith(b"\xff\xd8\xff"))

    def test_reference_image_input_keeps_alpha_formats_as_png_base64(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "transparent.gif"
            image = Image.new("RGBA", (4, 4), (248, 64, 64, 0))
            image.save(source, format="GIF", transparency=0)

            image_input = _reference_image_input(str(source))

        self.assertTrue(image_input.startswith("data:image/png;base64,"))
        self.assertTrue(base64.b64decode(image_input.split(",", 1)[1]).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_reference_preprocessor_uses_video_context_for_i2i_prompt(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"data": [{"b64_json": "cG5n"}]}

        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "default.png"
            _write_test_image(source)
            config = AI8VideoConfig(
                dry_run=False,
                image_base_url="https://api.example.com",
                image_api_key="sk-test",
                image_model="GPT-image2",
            )
            preprocessor = ReferenceImagePreprocessor(config)
            request = ParsedRequest(
                raw_text="生成三条视频",
                mode="batch_videos",
                reference_image=str(source),
                reference_image_transform_options={"autoChangeBackground": True},
            )
            video = VideoPrompt(index=2, title="第二条", prompt="老板在迪拜办公室讲全球客户跟进。")
            session = _FakeRequestsSession(post_response=FakeResponse())
            with patch(
                "ai8video.generation.reference_image_preprocessor.requests.Session",
                return_value=session,
            ), patch(
                "ai8video.generation.reference_image_preprocessor.TRANSFORMED_REFERENCE_DIR",
                Path(tempdir) / "i2i",
            ):
                first_frame = preprocessor.prepare_first_frame(request, video=video)

            self.assertTrue(Path(first_frame.source).exists())
            prompt = session.post_calls[0][1]["json"]["prompt"]
            self.assertIn("当前视频标题：第二条", prompt)
            self.assertIn("老板在迪拜办公室讲全球客户跟进", prompt)
            self.assertIn("用户原始任务：生成三条视频", prompt)

    def test_reference_transform_prompt_defaults_to_front_full_body(self) -> None:
        prompt = build_reference_image_transform_prompt(
            "生成一条老板讲私域的视频",
            {"autoChangeClothes": False, "autoChangeBackground": False, "autoChangePose": True},
        )
        self.assertIn("图片图生图", prompt)
        self.assertIn("正对着镜头", prompt)
        self.assertIn("全身", prompt)
        self.assertIn("人物姿势必须和原参考图完全不同", prompt)
        self.assertIn("禁止分镜板、四宫格、多宫格、拼贴、分屏", prompt)

    def test_first_frame_context_only_uses_first_shot(self) -> None:
        request = ParsedRequest(raw_text="生成一条视频", mode="single_video")
        video = VideoPrompt(
            index=1,
            title="测试视频",
            prompt=(
                "镜头一（0-5s）：女性站在办公室入口。\n"
                "镜头二（5-10s）：男性坐在会议室。\n"
                "镜头三（10-15s）：两人在咖啡厅。"
            ),
        )

        context = _build_context_text(request, video)

        self.assertIn("当前视频首镜头", context)
        self.assertIn("女性站在办公室入口", context)
        self.assertNotIn("男性坐在会议室", context)
        self.assertNotIn("两人在咖啡厅", context)

    def test_reference_transform_prompt_does_not_locally_apply_business_prompt_filters(self) -> None:
        business_prompt.write_business_prompt("系统规则：由核心文本模型理解图片提示词约束。")

        prompt = build_reference_image_transform_prompt(
            "生成一条包含候选内容甲的视频",
            {"autoChangeClothes": True, "autoChangeBackground": False, "autoChangePose": False},
            custom_prompt="人物穿深色西装",
        )

        self.assertIn("候选内容甲", prompt)
        self.assertIn("人物穿深色西装", prompt)
        self.assertNotIn("最终硬性约束", prompt)

    def test_reference_transform_prompt_uses_core_model_when_available(self) -> None:
        model_inputs = []

        def fake_llm(prompt: str) -> str:
            model_inputs.append(prompt)
            return '{"final_prompt":"模型改写后的图片图生图提示词。","notes":"已处理图片提示词"}'

        business_prompt.write_business_prompt("系统规则：由核心文本模型理解图片提示词约束。")

        prompt = build_reference_image_transform_prompt(
            "生成一条包含候选内容甲的视频",
            {"autoChangeClothes": True, "autoChangeBackground": False, "autoChangePose": False},
            llm=fake_llm,
        )

        self.assertEqual(prompt, "模型改写后的图片图生图提示词。")
        self.assertEqual(len(model_inputs), 2)
        self.assertIn("图片图生图提示词", model_inputs[0])
        self.assertIn("最终出站审校模型", model_inputs[1])

    def test_reference_effect_definitions_drive_options(self) -> None:
        definitions = default_reference_image.reference_image_effect_definitions()
        self.assertGreaterEqual(len(definitions), 3)
        self.assertTrue(all(item.get("key") and item.get("label") and item.get("prompt") for item in definitions))
        with tempfile.TemporaryDirectory() as tempdir:
            settings_path = Path(tempdir) / "参考图" / "settings.json"
            with patch.object(default_reference_image, "DEFAULT_REFERENCE_IMAGE_DIR", settings_path.parent), \
                    patch.object(default_reference_image, "DEFAULT_REFERENCE_IMAGE_SETTINGS_PATH", settings_path):
                normalized = default_reference_image.update_default_reference_image_options({
                    definitions[0]["key"]: True,
                    "futureUnknownOption": True,
                })
        self.assertTrue(normalized["options"][definitions[0]["key"]])
        self.assertNotIn("futureUnknownOption", normalized["options"])


if __name__ == "__main__":
    unittest.main()
