from __future__ import annotations

import tempfile
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from ai8video.integrations import http_client as ai8video_http_client
from ai8video.core.config import AI8VideoConfig
from ai8video.integrations.direct_video_model_client import (
    DOUBAO_CREATE_TIMEOUT_SECONDS,
    DirectVideoModelError,
    AI8VideoModelClient,
    _build_create_payload,
    _create_timeout_seconds,
    _format_create_timeout_error,
    _raise_for_response,
)
from ai8video.integrations.llm_provider import build_openai_compat_splitter
from ai8video.core.models import FirstFrameAsset, QuickVideoJob
from ai8video.generation.pipeline import _current_video_settings_trace, _is_generated_job
from ai8video.integrations.video_model_settings import (
    VideoModelSettings,
    get_video_resolution_options,
    normalize_video_model_settings,
    pull_model_catalog,
)


class AI8VideoVideoModelSettingsTest(unittest.TestCase):
    def test_api_request_disables_system_proxy_by_default(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.trust_env = True
                self.proxies = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
                self.closed = False
                self.calls: list[tuple[str, str, dict]] = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self.closed = True
                return False

            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                response = requests.Response()
                response.status_code = 200
                response._content = b'{"ok":true}'
                return response

            def mount(self, _prefix, _adapter):
                return None

        fake_session = FakeSession()
        with patch.dict(os.environ, {"AI8VIDEO_API_USE_SYSTEM_PROXY": ""}), patch(
            "ai8video.integrations.http_client.requests.Session",
            return_value=fake_session,
        ):
            response = ai8video_http_client.api_request("GET", "https://api.example.com/api/status", timeout=5)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(fake_session.trust_env)
        self.assertEqual(fake_session.proxies, {})
        self.assertTrue(fake_session.closed)
        self.assertEqual(fake_session.calls[0][0], "GET")

    def test_llm_provider_uses_api_request_helper(self) -> None:
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"choices":[{"message":{"content":"[]"}}]}'
        captured: dict[str, object] = {}

        def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["kwargs"] = kwargs
            return response

        config = AI8VideoConfig(
            llm_base_url="https://api.example.com",
            llm_api_key="sk-test",
            llm_model="deepseek-test",
        )
        llm = build_openai_compat_splitter(config)

        with patch("ai8video.integrations.llm_provider.api_request", side_effect=fake_request):
            result = llm("只输出空数组")

        self.assertEqual(result, "[]")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "https://api.example.com/v1/chat/completions")
        kwargs = captured["kwargs"]
        self.assertEqual(kwargs["json"]["model"], "deepseek-test")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test")

    def test_llm_provider_can_disable_streaming(self) -> None:
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"choices":[{"message":{"content":"{}"}}]}'
        config = AI8VideoConfig(llm_base_url="https://api.example.com", llm_api_key="sk-test", llm_model="deepseek-test")
        llm = build_openai_compat_splitter(config, stream=False, transport_retry_count=1)

        with patch("ai8video.integrations.llm_provider.api_request", return_value=response) as request:
            self.assertEqual(llm("只输出对象"), "{}")

        self.assertFalse(request.call_args.kwargs["stream"])
        self.assertFalse(request.call_args.kwargs["json"]["stream"])

    def test_llm_provider_retries_transport_interruption_once(self) -> None:
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"choices":[{"message":{"content":"{}"}}]}'
        config = AI8VideoConfig(llm_base_url="https://api.example.com", llm_api_key="sk-test", llm_model="deepseek-test")
        llm = build_openai_compat_splitter(config, stream=False, transport_retry_count=1)

        with patch(
            "ai8video.integrations.llm_provider.api_request",
            side_effect=[requests.ConnectionError("Response ended prematurely"), response],
        ) as request:
            self.assertEqual(llm("只输出对象"), "{}")

        self.assertEqual(request.call_count, 2)

    def test_llm_provider_does_not_retry_read_timeout(self) -> None:
        config = AI8VideoConfig(llm_base_url="https://api.example.com", llm_api_key="sk-test", llm_model="deepseek-test")
        llm = build_openai_compat_splitter(config, stream=False, transport_retry_count=1)

        with patch(
            "ai8video.integrations.llm_provider.api_request",
            side_effect=requests.ReadTimeout("read timeout=20"),
        ) as request:
            with self.assertRaisesRegex(RuntimeError, "共 1 次请求"):
                llm("只输出对象")

        self.assertEqual(request.call_count, 1)

    def test_pull_model_catalog_accepts_short_seedream_image_keys(self) -> None:
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"models":{"seedream":"Seedream Image","nano-banana-2":"Nano Banana","deepseek-v4-flash":"DeepSeek"}}'

        with patch("ai8video.integrations.video_model_settings.requests.get", return_value=response):
            result = pull_model_catalog(
                base_url="https://api.example.com",
                api_key="sk-test",
                allowed_types={"image"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual([item["modelId"] for item in result["models"]], ["seedream", "nano-banana-2"])

    def test_normalize_ai_manju_style_video_params(self) -> None:
        settings = normalize_video_model_settings({
            "seconds": 12,
            "videoCount": 3,
            "ratio": "16:9",
            "generateAudio": True,
            "serviceTier": "flex",
            "executionExpiresAfter": 86400,
            "draft": True,
            "cameraFixed": True,
            "seed": "12345",
            "promptExtend": False,
            "shotType": "single",
            "audio": True,
            "audioUrl": "https://example.invalid/audio.mp3",
            "resolution": "720p",
        })

        self.assertEqual(settings.seconds, 12)
        self.assertEqual(settings.video_count, 3)
        self.assertEqual(settings.ratio, "16:9")
        self.assertTrue(settings.generate_audio)
        self.assertEqual(settings.service_tier, "flex")
        self.assertEqual(settings.execution_expires_after, 86400)
        self.assertTrue(settings.draft)
        self.assertTrue(settings.camera_fixed)
        self.assertEqual(settings.seed, 12345)
        self.assertFalse(settings.prompt_extend)
        self.assertEqual(settings.shot_type, "single")
        self.assertTrue(settings.audio)
        self.assertEqual(settings.audio_url, "https://example.invalid/audio.mp3")
        self.assertFalse(settings.watermark)
        self.assertEqual(settings.resolution, "720p")

    def test_default_resolution_is_480_but_user_choice_is_allowed(self) -> None:
        default_settings = normalize_video_model_settings({})
        selected_settings = normalize_video_model_settings({"resolution": "1080p"})

        self.assertEqual(default_settings.resolution, "480p")
        self.assertEqual(selected_settings.resolution, "1080p")
        self.assertEqual(get_video_resolution_options("doubao-seedance"), ("480p", "720p", "1080p"))

    def test_size_resolution_mode_preserves_explicit_size(self) -> None:
        settings = normalize_video_model_settings({
            "resolutionMode": "size",
            "resolution": "720x1280",
            "ratio": "9:16",
        })

        self.assertEqual(settings.resolution_mode, "size")
        self.assertEqual(settings.resolution, "720x1280")

    def test_size_resolution_mode_defaults_by_ratio(self) -> None:
        settings = normalize_video_model_settings({
            "resolution_mode": "size",
            "resolution": "480p",
            "ratio": "16:9",
        })

        self.assertEqual(settings.resolution_mode, "size")
        self.assertEqual(settings.resolution, "720x480")

    def test_bailian_resolution_follows_model_options(self) -> None:
        flash_settings = normalize_video_model_settings({
            "template": "bailian-wan",
            "model": "wan2.6-i2v-flash",
            "resolution": "480P",
        })
        preview_settings = normalize_video_model_settings({
            "template": "bailian-wan",
            "model": "wan2.5-i2v-preview",
            "resolution": "480p",
        })

        self.assertEqual(flash_settings.resolution, "720P")
        self.assertEqual(preview_settings.resolution, "480P")

    def test_doubao_payload_includes_video_params(self) -> None:
        payload = _build_create_payload(
            template="doubao-seedance",
            model="doubao-seedance-1-5-pro-251215",
            prompt="测试视频",
            image=None,
            seconds=10,
            ratio="9:16",
            resolution="720p",
            preset="custom",
            enhance_prompt=True,
            return_last_frame=True,
            watermark=False,
            generate_audio=True,
            service_tier="flex",
            execution_expires_after=86400,
            draft=False,
            camera_fixed=True,
            seed=7,
            prompt_extend=True,
            shot_type="multi",
            audio=False,
            audio_url="",
            video_count=2,
        )

        self.assertEqual(payload["generate_audio"], True)
        self.assertEqual(payload["service_tier"], "flex")
        self.assertEqual(payload["execution_expires_after"], 86400)
        self.assertEqual(payload["return_last_frame"], True)
        self.assertEqual(payload["camera_fixed"], True)
        self.assertEqual(payload["seed"], 7)
        self.assertEqual(payload["size"], "720x1280")
        self.assertEqual(payload["metadata"]["resolution"], "720p")
        self.assertEqual(payload["metadata"]["video_count"], 2)

    def test_doubao_payload_can_submit_size_mode_without_ratio_mapping(self) -> None:
        payload = _build_create_payload(
            template="doubao-seedance",
            model="doubao-seedance-1-5-pro-251215",
            prompt="测试视频",
            image=None,
            seconds=10,
            ratio="9:16",
            resolution="480x720",
            preset="custom",
            enhance_prompt=True,
            return_last_frame=True,
            watermark=False,
            generate_audio=False,
            service_tier="default",
            execution_expires_after=86400,
            draft=False,
            camera_fixed=False,
            seed=None,
            prompt_extend=True,
            shot_type="multi",
            audio=False,
            audio_url="",
            video_count=1,
            resolution_mode="size",
        )

        self.assertEqual(payload["size"], "480x720")
        self.assertEqual(payload["metadata"]["resolution"], "480x720")
        self.assertEqual(payload["metadata"]["resolution_mode"], "size")

    def test_openai_compatible_payload_uses_single_reference_image_field(self) -> None:
        image = "data:image/png;base64,AAA"

        payload = _build_create_payload(
            template="openai-compatible",
            model="Grok-Video-GN",
            prompt="测试视频",
            image=image,
            seconds=10,
            ratio="9:16",
            resolution="480p",
            preset="custom",
            enhance_prompt=True,
            return_last_frame=False,
            watermark=False,
            generate_audio=True,
            service_tier="default",
            execution_expires_after=1,
            draft=False,
            camera_fixed=False,
            seed=None,
            prompt_extend=True,
            shot_type="multi",
            audio=False,
            audio_url="",
            video_count=1,
        )

        self.assertEqual(payload["image"], image)
        self.assertNotIn("input_reference", payload)
        self.assertNotIn("images", payload)

    def test_direct_client_submits_transformed_local_first_frame_to_openai_compatible_payload(self) -> None:
        settings = VideoModelSettings(
            base_url="https://api.example.com",
            api_key="sk-test",
            model="Grok-Video-GN",
            template="openai-compatible",
            ratio="9:16",
            resolution="720x1080",
            resolution_mode="size",
            generate_audio=True,
            service_tier="flex",
            execution_expires_after=86400,
        )
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"id":"task-1","status":"queued"}'
        captured: dict[str, object] = {}

        def fake_request(method, url, *, headers=None, json=None, timeout=None):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return response

        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "reference-i2i.png"
            image_path.write_bytes(b"png")
            client = AI8VideoModelClient(
                config=AI8VideoConfig(dry_run=False),
                settings=settings,
            )
            client.guard = SimpleNamespace(
                forced_duration_seconds=0,
                assert_can_create=lambda: None,
                record_job=lambda **_kwargs: None,
            )

            with patch(
                "ai8video.integrations.direct_video_model_client.load_video_model_settings",
                return_value=settings,
            ), patch("ai8video.integrations.direct_video_model_client.api_request", side_effect=fake_request):
                job = client.create_job(
                    text="测试视频",
                    episode_index=1,
                    first_frame=FirstFrameAsset(source=str(image_path)),
                    duration_seconds=10,
                )

        payload = captured["json"]
        self.assertIsInstance(payload, dict)
        image = payload["image"]
        self.assertTrue(str(image).startswith("data:image/png;base64,"))
        self.assertNotIn("input_reference", payload)
        self.assertNotIn("images", payload)
        self.assertEqual(payload["size"], "720x1080")
        self.assertEqual(job.job_id, "task-1")

    def test_video_model_http_error_includes_upstream_body(self) -> None:
        response = requests.Response()
        response.status_code = 400
        response.reason = "Bad Request"
        response.url = "https://api.example.com/v1/videos?model=doubao"
        response._content = b'{"error":{"message":"duration must be 5 or 10 seconds"}}'

        with self.assertRaisesRegex(DirectVideoModelError, "duration must be 5 or 10 seconds"):
            _raise_for_response(response, "创建视频任务")

    def test_direct_client_refreshes_video_model_settings_before_create(self) -> None:
        stale_settings = VideoModelSettings(
            model="doubao-seedance-1-5-pro-251215",
            template="doubao-seedance",
            api_key="sk-old",
        )
        latest_settings = VideoModelSettings(
            model="Grok-Video-GN",
            template="openai-compatible",
            api_key="sk-new",
        )
        client = AI8VideoModelClient(
            config=AI8VideoConfig(dry_run=True),
            settings=stale_settings,
        )

        with patch(
            "ai8video.integrations.direct_video_model_client.load_video_model_settings",
            return_value=latest_settings,
        ):
            job = client.create_job(text="测试视频", episode_index=1)

        self.assertEqual(job.usage["settings"]["model"], "Grok-Video-GN")
        self.assertEqual(job.usage["settings"]["template"], "openai-compatible")

    def test_doubao_create_requests_are_serialized_for_batch_submit(self) -> None:
        settings = VideoModelSettings(
            base_url="https://api.example.com",
            api_key="sk-test",
            model="doubao-seedance-1-5-pro-251215",
            template="doubao-seedance",
        )
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"id":"task-1","status":"queued"}'
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_request(method, url, *, headers=None, json=None, timeout=None):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return response

        client = AI8VideoModelClient(config=AI8VideoConfig(dry_run=False), settings=settings)
        client.guard = SimpleNamespace(
            forced_duration_seconds=0,
            assert_can_create=lambda: None,
            record_job=lambda **_kwargs: None,
        )

        with patch(
            "ai8video.integrations.direct_video_model_client.load_video_model_settings",
            return_value=settings,
        ), patch("ai8video.integrations.direct_video_model_client.api_request", side_effect=fake_request):
            with ThreadPoolExecutor(max_workers=3) as executor:
                list(executor.map(lambda index: client.create_job(text=f"测试 {index}", episode_index=index), range(1, 4)))

        self.assertEqual(max_active, 1)

    def test_doubao_create_timeout_is_longer_than_default_request_timeout(self) -> None:
        self.assertEqual(_create_timeout_seconds("doubao-seedance", 180), DOUBAO_CREATE_TIMEOUT_SECONDS)
        self.assertEqual(_create_timeout_seconds("openai-compatible", 180), 180)

    def test_doubao_create_timeout_warns_about_possible_orphan_upstream_job(self) -> None:
        settings = VideoModelSettings(
            base_url="https://api.example.com",
            api_key="sk-test",
            model="doubao-seedance-1-5-pro-251215",
            template="doubao-seedance",
        )
        client = AI8VideoModelClient(config=AI8VideoConfig(dry_run=False, timeout_seconds=180), settings=settings)
        client.guard = SimpleNamespace(
            forced_duration_seconds=0,
            assert_can_create=lambda: None,
            record_job=lambda **_kwargs: None,
        )

        with patch(
            "ai8video.integrations.direct_video_model_client.load_video_model_settings",
            return_value=settings,
        ), patch(
            "ai8video.integrations.direct_video_model_client.api_request",
            side_effect=requests.ReadTimeout("read timeout=420"),
        ):
            with self.assertRaisesRegex(DirectVideoModelError, "后台生成"):
                client.create_job(text="测试视频", episode_index=1)

    def test_openai_compatible_create_timeout_warns_about_possible_orphan_upstream_job(self) -> None:
        message = _format_create_timeout_error(
            "openai-compatible",
            "https://api.example.com/v1/videos",
            requests.ReadTimeout("read timeout=180"),
        )

        self.assertIn("上游可能已经接收请求", message)
        self.assertIn("后台生成", message)
        self.assertIn("不要立刻重复提交", message)
        self.assertNotIn("豆包上游", message)

    def test_video_settings_trace_keeps_openai_template_and_veo_model_separate(self) -> None:
        settings = VideoModelSettings(
            base_url="https://api.example.com",
            api_key="sk-test",
            model="veo_3_1-fast",
            template="openai-compatible",
            provider="openai-compatible",
        )
        client = AI8VideoModelClient(config=AI8VideoConfig(dry_run=False), settings=settings)

        with patch(
            "ai8video.integrations.direct_video_model_client.load_video_model_settings",
            return_value=settings,
        ):
            trace = _current_video_settings_trace(client)

        self.assertEqual(trace["template"], "openai-compatible")
        self.assertEqual(trace["model"], "veo_3_1-fast")
        self.assertEqual(trace["provider"], "openai-compatible")
        self.assertNotIn("api_key", trace)

    def test_failed_provider_status_does_not_treat_error_text_as_video_url(self) -> None:
        response = requests.Response()
        response.status_code = 200
        response._content = (
            b'{"id":"task-1","status":"failed","metadata":{"url":"'
            b'task failed: invalid media type or media url"},"error":{"message":"invalid media"}}'
        )
        settings = VideoModelSettings(
            base_url="https://api.example.com",
            api_key="sk-test",
            model="Grok-Video-GN",
            template="openai-compatible",
        )
        client = AI8VideoModelClient(
            config=AI8VideoConfig(dry_run=False),
            settings=settings,
        )

        def fake_request(method, url, *, headers=None, timeout=None):
            self.assertEqual(method, "GET")
            self.assertIn("/v1/videos/task-1", url)
            return response

        with patch("ai8video.integrations.direct_video_model_client.api_request", side_effect=fake_request):
            job = client.get_job("task-1", episode_index=1, prompt="测试视频")

        self.assertEqual(job.status, "failed")
        self.assertIsNone(job.video_url)
        self.assertEqual(job.provider_status, "failed")

    def test_poll_job_retries_transient_request_errors(self) -> None:
        client = AI8VideoModelClient(
            config=AI8VideoConfig(dry_run=False, poll_interval_seconds=0, max_poll_attempts=3),
        )
        submitted = QuickVideoJob(
            episode_index=1,
            job_id="task-1",
            status="pending",
            prompt="测试视频",
        )
        completed = QuickVideoJob(
            episode_index=1,
            job_id="task-1",
            status="succeeded",
            prompt="测试视频",
            video_url="https://example.invalid/task-1.mp4",
        )

        with patch.object(
            client,
            "get_job",
            side_effect=[requests.exceptions.SSLError("ssl eof"), completed],
        ) as mock_get_job, patch("ai8video.integrations.direct_video_model_client.time.sleep"):
            job = client.poll_job(submitted)

        self.assertEqual(mock_get_job.call_count, 2)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.video_url, "https://example.invalid/task-1.mp4")

    def test_poll_job_preserves_segment_metadata_on_latest_status(self) -> None:
        client = AI8VideoModelClient(
            config=AI8VideoConfig(dry_run=False, poll_interval_seconds=0, max_poll_attempts=1),
        )
        submitted = QuickVideoJob(
            episode_index=1,
            job_id="task-segment-2",
            status="pending",
            prompt="测试视频",
            segment_index=2,
            segment_label="片段 2",
        )
        latest = QuickVideoJob(
            episode_index=1,
            job_id="task-segment-2",
            status="succeeded",
            prompt="测试视频",
            video_url="https://example.invalid/task-segment-2.mp4",
        )

        with patch.object(client, "get_job", return_value=latest), patch("ai8video.integrations.direct_video_model_client.time.sleep"):
            job = client.poll_job(submitted)

        self.assertEqual(job.segment_index, 2)
        self.assertEqual(job.segment_label, "片段 2")

    def test_synthetic_storage_key_alone_is_not_generated_success(self) -> None:
        job = QuickVideoJob(
            episode_index=1,
            job_id="task-1",
            status="succeeded",
            storage_key="direct-video-model/task-1.mp4",
            video_url=None,
        )

        self.assertFalse(_is_generated_job(job))


if __name__ == "__main__":
    unittest.main()
