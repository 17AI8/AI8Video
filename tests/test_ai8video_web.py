from __future__ import annotations

import os
import tempfile
import threading
import unittest
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai8video.interfaces.web import app as ai8video_web
from ai8video.interfaces.web.routes import hot_topics as hot_topic_routes
from ai8video.generation import generation_progress
from ai8video.radar import hot_topic
from ai8video.radar import hot_topic_feeds
from ai8video.application import runtime as ai8video_runtime
from ai8video.assets import user_materials as ai8video_user_materials
from ai8video.assets.asset_store import JsonlAssetStore
from ai8video.core.models import VideoPrompt
from ai8video.interfaces.web.static_bundle import read_workbench_script, workbench_script_paths


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "ai8video" / "interfaces" / "web" / "static"


def read_static_source() -> str:
    paths = [
        STATIC_ROOT / "index.html",
        STATIC_ROOT / "workbench.css",
        *sorted((STATIC_ROOT / "styles").glob("*.css")),
        *workbench_script_paths(STATIC_ROOT),
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


class AI8VideoShortVideoWebTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.asset_env_backup = os.environ.get("AI8VIDEO_ASSET_STORE_PATH")
        self.env_backup = os.environ.get("AI8VIDEO_BATCH_REPORT_DIR")
        self.alert_env_backup = os.environ.get("AI8VIDEO_BATCH_ALERT_DIR")
        self.state_env_backup = os.environ.get("AI8VIDEO_BATCH_SUPERVISOR_STATE_PATH")
        self.admin_state_env_backup = os.environ.get("AI8VIDEO_BATCH_SUPERVISOR_ADMIN_STATE_PATH")
        self.lock_env_backup = os.environ.get("AI8VIDEO_BATCH_SUPERVISOR_LOCK_PATH")
        self.deployment_env_backup = os.environ.get("AI8VIDEO_BATCH_SUPERVISOR_LAUNCHD_PLIST_PATH")
        self.seed_file_env_backup = os.environ.get("AI8VIDEO_BATCH_SEED_FILE")
        self.background_music_env_backup = os.environ.get("AI8VIDEO_BACKGROUND_MUSIC_DIR")
        self.video_env_backup = {
            "AI8VIDEO_VIDEO_BASE_URL": os.environ.get("AI8VIDEO_VIDEO_BASE_URL"),
            "AI8VIDEO_VIDEO_API_KEY": os.environ.get("AI8VIDEO_VIDEO_API_KEY"),
            "AI8VIDEO_VIDEO_MODEL": os.environ.get("AI8VIDEO_VIDEO_MODEL"),
            "AI8VIDEO_VIDEO_TEMPLATE": os.environ.get("AI8VIDEO_VIDEO_TEMPLATE"),
        }
        self.llm_env_backup = {
            "AI8VIDEO_LLM_BASE_URL": os.environ.get("AI8VIDEO_LLM_BASE_URL"),
            "AI8VIDEO_LLM_API_KEY": os.environ.get("AI8VIDEO_LLM_API_KEY"),
            "AI8VIDEO_LLM_MODEL": os.environ.get("AI8VIDEO_LLM_MODEL"),
        }
        os.environ["AI8VIDEO_ASSET_STORE_PATH"] = str(self.root / "assets.jsonl")
        os.environ["AI8VIDEO_BATCH_REPORT_DIR"] = str(self.root / "batch_reports")
        os.environ["AI8VIDEO_BATCH_ALERT_DIR"] = str(self.root / "batch_alerts")
        os.environ["AI8VIDEO_BATCH_SUPERVISOR_STATE_PATH"] = str(self.root / "batch_supervisor_state.json")
        os.environ["AI8VIDEO_BATCH_SUPERVISOR_ADMIN_STATE_PATH"] = str(self.root / "batch_supervisor_admin_state.json")
        os.environ["AI8VIDEO_BATCH_SUPERVISOR_LOCK_PATH"] = str(self.root / "batch_supervisor.lock")
        os.environ["AI8VIDEO_BATCH_SUPERVISOR_LAUNCHD_PLIST_PATH"] = str(
            self.root / "com.ai8.video.supervisor.plist"
        )
        os.environ["AI8VIDEO_BATCH_SEED_FILE"] = str(self.root / "batch_supervisor" / "seed_messages.txt")
        os.environ["AI8VIDEO_BACKGROUND_MUSIC_DIR"] = str(self.root / "background_music")
        os.environ["AI8VIDEO_VIDEO_BASE_URL"] = "https://api.example.com"
        os.environ["AI8VIDEO_VIDEO_API_KEY"] = "sk-test-video"
        os.environ["AI8VIDEO_VIDEO_MODEL"] = "doubao-seedance-test"
        os.environ["AI8VIDEO_VIDEO_TEMPLATE"] = "doubao-seedance"
        os.environ["AI8VIDEO_LLM_BASE_URL"] = "https://api.example.com/v1"
        os.environ["AI8VIDEO_LLM_API_KEY"] = "sk-test-llm"
        os.environ["AI8VIDEO_LLM_MODEL"] = "test-model"
        ai8video_runtime.get_runtime(refresh=True)

    def test_cors_only_allows_same_loopback_workbench_origin(self) -> None:
        allowed_origin = ai8video_web._allowed_cors_origin

        self.assertEqual(
            allowed_origin("http://127.0.0.1:18720", "127.0.0.1:18720", "/api/chat"),
            "http://127.0.0.1:18720",
        )
        self.assertEqual(
            allowed_origin("http://localhost:18720", "localhost:18720", "/api/health"),
            "http://localhost:18720",
        )
        self.assertIsNone(
            allowed_origin("null", "127.0.0.1:18720", "/api/chat")
        )
        self.assertIsNone(
            allowed_origin("https://attacker.example", "127.0.0.1:18720", "/api/chat")
        )
        self.assertIsNone(
            allowed_origin("http://127.0.0.1:3000", "127.0.0.1:18720", "/api/chat")
        )

    def test_workbench_script_bundle_preserves_fragment_order(self) -> None:
        paths = workbench_script_paths(STATIC_ROOT)

        self.assertGreater(len(paths), 1)
        expected = "".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertEqual(read_workbench_script(STATIC_ROOT), expected)

    def test_continuation_timeline_is_repaired_to_new_video_duration(self) -> None:
        llm = Mock(return_value="【0-5秒，近景】继续动作\n【5-10秒，远景】完成动作")

        result = ai8video_web._repair_continuation_timeline(
            llm,
            "【10-15秒，近景】继续动作\n【15-20秒，远景】完成动作",
            10,
        )

        self.assertIn("【0-5秒", result)
        self.assertIn("【5-10秒", result)
        llm.assert_called_once()

    def test_origin_guard_rejects_untrusted_browser_writes_only(self) -> None:
        should_reject = ai8video_web._should_reject_untrusted_browser_write

        self.assertFalse(
            should_reject("POST", "http://127.0.0.1:18720", "127.0.0.1:18720", "/api/chat")
        )
        self.assertFalse(
            should_reject("POST", None, "127.0.0.1:18720", "/api/chat")
        )
        self.assertTrue(
            should_reject("POST", "https://attacker.example", "127.0.0.1:18720", "/api/chat")
        )
        self.assertTrue(
            should_reject("POST", "null", "127.0.0.1:18720", "/api/open-user-material-folder")
        )
        self.assertFalse(
            should_reject("GET", "https://attacker.example", "127.0.0.1:18720", "/api/health")
        )


    def test_static_progress_modal_uses_generation_progress_on_terminal_payloads(self) -> None:
        source = read_static_source()

        self.assertIn("function extractGenerationBatchId(payload)", source)
        self.assertIn("function mergePendingGenerationBatchId(previousPayload, nextPayload)", source)
        self.assertIn("const generationBatchId = extractGenerationBatchId(pendingPayload);", source)
        self.assertIn("params.set('generationBatchId', generationBatchId);", source)
        self.assertIn(
            "payload.meta?.operation === 'pending' || hasAgentProgress",
            source,
        )
        self.assertIn(
            "last.payload?.draft && !last.payload?.awaiting && ['completed', 'error'].includes",
            source,
        )
        self.assertIn(
            "['pending', 'planning'].includes(String(payload?.meta?.operation || '').trim())",
            source,
        )
        self.assertIn("readOnlyRecovery: !!data?.readOnlyRecovery", source)
        self.assertIn("willResumeGeneration: data?.willResumeGeneration !== false", source)
        self.assertIn(
            "['completed', 'completed_with_error', 'failed', 'idle', 'cancelled', 'canceled', 'recovered'].includes",
            source,
        )
        self.assertIn("if (progress.readOnlyRecovery) return false;", source)
        self.assertIn("if (progress.readOnlyRecovery) return '历史进度已恢复';", source)
        self.assertIn("服务重启前的任务进度已从账本恢复，仅供查看，不会自动继续生成。", source)
        self.assertIn(
            "if (pending.readOnlyRecovery || pending.generationProgress?.readOnlyRecovery) return false;",
            source,
        )
        self.assertIn("没有提交给上游生成服务", source)
        self.assertIn("本地超时未提交上游", source)
        self.assertIn('data-local-tts-preview', source)
        self.assertIn('今天天气真好，你下载AI8video 了吗', source)
        self.assertIn('localTtsPreviewSignature', source)
        self.assertIn('data-local-tts-volume-label', source)
        self.assertIn('name="localTtsVolume" type="range"', source)
        self.assertIn('name="localTtsApiKey"', source)
        self.assertIn('name="localTtsApiBaseUrl"', source)
        self.assertIn('name="localTtsCloneModel"', source)
        self.assertIn('data-add-local-tts-voice-clone', source)
        self.assertIn('data-open-local-tts-voice-clone-folder', source)
        self.assertIn("localTtsVoiceCloneUploadInput", source)
        self.assertNotIn('AI8VIDEO_LOCAL_TTS_ENGINE', source)
        self.assertNotIn('AI8VIDEO_LOCAL_TTS_RATE', source)
        self.assertNotIn('AI8VIDEO_LOCAL_TTS_ORIGINAL_AUDIO_VOLUME', source)
        self.assertNotIn('AI8VIDEO_LOCAL_TTS_STYLE_PROMPT', source)
        self.assertNotIn('AI8VIDEO_LOCAL_TTS_AUDIO_TAG', source)
        self.assertNotIn('name="localTtsRate"', source)
        self.assertNotIn('name="localTtsOriginalAudioVolume"', source)
        self.assertNotIn('name="localTtsStylePrompt"', source)
        self.assertNotIn('name="localTtsAudioTag"', source)
        self.assertNotIn('系统内置兜底', source)
        self.assertNotIn('sherpa-onnx Melo 中英（旧本地）', source)
        self.assertIn('max="400"', source)
        self.assertIn("const volumeInput = els.settingsModalBody?.querySelector('[name=\"localTtsVolume\"]');", source)
        self.assertIn("const apiKeyInput = els.settingsModalBody?.querySelector('[name=\"localTtsApiKey\"]');", source)
        self.assertIn("normalizeLocalTtsVolumePercent(volumeInput.value) / 100", source)
        self.assertIn('name="manualVideoModel"', source)
        self.assertIn("saveVideoModelSelection(value, '模型已保存')", source)
        self.assertNotIn('?t=${Date.now()}', source)
        read_only_recovery_index = source.index("if (progress.readOnlyRecovery) return false;")
        running_index = source.index("if (running > 0 || waiting > 0) return true;")
        terminal_index = source.index("if (isTerminalTaskStatus(progress.status)) return false;", running_index)
        self.assertLess(read_only_recovery_index, running_index)
        self.assertLess(running_index, terminal_index)

    def test_recycle_bin_modal_supports_batch_delete_left_of_open_folder(self) -> None:
        source = read_static_source()

        select_all_button_index = source.index('id="recycleBinSelectAllButton"')
        delete_button_index = source.index('id="recycleBinBatchDeleteButton"')
        open_folder_index = source.index('id="recycleBinOpenFolderButton"')
        self.assertLess(select_all_button_index, delete_button_index)
        self.assertLess(delete_button_index, open_folder_index)
        self.assertIn("function toggleAllRecycleBinTasks()", source)
        self.assertIn("allSelected ? '取消全选' : '一键全选'", source)
        self.assertIn('data-select-recycle-bin-folder="${escapeHtml(folder)}"', source)
        self.assertIn("async function deleteSelectedRecycleBinTasks()", source)
        self.assertIn("fetch('/api/user-recycle-bin/delete'", source)
        self.assertIn("确认永久删除选中的", source)
        self.assertIn("批量删除接口未加载，请重启AI8video 服务并刷新页面后重试。", source)

    def test_hot_radar_uses_compact_native_workbench_layout(self) -> None:
        source = read_static_source()
        modal_source = source[source.index('id="hotRadarModal"'):source.index('id="progressModal"')]

        self.assertIn("热点雷达采用AI8video 原生工作台布局", source)
        self.assertIn("热点雷达复用AI8video 蓝紫玻璃设计系统", source)
        self.assertIn("--hot-radar-brand: #4f6dff", source)
        self.assertIn("backdrop-filter: blur(28px) saturate(1.18)", source)
        self.assertIn("@media (max-width: 820px)", source)
        self.assertIn("#hotRadarModal .hot-radar-detail-panel {\n        position: static;\n        order: -1;", source)
        self.assertIn("#hotRadarModal #hotRadarTopicList .hot-radar-topic-meta", source)
        self.assertIn('class="hot-radar-topic-meta-item"', source)
        self.assertIn("flex-wrap: wrap", source)
        self.assertIn("#hotRadarModal #hotRadarTopicList .hot-radar-topic-card > *", source)
        self.assertIn("min-inline-size: 0", source)
        self.assertIn("overflow-wrap: anywhere", source)
        self.assertIn("统一热点雷达实际控件的蓝紫玻璃状态", source)
        self.assertIn('id="hotRadarSourceSelect"', source)
        self.assertIn('class="hot-radar-filter-toolbar"', source)
        self.assertIn("grid-template-columns: minmax(230px, 0.72fr) minmax(260px, 1.28fr) auto auto", source)
        self.assertIn('id="hotRadarColumnToggleButton"', source)
        self.assertIn("HOT_RADAR_COLUMN_COUNT_STORAGE_KEY", source)
        self.assertIn("HOT_RADAR_VIEW_STATE_STORAGE_KEY", source)
        self.assertIn("function loadHotRadarColumnCount()", source)
        self.assertIn("function loadHotRadarViewState()", source)
        self.assertIn("function persistHotRadarViewState(hotRadar)", source)
        self.assertNotIn("前端现在只展示本轮已提交的原始需求和占位状态", source)
        self.assertIn('id="progressModalCancelSlot"', source)
        self.assertIn("els.progressModalCancelSlot.innerHTML", source)
        self.assertIn("function resultNotifyRatioClass(item = {})", source)
        self.assertIn(".result-notify-card.ratio-portrait .result-notify-preview", source)
        self.assertIn(".result-notify-card.ratio-landscape .result-notify-preview", source)
        self.assertIn("data-retry-generation-video", source)
        self.assertIn("async function retryFailedGenerationVideo(button)", source)
        self.assertIn('data-video-preview-action="extend-video"', source)
        self.assertIn(".video-preview-extend-actions", source)
        self.assertIn('data-video-preview-action="delete-extension"', source)
        self.assertIn("async function deleteVideoPreviewExtensionState(userGeneratedKey, button)", source)
        self.assertIn("function setVideoPreviewMainControlsDisabled(disabled)", source)
        self.assertIn("data-extension-disabled-before", source)
        self.assertIn('data-video-preview-action="edit-video-prompt"', source)
        self.assertIn("async function openVideoPromptEditor(userGeneratedKey)", source)
        self.assertIn("async function generateVideoPreviewExtension(userGeneratedKey, button)", source)
        self.assertIn("async function syncVideoPreviewExtensionGenerateButton(userGeneratedKey)", source)
        self.assertIn("function updateVideoPreviewExtensionState(userGeneratedKey, patch)", source)
        self.assertIn("generationStartedAt", source)
        self.assertIn("sessionId: generationSessionId", source)
        self.assertIn("startGenerationProgress(generationSessionId, '延长视频', { count: 1, kind: 'extension' })", source)
        self.assertIn("async function refreshExtensionGenerationProgress(progress)", source)
        self.assertIn("async function reconcileVideoPreviewExtensionGeneration(userGeneratedKey)", source)
        self.assertIn("/api/user-generated-results/extension-video/status", source)
        self.assertIn("pendingSince: new Date(progress.startedAt).toISOString()", source)
        self.assertIn("data-continue-video-prompt", source)
        self.assertIn('data-transform-video-prompt="polish"', source)
        self.assertIn('data-transform-video-prompt="expand"', source)
        self.assertIn("正在检索知识库并", source)
        self.assertIn("/api/user-generated-results/video-prompt/continue", source)
        self.assertIn("/api/user-generated-results/extension-video/generate", source)
        self.assertIn("async function prepareVideoExtensionPreview(userGeneratedKey, button, savedState = null)", source)
        self.assertIn("VIDEO_PREVIEW_EXTENSION_STORAGE_KEY", source)
        self.assertIn("async function saveVideoPreviewExtensionFrame(userGeneratedKey, frameTime)", source)
        self.assertIn("fetch('/api/user-generated-results/extension-frame'", source)
        self.assertIn("function restoreVideoPreviewExtensionState(video, userGeneratedKey, button)", source)
        self.assertIn("video.addEventListener('loadeddata', seekSavedFrame", source)
        self.assertIn("video.addEventListener('seeked', () => void renderSavedFrame()", source)
        self.assertIn("const framePreview = video.cloneNode(true)", source)
        self.assertIn("framePreview.currentTime = video.currentTime", source)
        self.assertIn("framePreview.dataset.framePreview = 'true'", source)
        self.assertIn("video.pause()", source)
        self.assertIn("setVideoPreviewButtonLabel(button, '重新截取')", source)
        self.assertIn(".video-preview-merge-control", source)
        self.assertIn("data-video-preview-merge disabled>待生成", source)
        self.assertIn("mergeButton.disabled ? '待生成' : '合并'", source)

        self.assertIn("function syncVideoPreviewMergeAvailability()", source)
        self.assertIn("async function mergeExtendedPreviewVideos(leftKey, button)", source)
        self.assertIn("fetch('/api/user-generated-results/merge'", source)
        self.assertIn('value="direct"', source)
        self.assertIn('value="continuation"', source)
        self.assertIn("mergeMode, splitTime", source)
        self.assertIn("const needsFilteredRestore = !!state.hotRadar.selectedSourceId || !!state.hotRadar.keyword", source)
        self.assertIn("topicList.classList.toggle('is-two-columns'", source)
        self.assertIn("columnToggleButton.textContent = twoColumns ? '双列' : '单列'", source)
        self.assertIn("function renderHotRadarSourceSelect", source)
        self.assertNotIn('id="hotRadarCategoryList"', source)
        self.assertIn("#hotRadarModal #hotRadarTopicList .hot-radar-topic-card.active", source)
        self.assertIn("--hot-radar-surface: #ffffff", source)
        self.assertIn("grid-template-rows: minmax(0, 1fr)", source)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(300px, 350px)", source)
        self.assertIn("HOT_RADAR_SNAPSHOT_STORAGE_KEY", source)
        self.assertIn("function loadHotRadarSnapshot()", source)
        self.assertIn("function persistHotRadarSnapshot(hotRadar)", source)
        self.assertIn('点击“刷新”获取最新热榜', source)
        self.assertIn('<h1 id="hotRadarTitle">热点雷达</h1>', modal_source)
        self.assertNotIn('<header class="hot-radar-header">', modal_source)
        self.assertLess(
            modal_source.index('id="hotRadarRefreshButton"'),
            modal_source.index('class="hot-radar-app-shell"'),
        )
        self.assertLess(
            modal_source.index('id="hotRadarCloseButton"'),
            modal_source.index('class="hot-radar-app-shell"'),
        )
        self.assertIn('<div id="hotRadarDetailMeta" class="hot-radar-detail-sub">热点摘要</div>', modal_source)
        self.assertIn("公开热点聚合与选题工作台", modal_source)
        self.assertIn("热点来源", modal_source)
        self.assertNotIn('class="hot-radar-sidebar-footer"', modal_source)
        self.assertNotIn('id="hotRadarFetchRouteBadge"', modal_source)
        self.assertIn('id="hotRadarFlameGradient"', modal_source)
        self.assertIn('id="hotRadarSourceManagerModal"', source)
        self.assertIn('value="__add_source__">＋ 新增数据源…', source)
        self.assertIn("selectedValue === '__add_source__'", source)
        self.assertIn("async function saveHotRadarCustomSources()", source)
        self.assertNotIn('id="hotRadarSourceList"', modal_source)
        self.assertIn("全部来源", source)
        self.assertIn("hot-radar-topic-list.is-switching", source)
        self.assertIn("正在切换热点…", source)
        self.assertIn("hotRadarTopicEnter", source)
        self.assertIn("topicList.setAttribute('aria-busy'", source)
        self.assertIn("function formatHotRadarUpdatedAt(value)", source)
        self.assertIn("最后更新：${formatted}", source)
        self.assertNotIn("String(item.category || '未分类')", source)
        self.assertNotIn("String(item.trend || 'stable')", source)
        self.assertNotIn("String(selectedTopic.category || '')", source)
        self.assertNotIn("趋势分析", modal_source)
        self.assertNotIn("推送规则", modal_source)
        self.assertNotIn("定时摘要", modal_source)

    def test_completed_extension_video_matches_saved_frame_source(self) -> None:
        result_root = self.root / "用户生成结果"
        left_path = result_root / "video" / "left.mp4"
        left_path.parent.mkdir(parents=True)
        left_path.write_bytes(b"left")
        frame_name = hashlib.sha256(b"video/left.mp4").hexdigest()[:24]
        frame_path = result_root / "extension-frame" / f"{frame_name}.png"
        frame_path.parent.mkdir(parents=True)
        frame_path.write_bytes(b"frame")
        right_path = result_root / "video" / "right.mp4"
        right_path.write_bytes(b"right")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all([{
            "generationStatus": "generated",
            "archiveKey": "video/right.mp4",
            "firstFrame": {"source": str(frame_path)},
        }])

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            body = ai8video_web._completed_extension_video("video/left.mp4")

        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["userGeneratedKey"], "video/right.mp4")

    def test_hot_topic_sources_are_fetched_concurrently(self) -> None:
        source_ids = ["weibo", "zhihu", "bilibili"]
        fetch_barrier = threading.Barrier(len(source_ids), timeout=1)

        def fetch_source(
            source: hot_topic_feeds.FeedSource,
            timeout_seconds: int,
        ) -> list[hot_topic_feeds.FeedEntry]:
            self.assertEqual(timeout_seconds, 1)
            fetch_barrier.wait()
            return [hot_topic_feeds.FeedEntry(source.id, f"https://example.com/{source.id}")]

        with patch.object(hot_topic_feeds, "fetch_feed_entries", side_effect=fetch_source):
            payloads = hot_topic_feeds.fetch_source_payloads(
                hot_topic._source_registry(),
                source_ids,
                1,
            )

        self.assertEqual(list(payloads), source_ids)
        self.assertEqual(
            [payloads[source_id]["entries"][0].title for source_id in source_ids],
            source_ids,
        )

    def test_hot_topic_cache_write_atomically_replaces_previous_snapshot(self) -> None:
        cache_path = self.root / "hot-topic-cache.json"

        hot_topic._write_json(cache_path, {"updatedAt": "first", "items": [{"id": "old"}]})
        hot_topic._write_json(cache_path, {"updatedAt": "second", "items": [{"id": "new"}]})

        self.assertEqual(
            json.loads(cache_path.read_text(encoding="utf-8")),
            {"updatedAt": "second", "items": [{"id": "new"}]},
        )
        self.assertEqual(list(self.root.iterdir()), [cache_path])

    def test_custom_hot_topic_sources_are_saved_without_builtin_duplication(self) -> None:
        config_path = self.root / "feeds.json"

        saved = hot_topic_feeds.save_custom_sources(
            config_path,
            [{"id": "custom-ai", "name": "AI 热点", "url": "https://example.com/feed.xml"}],
        )

        self.assertEqual([item.id for item in saved], ["custom-ai"])
        registry = hot_topic_feeds.load_source_registry(config_path)
        self.assertIn("weibo", registry)
        self.assertEqual(registry["custom-ai"].url, "https://example.com/feed.xml")

        with self.assertRaisesRegex(ValueError, "数据源标识重复"):
            hot_topic_feeds.save_custom_sources(
                config_path,
                [{"id": "weibo", "name": "重复微博", "url": "https://example.com/feed.xml"}],
            )

    def test_retry_inputs_require_persisted_first_frame(self) -> None:
        record = {
            "videoIndex": 3,
            "videoTitle": "布局窗口期",
            "prompt": "复用现有最终方案",
            "request": {"durationSeconds": 10, "ratio": "9:16", "resolution": "480p", "preset": "custom"},
            "firstFrame": None,
        }
        with self.assertRaisesRegex(ValueError, "中间产物已丢失"):
            ai8video_web._build_retry_inputs(record)

        first_frame_path = self.root / "first-frame.png"
        first_frame_path.write_bytes(b"image")
        record["firstFrame"] = {"source": str(first_frame_path)}
        retry_request, video, first_frame = ai8video_web._build_retry_inputs(record)
        self.assertEqual(retry_request.ratio, "9:16")
        self.assertEqual(video.index, 3)
        self.assertEqual(first_frame.source, str(first_frame_path))

    def test_hot_topic_parser_supports_rss_and_atom(self) -> None:
        source = hot_topic_feeds.FeedSource("sample", "示例源", "测试", "https://example.com/feed")
        rss_items = hot_topic_feeds.parse_feed_entries(
            source,
            "<rss><channel><item><title>RSS 标题</title><link>https://example.com/rss</link>"
            "<description><![CDATA[<b>RSS 摘要</b>]]></description></item></channel></rss>",
        )
        atom_items = hot_topic_feeds.parse_feed_entries(
            source,
            "<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>Atom 标题</title>"
            "<link href='https://example.com/atom'/><summary>Atom 摘要</summary></entry></feed>",
        )

        self.assertEqual(rss_items[0].description, "RSS 摘要")
        self.assertEqual(atom_items[0].url, "https://example.com/atom")

    def test_hot_topic_parser_supports_rank_html_and_bilibili_json(self) -> None:
        rank_source = hot_topic_feeds.FeedSource(
            "weibo",
            "微博热搜",
            "中文热榜",
            "https://example.com/rank",
            "rank-html",
        )
        rank_items = hot_topic_feeds.parse_feed_entries(
            rank_source,
            "<table><tr><td>1.</td><td><a href='https://example.com/topic'>微博话题</a></td>"
            "<td>123 万</td></tr></table>",
        )
        bilibili_source = hot_topic_feeds.FeedSource(
            "bilibili",
            "B站热搜",
            "视频趋势",
            "https://example.com/bilibili",
            "bilibili-json",
        )
        bilibili_items = hot_topic_feeds.parse_feed_entries(
            bilibili_source,
            json.dumps({"data": {"trending": {"list": [{"keyword": "热点 A"}]}}}),
        )

        self.assertEqual(rank_items[0].title, "微博话题")
        self.assertEqual(rank_items[0].heat, "123 万")
        self.assertEqual(bilibili_items[0].title, "热点 A")
        self.assertIn("search.bilibili.com", bilibili_items[0].url)

    def test_hot_topic_items_are_filtered_by_selected_sources(self) -> None:
        items = [
            {"sourceId": "weibo", "title": "中文热点", "description": "", "sourceName": "微博热搜"},
            {"sourceId": "v2ex", "title": "技术热点", "description": "", "sourceName": "V2EX"},
        ]

        filtered = hot_topic._filter_items(items, None, ["weibo"])

        self.assertEqual([item["sourceId"] for item in filtered], ["weibo"])

    def test_hot_topic_unknown_filter_does_not_expand_to_all_sources(self) -> None:
        registry = hot_topic._source_registry()

        source_ids = hot_topic._resolve_source_ids(registry, sources=None, category="不存在的分类")

        self.assertEqual(source_ids, [])

    def test_hot_topic_api_decodes_unicode_query_values(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            environ={
                "QUERY_STRING": (
                    "category=%E4%B8%AD%E6%96%87%E7%83%AD%E6%A6%9C&"
                    "keyword=%E7%9F%AD%E8%A7%86%E9%A2%91&refresh=1"
                )
            },
            query={},
        )
        try:
            with patch.object(hot_topic_routes, "request", ai8video_web.request), patch.object(
                hot_topic_routes,
                "list_hot_topics",
                return_value={"ok": True},
            ) as list_topics:
                body = ai8video_web.api_hot_topics()
        finally:
            ai8video_web.request = request_backup

        self.assertEqual(body, {"ok": True})
        list_topics.assert_called_once_with(
            sources=None,
            category="中文热榜",
            keyword="短视频",
            force_refresh=True,
        )

    def test_recycle_bin_delete_api_rejects_non_array_folders(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"folders": "task-one"},
        )
        ai8video_web.response = fake_response
        try:
            body = ai8video_web.api_delete_user_recycle_bin_tasks()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 400)
        self.assertEqual(body, {"ok": False, "error": "folders must be an array"})

    def test_recycle_bin_delete_api_passes_selected_folders_to_service(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        selected_folders = ["task-one", "task-two"]
        expected_body = {
            "ok": True,
            "deletedCount": 2,
            "deletedFolders": selected_folders,
        }
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"folders": selected_folders},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(
                ai8video_web,
                "delete_failed_video_tasks",
                return_value=expected_body,
            ) as delete_failed_video_tasks:
                body = ai8video_web.api_delete_user_recycle_bin_tasks()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        delete_failed_video_tasks.assert_called_once_with(selected_folders)
        self.assertEqual(body, expected_body)

    def test_recycle_bin_restore_api_passes_folder_to_service(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        expected_body = {"ok": True, "restoredCount": 1, "removedFolder": "task-one"}
        ai8video_web.request = SimpleNamespace(method="POST", json={"folder": "task-one"})
        ai8video_web.response = fake_response
        try:
            with patch.object(
                ai8video_web,
                "restore_failed_video_task",
                return_value=expected_body,
            ) as restore_failed_video_task:
                body = ai8video_web.api_restore_user_recycle_bin_task()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        restore_failed_video_task.assert_called_once_with("task-one")
        self.assertEqual(body, expected_body)

    def test_auth_settings_image_model_pull_requires_real_image_credentials(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        fake_config = SimpleNamespace(image_base_url=None, image_api_key=None)
        ai8video_web.request = SimpleNamespace(method="POST", json={"envName": "AI8VIDEO_IMAGE_MODEL"})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web.AI8VideoConfig, "from_env", return_value=fake_config):
                body = ai8video_web.api_auth_settings_models()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "图片模型没有真实接口地址或 API Key，不能拉取模型。")

    def test_auth_settings_image_model_pull_saves_image_catalog(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        fake_config = SimpleNamespace(image_base_url="https://image.example.com", image_api_key="sk-image")
        models = [{"modelId": "GPT-image2", "name": "GPT-image2", "type": "image"}]
        ai8video_web.request = SimpleNamespace(method="POST", json={"envName": "AI8VIDEO_IMAGE_MODEL"})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web.AI8VideoConfig, "from_env", return_value=fake_config), patch.object(
                ai8video_web,
                "pull_model_catalog",
                return_value={"ok": True, "models": models, "attempts": []},
            ), patch.object(
                ai8video_web,
                "save_model_catalog",
                side_effect=lambda _env_name, catalog: catalog,
            ) as save_catalog:
                body = ai8video_web.api_auth_settings_models()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertTrue(body["ok"])
        self.assertEqual(body["models"], models)
        save_catalog.assert_called_once_with("AI8VIDEO_IMAGE_MODEL", models)


    def test_result_bubble_includes_collapsed_review_suggestions(self) -> None:
        source = read_static_source()

        self.assertIn('function renderResultReviewSuggestions(result)', source)
        self.assertIn('<details class="result-review-details">', source)
        self.assertIn('审核建议（${suggestions.length}）', source)
        self.assertIn('review.userAdvisories', source)
        self.assertIn('const advisoryGroups = new Map()', source)
        self.assertIn('advisoryGroups.get(text)', source)
        self.assertIn('｜请注意：', source)
        self.assertIn('｜已修正：', source)
        self.assertNotIn('<details class="result-review-details" open>', source)

    def test_progress_video_cards_are_fifty_percent_larger(self) -> None:
        source = read_static_source()

        self.assertIn("width: 186px;\n      min-width: 186px;\n      height: 111px;", source)
        self.assertIn("width: 162px;\n      min-width: 162px;\n      height: 99px;", source)

    def test_result_meta_long_text_scrolls_on_hover(self) -> None:
        source = read_static_source()

        self.assertIn("function renderHoverScrollText(value, threshold = 10)", source)
        self.assertIn("animation: resultMetaHoverScroll 5s linear infinite alternate", source)
        self.assertIn("@keyframes resultMetaHoverScroll", source)

    def test_tts_ai_working_status_uses_green(self) -> None:
        source = read_static_source()

        self.assertIn(".video-preview-tts-status.is-working", source)
        self.assertIn("color: #15803d;", source)
        self.assertIn("setTtsStatus(options.statusText, 'working')", source)
        self.assertIn("setTtsStatus(message.includes('台词已删除') ? '台词已删除' : message, 'error')", source)

    def tearDown(self) -> None:
        if self.asset_env_backup is None:
            os.environ.pop("AI8VIDEO_ASSET_STORE_PATH", None)
        else:
            os.environ["AI8VIDEO_ASSET_STORE_PATH"] = self.asset_env_backup
        if self.env_backup is None:
            os.environ.pop("AI8VIDEO_BATCH_REPORT_DIR", None)
        else:
            os.environ["AI8VIDEO_BATCH_REPORT_DIR"] = self.env_backup
        if self.alert_env_backup is None:
            os.environ.pop("AI8VIDEO_BATCH_ALERT_DIR", None)
        else:
            os.environ["AI8VIDEO_BATCH_ALERT_DIR"] = self.alert_env_backup
        if self.state_env_backup is None:
            os.environ.pop("AI8VIDEO_BATCH_SUPERVISOR_STATE_PATH", None)
        else:
            os.environ["AI8VIDEO_BATCH_SUPERVISOR_STATE_PATH"] = self.state_env_backup
        if self.admin_state_env_backup is None:
            os.environ.pop("AI8VIDEO_BATCH_SUPERVISOR_ADMIN_STATE_PATH", None)
        else:
            os.environ["AI8VIDEO_BATCH_SUPERVISOR_ADMIN_STATE_PATH"] = self.admin_state_env_backup
        if self.lock_env_backup is None:
            os.environ.pop("AI8VIDEO_BATCH_SUPERVISOR_LOCK_PATH", None)
        else:
            os.environ["AI8VIDEO_BATCH_SUPERVISOR_LOCK_PATH"] = self.lock_env_backup
        if self.deployment_env_backup is None:
            os.environ.pop("AI8VIDEO_BATCH_SUPERVISOR_LAUNCHD_PLIST_PATH", None)
        else:
            os.environ["AI8VIDEO_BATCH_SUPERVISOR_LAUNCHD_PLIST_PATH"] = self.deployment_env_backup
        if self.seed_file_env_backup is None:
            os.environ.pop("AI8VIDEO_BATCH_SEED_FILE", None)
        else:
            os.environ["AI8VIDEO_BATCH_SEED_FILE"] = self.seed_file_env_backup
        if self.background_music_env_backup is None:
            os.environ.pop("AI8VIDEO_BACKGROUND_MUSIC_DIR", None)
        else:
            os.environ["AI8VIDEO_BACKGROUND_MUSIC_DIR"] = self.background_music_env_backup
        for key, value in self.video_env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for key, value in self.llm_env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tempdir.cleanup()

    def test_runtime_supervisor_admin_result_path_uses_configured_location(self) -> None:
        target = self.root / "batch_supervisor_admin_state.json"
        resolved = ai8video_runtime.get_supervisor_admin_result_path(refresh=True)
        self.assertEqual(resolved, target.resolve())

    def test_resolve_batch_report_path_accepts_relative_path_inside_root(self) -> None:
        target = self.root / "batch_reports" / "2026-06-13" / "report.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")

        resolved = ai8video_web._resolve_batch_report_path("2026-06-13/report.json")

        self.assertEqual(resolved, target.resolve())

    def test_resolve_batch_report_path_accepts_absolute_path_inside_root(self) -> None:
        target = self.root / "batch_reports" / "2026-06-13" / "report.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")

        resolved = ai8video_web._resolve_batch_report_path(str(target))

        self.assertEqual(resolved, target.resolve())

    def test_resolve_batch_report_path_rejects_outside_root(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "outside batch report dir"):
            ai8video_web._resolve_batch_report_path(str(outside))

    def test_resolve_batch_report_path_requires_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "reportPath is required"):
            ai8video_web._resolve_batch_report_path("")

    def test_resolve_batch_alert_path_accepts_relative_path_inside_root(self) -> None:
        target = self.root / "batch_alerts" / "2026-06-13" / "alert.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")

        resolved = ai8video_web._resolve_batch_alert_path("2026-06-13/alert.json")

        self.assertEqual(resolved, target.resolve())

    def test_resolve_batch_alert_path_rejects_outside_root(self) -> None:
        outside = self.root / "outside-alert.json"
        outside.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "outside batch alert dir"):
            ai8video_web._resolve_batch_alert_path(str(outside))

    def test_batch_supervisor_state_path_uses_configured_location(self) -> None:
        target = self.root / "batch_supervisor_state.json"
        resolved = ai8video_web._batch_supervisor_state_path()
        self.assertEqual(resolved, target.resolve())

    def test_batch_supervisor_admin_state_path_uses_configured_location(self) -> None:
        target = self.root / "batch_supervisor_admin_state.json"
        resolved = ai8video_web._batch_supervisor_admin_state_path()
        self.assertEqual(resolved, target.resolve())

    def test_batch_supervisor_lock_path_uses_configured_location(self) -> None:
        target = self.root / "batch_supervisor.lock"
        resolved = ai8video_web._batch_supervisor_lock_path()
        self.assertEqual(resolved, target.resolve())

    def test_batch_supervisor_deployment_path_uses_configured_location(self) -> None:
        target = self.root / "com.ai8.video.supervisor.plist"
        resolved = ai8video_web._batch_supervisor_deployment_path()
        self.assertEqual(resolved, target.resolve())

    def test_batch_seed_file_path_uses_configured_location(self) -> None:
        target = self.root / "batch_supervisor" / "seed_messages.txt"
        resolved = ai8video_web._batch_seed_file_path()
        self.assertEqual(resolved, target.resolve())

    def test_api_generation_mode_saves_concurrent_generation(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="POST", json={"concurrentGeneration": True})
        try:
            with patch.object(
                ai8video_web,
                "update_generation_mode",
                return_value={"ok": True, "concurrentGeneration": True},
            ) as update:
                body = ai8video_web.api_generation_mode()
        finally:
            ai8video_web.request = request_backup

        update.assert_called_once_with(concurrent_generation=True)
        self.assertTrue(body["ok"])
        self.assertTrue(body["concurrentGeneration"])

    def test_api_html_motion_overlay_saves_enabled_state(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="POST", json={"enabled": True})
        try:
            with patch.object(
                ai8video_web,
                "update_html_motion_overlay",
                return_value={"ok": True, "enabled": True},
            ) as update:
                body = ai8video_web.api_html_motion_overlay()
        finally:
            ai8video_web.request = request_backup

        update.assert_called_once_with(enabled=True)
        self.assertTrue(body["ok"])
        self.assertTrue(body["enabled"])

    def test_api_narration_review_saves_review_count(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="POST", json={"reviewCount": 3})
        try:
            with patch.object(
                ai8video_web,
                "update_narration_review_count",
                return_value={"ok": True, "reviewCount": 3},
            ) as update:
                body = ai8video_web.api_narration_review()
        finally:
            ai8video_web.request = request_backup

        update.assert_called_once_with(3)
        self.assertEqual(body["reviewCount"], 3)

    def test_api_html_motion_safe_zone_saves_current_ratio(self) -> None:
        request_backup = ai8video_web.request
        payload = {
            "aspectRatio": "9:16",
            "safeZone": {"x": 10, "y": 12, "width": 70, "height": 36},
        }
        ai8video_web.request = SimpleNamespace(method="POST", json=payload)
        try:
            with patch.object(
                ai8video_web,
                "update_html_motion_safe_zone",
                return_value={"ok": True, **payload},
            ) as update:
                body = ai8video_web.api_html_motion_safe_zone()
        finally:
            ai8video_web.request = request_backup

        update.assert_called_once_with("9:16", payload["safeZone"])
        self.assertTrue(body["ok"])
        self.assertEqual(body["safeZone"]["width"], 70)

    def test_api_regenerate_html_motion_reports_deleted_prompt(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"userGeneratedKey": "video/demo.mp4"},
        )
        try:
            with patch.object(ai8video_web, "_resolve_user_generated_video_key", return_value=(self.root / "demo.mp4", "video/demo.mp4")), patch.object(
                ai8video_web,
                "_video_prompt_for_user_generated_video",
                side_effect=LookupError("视频提示词已删除"),
            ):
                body = ai8video_web.api_regenerate_user_generated_html_motion()
        finally:
            ai8video_web.request = request_backup

        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "VIDEO_PROMPT_DELETED")

    def test_api_regenerate_html_motion_returns_async_task(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"userGeneratedKey": "video/demo.mp4"},
        )
        task = {
            "ok": True,
            "taskId": "task-demo",
            "status": "queued",
            "phase": "queued",
        }
        try:
            with patch.object(
                ai8video_web,
                "_resolve_user_generated_video_key",
                return_value=(self.root / "demo.mp4", "video/demo.mp4"),
            ), patch.object(
                ai8video_web,
                "_video_prompt_for_user_generated_video",
                return_value=("留存提示词", {}, "asset"),
            ), patch.object(
                ai8video_web.html_motion_task_service,
                "submit",
                return_value=task,
            ) as submit:
                body = ai8video_web.api_regenerate_user_generated_html_motion()
        finally:
            ai8video_web.request = request_backup

        self.assertEqual(body["taskId"], "task-demo")
        self.assertEqual(body["pollUrl"], "/api/user-generated-results/html-motion-tasks/task-demo")
        submit.assert_called_once()

    def test_html_motion_task_status_merges_completed_result(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="GET")
        snapshot = {
            "ok": True,
            "taskId": "task-demo",
            "status": "preview_ready",
            "phase": "preview_ready",
            "result": {
                "ok": True,
                "htmlMotionOverlay": {"status": "preview_ready", "previewUrl": "/preview"},
            },
        }
        try:
            with patch.object(ai8video_web.html_motion_task_service, "get", return_value=snapshot):
                body = ai8video_web.api_html_motion_task_status("task-demo")
        finally:
            ai8video_web.request = request_backup

        self.assertEqual(body["htmlMotionOverlay"]["status"], "preview_ready")
        self.assertEqual(body["taskStatus"], "preview_ready")

    def test_api_html_motion_active_returns_in_flight_task(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"userGeneratedKey": "video/demo.mp4"},
        )
        snapshot = {
            "ok": True,
            "taskId": "task-active",
            "status": "rendering",
            "phase": "rendering",
            "userGeneratedKey": "video/demo.mp4",
        }
        try:
            with patch.object(
                ai8video_web,
                "_resolve_user_generated_video_key",
                return_value=(self.root / "demo.mp4", "video/demo.mp4"),
            ), patch.object(
                ai8video_web.html_motion_task_service,
                "get_active",
                return_value=snapshot,
            ):
                body = ai8video_web.api_html_motion_active_task()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["active"])
        self.assertEqual(body["taskId"], "task-active")
        self.assertEqual(body["pollUrl"], "/api/user-generated-results/html-motion-tasks/task-active")

    def test_api_html_motion_active_returns_inactive_when_idle(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"userGeneratedKey": "video/demo.mp4"},
        )
        try:
            with patch.object(
                ai8video_web,
                "_resolve_user_generated_video_key",
                return_value=(self.root / "demo.mp4", "video/demo.mp4"),
            ), patch.object(
                ai8video_web.html_motion_task_service,
                "get_active",
                return_value=None,
            ):
                body = ai8video_web.api_html_motion_active_task()
        finally:
            ai8video_web.request = request_backup

        self.assertFalse(body["active"])
        self.assertEqual(body["taskId"], "")

    def test_static_html_motion_overlay_exposes_toggle_and_degraded_status(self) -> None:
        source = read_static_source()

        self.assertIn('id="htmlMotionOverlayButton"', source)
        self.assertIn('id="htmlMotionOverlayDrawer"', source)
        self.assertIn('id="htmlMotionOverlayButton" type="button"', source)
        self.assertIn('aria-controls="htmlMotionOverlayDrawer" hidden', source)
        self.assertIn('id="htmlMotionOverlayDrawer" class="system-prompt-drawer background-music-drawer" aria-hidden="true" hidden', source)
        self.assertIn("function refreshHtmlMotionOverlay()", source)
        self.assertIn("function saveHtmlMotionOverlay(enabled)", source)
        self.assertIn("label: '已叠加'", source)
        self.assertIn("label: '已降级，基础视频已保留'", source)

    def test_api_video_text_overlay_saves_visible_settings(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={
                "enabled": True,
                "text": "限时福利",
                "canvasWidth": 9,
                "canvasHeight": 16,
                "fontFamily": "custom.ttf",
                "fontWeight": 900,
            },
        )
        try:
            with patch.object(
                ai8video_web,
                "update_video_text_overlay",
                return_value={
                    "ok": True,
                    "enabled": True,
                    "text": "限时福利",
                    "canvasWidth": 9,
                    "canvasHeight": 16,
                    "fontFamily": "custom.ttf",
                    "fontWeight": 900,
                },
            ) as update:
                body = ai8video_web.api_video_text_overlay()
        finally:
            ai8video_web.request = request_backup

        update.assert_called_once_with(
            enabled=True,
            text="限时福利",
            canvas_width=9,
            canvas_height=16,
            text_color=None,
            stroke_color=None,
            font_family="custom.ttf",
            font_size=None,
            font_weight=900,
            stroke_width=None,
            position=None,
            text_x=None,
            text_y=None,
            animation_delay_seconds=None,
            animation_type=None,
            watermark_enabled=None,
            watermark_image=None,
            watermark_size=None,
            watermark_opacity=None,
            watermark_animation_delay_seconds=None,
            watermark_animation_type=None,
            watermark_position=None,
            watermark_x=None,
            watermark_y=None,
            watermark2_enabled=None,
            watermark2_image=None,
            watermark2_size=None,
            watermark2_opacity=None,
            watermark2_animation_delay_seconds=None,
            watermark2_animation_type=None,
            watermark2_position=None,
            watermark2_x=None,
            watermark2_y=None,
            preview_background_color=None,
            preview_background_image=None,
        )
        self.assertTrue(body["ok"])
        self.assertTrue(body["enabled"])

    def test_api_video_text_overlay_preview_returns_png_response(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"text": "限时福利", "canvasWidth": 9, "canvasHeight": 16, "targetWidth": 405, "targetHeight": 720},
        )
        try:
            with patch.object(
                ai8video_web,
                "render_video_text_overlay_preview",
                return_value=b"\x89PNG\r\n\x1a\npreview",
            ) as render:
                body = ai8video_web.api_video_text_overlay_preview()
        finally:
            ai8video_web.request = request_backup

        render.assert_called_once_with(
            {"text": "限时福利", "canvasWidth": 9, "canvasHeight": 16, "targetWidth": 405, "targetHeight": 720},
            target_width=405,
            target_height=720,
        )
        self.assertEqual(body.status_code, 200)
        self.assertEqual(body.headers["Content-Type"], "image/png")
        self.assertEqual(body.body, b"\x89PNG\r\n\x1a\npreview")

    def test_api_local_tts_preview_generates_fixed_demo_text(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        preview_dir = self.root / "tts-output"
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={
                "voice": "冰糖",
                "rate": 180,
            },
        )
        ai8video_web.response = fake_response

        def fake_synthesize(text, output_path, *, settings=None, ffmpeg_bin=None, output_volume=None):
            Path(output_path).write_bytes(b"audio")
            return {"status": "generated", "path": str(output_path), "sizeBytes": 5}

        try:
            with patch.object(
                ai8video_web,
                "local_tts_status",
                return_value={
                    "ok": True,
                    "available": True,
                    "engine": "mimo-api",
                    "voice": "冰糖",
                    "voiceLabel": "冰糖",
                    "rate": 185,
                    "volume": 1,
                },
            ), patch.object(
                ai8video_web,
                "local_tts_output_dir",
                return_value=preview_dir,
            ), patch.object(
                ai8video_web,
                "synthesize_local_tts",
                side_effect=fake_synthesize,
            ) as synthesize:
                body = ai8video_web.api_local_tts_preview()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertTrue(body["ok"])
        self.assertEqual(body["text"], "今天天气真好，你下载AI8video 了吗")
        self.assertTrue(body["audioUrl"].startswith("/api/local-tts/preview-audio/preview-cache-"))
        self.assertEqual(len(body["cacheKey"]), 16)
        self.assertFalse(body["cached"])
        synthesize.assert_called_once()
        self.assertEqual(synthesize.call_args.args[0], "今天天气真好，你下载AI8video 了吗")
        self.assertEqual(synthesize.call_args.kwargs["settings"]["voice"], "冰糖")
        self.assertEqual(synthesize.call_args.kwargs["settings"]["rate"], 185)
        self.assertNotIn("stylePrompt", synthesize.call_args.kwargs["settings"])
        self.assertNotIn("audioTag", synthesize.call_args.kwargs["settings"])
        self.assertEqual(synthesize.call_args.kwargs["output_volume"], 1.0)

    def test_api_local_tts_preview_reuses_cached_audio_for_same_settings(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        preview_dir = self.root / "tts-output"

        def fake_synthesize(text, output_path, *, settings=None, ffmpeg_bin=None, output_volume=None):
            Path(output_path).write_bytes(b"audio")
            return {"status": "generated", "path": str(output_path), "sizeBytes": 5}

        try:
            with patch.object(
                ai8video_web,
                "local_tts_status",
                return_value={
                    "ok": True,
                    "available": True,
                    "engine": "mimo-api",
                    "voice": "冰糖",
                    "voiceLabel": "冰糖",
                    "rate": 185,
                    "volume": 0.8,
                },
            ), patch.object(
                ai8video_web,
                "local_tts_output_dir",
                return_value=preview_dir,
            ), patch.object(
                ai8video_web,
                "synthesize_local_tts",
                side_effect=fake_synthesize,
            ) as synthesize:
                ai8video_web.request = SimpleNamespace(
                    method="POST",
                    json={"voice": "冰糖", "rate": 180, "volume": 0.8},
                )
                ai8video_web.response = fake_response
                first = ai8video_web.api_local_tts_preview()

                ai8video_web.request = SimpleNamespace(
                    method="POST",
                    json={"voice": "冰糖", "rate": 260, "volume": 0.8},
                )
                second = ai8video_web.api_local_tts_preview()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(first["audioUrl"], second["audioUrl"])
        self.assertEqual(first["cacheKey"], second["cacheKey"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        synthesize.assert_called_once()

    def test_local_tts_preview_cache_changes_when_clone_sample_changes(self) -> None:
        with patch.object(
            ai8video_web,
            "local_tts_voice_clone_cache_signature",
            side_effect=["sample.wav:100:1", "sample.wav:120:2"],
        ):
            first_key, first_name = ai8video_web._local_tts_preview_cache_info(
                ai8video_web.LOCAL_TTS_PREVIEW_TEXT,
                {"voice": "clone:sample.wav", "volume": 1},
            )
            second_key, second_name = ai8video_web._local_tts_preview_cache_info(
                ai8video_web.LOCAL_TTS_PREVIEW_TEXT,
                {"voice": "clone:sample.wav", "volume": 1},
            )

        self.assertNotEqual(first_key, second_key)
        self.assertNotEqual(first_name, second_name)

    def test_api_local_tts_preview_uses_different_cache_for_different_volume(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        preview_dir = self.root / "tts-output"

        def fake_synthesize(text, output_path, *, settings=None, ffmpeg_bin=None, output_volume=None):
            Path(output_path).write_bytes(f"audio-{output_volume}".encode("utf-8"))
            return {"status": "generated", "path": str(output_path), "sizeBytes": Path(output_path).stat().st_size}

        try:
            with patch.object(
                ai8video_web,
                "local_tts_status",
                return_value={
                    "ok": True,
                    "available": True,
                    "engine": "mimo-api",
                    "voice": "冰糖",
                    "voiceLabel": "冰糖",
                    "rate": 185,
                    "volume": 1,
                },
            ), patch.object(
                ai8video_web,
                "local_tts_output_dir",
                return_value=preview_dir,
            ), patch.object(
                ai8video_web,
                "synthesize_local_tts",
                side_effect=fake_synthesize,
            ) as synthesize:
                ai8video_web.request = SimpleNamespace(
                    method="POST",
                    json={"voice": "冰糖", "volume": 0.4},
                )
                ai8video_web.response = fake_response
                low = ai8video_web.api_local_tts_preview()

                ai8video_web.request = SimpleNamespace(
                    method="POST",
                    json={"voice": "冰糖", "volume": 1.6},
                )
                high = ai8video_web.api_local_tts_preview()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertNotEqual(low["cacheKey"], high["cacheKey"])
        self.assertNotEqual(low["audioUrl"], high["audioUrl"])
        self.assertEqual(synthesize.call_count, 2)

    def test_api_local_tts_preview_audio_uses_long_cache_control(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        preview_dir = self.root / "tts-output"
        preview_dir.mkdir(parents=True, exist_ok=True)
        (preview_dir / "preview-cache-demo.m4a").write_bytes(b"audio")
        ai8video_web.request = SimpleNamespace(method="GET")
        ai8video_web.response = SimpleNamespace(status=200)
        try:
            with patch.object(
                ai8video_web,
                "local_tts_output_dir",
                return_value=preview_dir,
            ):
                body = ai8video_web.api_local_tts_preview_audio("preview-cache-demo.m4a")
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(body.status_code, 200)
        self.assertEqual(body.headers.get("Cache-Control"), "public, max-age=31536000, immutable")
        if getattr(body, "body", None) and hasattr(body.body, "close"):
            body.body.close()

    def test_api_local_tts_voice_clone_upload_returns_updated_status(self) -> None:
        class _FakeUpload:
            filename = "主播样本.mp4"

        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            files=SimpleNamespace(get=lambda key: _FakeUpload()),
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(
                ai8video_web,
                "save_local_tts_voice_clone_upload",
                return_value={"ok": True, "voice": "clone:主播样本.mp3", "voiceCount": 10},
            ) as save_upload:
                body = ai8video_web.api_local_tts_voice_clone()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        save_upload.assert_called_once()
        self.assertTrue(body["ok"])
        self.assertEqual(body["voice"], "clone:主播样本.mp3")

    def test_api_open_batch_supervisor_state_opens_file_when_present(self) -> None:
        state_path = self.root / "batch_supervisor_state.json"
        state_path.write_text("{}", encoding="utf-8")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "_open_path") as open_path:
                body = ai8video_web.api_open_batch_supervisor_state()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_path.assert_called_once_with(state_path.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "file")

    def test_api_open_batch_supervisor_state_opens_parent_dir_when_file_missing(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "_open_in_file_manager") as open_dir:
                body = ai8video_web.api_open_batch_supervisor_state()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_dir.assert_called_once_with((self.root).resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "directory")

    def test_api_open_batch_supervisor_admin_state_opens_file_when_present(self) -> None:
        state_path = self.root / "batch_supervisor_admin_state.json"
        state_path.write_text("{}", encoding="utf-8")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "_open_path") as open_path:
                body = ai8video_web.api_open_batch_supervisor_admin_state()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_path.assert_called_once_with(state_path.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "file")

    def test_api_open_batch_supervisor_admin_state_opens_parent_dir_when_file_missing(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "_open_in_file_manager") as open_dir:
                body = ai8video_web.api_open_batch_supervisor_admin_state()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_dir.assert_called_once_with((self.root).resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "directory")

    def test_api_live_preflight_uses_safe_checks_by_default(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        try:
            with patch.object(
                ai8video_web,
                "run_preflight_checks",
                return_value={"checks": {"llm": {"status": "ok"}}, "timestamp": "2026-06-13 07:30:00"},
            ) as run_checks:
                body = ai8video_web.api_live_preflight()
        finally:
            ai8video_web.request = request_backup

        run_checks.assert_called_once()
        args = run_checks.call_args.args
        self.assertEqual(args[1], ["llm", "archive_config"])
        self.assertEqual(body["requestedChecks"], ["llm", "archive_config"])
        self.assertEqual(body["checks"]["llm"]["status"], "ok")

    def test_api_live_preflight_rejects_non_list_checks(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={"checks": "llm"})
        ai8video_web.response = fake_response
        try:
            body = ai8video_web.api_live_preflight()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 400)
        self.assertEqual(body["error"], "checks must be a list")

    def test_api_open_archive_dir_accepts_local_path_inside_archive_root(self) -> None:
        local_video = self.root / "archive" / "2026" / "06" / "13" / "demo.mp4"
        local_video.parent.mkdir(parents=True, exist_ok=True)
        local_video.write_text("demo", encoding="utf-8")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"localPath": str(local_video)},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "_archive_roots", return_value=[(self.root / "archive").resolve()]), patch.object(
                ai8video_web,
                "_open_in_file_manager",
            ) as open_dir:
                body = ai8video_web.api_open_archive_dir()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_dir.assert_called_once_with(local_video.parent.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(Path(body["path"]).resolve(), local_video.parent.resolve())

    def test_api_open_user_generated_results_folder_opens_root_without_resync(self) -> None:
        generated_root = self.root / "用户生成结果"
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "USER_GENERATED_RESULT_ROOT", generated_root.resolve()), patch.object(
                ai8video_web,
                "ensure_user_generated_result_dir",
                return_value=generated_root.resolve(),
            ), patch.object(
                ai8video_web,
                "_open_in_file_manager",
            ) as open_dir:
                body = ai8video_web.api_open_user_generated_results_folder()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_dir.assert_called_once_with(generated_root.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(Path(body["path"]).resolve(), generated_root.resolve())

    def test_api_open_user_generated_results_folder_always_opens_root(self) -> None:
        generated_root = self.root / "用户生成结果"
        archive_root = self.root / "archive"
        archive_video = archive_root / "ai8video" / "2026" / "06" / "13" / "demo.mp4"
        mirrored_video = generated_root / "ai8video" / "2026" / "06" / "13" / "demo.mp4"
        archive_video.parent.mkdir(parents=True, exist_ok=True)
        mirrored_video.parent.mkdir(parents=True, exist_ok=True)
        archive_video.write_text("archive", encoding="utf-8")
        mirrored_video.write_text("mirror", encoding="utf-8")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"localPath": str(archive_video.resolve())},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "USER_GENERATED_RESULT_ROOT", generated_root.resolve()), patch.object(
                ai8video_web,
                "ensure_user_generated_result_dir",
                return_value=generated_root.resolve(),
            ), patch.object(
                ai8video_web,
                "_archive_roots",
                return_value=[archive_root.resolve()],
            ), patch.object(ai8video_web, "_open_in_file_manager") as open_dir:
                body = ai8video_web.api_open_user_generated_results_folder()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_dir.assert_called_once_with(generated_root.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(Path(body["path"]).resolve(), generated_root.resolve())

    def test_api_user_generated_results_reads_live_folder_instead_of_stale_jsonl(self) -> None:
        generated_root = self.root / "用户生成结果"
        video_rel = Path("ai8video/2026/06/13/video/demo.mp4")
        cover_rel = Path("ai8video/2026/06/13/cover/demo.jpg")
        preview_rel = Path("ai8video/2026/06/13/preview/demo.jpg")
        video_path = generated_root / video_rel
        cover_path = generated_root / cover_rel
        preview_path = generated_root / preview_rel
        video_path.parent.mkdir(parents=True, exist_ok=True)
        cover_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_text("video", encoding="utf-8")
        cover_path.write_text("cover", encoding="utf-8")
        preview_path.write_text("preview", encoding="utf-8")
        asset_store_path = self.root / "assets.jsonl"
        asset_store_path.write_text(
            json.dumps({
                "archiveKey": video_rel.as_posix(),
                "archiveCoverKey": cover_rel.as_posix(),
                "archiveBackend": "local",
                "archiveStatus": "archived",
                "videoTitle": "演示视频",
                "createdAt": "2026-06-13T10:00:00+08:00",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(get=lambda key, default="200": default),
        )
        try:
            with patch.object(ai8video_web, "USER_GENERATED_RESULT_ROOT", generated_root.resolve()), patch.object(
                ai8video_web,
                "ensure_user_generated_result_dir",
                return_value=generated_root.resolve(),
            ):
                first = ai8video_web.api_user_generated_results()
                video_path.unlink()
                second = ai8video_web.api_user_generated_results()
        finally:
            ai8video_web.request = request_backup

        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(first["items"][0]["userGeneratedKey"], video_rel.as_posix())
        self.assertEqual(first["items"][0]["userGeneratedPreviewKey"], preview_rel.as_posix())
        self.assertEqual(first["items"][0]["userGeneratedCoverKey"], cover_rel.as_posix())
        self.assertEqual(second["items"], [])

    def test_api_user_recycle_bin_lists_failed_tasks_with_existing_videos(self) -> None:
        recycle_root = self.root / "回收站"
        failed_folder = recycle_root / "20260618-112233-01-demo-job-a"
        video_path = failed_folder / "video" / "01-demo.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"video")
        (failed_folder / "manifest.json").write_text(
            json.dumps(
                {
                    "createdAt": "2026-06-18T03:22:33+00:00",
                    "videoIndex": 1,
                    "videoTitle": "花字失败样片",
                    "jobId": "job-a",
                    "reason": "_mix_video() got an unexpected keyword argument 'preserve_original_audio_override'",
                    "videos": [
                        {
                            "name": "01-demo.mp4",
                            "relativePath": "20260618-112233-01-demo-job-a/video/01-demo.mp4",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        missing_folder = recycle_root / "20260618-112244-02-empty-job-b"
        missing_folder.mkdir(parents=True, exist_ok=True)
        (missing_folder / "manifest.json").write_text(
            json.dumps(
                {
                    "createdAt": "2026-06-18T03:24:44+00:00",
                    "videoTitle": "无视频失败",
                    "reason": "上游失败",
                    "videos": [{"relativePath": "missing.mp4"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(get=lambda key, default="100": default),
        )
        try:
            with patch(
                "ai8video.assets.user_recycle_bin.USER_RECYCLE_BIN_ROOT",
                recycle_root.resolve(),
            ), patch("ai8video.assets.user_recycle_bin.ensure_user_file_root", return_value=self.root):
                body = ai8video_web.api_user_recycle_bin()
        finally:
            ai8video_web.request = request_backup

        self.assertEqual(body["count"], 1)
        self.assertEqual(len(body["items"]), 1)
        item = body["items"][0]
        self.assertEqual(item["videoTitle"], "花字失败样片")
        self.assertIn("_mix_video()", item["reason"])
        self.assertEqual(
            item["displayReason"],
            "视频后处理失败，背景音乐或原声音轨合成没有完成。请重新生成，或先关闭背景音乐后再试。",
        )
        self.assertEqual(item["videoCount"], 1)
        self.assertEqual(item["videos"][0]["url"], "/user-recycle-bin/20260618-112233-01-demo-job-a/video/01-demo.mp4")

    def test_api_open_user_recycle_bin_folder_opens_visible_root(self) -> None:
        recycle_root = self.root / "回收站"
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = SimpleNamespace(status=200)
        try:
            with patch.object(
                ai8video_web,
                "ensure_user_recycle_bin_dir",
                return_value=recycle_root.resolve(),
            ), patch.object(ai8video_web, "_open_in_file_manager") as open_dir:
                body = ai8video_web.api_open_user_recycle_bin_folder()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_dir.assert_called_once_with(recycle_root.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(Path(body["path"]).resolve(), recycle_root.resolve())

    def test_api_delete_user_generated_result_removes_video_preview_and_cover(self) -> None:
        generated_root = self.root / "用户生成结果"
        video_rel = Path("ai8video/2026/06/13/video/demo.mp4")
        preview_rel = Path("ai8video/2026/06/13/preview/demo.jpg")
        cover_rel = Path("ai8video/2026/06/13/cover/demo.jpg")
        other_rel = Path("ai8video/2026/06/13/video/other.mp4")
        video_path = generated_root / video_rel
        preview_path = generated_root / preview_rel
        cover_path = generated_root / cover_rel
        other_path = generated_root / other_rel
        video_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        cover_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_text("video", encoding="utf-8")
        preview_path.write_text("preview", encoding="utf-8")
        cover_path.write_text("cover", encoding="utf-8")
        other_path.write_text("other", encoding="utf-8")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"userGeneratedKey": video_rel.as_posix()},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "USER_GENERATED_RESULT_ROOT", generated_root.resolve()), patch.object(
                ai8video_web,
                "ensure_user_generated_result_dir",
                return_value=generated_root.resolve(),
            ):
                body = ai8video_web.api_delete_user_generated_result()
                remaining = ai8video_web._user_generated_result_items(limit=200)
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertTrue(body["ok"])
        self.assertEqual(body["userGeneratedKey"], video_rel.as_posix())
        self.assertIn(video_rel.as_posix(), body["deleted"])
        self.assertIn(preview_rel.as_posix(), body["deleted"])
        self.assertIn(cover_rel.as_posix(), body["deleted"])
        self.assertFalse(video_path.exists())
        self.assertFalse(preview_path.exists())
        self.assertFalse(cover_path.exists())
        self.assertTrue(other_path.exists())
        self.assertEqual([item["userGeneratedKey"] for item in remaining], [other_rel.as_posix()])

    def test_api_regenerate_user_generated_previews_rebuilds_from_current_videos(self) -> None:
        generated_root = self.root / "用户生成结果"
        video_rel = Path("video/demo.mp4")
        stale_preview = generated_root / "preview" / "stale.jpg"
        video_path = generated_root / video_rel
        video_path.parent.mkdir(parents=True, exist_ok=True)
        stale_preview.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"video")
        stale_preview.write_bytes(b"stale")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = SimpleNamespace(status=200)

        def fake_generate(video, root, relative_key):
            target = root / "preview" / f"{Path(relative_key).stem}.jpg"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"preview")
            return {"ok": True, "previewKey": target.relative_to(root).as_posix(), "sizeBytes": 7}

        try:
            with patch.object(
                ai8video_web,
                "ensure_user_generated_result_dir",
                return_value=generated_root.resolve(),
            ), patch(
                "ai8video.assets.user_generated_previews.generate_preview_for_video",
                side_effect=fake_generate,
            ):
                body = ai8video_web.api_regenerate_user_generated_previews()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertTrue(body["ok"])
        self.assertEqual(body["videoCount"], 1)
        self.assertFalse(stale_preview.exists())
        self.assertEqual((generated_root / "preview" / "demo.jpg").read_bytes(), b"preview")

    def test_stateless_chat_status_marks_deleted_local_archive(self) -> None:
        video_path = self.root / "用户生成结果" / "video" / "demo.mp4"
        (self.root / "assets.jsonl").write_text(
            json.dumps(
                {
                    "videoIndex": 1,
                    "videoTitle": "已删除视频",
                    "jobId": "task_deleted",
                    "status": "succeeded",
                    "generationStatus": "generated",
                    "videoUrl": "https://example.test/demo.mp4",
                    "archiveStatus": "archived",
                    "archiveBackend": "local",
                    "archiveKey": "video/demo.mp4",
                    "archiveLocalPath": str(video_path),
                    "archiveError": None,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        body = ai8video_web._query_video_jobs_progress(
            "s-test",
            [{"videoIndex": 1, "jobId": "task_deleted"}],
        )

        progress = body["generationProgress"]
        self.assertEqual(progress["succeededCount"], 0)
        self.assertEqual(progress["deletedCount"], 1)
        self.assertEqual(progress["items"][0]["status"], "deleted")
        self.assertFalse(progress["items"][0]["hasLocalAsset"])

    def test_stateless_chat_status_maps_deleted_merge_segment_job(self) -> None:
        video_path = self.root / "用户生成结果" / "video" / "merge-deleted.mp4"
        (self.root / "assets.jsonl").write_text(
            json.dumps(
                {
                    "videoIndex": 6,
                    "videoTitle": "发布进入倒计时的冲刺感",
                    "jobId": "merge2-task_segment_1-task_segment_2",
                    "status": "succeeded",
                    "generationStatus": "generated",
                    "videoUrl": None,
                    "archiveStatus": "archived",
                    "archiveBackend": "local",
                    "archiveKey": "video/merge-deleted.mp4",
                    "archiveLocalPath": str(video_path),
                    "generationMeta": {
                        "mergeMode": "merge2",
                        "segmentRecords": [
                            {"role": "segment1", "jobId": "task_segment_1"},
                            {"role": "segment2", "jobId": "task_segment_2"},
                        ],
                    },
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        body = ai8video_web._query_video_jobs_progress(
            "s-test",
            [{"videoIndex": 6, "jobId": "task_segment_2"}],
        )

        progress = body["generationProgress"]
        self.assertEqual(progress["deletedCount"], 1)
        self.assertEqual(progress["items"][0]["status"], "deleted")
        self.assertEqual(progress["items"][0]["jobId"], "task_segment_2")
        self.assertFalse(progress["items"][0]["hasLocalAsset"])

    def test_stateless_chat_status_restores_merge_segment_status_from_asset_record(self) -> None:
        video_path = self.root / "用户生成结果" / "video" / "merge-done.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"video")
        (self.root / "assets.jsonl").write_text(
            json.dumps(
                {
                    "videoIndex": 3,
                    "videoTitle": "全球连接的时代已经到来",
                    "jobId": "merge2-task_segment_1-task_segment_2",
                    "status": "succeeded",
                    "generationStatus": "generated",
                    "videoUrl": None,
                    "archiveStatus": "archived",
                    "archiveBackend": "local",
                    "archiveKey": "video/merge-done.mp4",
                    "archiveUrl": "video/merge-done.mp4",
                    "archiveLocalPath": str(video_path),
                    "generationMeta": {
                        "mergeMode": "merge2",
                        "segmentRecords": [
                            {
                                "role": "segment1",
                                "jobId": "task_segment_1",
                                "status": "succeeded",
                                "videoUrl": "https://example.test/segment-1.mp4",
                            },
                            {
                                "role": "segment2",
                                "jobId": "task_segment_2",
                                "status": "succeeded",
                                "videoUrl": "https://example.test/segment-2.mp4",
                            },
                        ],
                    },
                    "request": {"videoCount": 3},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        fake_client = SimpleNamespace(get_job=Mock(side_effect=RuntimeError("should restore from asset record")))
        with patch.object(
            ai8video_web,
            "AI8VideoModelClient",
            return_value=fake_client,
        ):
            body = ai8video_web._query_video_jobs_progress(
                "s-test",
                [{"videoIndex": 3, "jobId": "merge2-task_segment_1-task_segment_2"}],
                video_count=3,
            )

        progress = body["generationProgress"]
        item = progress["items"][2]
        self.assertEqual(item["status"], "succeeded")
        self.assertEqual(item["jobId"], "merge2-task_segment_1-task_segment_2")
        self.assertEqual([segment["segmentLabel"] for segment in item["segmentStatus"]], ["片段 1", "片段 2"])
        self.assertEqual([segment["status"] for segment in item["segmentStatus"]], ["succeeded", "succeeded"])
        self.assertEqual(item["segmentStatus"][1]["jobId"], "task_segment_2")
        fake_client.get_job.assert_not_called()

    def test_in_memory_progress_marks_missing_asset_record_deleted(self) -> None:
        video_path = self.root / "用户生成结果" / "video" / "memory-deleted.mp4"
        body = {
            "generationProgress": {
                "items": [
                    {
                        "videoIndex": 6,
                        "title": "发布进入倒计时的冲刺感 · 片段 2",
                        "status": "succeeded",
                        "statusLabel": "已生成",
                        "jobId": "merge2-task_segment_1-task_segment_2",
                        "assetRecord": {
                            "jobId": "merge2-task_segment_1-task_segment_2",
                            "archiveStatus": "archived",
                            "archiveLocalPath": str(video_path),
                        },
                    }
                ],
                "succeededCount": 1,
                "failedCount": 0,
                "skippedCount": 0,
            }
        }

        ai8video_web._apply_deleted_asset_progress_state(body)

        progress = body["generationProgress"]
        self.assertEqual(progress["succeededCount"], 0)
        self.assertEqual(progress["deletedCount"], 1)
        self.assertEqual(progress["items"][0]["status"], "deleted")
        self.assertFalse(progress["items"][0]["hasLocalAsset"])

    def test_in_memory_progress_marks_archived_local_asset_completed(self) -> None:
        video_path = self.root / "用户生成结果" / "video" / "memory-done.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"video")
        body = {
            "status": "pending",
            "phase": "postprocessing",
            "statusLabel": "后台处理中",
            "generationProgress": {
                "status": "active",
                "items": [
                    {
                        "videoIndex": 1,
                        "title": "沟通的鸿沟 · 片段 2",
                        "status": "archiving",
                        "statusLabel": "后台处理中",
                        "jobId": "task-segment-2",
                        "assetRecord": {
                            "videoTitle": "沟通的鸿沟",
                            "jobId": "merge2-task-segment-1-task-segment-2",
                            "archiveStatus": "archived",
                            "archiveUrl": "video/memory-done.mp4",
                            "archiveLocalPath": str(video_path),
                        },
                    }
                ],
                "runningCount": 1,
                "postProcessingCount": 1,
                "succeededCount": 0,
                "failedCount": 0,
                "deletedCount": 0,
                "skippedCount": 0,
            },
        }

        ai8video_web._apply_deleted_asset_progress_state(body)

        progress = body["generationProgress"]
        item = progress["items"][0]
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["phase"], "completed")
        self.assertEqual(body["statusLabel"], "视频已生成")
        self.assertEqual(progress["status"], "completed")
        self.assertEqual(progress["runningCount"], 0)
        self.assertEqual(progress["postProcessingCount"], 0)
        self.assertEqual(progress["succeededCount"], 1)
        self.assertEqual(item["status"], "succeeded")
        self.assertEqual(item["statusLabel"], "已生成")
        self.assertEqual(item["title"], "沟通的鸿沟")
        self.assertEqual(item["jobId"], "merge2-task-segment-1-task-segment-2")
        self.assertTrue(item["hasLocalAsset"])

    def test_api_delete_user_generated_result_rejects_outside_path(self) -> None:
        generated_root = self.root / "用户生成结果"
        outside = self.root / "outside.mp4"
        outside.write_text("outside", encoding="utf-8")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"userGeneratedKey": "../outside.mp4"},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "USER_GENERATED_RESULT_ROOT", generated_root.resolve()), patch.object(
                ai8video_web,
                "ensure_user_generated_result_dir",
                return_value=generated_root.resolve(),
            ):
                body = ai8video_web.api_delete_user_generated_result()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 400)
        self.assertFalse(body["ok"])
        self.assertTrue(outside.exists())

    def test_api_delete_user_generated_result_rejects_non_video_file(self) -> None:
        generated_root = self.root / "用户生成结果"
        image_rel = Path("cover/demo.jpg")
        image_path = generated_root / image_rel
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_text("cover", encoding="utf-8")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"userGeneratedKey": image_rel.as_posix()},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "USER_GENERATED_RESULT_ROOT", generated_root.resolve()), patch.object(
                ai8video_web,
                "ensure_user_generated_result_dir",
                return_value=generated_root.resolve(),
            ):
                body = ai8video_web.api_delete_user_generated_result()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 400)
        self.assertFalse(body["ok"])
        self.assertTrue(image_path.exists())

    def test_api_background_music_status_empty(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="GET")
        try:
            body = ai8video_web.api_background_music()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["ok"])
        self.assertFalse(body["enabled"])
        self.assertEqual(body["name"], "")
        self.assertEqual(body["volumePercent"], 28)
        self.assertTrue(body["preserveOriginalAudio"])

    def test_api_background_music_volume_updates_setting(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="POST", json={"volume": 45})
        try:
            body = ai8video_web.api_background_music_volume()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["ok"])
        self.assertEqual(body["volume"], 0.45)
        self.assertEqual(body["volumePercent"], 45)

    def test_api_background_music_original_audio_updates_setting(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="POST", json={"preserveOriginalAudio": False})
        try:
            body = ai8video_web.api_background_music_original_audio()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["ok"])
        self.assertFalse(body["preserveOriginalAudio"])

    def test_api_background_music_upload_keeps_single_library_file(self) -> None:
        class _FakeUpload:
            filename = "theme.mp3"

            def save(self, target: str, overwrite: bool = False) -> None:
                Path(target).write_bytes(b"mp3-data")

        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            files=SimpleNamespace(get=lambda key: _FakeUpload()),
        )
        try:
            body = ai8video_web.api_background_music()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["ok"])
        self.assertTrue(body["enabled"])
        self.assertEqual(body["name"], "theme.mp3")
        self.assertFalse((self.root / "background_music" / "current.mp3").exists())
        library_files = list((self.root / "background_music" / "素材库").glob("*.mp3"))
        self.assertEqual(len(library_files), 1)
        self.assertEqual(library_files[0].read_bytes(), b"mp3-data")

    def test_api_background_music_upload_preserves_unicode_raw_filename(self) -> None:
        class _FakeUpload:
            raw_filename = "AI8主题.mp3"
            filename = "mp3"

            def save(self, target: str, overwrite: bool = False) -> None:
                Path(target).write_bytes(b"mp3-data")

        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            files=SimpleNamespace(get=lambda key: _FakeUpload()),
        )
        try:
            body = ai8video_web.api_background_music()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["ok"])
        self.assertEqual(body["name"], "AI8主题.mp3")
        self.assertEqual(body["sourceName"], "AI8主题.mp3")

    def test_api_background_music_upload_video_keeps_source_without_current_copy(self) -> None:
        class _FakeUpload:
            filename = "theme.mp4"

            def save(self, target: str, overwrite: bool = False) -> None:
                Path(target).write_bytes(b"video-data")

        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            files=SimpleNamespace(get=lambda key: _FakeUpload()),
        )
        try:
            with patch(
                "ai8video.media.background_music.extract_background_music_from_video",
            ) as extract:
                body = ai8video_web.api_background_music()
        finally:
            ai8video_web.request = request_backup

        extract.assert_not_called()
        self.assertTrue(body["ok"])
        self.assertTrue(body["enabled"])
        self.assertEqual(body["name"], "theme.mp4")
        self.assertEqual(body["sourceType"], "video")
        self.assertEqual(body["sourceName"], "theme.mp4")
        source_files = list((self.root / "background_music" / "source").glob("*.mp4"))
        self.assertEqual(len(source_files), 1)
        self.assertEqual(source_files[0].read_bytes(), b"video-data")
        self.assertFalse((self.root / "background_music" / "current.mp3").exists())
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["selectedId"], body["items"][0]["id"])

    def test_api_background_music_select_existing_item(self) -> None:
        music_root = self.root / "background_music"
        library = music_root / "素材库"
        library.mkdir(parents=True, exist_ok=True)
        first = library / "first.mp3"
        second = library / "second.mp3"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        (music_root / "items.json").write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "id": "first",
                            "name": "first.mp3",
                            "sourceName": "first.mp3",
                            "sourceType": "audio",
                            "path": str(first),
                            "sizeBytes": first.stat().st_size,
                            "updatedAt": "2026-06-14T00:00:00+00:00",
                        },
                        {
                            "id": "second",
                            "name": "second.mp3",
                            "sourceName": "second.mp3",
                            "sourceType": "audio",
                            "path": str(second),
                            "sizeBytes": second.stat().st_size,
                            "updatedAt": "2026-06-14T00:01:00+00:00",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="POST", json={"id": "second"})
        try:
            body = ai8video_web.api_background_music_select()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["ok"])
        self.assertTrue(body["enabled"])
        self.assertEqual(body["selectedId"], "second")
        self.assertFalse((music_root / "current.mp3").exists())
        selected = [item for item in body["items"] if item["selected"]]
        self.assertEqual([item["id"] for item in selected], ["second"])

    def test_api_background_music_clear_selection(self) -> None:
        music_root = self.root / "background_music"
        library = music_root / "素材库"
        library.mkdir(parents=True, exist_ok=True)
        music = library / "theme.mp3"
        music.write_bytes(b"theme")
        current = music_root / "current.mp3"
        current.write_bytes(b"theme")
        (music_root / "items.json").write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "id": "theme",
                            "name": "theme.mp3",
                            "sourceName": "theme.mp3",
                            "sourceType": "audio",
                            "path": str(music),
                            "sizeBytes": music.stat().st_size,
                            "updatedAt": "2026-06-14T00:00:00+00:00",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (music_root / "current.json").write_text(
            json.dumps({"selectedId": "theme", "name": "theme.mp3"}, ensure_ascii=False),
            encoding="utf-8",
        )

        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="POST")
        try:
            body = ai8video_web.api_background_music_clear()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["ok"])
        self.assertFalse(body["enabled"])
        self.assertEqual(body["selectedId"], "")
        self.assertFalse(current.exists())
        self.assertEqual([item["name"] for item in body["items"]], ["theme.mp3"])

    def test_api_open_background_music_folder(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="POST")
        try:
            with patch.object(ai8video_web, "_open_in_file_manager") as open_dir:
                body = ai8video_web.api_open_background_music_folder()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["ok"])
        expected = (self.root / "background_music").resolve()
        self.assertEqual(Path(body["path"]), expected)
        open_dir.assert_called_once_with(expected)

    def test_api_upload_user_material_preserves_unicode_raw_filename(self) -> None:
        image_dir = self.root / "user_materials" / "图片素材库"
        script_dir = self.root / "user_materials" / "剧本素材库"

        class _FakeUpload:
            raw_filename = "AI8.png"
            filename = "png"

            def save(self, target: str, overwrite: bool = False) -> None:
                Path(target).write_bytes(b"png-data")

        def fake_material_dir(kind: str):
            return script_dir if str(kind or "").strip().lower() == "script" else image_dir

        def fake_ensure_dirs() -> None:
            image_dir.mkdir(parents=True, exist_ok=True)
            script_dir.mkdir(parents=True, exist_ok=True)

        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            forms={"kind": "image"},
            files=SimpleNamespace(getall=lambda key: [_FakeUpload()]),
        )
        try:
            with patch.object(ai8video_web, "material_dir", side_effect=fake_material_dir), patch.object(
                ai8video_web, "ensure_user_material_dirs", side_effect=fake_ensure_dirs
            ):
                body = ai8video_web.api_upload_user_material()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "image")
        self.assertEqual(body["saved"][0]["name"], "AI8.png")
        self.assertEqual(body["saved"][0]["relativePath"], "AI8.png")
        self.assertTrue((image_dir / "AI8.png").is_file())
        self.assertEqual((image_dir / "AI8.png").read_bytes(), b"png-data")

    def test_api_script_knowledge_forwards_query_and_limit(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            environ={"QUERY_STRING": "q=%E7%A7%81%E5%9F%9F&limit=20"},
            query={"q": "私域", "limit": "20"},
        )
        expected = {"ok": True, "items": [{"id": 1, "title": "私域脚本"}]}
        try:
            with patch.object(
                ai8video_web,
                "script_knowledge_payload",
                return_value=expected,
            ) as payload:
                body = ai8video_web.api_script_knowledge()
        finally:
            ai8video_web.request = request_backup

        payload.assert_called_once_with("私域", limit=20)
        self.assertEqual(body, expected)

    def test_api_script_knowledge_document_updates_metadata(self) -> None:
        store = Mock()
        store.update_document.return_value = {"id": 7, "title": "发布脚本", "tags": ["发布"]}
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"title": "发布脚本", "summary": "预热", "tags": ["发布"]},
        )
        try:
            with patch.object(ai8video_web, "get_script_knowledge_store", return_value=store):
                body = ai8video_web.api_script_knowledge_document(7)
        finally:
            ai8video_web.request = request_backup

        store.update_document.assert_called_once_with(
            7,
            title="发布脚本",
            summary="预热",
            tags=["发布"],
        )
        self.assertTrue(body["ok"])
        self.assertEqual(body["document"]["id"], 7)

    def test_api_upload_user_material_saves_flower_watermark_separately(self) -> None:
        image_dir = self.root / "user_materials" / "图片素材库"
        script_dir = self.root / "user_materials" / "剧本素材库"
        watermark_dir = self.root / "user_materials" / "花字水印库"
        stale = watermark_dir / "旧水印.png"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"old")

        class _FakeUpload:
            raw_filename = "水印.png"
            filename = "png"

            def save(self, target: str, overwrite: bool = False) -> None:
                Path(target).write_bytes(b"watermark-data")

        def fake_material_dir(kind: str):
            normalized = str(kind or "").strip().lower()
            if normalized == "script":
                return script_dir
            if normalized == "flower-watermark":
                return watermark_dir
            return image_dir

        def fake_ensure_dirs() -> None:
            image_dir.mkdir(parents=True, exist_ok=True)
            script_dir.mkdir(parents=True, exist_ok=True)
            watermark_dir.mkdir(parents=True, exist_ok=True)

        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            forms={"kind": "flower-watermark"},
            files=SimpleNamespace(getall=lambda key: [_FakeUpload()]),
        )
        try:
            with patch.object(ai8video_web, "material_dir", side_effect=fake_material_dir), patch.object(
                ai8video_web, "ensure_user_material_dirs", side_effect=fake_ensure_dirs
            ):
                body = ai8video_web.api_upload_user_material()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "flower-watermark")
        self.assertEqual(body["saved"][0]["name"], "水印.png")
        self.assertEqual(body["saved"][0]["relativePath"], "水印.png")
        self.assertFalse((image_dir / "水印.png").exists())
        self.assertTrue(stale.exists())
        self.assertEqual(stale.read_bytes(), b"old")
        self.assertTrue((watermark_dir / "水印.png").is_file())
        self.assertEqual((watermark_dir / "水印.png").read_bytes(), b"watermark-data")

    def test_delete_user_material_removes_real_file_from_library(self) -> None:
        material_root = self.root / "user_materials"
        image_dir = material_root / "图片素材库"
        script_dir = material_root / "剧本素材库"
        target = image_dir / "AI8.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png-data")

        with patch.object(ai8video_user_materials, "USER_MATERIAL_ROOT", material_root.resolve()), patch.object(
            ai8video_user_materials,
            "USER_IMAGE_MATERIAL_DIR",
            image_dir.resolve(),
        ), patch.object(
            ai8video_user_materials,
            "USER_SCRIPT_MATERIAL_DIR",
            script_dir.resolve(),
        ), patch.object(
            ai8video_user_materials,
            "ensure_user_file_root",
            return_value=material_root.parent.resolve(),
        ):
            body = ai8video_user_materials.delete_user_material("image", "AI8.png")

        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "image")
        self.assertEqual(body["deleted"]["name"], "AI8.png")
        self.assertEqual(body["deleted"]["relativePath"], "AI8.png")
        self.assertFalse(target.exists())

    def test_api_delete_user_material_forwards_kind_and_path(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"kind": "script", "relativePath": "片段/demo.md"},
        )
        try:
            with patch.object(
                ai8video_web,
                "delete_user_material",
                return_value={
                    "ok": True,
                    "kind": "script",
                    "deleted": {"name": "demo.md", "relativePath": "片段/demo.md"},
                },
            ) as delete_material:
                body = ai8video_web.api_delete_user_material()
        finally:
            ai8video_web.request = request_backup

        delete_material.assert_called_once_with("script", "片段/demo.md")
        self.assertTrue(body["ok"])
        self.assertEqual(body["deleted"]["relativePath"], "片段/demo.md")

    def test_api_background_music_rejects_non_mp3(self) -> None:
        class _FakeUpload:
            filename = "theme.wav"

            def save(self, target: str, overwrite: bool = False) -> None:
                Path(target).write_bytes(b"wav")

        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            files=SimpleNamespace(get=lambda key: _FakeUpload()),
        )
        ai8video_web.response = fake_response
        try:
            body = ai8video_web.api_background_music()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("MP3", body["error"])

    def test_api_open_batch_supervisor_lock_opens_file_when_present(self) -> None:
        lock_path = self.root / "batch_supervisor.lock"
        lock_path.write_text("locked", encoding="utf-8")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "_open_path") as open_path:
                body = ai8video_web.api_open_batch_supervisor_lock()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_path.assert_called_once_with(lock_path.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "file")

    def test_api_open_batch_supervisor_lock_opens_parent_dir_when_file_missing(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "_open_in_file_manager") as open_dir:
                body = ai8video_web.api_open_batch_supervisor_lock()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_dir.assert_called_once_with((self.root).resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "directory")

    def test_api_open_batch_supervisor_deployment_opens_file_when_present(self) -> None:
        deployment_path = self.root / "com.ai8.video.supervisor.plist"
        deployment_path.write_text("<plist/>", encoding="utf-8")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "_open_path") as open_path:
                body = ai8video_web.api_open_batch_supervisor_deployment()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_path.assert_called_once_with(deployment_path.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "file")

    def test_api_open_batch_supervisor_deployment_opens_parent_dir_when_file_missing(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "_open_in_file_manager") as open_dir:
                body = ai8video_web.api_open_batch_supervisor_deployment()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_dir.assert_called_once_with((self.root).resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "directory")

    def test_api_open_batch_seed_file_opens_file_when_present(self) -> None:
        seed_path = self.root / "batch_supervisor" / "seed_messages.txt"
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        seed_path.write_text("老板讲封号风险\n", encoding="utf-8")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "_open_path") as open_path:
                body = ai8video_web.api_open_batch_seed_file()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_path.assert_called_once_with(seed_path.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "file")

    def test_api_open_batch_seed_file_opens_parent_dir_when_file_missing(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(ai8video_web, "_open_in_file_manager") as open_dir:
                body = ai8video_web.api_open_batch_seed_file()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_dir.assert_called_once_with((self.root / "batch_supervisor").resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "directory")

    def test_api_build_batch_seed_file_returns_payload(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        try:
            with patch.object(
                ai8video_web,
                "build_batch_seed_file_payload",
                return_value={"path": str(self.root / "batch_supervisor" / "seed_messages.txt"), "lineCount": 3},
            ) as build_seed:
                body = ai8video_web.api_build_batch_seed_file()
        finally:
            ai8video_web.request = request_backup

        build_seed.assert_called_once_with(report_limit=8, max_messages=40, refresh=True)
        self.assertEqual(body["lineCount"], 3)

    def test_api_build_batch_seed_file_returns_bad_request_on_value_error(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(method="POST", json={})
        ai8video_web.response = fake_response
        try:
            with patch.object(
                ai8video_web,
                "build_batch_seed_file_payload",
                side_effect=ValueError("最近日报里还没有可用候选内容"),
            ):
                body = ai8video_web.api_build_batch_seed_file()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 400)
        self.assertEqual(body["error"], "最近日报里还没有可用候选内容")

    def test_api_write_batch_supervisor_deployment_auto_builds_seed_and_returns_status(self) -> None:
        seed_path = self.root / "batch_supervisor" / "seed_messages.txt"
        admin_state_path = self.root / "batch_supervisor_admin_state.json"

        def _build_seed(*, report_limit: int, max_messages: int, refresh: bool) -> dict:
            seed_path.parent.mkdir(parents=True, exist_ok=True)
            seed_path.write_text("老板讲封号风险\n", encoding="utf-8")
            return {"path": str(seed_path), "lineCount": 1}

        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"scheduleTimes": "09:00,13:15", "targetPassCount": "5", "styleHint": "商务"},
        )
        try:
            with patch.object(ai8video_web, "build_batch_seed_file_payload", side_effect=_build_seed) as build_seed, patch.object(
                ai8video_web,
                "build_launchd_plist",
                return_value={"ProgramArguments": []},
            ) as build_plist, patch.object(
                ai8video_web,
                "write_launchd_plist",
                return_value=self.root / "com.ai8.video.supervisor.plist",
            ) as write_plist, patch.object(
                ai8video_web,
                "inspect_launchd_deployment",
                return_value={"exists": True, "loaded": False, "scheduleTimes": ["09:00", "13:15"]},
            ):
                body = ai8video_web.api_write_batch_supervisor_deployment()
        finally:
            ai8video_web.request = request_backup

        build_seed.assert_called_once_with(report_limit=8, max_messages=40, refresh=True)
        build_plist.assert_called_once()
        write_plist.assert_called_once()
        self.assertTrue(body["ok"])
        self.assertEqual(body["action"], "write")
        self.assertEqual(body["seedFile"], str(seed_path.resolve()))
        self.assertEqual(body["adminResult"]["action"], "write")
        saved = json.loads(admin_state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["action"], "write")
        self.assertEqual(saved["seedFile"], str(seed_path.resolve()))
        self.assertEqual(
            Path(saved["path"]).resolve(),
            (self.root / "com.ai8.video.supervisor.plist").resolve(),
        )

    def test_api_install_batch_supervisor_deployment_returns_bad_request_when_seed_missing(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"scheduleTimes": "09:00", "autoBuildSeedFile": False},
        )
        ai8video_web.response = fake_response
        try:
            body = ai8video_web.api_install_batch_supervisor_deployment()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 400)
        self.assertEqual(body["error"], "值守种子文件还没生成，请先生成种子文件。")

    def test_api_install_batch_supervisor_deployment_writes_admin_state(self) -> None:
        seed_path = self.root / "batch_supervisor" / "seed_messages.txt"
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        seed_path.write_text("老板讲封号风险\n", encoding="utf-8")
        admin_state_path = self.root / "batch_supervisor_admin_state.json"
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"scheduleTimes": "09:00", "autoBuildSeedFile": False},
        )
        try:
            with patch.object(
                ai8video_web,
                "build_launchd_plist",
                return_value={"ProgramArguments": []},
            ), patch.object(
                ai8video_web,
                "write_launchd_plist",
                return_value=self.root / "com.ai8.video.supervisor.plist",
            ), patch.object(
                ai8video_web,
                "install_launchd_service",
                return_value={"exists": True, "loaded": True, "scheduleTimes": ["09:00"]},
            ):
                body = ai8video_web.api_install_batch_supervisor_deployment()
        finally:
            ai8video_web.request = request_backup

        self.assertTrue(body["ok"])
        self.assertEqual(body["action"], "install")
        self.assertEqual(body["adminResult"]["action"], "install")
        saved = json.loads(admin_state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["action"], "install")
        self.assertTrue(saved["loaded"])
        self.assertEqual(saved["seedFile"], str(seed_path.resolve()))

    def test_api_uninstall_batch_supervisor_deployment_returns_status(self) -> None:
        admin_state_path = self.root / "batch_supervisor_admin_state.json"
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"keepPlist": True},
        )
        try:
            with patch.object(
                ai8video_web,
                "uninstall_launchd_service",
                return_value={"exists": False, "loaded": False, "removed": True},
            ) as uninstall_service:
                body = ai8video_web.api_uninstall_batch_supervisor_deployment()
        finally:
            ai8video_web.request = request_backup

        uninstall_service.assert_called_once()
        self.assertTrue(body["ok"])
        self.assertEqual(body["action"], "uninstall")
        self.assertTrue(body["keepPlist"])
        self.assertEqual(body["adminResult"]["action"], "uninstall")
        saved = json.loads(admin_state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["action"], "uninstall")
        self.assertTrue(saved["keepPlist"])

    def test_api_health_returns_supervisor_admin_result(self) -> None:
        admin_state_path = self.root / "batch_supervisor_admin_state.json"
        admin_state_path.write_text(
            json.dumps({
                "action": "install",
                "savedAt": "2026-06-13T08:00:00+08:00",
                "path": str((self.root / "com.ai8.video.supervisor.plist").resolve()),
                "seedFile": str((self.root / "batch_supervisor" / "seed_messages.txt").resolve()),
                "deployment": {"exists": True, "loaded": True},
                "exists": True,
                "loaded": True,
                "keepPlist": False,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="GET")
        try:
            body = ai8video_web.api_health()
        finally:
            ai8video_web.request = request_backup

        self.assertEqual(body["chatBackend"], "ai8video-runtime")
        self.assertEqual(body["batchSupervisorAdminResult"]["action"], "install")
        self.assertTrue(body["batchSupervisorAdminResult"]["loaded"])

    def test_api_health_refreshes_runtime_config(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(method="GET")
        try:
            with patch.object(
                ai8video_web,
                "get_health_payload",
                return_value={"ok": True, "dryRun": False},
            ) as health_payload:
                body = ai8video_web.api_health()
        finally:
            ai8video_web.request = request_backup

        health_payload.assert_called_once_with(refresh=True)
        self.assertEqual(body["chatBackend"], "ai8video-runtime")

    def test_api_chat_status_requires_session_id(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(get=lambda key, default="": ""),
        )
        ai8video_web.response = fake_response
        try:
            body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 400)
        self.assertEqual(body["error"], "sessionId is required")

    def test_api_chat_status_returns_ai8video_snapshot(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(get=lambda key, default="": "session-a"),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "pending", "sessionId": "session-a", "elapsedSeconds": 42},
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["sessionId"], "session-a")
        self.assertEqual(body["elapsedSeconds"], 42)

    def test_api_chat_status_rejects_unknown_generation_batch_id(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        query_values = {
            "sessionId": "session-a",
            "generationBatchId": "gb-missing-batch",
        }
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(get=lambda key, default="": query_values.get(key, default)),
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={
                    "status": "not_found",
                    "phase": "unknown_generation_batch",
                    "sessionId": "session-a",
                    "generationBatchId": "gb-missing-batch",
                },
            ) as chat_status:
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        chat_status.assert_called_once_with(
            session_id="session-a",
            generation_batch_id="gb-missing-batch",
        )
        self.assertEqual(fake_response.status, 404)
        self.assertEqual(body["status"], "not_found")
        self.assertEqual(body["generationBatchId"], "gb-missing-batch")

    def test_api_chat_status_settles_stale_unsubmitted_planning_progress(self) -> None:
        request_backup = ai8video_web.request
        query = SimpleNamespace(get=lambda key, default="": {"sessionId": "session-stale"}.get(key, default))
        ai8video_web.request = SimpleNamespace(method="GET", query=query)
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={
                    "status": "pending",
                    "phase": "planning",
                    "sessionId": "session-stale",
                    "pendingSince": "2020-01-01T00:00:00+00:00",
                    "generationProgress": {
                        "status": "planning",
                        "updatedAt": "2020-01-01T00:00:00+00:00",
                        "totalRequested": 1,
                        "submittedCount": 0,
                        "runningCount": 1,
                        "waitingCount": 1,
                        "succeededCount": 0,
                        "failedCount": 0,
                        "items": [
                            {"videoIndex": 1, "title": "视频 1", "status": "planning", "jobId": None},
                        ],
                    },
                },
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        self.assertEqual(body["status"], "failed")
        self.assertTrue(body["stalePlanningRecovered"])
        self.assertEqual(body["generationProgress"]["runningCount"], 0)
        self.assertEqual(body["generationProgress"]["failedCount"], 1)
        self.assertEqual(body["generationProgress"]["items"][0]["statusLabel"], "生成失败")

    def test_api_chat_status_can_refresh_stateless_video_jobs(self) -> None:
        class FakeVideoClient:
            def get_job(self, job_id, video_index=1):
                if job_id == "job-done":
                    return SimpleNamespace(
                        status="succeeded",
                        provider_status="completed",
                        provider_progress=100,
                        video_url="https://example.com/done.mp4",
                        error=None,
                    )
                return SimpleNamespace(
                    status="failed",
                    provider_status="failed",
                    provider_progress=100,
                    video_url=None,
                    error="upstream failed",
                )

        jobs = json.dumps([
            {"videoIndex": 1, "title": "视频 1", "jobId": "job-done"},
            {"videoIndex": 2, "title": "视频 2", "jobId": "job-failed"},
        ])
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-a",
                    "jobs": jobs,
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-a"},
            ), patch.object(
                ai8video_web,
                "AI8VideoModelClient",
                return_value=FakeVideoClient(),
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["statusLabel"], "后台处理中")
        self.assertEqual(progress["status"], "active")
        self.assertEqual(progress["succeededCount"], 0)
        self.assertEqual(progress["failedCount"], 1)
        self.assertEqual(progress["runningCount"], 1)
        self.assertEqual(progress["postProcessingCount"], 1)
        self.assertEqual(progress["items"][0]["status"], "archiving")
        self.assertEqual(progress["items"][0]["statusLabel"], "后台处理中")
        self.assertEqual(progress["items"][1]["status"], "failed")

    def test_api_chat_status_uses_trace_video_job_created_over_first_frame_error(self) -> None:
        class FakeVideoClient:
            def get_job(self, job_id, video_index=1):
                self.last_job_id = job_id
                return SimpleNamespace(
                    status="succeeded",
                    provider_status="completed",
                    provider_progress=100,
                    video_url="https://example.com/generated.mp4",
                    error=None,
                )

        trace_path = self.root / "prompt_traces.jsonl"
        now = datetime.now(timezone.utc).replace(microsecond=0)
        trace_path.write_text(
            "\n".join([
                json.dumps({
                    "createdAt": now.isoformat(),
                    "event": "merged_final_video_prompt",
                    "sessionId": "session-video-created",
                    "payload": {"videoIndex": 1, "title": "第一条"},
                }, ensure_ascii=False),
                json.dumps({
                    "createdAt": now.isoformat(),
                    "event": "first_frame_image_error",
                    "sessionId": "session-video-created",
                    "payload": {
                        "videoIndex": 1,
                        "error": "status_code=400, invalid image base64 data",
                    },
                }, ensure_ascii=False),
                json.dumps({
                    "createdAt": now.isoformat(),
                    "event": "video_job_created",
                    "sessionId": "session-video-created",
                    "payload": {
                        "videoIndex": 1,
                        "title": "第一条",
                        "jobId": "task-real-video",
                        "status": "pending",
                    },
                }, ensure_ascii=False),
            ]) + "\n",
            encoding="utf-8",
        )
        fake_client = FakeVideoClient()
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-video-created",
                    "videoCount": "1",
                    "pendingSince": now.isoformat(),
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-video-created"},
            ), patch.object(
                ai8video_web,
                "PROMPT_TRACE_PATH",
                trace_path,
            ), patch.object(
                ai8video_web,
                "AI8VideoModelClient",
                return_value=fake_client,
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        self.assertEqual(fake_client.last_job_id, "task-real-video")
        self.assertTrue(body["traceRecovered"])
        self.assertEqual(body["status"], "pending")
        self.assertEqual(progress["failedCount"], 0)
        self.assertEqual(progress["runningCount"], 1)
        self.assertEqual(progress["items"][0]["jobId"], "task-real-video")
        self.assertEqual(progress["items"][0]["status"], "archiving")
        self.assertNotIn("首帧图", progress["items"][0].get("statusLabel") or "")

    def test_api_chat_status_treats_merge_failed_placeholder_as_local_failure(self) -> None:
        class FakeVideoClient:
            def get_job(self, job_id, video_index=1):
                raise RuntimeError("task_not_exist")

        jobs = json.dumps([
            {"videoIndex": 1, "title": "视频 1", "jobId": "merge2-failed-1"},
        ])
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-local-failed",
                    "jobs": jobs,
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-local-failed"},
            ), patch.object(
                ai8video_web,
                "AI8VideoModelClient",
                return_value=FakeVideoClient(),
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        self.assertEqual(body["status"], "failed")
        self.assertEqual(progress["failedCount"], 1)
        self.assertEqual(progress["items"][0]["status"], "failed")
        self.assertEqual(progress["items"][0]["providerStatus"], "local_failed")
        self.assertIn("视频合成失败", progress["items"][0]["error"])

    def test_api_chat_status_restores_local_session_terminal_records(self) -> None:
        video_path = self.root / "用户生成结果" / "video" / "done.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"video")
        (self.root / "assets.jsonl").write_text(
            json.dumps(
                {
                    "createdAt": "2026-06-20T02:05:47+00:00",
                    "videoIndex": 1,
                    "videoTitle": "连接世界的新时代",
                    "jobId": "merge2-task_done_1-task_done_2",
                    "status": "succeeded",
                    "videoUrl": None,
                    "archiveStatus": "archived",
                    "archiveBackend": "local",
                    "archiveKey": "video/done.mp4",
                    "archiveUrl": "video/done.mp4",
                    "archiveLocalPath": str(video_path),
                    "generationMeta": {
                        "mergeMode": "merge2",
                        "segmentRecords": [
                            {
                                "role": "segment1",
                                "jobId": "task_done_1",
                                "tailFramePath": "/tmp/视频合并/s-local-terminal/01-segment-1-tail.png",
                            }
                        ],
                    },
                    "request": {"videoCount": 3},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        fake_client = SimpleNamespace(get_job=Mock(side_effect=RuntimeError("should not query local placeholder")))
        with patch.object(
            ai8video_web,
            "list_failed_video_tasks",
            return_value={
                "items": [
                    {
                        "createdAt": "2026-06-20T02:06:22+00:00",
                        "videoIndex": 2,
                        "videoTitle": "一个APP解决五大痛点",
                        "jobId": "merge2-failed-2",
                        "reason": "raw upstream reason",
                        "displayReason": "内容审核未通过，请换成非真人或非写实主体后重试。",
                        "meta": {"progressSessionId": "s-local-terminal"},
                    }
                ]
            },
        ), patch.object(
            ai8video_web,
            "AI8VideoModelClient",
            return_value=fake_client,
        ):
            body = ai8video_web._query_video_jobs_progress(
                "s-local-terminal",
                [{"videoIndex": 1, "jobId": "merge2-failed-1"}],
                pending_since=datetime(2026, 6, 20, 2, 0, tzinfo=timezone.utc),
            )

        progress = body["generationProgress"]
        self.assertEqual(body["status"], "completed_with_error")
        self.assertEqual(progress["totalRequested"], 3)
        self.assertEqual(progress["succeededCount"], 1)
        self.assertEqual(progress["failedCount"], 1)
        self.assertEqual(progress["skippedCount"], 1)
        self.assertEqual(
            [item["status"] for item in progress["items"]],
            ["succeeded", "failed", "skipped"],
        )
        self.assertEqual(progress["items"][1]["providerStatus"], "local_failed")
        self.assertIn("内容审核未通过", progress["items"][1]["error"])
        self.assertEqual(progress["items"][2]["statusLabel"], "未继续生成")
        self.assertIn("内容审核未通过", progress["items"][2]["error"])
        fake_client.get_job.assert_not_called()

    def test_api_chat_status_humanizes_model_duration_limit(self) -> None:
        class FakeVideoClient:
            def get_job(self, job_id, video_index=1):
                return SimpleNamespace(
                    status="failed",
                    provider_status="failed",
                    provider_progress=100,
                    video_url="",
                    error=(
                        'video submit failed: 400 {"error_code":"bad_request",'
                        '"message":"Only [4, 6, 8] seconds durations are supported for this model."}'
                    ),
                )

        with patch.object(
            ai8video_web,
            "AI8VideoModelClient",
            return_value=FakeVideoClient(),
        ):
            body = ai8video_web._query_video_jobs_progress(
                "session-duration-limit",
                [{"videoIndex": 1, "jobId": "task-duration-limit"}],
                video_count=1,
            )

        progress = body["generationProgress"]
        self.assertEqual(body["status"], "failed")
        self.assertEqual(progress["failedCount"], 1)
        self.assertEqual(
            progress["items"][0]["error"],
            "当前模型只支持 4、6 或 8 秒，请把视频时长改成支持的秒数后重试。",
        )

    def test_api_chat_status_humanizes_failed_asset_record_reason(self) -> None:
        (self.root / "assets.jsonl").write_text(
            json.dumps(
                {
                    "createdAt": "2026-06-20T02:05:47+00:00",
                    "videoIndex": 1,
                    "videoTitle": "审核失败视频",
                    "jobId": "merge2-task-ok-task-review-failed",
                    "status": "failed",
                    "archiveStatus": "failed",
                    "archiveError": (
                        "This request didn't pass content review "
                        "(e.g. an identifiable real person, unsafe content, or protected IP)."
                    ),
                    "generationMeta": {
                        "segmentRecords": [
                            {"role": "segment2", "jobId": "task-review-failed"},
                        ],
                    },
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        fake_client = SimpleNamespace(get_job=Mock(side_effect=RuntimeError("should not query failed asset record")))
        with patch.object(
            ai8video_web,
            "AI8VideoModelClient",
            return_value=fake_client,
        ):
            body = ai8video_web._query_video_jobs_progress(
                "session-review-failed",
                [{"videoIndex": 1, "jobId": "task-review-failed"}],
                video_count=1,
            )

        progress = body["generationProgress"]
        self.assertEqual(body["status"], "failed")
        self.assertEqual(progress["failedCount"], 1)
        self.assertEqual(progress["items"][0]["providerProgress"], 100)
        self.assertEqual(progress["items"][0]["error"], "内容审核未通过，请换图或改成非真人风格后重试。")
        fake_client.get_job.assert_not_called()

    def test_api_chat_status_stateless_terminal_counts_do_not_depend_on_two_items(self) -> None:
        class FakeVideoClient:
            def get_job(self, job_id, video_index=1):
                if job_id == "job-failed":
                    return SimpleNamespace(
                        status="failed",
                        provider_status="failed",
                        provider_progress=100,
                        video_url=None,
                        error="upstream failed",
                    )
                return SimpleNamespace(
                    status="succeeded",
                    provider_status="completed",
                    provider_progress=100,
                    video_url=f"https://example.com/{job_id}.mp4",
                    error=None,
                )

        jobs = json.dumps([
            {"videoIndex": 1, "title": "视频 1", "jobId": "job-done-1"},
            {"videoIndex": 2, "title": "视频 2", "jobId": "job-failed"},
            {"videoIndex": 3, "title": "视频 3", "jobId": "job-done-3"},
            {"videoIndex": 4, "title": "视频 4", "jobId": "job-done-4"},
        ])
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-many",
                    "jobs": jobs,
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-many"},
            ), patch.object(
                ai8video_web,
                "AI8VideoModelClient",
                return_value=FakeVideoClient(),
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        self.assertEqual(body["status"], "pending")
        self.assertEqual(progress["totalRequested"], 4)
        self.assertEqual(progress["succeededCount"], 0)
        self.assertEqual(progress["failedCount"], 1)
        self.assertEqual(progress["runningCount"], 3)
        self.assertEqual(progress["postProcessingCount"], 3)
        self.assertEqual(
            [item["status"] for item in progress["items"]],
            ["archiving", "failed", "archiving", "archiving"],
        )

    def test_api_chat_status_stateless_counts_archived_local_asset_as_generated(self) -> None:
        class FakeVideoClient:
            def get_job(self, job_id, video_index=1):
                return SimpleNamespace(
                    status="succeeded",
                    provider_status="completed",
                    provider_progress=100,
                    video_url="https://example.com/done.mp4",
                    error=None,
                )

        video_path = self.root / "用户生成结果" / "video" / "done.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"video")
        (self.root / "assets.jsonl").write_text(
            json.dumps(
                {
                    "videoIndex": 1,
                    "videoTitle": "已归档视频",
                    "jobId": "job-done",
                    "status": "succeeded",
                    "videoUrl": "https://example.com/done.mp4",
                    "archiveStatus": "archived",
                    "archiveBackend": "local",
                    "archiveKey": "video/done.mp4",
                    "archiveLocalPath": str(video_path),
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        jobs = json.dumps([
            {"videoIndex": 1, "title": "视频 1", "jobId": "job-done"},
        ])
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-archived",
                    "jobs": jobs,
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-archived"},
            ), patch.object(
                ai8video_web,
                "AI8VideoModelClient",
                return_value=FakeVideoClient(),
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["statusLabel"], "视频已生成")
        self.assertEqual(progress["succeededCount"], 1)
        self.assertEqual(progress["runningCount"], 0)
        self.assertEqual(progress["items"][0]["status"], "succeeded")
        self.assertTrue(progress["items"][0]["hasLocalAsset"])

    def test_api_chat_status_stateless_keeps_pending_when_any_job_is_running(self) -> None:
        class FakeVideoClient:
            def get_job(self, job_id, video_index=1):
                if job_id == "job-running":
                    return SimpleNamespace(
                        status="pending",
                        provider_status="processing",
                        provider_progress=45,
                        video_url=None,
                        error=None,
                    )
                return SimpleNamespace(
                    status="succeeded",
                    provider_status="completed",
                    provider_progress=100,
                    video_url=f"https://example.com/{job_id}.mp4",
                    error=None,
                )

        jobs = json.dumps([
            {"videoIndex": 1, "title": "视频 1", "jobId": "job-done-1"},
            {"videoIndex": 2, "title": "视频 2", "jobId": "job-running"},
            {"videoIndex": 3, "title": "视频 3", "jobId": "job-done-3"},
        ])
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-running",
                    "jobs": jobs,
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-running"},
            ), patch.object(
                ai8video_web,
                "AI8VideoModelClient",
                return_value=FakeVideoClient(),
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        self.assertEqual(body["status"], "pending")
        self.assertEqual(progress["status"], "active")
        self.assertEqual(progress["succeededCount"], 0)
        self.assertEqual(progress["runningCount"], 3)
        self.assertEqual(progress["postProcessingCount"], 2)
        self.assertEqual(progress["items"][1]["title"], "视频 2")
        self.assertEqual(progress["items"][1]["statusLabel"], "真实生成进度 45%")

    def test_api_chat_status_stateless_ignores_query_title_to_avoid_mojibake(self) -> None:
        class FakeVideoClient:
            def get_job(self, job_id, video_index=1):
                return SimpleNamespace(
                    status="pending",
                    provider_status="processing",
                    provider_progress=95,
                    video_url=None,
                    error=None,
                )

        jobs = json.dumps([
            {
                "videoIndex": 1,
                "title": "Ã¨Â®Â©Ã§Â¿Â»Ã¨Â¯ÂÃ¯Â¼ÂÃ¦ÂÂÃ¤Â¸ÂºÃ¥ÂÂÃ¥ÂÂ²",
                "jobId": "job-running",
            },
        ])
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-mojibake",
                    "jobs": jobs,
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-mojibake"},
            ), patch.object(
                ai8video_web,
                "AI8VideoModelClient",
                return_value=FakeVideoClient(),
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        item = body["generationProgress"]["items"][0]
        self.assertEqual(item["title"], "视频 1")
        self.assertNotIn("Ã", item["title"])

    def test_api_chat_status_recovers_first_frame_disconnect_from_trace(self) -> None:
        trace_path = self.root / "prompt_traces.jsonl"

        def line(created_at: str, event: str, video_index: int, payload: dict | None = None) -> str:
            data = {
                "createdAt": created_at,
                "event": event,
                "sessionId": "session-trace",
                "payload": {"videoIndex": video_index, **(payload or {})},
            }
            return json.dumps(data, ensure_ascii=False)

        trace_path.write_text(
            "\n".join([
                line("2026-06-20T09:00:18+00:00", "merged_final_video_prompt", 1, {"title": "第一条"}),
                line("2026-06-20T09:00:18+00:00", "merged_final_video_prompt", 2, {"title": "第二条"}),
                line("2026-06-20T09:00:18+00:00", "merged_final_video_prompt", 3, {"title": "第三条"}),
                line("2026-06-20T09:00:43+00:00", "first_frame_image_prompt", 1),
                line("2026-06-20T09:00:44+00:00", "first_frame_image_request", 1),
                line("2026-06-20T09:00:51+00:00", "first_frame_image_prompt", 2),
                line("2026-06-20T09:00:57+00:00", "first_frame_image_prompt", 3),
                line(
                    "2026-06-20T09:01:43+00:00",
                    "first_frame_image_error",
                    2,
                    {
                        "error": (
                            "HTTPSConnectionPool(host='api.example.com', port=443): Max retries exceeded "
                            "with url: /v1/images/generations (Caused by ProxyError('Cannot connect to proxy.', "
                            "RemoteDisconnected('Remote end closed connection without response')))"
                        )
                    },
                ),
                line(
                    "2026-06-20T09:01:51+00:00",
                    "first_frame_image_error",
                    3,
                    {
                        "error": (
                            "HTTPSConnectionPool(host='api.example.com', port=443): Max retries exceeded "
                            "with url: /v1/images/generations (Caused by ProxyError('Cannot connect to proxy.', "
                            "RemoteDisconnected('Remote end closed connection without response')))"
                        )
                    },
                ),
            ]) + "\n",
            encoding="utf-8",
        )
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-trace",
                    "videoCount": "3",
                    "pendingSince": "2026-06-20T17:00:00+08:00",
                }.get(key, default)
            ),
        )
        try:
            with patch.dict(os.environ, {"AI8VIDEO_FIRST_FRAME_LOST_RECOVERY_SECONDS": "0"}), patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-trace", "stalePending": True},
            ), patch.object(
                ai8video_web,
                "PROMPT_TRACE_PATH",
                trace_path,
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        self.assertTrue(body["traceRecovered"])
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["statusLabel"], "首帧图结果未回填")
        self.assertEqual(progress["totalRequested"], 3)
        self.assertEqual(progress["submittedCount"], 0)
        self.assertEqual(progress["failedCount"], 3)
        self.assertEqual([item["status"] for item in progress["items"]], ["failed", "failed", "failed"])
        self.assertEqual([item["statusLabel"] for item in progress["items"]], ["首帧图未回填", "首帧图未回填", "首帧图未回填"])
        self.assertEqual([item["providerStatus"] for item in progress["items"]], [
            "first_frame_response_lost",
            "first_frame_response_lost",
            "first_frame_response_lost",
        ])
        self.assertIn("首帧图生成时连接断开", progress["items"][0]["error"])
        self.assertIn("本地没有拿到图片结果", progress["items"][1]["error"])
        self.assertIn("不会用原图冒充成功", progress["items"][0]["error"])
        self.assertIn("仍可能在服务端完成并扣费", progress["items"][0]["error"])
        self.assertNotIn("真实结果回填为准", progress["items"][0]["error"])
        self.assertNotIn("视频任务没有提交", progress["items"][0]["error"])

    def test_api_chat_status_keeps_video_submit_without_job_id_pending(self) -> None:
        trace_path = self.root / "prompt_traces.jsonl"

        def line(created_at: str, event: str, video_index: int, payload: dict | None = None) -> str:
            data = {
                "createdAt": created_at,
                "event": event,
                "sessionId": "session-create-response-lost",
                "payload": {"videoIndex": video_index, **(payload or {})},
            }
            return json.dumps(data, ensure_ascii=False)

        disconnect_error = (
            "HTTPSConnectionPool(host='api.example.com', port=443): Max retries exceeded "
            "with url: /v1/images/generations (Caused by ProxyError('Cannot connect to proxy.', "
            "RemoteDisconnected('Remote end closed connection without response')))"
        )
        trace_path.write_text(
            "\n".join([
                line("2026-06-20T15:43:00+00:00", "merged_final_video_prompt", 1, {"title": "片段一"}),
                line("2026-06-20T15:43:00+00:00", "merged_final_video_prompt", 2, {"title": "片段二"}),
                line("2026-06-20T15:43:00+00:00", "merged_final_video_prompt", 3, {"title": "片段三"}),
                line("2026-06-20T15:43:18+00:00", "video_submit", 1, {
                    "title": "片段一",
                    "segmentLabel": "片段 1",
                    "durationSeconds": 10,
                    "videoModel": {"template": "openai-compatible", "model": "veo_3_1-fast"},
                }),
                line("2026-06-20T15:43:50+00:00", "first_frame_image_error", 2, {"error": disconnect_error}),
                line("2026-06-20T15:44:05+00:00", "first_frame_image_error", 3, {"error": disconnect_error}),
            ]) + "\n",
            encoding="utf-8",
        )
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-create-response-lost",
                    "videoCount": "3",
                    "pendingSince": "2026-06-20T23:43:18+08:00",
                }.get(key, default)
            ),
        )
        try:
            with patch.dict(os.environ, {"AI8VIDEO_FIRST_FRAME_LOST_RECOVERY_SECONDS": "0"}), patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-create-response-lost", "stalePending": True},
            ), patch.object(
                ai8video_web,
                "PROMPT_TRACE_PATH",
                trace_path,
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        first_item = progress["items"][0]
        self.assertEqual(body["status"], "pending")
        self.assertEqual(progress["status"], "active")
        self.assertEqual(progress["runningCount"], 1)
        self.assertEqual(progress["failedCount"], 2)
        self.assertEqual(first_item["status"], "polling")
        self.assertIsNone(first_item["jobId"])
        self.assertEqual(first_item["providerStatus"], "video_create_response_lost")
        self.assertEqual(first_item["providerProgress"], 1)
        self.assertEqual(first_item["statusLabel"], "片段 1 已提交上游，等待任务号回填")
        self.assertIn("请求已经发给上游", first_item["error"])
        self.assertIn("不要立刻重复提交", first_item["error"])
        self.assertNotIn("interrupted-before-submit", json.dumps(progress, ensure_ascii=False))
        self.assertNotIn("未提交给生成服务", first_item["error"])

    def test_generation_progress_keeps_lost_create_response_polling(self) -> None:
        session_id = "session-progress-create-lost"
        video = VideoPrompt(index=1, title="片段一", prompt="测试")
        generation_progress.start_generation_progress(session_id, [video])
        try:
            generation_progress.mark_job_submitting(session_id, video)
            generation_progress.mark_job_failed(
                session_id,
                1,
                "创建视频任务超时：上游可能已经接收请求并继续在后台生成，"
                "但本地尚未拿到任务 ID。RemoteDisconnected('Remote end closed connection without response')",
            )
            progress = generation_progress.get_generation_progress(session_id)
        finally:
            generation_progress.clear_generation_progress(session_id)

        self.assertIsNotNone(progress)
        item = progress["items"][0]
        self.assertEqual(progress["status"], "active")
        self.assertEqual(progress["runningCount"], 1)
        self.assertEqual(progress["failedCount"], 0)
        self.assertEqual(item["status"], "polling")
        self.assertEqual(item["providerStatus"], "video_create_response_lost")
        self.assertEqual(item["providerProgress"], 1)
        self.assertIn("请求已经发给上游", item["error"])

    def test_api_chat_status_recovers_merge_segments_from_trace(self) -> None:
        class FakeVideoClient:
            def get_job(self, job_id, video_index=1):
                if job_id == "task-segment-1":
                    return SimpleNamespace(
                        status="succeeded",
                        provider_status="completed",
                        provider_progress=100,
                        video_url="https://example.invalid/segment-1.mp4",
                        error=None,
                    )
                return SimpleNamespace(
                    status="pending",
                    provider_status="processing",
                    provider_progress=None,
                    video_url=None,
                    error=None,
                )

        trace_path = self.root / "prompt_traces.jsonl"
        now = datetime.now(timezone.utc).replace(microsecond=0)
        trace_path.write_text(
            "\n".join([
                json.dumps({
                    "createdAt": now.isoformat(),
                    "event": "merged_final_video_prompt",
                    "sessionId": "session-merge-segments",
                    "payload": {"videoIndex": 1, "title": "第一条"},
                }, ensure_ascii=False),
                json.dumps({
                    "createdAt": now.isoformat(),
                    "event": "video_job_created",
                    "sessionId": "session-merge-segments",
                    "payload": {
                        "videoIndex": 1,
                        "title": "第一条 · 片段 1",
                        "jobId": "task-segment-1",
                        "status": "pending",
                        "segmentLabel": "片段 1",
                    },
                }, ensure_ascii=False),
                json.dumps({
                    "createdAt": now.isoformat(),
                    "event": "video_job_created",
                    "sessionId": "session-merge-segments",
                    "payload": {
                        "videoIndex": 1,
                        "title": "第一条 · 片段 2",
                        "jobId": "task-segment-2",
                        "status": "pending",
                        "segmentLabel": "片段 2",
                    },
                }, ensure_ascii=False),
            ]) + "\n",
            encoding="utf-8",
        )
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-merge-segments",
                    "videoCount": "1",
                    "pendingSince": now.isoformat(),
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-merge-segments", "stalePending": True},
            ), patch.object(
                ai8video_web,
                "PROMPT_TRACE_PATH",
                trace_path,
            ), patch.object(
                ai8video_web,
                "AI8VideoModelClient",
                return_value=FakeVideoClient(),
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        item = body["generationProgress"]["items"][0]
        self.assertEqual(body["status"], "pending")
        self.assertEqual(item["jobId"], "task-segment-2")
        self.assertEqual(item["segmentLabel"], "片段 2")
        self.assertEqual(item["statusLabel"], "片段 2：上游状态：processing")
        self.assertEqual([segment["segmentLabel"] for segment in item["segmentStatus"]], ["片段 1", "片段 2"])
        self.assertEqual([segment["status"] for segment in item["segmentStatus"]], ["succeeded", "polling"])
        self.assertEqual(item["segmentStatus"][1]["providerStatus"], "processing")

    def test_api_chat_status_keeps_recent_first_frame_disconnect_recovering_when_enabled(self) -> None:
        trace_path = self.root / "prompt_traces.jsonl"
        now = datetime.now(timezone.utc).replace(microsecond=0)
        trace_path.write_text(
            "\n".join([
                json.dumps({
                    "createdAt": now.isoformat(),
                    "event": "merged_final_video_prompt",
                    "sessionId": "session-recovering",
                    "payload": {"videoIndex": 1, "title": "第一条"},
                }, ensure_ascii=False),
                json.dumps({
                    "createdAt": now.isoformat(),
                    "event": "first_frame_image_error",
                    "sessionId": "session-recovering",
                    "payload": {
                        "videoIndex": 1,
                        "error": "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))",
                    },
                }, ensure_ascii=False),
            ]) + "\n",
            encoding="utf-8",
        )
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-recovering",
                    "videoCount": "1",
                    "pendingSince": now.isoformat(),
                }.get(key, default)
            ),
        )
        try:
            with patch.dict(os.environ, {"AI8VIDEO_FIRST_FRAME_LOST_RECOVERY_SECONDS": "1800"}), patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-recovering", "stalePending": True},
            ), patch.object(
                ai8video_web,
                "PROMPT_TRACE_PATH",
                trace_path,
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        item = progress["items"][0]
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["statusLabel"], "等待生成结果回填")
        self.assertEqual(progress["status"], "active")
        self.assertEqual(progress["runningCount"], 1)
        self.assertEqual(progress["failedCount"], 0)
        self.assertEqual(item["status"], "polling")
        self.assertEqual(item["statusLabel"], "等待生成结果回填")
        self.assertIn("正在等待生成结果回填", item["error"])

    def test_api_chat_status_prefers_local_success_over_first_frame_trace_error(self) -> None:
        trace_path = self.root / "prompt_traces.jsonl"
        now = datetime.now(timezone.utc).replace(microsecond=0)
        video_path = self.root / "用户生成结果" / "video" / "done.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"video")
        (self.root / "assets.jsonl").write_text(
            json.dumps({
                "createdAt": now.isoformat(),
                "videoIndex": 1,
                "videoTitle": "后台已生成",
                "jobId": "task-real-video",
                "status": "succeeded",
                "archiveStatus": "archived",
                "archiveLocalPath": str(video_path),
                "progressSessionId": "session-local-wins",
                "request": {"videoCount": 1},
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        trace_path.write_text(
            "\n".join([
                json.dumps({
                    "createdAt": now.isoformat(),
                    "event": "merged_final_video_prompt",
                    "sessionId": "session-local-wins",
                    "payload": {"videoIndex": 1, "title": "第一条"},
                }, ensure_ascii=False),
                json.dumps({
                    "createdAt": now.isoformat(),
                    "event": "first_frame_image_error",
                    "sessionId": "session-local-wins",
                    "payload": {
                        "videoIndex": 1,
                        "error": "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))",
                    },
                }, ensure_ascii=False),
            ]) + "\n",
            encoding="utf-8",
        )
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-local-wins",
                    "videoCount": "1",
                    "pendingSince": now.isoformat(),
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-local-wins", "stalePending": True},
            ), patch.object(
                ai8video_web,
                "PROMPT_TRACE_PATH",
                trace_path,
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        item = progress["items"][0]
        self.assertTrue(body["localTerminalRecovered"])
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["statusLabel"], "视频已生成")
        self.assertEqual(progress["succeededCount"], 1)
        self.assertEqual(progress["failedCount"], 0)
        self.assertEqual(item["status"], "succeeded")
        self.assertEqual(item["title"], "后台已生成")

    def test_api_chat_status_prefers_local_terminal_over_stale_postprocessing(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        video_dir = self.root / "用户生成结果" / "video"
        video_dir.mkdir(parents=True, exist_ok=True)
        records = []
        for video_index in range(1, 4):
            video_path = video_dir / f"done-{video_index}.mp4"
            video_path.write_bytes(b"video")
            records.append(json.dumps({
                "createdAt": now.isoformat(),
                "videoIndex": video_index,
                "videoTitle": f"成片 {video_index}",
                "jobId": f"merge2-task-seg-a-{video_index}-task-seg-b-{video_index}",
                "status": "succeeded",
                "archiveStatus": "archived",
                "archiveLocalPath": str(video_path),
                "progressSessionId": "session-postprocessing-done",
                "request": {"videoCount": 3},
            }, ensure_ascii=False))
        (self.root / "assets.jsonl").write_text("\n".join(records) + "\n", encoding="utf-8")
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-postprocessing-done",
                    "videoCount": "4",
                    "pendingSince": now.isoformat(),
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={
                    "status": "pending",
                    "phase": "postprocessing",
                    "statusLabel": "后台处理中",
                    "sessionId": "session-postprocessing-done",
                    "generationProgress": {
                        "sessionId": "session-postprocessing-done",
                        "status": "active",
                        "totalRequested": 3,
                        "items": [
                            {"videoIndex": 1, "status": "archiving", "statusLabel": "后台处理中"},
                            {"videoIndex": 2, "status": "succeeded", "statusLabel": "已生成"},
                            {"videoIndex": 3, "status": "archiving", "statusLabel": "后台处理中"},
                        ],
                        "runningCount": 2,
                        "postProcessingCount": 2,
                        "waitingCount": 0,
                        "succeededCount": 1,
                        "failedCount": 0,
                        "deletedCount": 0,
                        "skippedCount": 0,
                    },
                },
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        self.assertTrue(body["localTerminalRecovered"])
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["statusLabel"], "视频已生成")
        self.assertEqual(progress["totalRequested"], 3)
        self.assertEqual(progress["succeededCount"], 3)
        self.assertEqual(progress["runningCount"], 0)
        self.assertEqual(progress["skippedCount"], 0)
        self.assertEqual([item["status"] for item in progress["items"]], ["succeeded", "succeeded", "succeeded"])

    def test_api_chat_status_prefers_local_terminal_over_stale_planning(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        old = "2020-01-01T00:00:00+00:00"
        video_dir = self.root / "用户生成结果" / "video"
        video_dir.mkdir(parents=True, exist_ok=True)
        records = []
        for video_index in range(1, 3):
            video_path = video_dir / f"done-planning-{video_index}.mp4"
            video_path.write_bytes(b"video")
            records.append(json.dumps({
                "createdAt": now.isoformat(),
                "videoIndex": video_index,
                "videoTitle": f"规划后成片 {video_index}",
                "jobId": f"merge2-task-plan-a-{video_index}-task-plan-b-{video_index}",
                "status": "succeeded",
                "archiveStatus": "archived",
                "archiveLocalPath": str(video_path),
                "progressSessionId": "session-planning-done",
                "request": {"videoCount": 2},
            }, ensure_ascii=False))
        (self.root / "assets.jsonl").write_text("\n".join(records) + "\n", encoding="utf-8")
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-planning-done",
                    "videoCount": "4",
                    "pendingSince": now.isoformat(),
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={
                    "status": "pending",
                    "phase": "planning",
                    "statusLabel": "正在整理视频提示词",
                    "sessionId": "session-planning-done",
                    "pendingSince": old,
                    "generationProgress": {
                        "sessionId": "session-planning-done",
                        "status": "planning",
                        "updatedAt": old,
                        "totalRequested": 2,
                        "items": [
                            {"videoIndex": 1, "status": "planning", "statusLabel": "正在整理视频提示词"},
                            {"videoIndex": 2, "status": "planning", "statusLabel": "正在整理视频提示词"},
                        ],
                        "runningCount": 2,
                        "waitingCount": 2,
                        "succeededCount": 0,
                        "failedCount": 0,
                        "deletedCount": 0,
                        "skippedCount": 0,
                    },
                },
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        self.assertTrue(body["localTerminalRecovered"])
        self.assertNotIn("stalePlanningRecovered", body)
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["statusLabel"], "视频已生成")
        self.assertEqual(progress["totalRequested"], 2)
        self.assertEqual(progress["succeededCount"], 2)
        self.assertEqual(progress["failedCount"], 0)
        self.assertEqual(progress["skippedCount"], 0)
        self.assertEqual([item["status"] for item in progress["items"]], ["succeeded", "succeeded"])

    def test_api_chat_status_reports_planning_progress_from_trace(self) -> None:
        trace_path = self.root / "prompt_traces.jsonl"

        def line(created_at: str, event: str, payload: dict | None = None) -> str:
            data = {
                "createdAt": created_at,
                "event": event,
                "sessionId": "session-planning",
                "payload": payload or {},
            }
            return json.dumps(data, ensure_ascii=False)

        trace_path.write_text(
            "\n".join([
                line("2026-06-20T09:25:53+00:00", "keyword_model_input", {"videoCount": 3}),
                line("2026-06-20T09:26:18+00:00", "keyword_model_output", {"videoCount": 3}),
                line("2026-06-20T09:26:18+00:00", "split_model_input", {"videoCount": 3}),
                line("2026-06-20T09:26:49+00:00", "split_model_output", {"videoCount": 3}),
                line("2026-06-20T09:26:49+00:00", "business_prompt_batch_model_input", {"videoCount": 3}),
                line("2026-06-20T09:27:23+00:00", "business_prompt_batch_model_output", {"videoCount": 3}),
                line("2026-06-20T09:27:23+00:00", "business_prompt_validation_model_input", {"videoIndex": 1}),
                line("2026-06-20T09:27:38+00:00", "business_prompt_validation_model_output", {"videoIndex": 1}),
                line("2026-06-20T09:27:38+00:00", "business_prompt_validation_model_input", {"videoIndex": 2}),
            ]) + "\n",
            encoding="utf-8",
        )
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-planning",
                    "videoCount": "3",
                    "pendingSince": "2026-06-20T17:25:49+08:00",
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={
                    "status": "pending",
                    "phase": "planning",
                    "sessionId": "session-planning",
                    "pendingSince": "2026-06-20T17:25:49+08:00",
                    "elapsedSeconds": 102,
                },
            ), patch.object(
                ai8video_web,
                "PROMPT_TRACE_PATH",
                trace_path,
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["phase"], "planning")
        self.assertEqual(progress["status"], "planning")
        self.assertEqual(progress["totalRequested"], 3)
        self.assertEqual(body["statusLabel"], "正在检查第 2/3 条视频脚本")
        self.assertEqual([item["statusLabel"] for item in progress["items"]], [
            "视频脚本检查完成",
            "正在检查视频脚本",
            "正在完善视频脚本",
        ])
        self.assertGreater(progress["items"][0]["providerProgress"], 0)

    def test_api_chat_status_prefers_latest_planning_attempt_over_old_jobs(self) -> None:
        trace_path = self.root / "prompt_traces.jsonl"

        def line(created_at: str, event: str, payload: dict | None = None) -> str:
            data = {
                "createdAt": created_at,
                "event": event,
                "sessionId": "session-reused",
                "payload": payload or {},
            }
            return json.dumps(data, ensure_ascii=False)

        trace_path.write_text(
            "\n".join([
                line("2026-06-20T13:16:10+00:00", "merged_final_video_prompt", {
                    "videoIndex": 1,
                    "title": "旧轮视频",
                }),
                line("2026-06-20T13:16:13+00:00", "video_job_created", {
                    "videoIndex": 1,
                    "title": "旧轮视频",
                    "jobId": "task-old-failed",
                    "status": "pending",
                }),
                line("2026-06-20T14:12:06+00:00", "keyword_model_input", {"videoCount": 4}),
                line("2026-06-20T14:12:36+00:00", "keyword_model_output", {"videoCount": 4}),
                line("2026-06-20T14:12:36+00:00", "split_model_input", {"videoCount": 4}),
                line("2026-06-20T14:13:08+00:00", "split_model_output", {"videoCount": 4}),
                line("2026-06-20T14:13:08+00:00", "business_prompt_batch_model_input", {"videoCount": 4}),
                line("2026-06-20T14:13:47+00:00", "business_prompt_batch_model_output", {"videoCount": 4}),
                line("2026-06-20T14:13:47+00:00", "business_prompt_validation_model_input", {"videoIndex": 1}),
                line("2026-06-20T14:13:58+00:00", "business_prompt_validation_model_output", {"videoIndex": 1}),
                line("2026-06-20T14:13:58+00:00", "business_prompt_validation_model_input", {"videoIndex": 2}),
            ]) + "\n",
            encoding="utf-8",
        )

        class FakeVideoClient:
            def get_job(self, job_id, video_index=1):
                raise AssertionError(f"old job should not be polled: {job_id}")

        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(
                get=lambda key, default="": {
                    "sessionId": "session-reused",
                    "videoCount": "4",
                }.get(key, default)
            ),
        )
        try:
            with patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={"status": "idle", "sessionId": "session-reused", "stalePending": True},
            ), patch.object(
                ai8video_web,
                "PROMPT_TRACE_PATH",
                trace_path,
            ), patch.object(
                ai8video_web,
                "AI8VideoModelClient",
                return_value=FakeVideoClient(),
            ):
                body = ai8video_web.api_chat_status()
        finally:
            ai8video_web.request = request_backup

        progress = body["generationProgress"]
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["phase"], "planning")
        self.assertTrue(body["traceRecovered"])
        self.assertEqual(body["statusLabel"], "正在检查第 2/4 条视频脚本")
        self.assertEqual(progress["submittedCount"], 0)
        self.assertEqual(progress["failedCount"], 0)
        self.assertEqual([item["status"] for item in progress["items"]], ["planning", "planning", "planning", "planning"])

    def test_api_chat_cancel_returns_cancelled_status(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"sessionId": "session-cancel", "reason": "用户强行终止"},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(
                ai8video_web,
                "cancel_chat_via_ai8video",
                return_value={
                    "status": "cancelled",
                    "phase": "cancelled",
                    "statusLabel": "已强行终止",
                    "sessionId": "session-cancel",
                    "generationProgress": {"status": "cancelled", "items": []},
                },
            ) as cancel_mock:
                body = ai8video_web.api_chat_cancel()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 200)
        self.assertEqual(body["status"], "cancelled")
        self.assertEqual(body["statusLabel"], "已强行终止")
        cancel_mock.assert_called_once_with(session_id="session-cancel", reason="用户强行终止")

    def test_api_chat_timeout_without_generation_returns_planning_pending(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"sessionId": "session-timeout", "message": "老板在会议室讲封号风险"},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(
                ai8video_web,
                "handle_chat_via_ai8video",
                side_effect=TimeoutError("timeout"),
            ), patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={
                    "status": "pending",
                    "sessionId": "session-timeout",
                    "pendingSince": "2026-06-13T03:00:00",
                    "elapsedSeconds": 12,
                },
            ):
                body = ai8video_web.api_chat()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 200)
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["sessionId"], "session-timeout")
        self.assertEqual(body["phase"], "planning")
        self.assertEqual(body["reply"]["stage"], "pending")
        self.assertEqual(body["reply"]["meta"]["operation"], "planning")

    def test_api_chat_timeout_with_unsubmitted_planning_progress_returns_failure(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"sessionId": "session-timeout", "message": "老板在会议室讲封号风险"},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(
                ai8video_web,
                "handle_chat_via_ai8video",
                side_effect=TimeoutError("timeout"),
            ), patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={
                    "status": "pending",
                    "phase": "planning",
                    "sessionId": "session-timeout",
                    "pendingSince": "2026-06-13T03:00:00",
                    "elapsedSeconds": 660,
                    "generationProgress": {
                        "status": "planning",
                        "totalRequested": 2,
                        "submittedCount": 0,
                        "runningCount": 2,
                        "waitingCount": 2,
                        "succeededCount": 0,
                        "failedCount": 0,
                        "items": [
                            {"videoIndex": 1, "title": "视频 1", "status": "planning", "jobId": None},
                            {"videoIndex": 2, "title": "视频 2", "status": "planning", "jobId": None},
                        ],
                    },
                },
            ):
                body = ai8video_web.api_chat()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 200)
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["reply"]["stage"], "error")
        self.assertIn("没有提交给上游生成服务", body["reply"]["text"])
        self.assertEqual(body["generationProgress"]["failedCount"], 2)
        self.assertEqual(body["generationProgress"]["items"][0]["statusLabel"], "生成失败")

    def test_api_chat_timeout_exposes_pending_status_when_generation_started(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"sessionId": "session-timeout", "message": "老板在会议室讲封号风险"},
        )
        try:
            with patch.object(
                ai8video_web,
                "handle_chat_via_ai8video",
                side_effect=TimeoutError("timeout"),
            ), patch.object(
                ai8video_web,
                "get_chat_status_via_ai8video",
                return_value={
                    "status": "pending",
                    "sessionId": "session-timeout",
                    "pendingSince": "2026-06-13T03:00:00",
                    "elapsedSeconds": 12,
                    "generationProgress": {
                        "status": "running",
                        "totalRequested": 2,
                        "items": [],
                    },
                },
            ):
                body = ai8video_web.api_chat()
        finally:
            ai8video_web.request = request_backup

        self.assertEqual(body["reply"]["meta"]["operation"], "pending")
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["sessionId"], "session-timeout")
        self.assertEqual(body["elapsedSeconds"], 12)
        self.assertEqual(body["generationProgress"]["totalRequested"], 2)
        self.assertIn("自动显示", body["reply"]["text"])

    def test_api_chat_rejects_missing_core_llm(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"sessionId": "session-no-llm", "message": "老板在会议室讲封号风险"},
        )
        ai8video_web.response = fake_response
        fake_config = SimpleNamespace(
            dry_run=False,
            has_llm=lambda: False,
        )
        try:
            with patch.object(ai8video_web.AI8VideoConfig, "from_env", return_value=fake_config):
                body = ai8video_web.api_chat()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 503)
        self.assertEqual(body["code"], "MISSING_CORE_LLM")
        self.assertIn("核心模型", body["error"])

    def test_api_chat_runtime_failure_returns_error_without_fallback(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"sessionId": "session-core-fail", "message": "老板在会议室讲封号风险"},
        )
        ai8video_web.response = fake_response
        fake_config = SimpleNamespace(
            dry_run=False,
            llm_base_url="https://api.example.com",
            llm_api_key="sk-test-llm",
            has_llm=lambda: True,
        )
        try:
            with patch.object(ai8video_web.AI8VideoConfig, "from_env", return_value=fake_config), patch.object(
                ai8video_web,
                "handle_chat_via_ai8video",
                side_effect=RuntimeError("runtime down"),
            ):
                body = ai8video_web.api_chat()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 502)
        self.assertEqual(body["code"], "AI8VIDEO_RUNTIME_FAILED")
        self.assertEqual(body["chatBackend"], "ai8video-runtime")
        self.assertIn("runtime down", body["error"])

    def test_api_chat_passes_short_web_timeout_to_ai8video(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"sessionId": "session-fast", "message": "老板在会议室讲封号风险"},
        )
        try:
            with patch.object(
                ai8video_web,
                "handle_chat_via_ai8video",
                return_value={"reply": {"text": "ok"}},
            ) as handle_chat:
                body = ai8video_web.api_chat()
        finally:
            ai8video_web.request = request_backup

        self.assertEqual(body["chatBackend"], "ai8video-runtime")
        handle_chat.assert_called_once()
        self.assertEqual(handle_chat.call_args.kwargs["timeout_seconds"], ai8video_web._web_chat_timeout_seconds())

    def test_api_chat_clears_terminal_progress_before_new_message(self) -> None:
        request_backup = ai8video_web.request
        session_id = "session-reused-after-terminal"
        videos = [VideoPrompt(index=1, title="旧视频", prompt="old")]
        generation_progress.start_generation_progress(session_id, videos)
        generation_progress.mark_job_failed(session_id, 1, "旧任务失败")
        generation_progress.fail_generation_progress(session_id, "旧任务失败", skip_pending=False)
        self.assertEqual(generation_progress.get_generation_progress(session_id)["status"], "failed")

        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"sessionId": session_id, "message": "10 个"},
        )
        try:
            with patch.object(
                ai8video_web,
                "handle_chat_via_ai8video",
                return_value={"reply": {"text": "ok"}},
            ) as handle_chat:
                body = ai8video_web.api_chat()

            self.assertEqual(body["chatBackend"], "ai8video-runtime")
            handle_chat.assert_called_once()
            self.assertIsNone(generation_progress.get_generation_progress(session_id))
        finally:
            ai8video_web.request = request_backup
            generation_progress.clear_generation_progress(session_id)

    def test_static_pending_overview_treats_cancelled_progress_as_terminal(self) -> None:
        html = read_static_source()

        self.assertIn("function isTerminalProgressStatus(status)", html)
        self.assertIn("'skipped', 'cancelled', 'canceled'", html)
        self.assertIn("function isTerminalProgressStage(stage)", html)
        self.assertIn("'已取消', '已强行终止'", html)
        self.assertIn(".progress-overview-track.pending:not(.terminal) .progress-overview-fill", html)
        self.assertIn("const terminalClass = overview.terminal ? ' terminal' : ''", html)
        self.assertIn("terminal: !pending && model?.isActive === false", html)
        self.assertIn("cancelled: 100", html)
        self.assertIn("function normalizePendingStatusProgress(pendingStatus = {})", html)
        self.assertIn("status: terminalStateless ? 'skipped' : 'pending_submission'", html)
        self.assertIn("nextPayload.pendingStatus = normalizePendingStatusProgress(nextPayload.pendingStatus);", html)
        self.assertNotIn("if (nextPayload.pendingStatus.statelessProgress) return;", html)
        self.assertIn("const statelessTerminal = !!(pending.statelessProgress && backendProgress && !isBackendGenerationProgressActive(backendProgress));", html)
        self.assertIn("while (!statelessTerminal && videos.length < boundedExpected)", html)
        self.assertIn("pending: !isTerminalProgressStatus(status)", html)
        self.assertIn("pending: !isTerminalProgressStage(stage)", html)
        self.assertIn("params.set('videoCount', String(videoCount));", html)
        self.assertIn("params.set('pendingSince', String(pendingStatus.pendingSince));", html)
        self.assertNotIn("pending: stage !== '已生成' && stage !== '生成失败'", html)
        self.assertNotIn("pending: !['succeeded', 'failed'].includes(status)", html)

    def test_static_pending_message_renders_agent_step_chain(self) -> None:
        html = read_static_source()

        self.assertIn("function renderAgentStepChain(pending = {})", html)
        self.assertIn("function buildAgentStepChainModel(pending = {})", html)
        self.assertIn("${renderAgentStepChain(pending)}", html)
        self.assertIn("理解需求", html)
        self.assertIn("规划任务", html)
        self.assertIn("提交生成", html)
        self.assertIn("生成视频", html)
        self.assertIn("归档结果", html)
        self.assertIn(".agent-step-chain", html)
        self.assertIn(".agent-step-details", html)
        self.assertIn("agent-step-detail-marker", html)
        self.assertIn("flex-direction: column-reverse", html)
        self.assertIn("max-height: 78px;", html)
        self.assertIn(".agent-step-details::before", html)
        self.assertIn(".message:not(.user) .bubble", html)
        self.assertIn("width: 70%;", html)
        self.assertIn("function renderAgentExecutionEvents(pending = {})", html)
        self.assertIn("function collapseAgentPollingEvents(rawEvents)", html)
        self.assertIn("const latestStatusIndex = new Map();", html)
        self.assertIn("const eventKey = status ? `${videoIndex}:${segmentIndex}:${status}:${eventKind}` : '';", html)
        self.assertIn("function buildTerminalAgentPendingStatus(payload, resultGroups, summary, sessionId)", html)
        self.assertIn("function isLocalVideoPostprocessFailure(value)", html)
        self.assertIn("function getGenerationFailureStageLabel(itemOrReason = {})", html)
        self.assertIn("const hasAgentProgress = !!renderedPendingStatus?.generationProgress;", html)
        self.assertIn("if (payload.meta?.operation === 'pending' || hasAgentProgress)", html)
        self.assertIn("if (isGeneratedResult && summary && !hasAgentProgress)", html)
        self.assertIn("const generatingStatuses = new Set(['submitting', 'preparing_first_frame', 'submitted', 'polling']);", html)
        self.assertIn("status === 'polling' && Number.isFinite(Number(event.providerProgress))", html)
        self.assertIn("index === 0 && !['succeeded', 'completed'].includes(status)", html)
        self.assertIn("本轮已结束：已生成 ${done}/${total}，失败 ${failed} 条。", html)
        self.assertIn("本机视频后处理编码器不兼容，开头裁剪失败", html)
        self.assertIn("hasLocalPostprocessFailure ? '本地后处理失败' : '视频生成失败'", html)
        self.assertIn("last.payload.pendingStatus = normalizePendingStatusProgress({", html)
        self.assertIn('class="pending-card-status"', html)
        self.assertIn("function renderAgentVideoThumbnails(pending = {})", html)
        self.assertIn("String(progress.status || '').trim() === 'planning'", html)
        self.assertIn("if (planning) return '';", html)
        self.assertIn("${renderProgressResultStrip([], pendingCount)}", html)
        self.assertIn("return buildProgressStatusResultItem(item, index);", html)
        self.assertIn("function humanizePublicExecutionStatus(value)", html)
        self.assertIn("后台真实执行事件", html)
        self.assertIn(".agent-video-results", html)
        self.assertIn("历史任务已结束", html)

    def test_static_video_preview_derives_delete_key_from_user_generated_url(self) -> None:
        html = read_static_source()

        self.assertIn("function deriveUserGeneratedKeyFromMediaUrl(value)", html)
        self.assertIn("const prefix = '/user-generated-results/';", html)
        self.assertIn("const userGeneratedKey = item.userGeneratedKey || deriveUserGeneratedKeyFromMediaUrl(videoSrc);", html)
        self.assertIn("const explicitKey = trigger?.getAttribute?.('data-video-user-generated-key') || '';", html)
        self.assertIn("const userGeneratedKey = explicitKey || deriveUserGeneratedKeyFromMediaUrl(src);", html)
        self.assertIn("function resolvePlayablePreviewSrc(item)", html)
        self.assertIn("function deriveLocalPreviewKey(videoKey)", html)
        self.assertIn("data-video-user-generated-preview-key", html)
        self.assertIn("data-regenerate-user-generated-previews", html)
        self.assertIn("/api/user-generated-previews/regenerate", html)
        self.assertIn("data-video-preview-action=\"delete-video\"", html)
        self.assertIn("data-video-preview-action=\"regenerate-tts\"", html)
        self.assertIn("data-video-preview-action=\"edit-tts-text\"", html)
        self.assertIn("video-preview-split-button", html)
        self.assertIn("/api/user-generated-results/tts-narration", html)
        self.assertIn("/api/user-generated-results/tts-narration/polish", html)
        self.assertIn("/api/user-generated-results/tts-narration/expand", html)
        self.assertIn("persistOpenTtsEditorBeforeHtmlMotion", html)
        self.assertIn("await persistOpenTtsEditorBeforeHtmlMotion(key)", html)
        self.assertIn("/api/user-generated-results/regenerate-tts", html)
        self.assertIn("/api/user-generated-results/regenerate-html-motion", html)
        self.assertIn("/api/user-generated-results/confirm-html-motion", html)
        self.assertIn("/api/user-generated-results/html-motion-review", html)
        self.assertIn("/api/user-generated-results/html-motion-tasks/", html)
        self.assertIn("pollUrl", html)
        self.assertIn("waitForHtmlMotionTask", html)
        self.assertIn("rememberHtmlMotionJob", html)
        self.assertIn("resumeHtmlMotionFromVideoPreview", html)
        self.assertIn("/api/user-generated-results/html-motion-active", html)
        self.assertIn("Only detach UI polling", html)
        self.assertIn("formatHtmlMotionElapsed", html)
        self.assertIn("formatHtmlMotionPhaseSummary", html)
        self.assertIn("resolveHtmlMotionTiming", html)
        self.assertIn("buildHtmlMotionProgressStatus", html)
        self.assertIn("htmlMotionTickTimer", html)
        self.assertIn("setInterval(refreshProgress, 250)", html)
        self.assertNotIn("attempts > 300", html)
        self.assertNotIn("HTML 动效预览等待超时", html)
        self.assertIn("elapsedSeconds", html)
        self.assertIn("phaseTimings", html)
        self.assertIn("phaseElapsedSeconds", html)
        self.assertIn("（${summary} · 当前 ${phase}）", html)
        self.assertIn("preview_ready", html)
        self.assertIn("data-video-preview-html-motion-status", html)
        self.assertIn(".video-preview-controls {\n      display: flex;\n      align-items: flex-end;", html)
        self.assertIn(".video-preview-side-actions {", html)
        self.assertIn("min-height: 32px;", html)
        self.assertIn("重新生成TTS配音", html)
        self.assertIn("修改台词", html)
        self.assertIn("重新生成 HTML 动效", html)
        self.assertIn("强行停止", html)
        self.assertIn("cancelHtmlMotionFromVideoPreview", html)
        self.assertIn("确认烧录", html)
        self.assertIn('data-video-preview-action="regenerate-html-motion"', html)
        self.assertIn('data-video-preview-action="confirm-html-motion"', html)
        self.assertIn("function regenerateHtmlMotionFromVideoPreview(userGeneratedKey, button, confirmButton)", html)
        self.assertIn("HTML_MOTION_QUALITY_RETRY_COUNT", html)
        self.assertIn("data-html-motion-quality-retry", html)
        self.assertIn("NARRATION_REVIEW_COUNT", html)
        self.assertIn("data-narration-review-count", html)
        self.assertIn("saveNarrationReviewCount", html)
        self.assertIn("HTML_MOTION_BEAT_INTERVAL_SECONDS", html)
        self.assertIn("data-html-motion-beat-interval", html)
        self.assertIn('step="0.1"', html)
        self.assertIn("data-html-motion-smart-beat", html)
        self.assertIn("已切换为智能模式", html)
        self.assertIn("saveHtmlMotionSmartBeatInterval", html)
        self.assertIn('id="settingsSaveBadge"', html)
        self.assertIn("showSettingsSavedBadge", html)
        self.assertIn('data-status="retry"', html)
        self.assertIn("审核结果：${retrySummary}・正在第 ${retryCount}", html)
        self.assertIn("data?.auditResult || data?.retryReason", html)
        self.assertIn("summarizeHtmlMotionRetryReason", html)
        self.assertNotIn("retryReason.slice", html)
        self.assertIn("任务因服务重启中断，请重新生成", html)
        html_motion_button = html.index('data-video-preview-action="regenerate-html-motion"')
        confirm_button = html.index('data-video-preview-action="confirm-html-motion"', html_motion_button)
        split_end = html.index("</span>", html_motion_button)
        self.assertLess(confirm_button, split_end)
        self.assertIn("AI 润色", html)
        self.assertIn("AI 扩写", html)
        self.assertIn("video-preview-tts-ai-group", html)

    def test_polish_tts_narration_uses_text_model(self) -> None:
        prompts: list[str] = []
        with patch.object(ai8video_web, "build_openai_compat_llm", return_value=lambda prompt: prompts.append(prompt) or "更顺口的新台词。") as build_llm:
            body = ai8video_web._polish_tts_narration_text("旧台词。", 14)

        self.assertTrue(body["ok"])
        self.assertEqual(body["text"], "更顺口的新台词")
        self.assertIn("当前视频时长：14.00 秒", prompts[0])
        build_llm.assert_called_once()

    def test_polish_tts_narration_accepts_json_model_output(self) -> None:
        with patch.object(
            ai8video_web,
            "build_openai_compat_llm",
            return_value=lambda prompt: '{"text":"JSON 润色台词。"}',
        ):
            body = ai8video_web._polish_tts_narration_text("旧台词。")

        self.assertEqual(body["text"], "JSON 润色台词")

    def test_polish_tts_narration_injects_top_k_script_knowledge(self) -> None:
        prompts: list[str] = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "知识库增强后的台词。"

        knowledge = {
            "contextText": "[知识段 1｜私域资产]\n客户资产才是真正的资产。",
            "meta": {"used": True, "query": "私域资产", "recallCount": 20, "topK": 5, "rerankApplied": True},
        }
        with patch.object(ai8video_web, "_tts_script_knowledge", return_value=knowledge), patch.object(
            ai8video_web,
            "build_openai_compat_llm",
            return_value=fake_llm,
        ):
            body = ai8video_web._polish_tts_narration_text("客户不能流失。")

        self.assertIn("[知识段 1｜私域资产]", prompts[0])
        self.assertIn("用户系统提示词", prompts[0])
        self.assertEqual(body["knowledge"]["topK"], 5)

    def test_polish_tts_narration_requires_text_model(self) -> None:
        with patch.object(ai8video_web, "build_openai_compat_llm", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "文本/视频规划模型"):
                ai8video_web._polish_tts_narration_text("旧台词。")

    def test_expand_tts_narration_uses_text_model(self) -> None:
        with patch.object(ai8video_web, "build_openai_compat_llm", return_value=lambda prompt: "扩写后的新台词，节奏更完整。") as build_llm:
            body = ai8video_web._expand_tts_narration_text("旧台词。")

        self.assertTrue(body["ok"])
        self.assertEqual(body["text"], "扩写后的新台词，节奏更完整")
        build_llm.assert_called_once()

    def test_expand_tts_narration_accepts_json_model_output(self) -> None:
        with patch.object(
            ai8video_web,
            "build_openai_compat_llm",
            return_value=lambda prompt: '{"text":"JSON 扩写台词。"}',
        ):
            body = ai8video_web._expand_tts_narration_text("旧台词。")

        self.assertEqual(body["text"], "JSON 扩写台词")

    def test_tts_narration_payload_reads_asset_text(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        video_path = video_dir / "demo.mp4"
        video_path.write_bytes(b"video")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all(
            [{
                "archiveKey": "video/demo.mp4",
                "archiveLocalPath": str(video_path),
                "generationMeta": {"localTtsNarrationText": "第一句台词。第二句台词。"},
            }]
        )

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            body = ai8video_web._tts_narration_text_payload_for_user_generated_video("video/demo.mp4")

        self.assertTrue(body["ok"])
        self.assertEqual(body["text"], "第一句台词。第二句台词")
        self.assertFalse(body["manual"])

    def test_saved_tts_narration_overrides_regenerate_text(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        video_path = video_dir / "demo.mp4"
        video_path.write_bytes(b"video")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all(
            [{
                "archiveKey": "video/demo.mp4",
                "archiveLocalPath": str(video_path),
                "videoIndex": 2,
                "jobId": "job-demo",
                "generationMeta": {"localTtsNarrationText": "旧台词。"},
            }]
        )

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            saved = ai8video_web._save_tts_narration_text_for_user_generated_video("video/demo.mp4", "新台词。")
            body = ai8video_web._tts_narration_text_payload_for_user_generated_video("video/demo.mp4")

        self.assertTrue(saved["ok"])
        self.assertEqual(body["text"], "新台词")
        self.assertTrue(body["manual"])

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "attach_local_tts_to_video",
            return_value={"status": "mixed", "textChars": 3, "audioPath": "/tmp/demo.m4a"},
        ) as attach_tts, patch.object(
            ai8video_web,
            "mix_background_music",
            return_value={"enabled": False, "status": "skipped"},
        ):
            ai8video_web._regenerate_user_generated_tts("video/demo.mp4")

        self.assertEqual(attach_tts.call_args.kwargs["narration_text"], "新台词")

    def test_restored_latest_tts_overrides_stale_asset_text(self) -> None:
        result_root = self.root / "用户生成结果"
        video_path = result_root / "video" / "demo.mp4"
        metadata_path = result_root / ".restored-meta" / "video" / "demo.mp4.json"
        video_path.parent.mkdir(parents=True)
        metadata_path.parent.mkdir(parents=True)
        video_path.write_bytes(b"video")
        metadata_path.write_text(
            json.dumps({"generationMeta": {"userTtsNarrationText": "最新恢复台词。"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all([{
            "archiveKey": "video/demo.mp4",
            "archiveLocalPath": str(video_path),
            "generationMeta": {"userTtsNarrationText": "旧资产台词。"},
        }])

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            body = ai8video_web._tts_narration_text_payload_for_user_generated_video("video/demo.mp4")

        self.assertEqual(body["text"], "最新恢复台词")
        self.assertTrue(body["manual"])

    def test_restored_result_keeps_archived_narration_and_supports_manual_edit(self) -> None:
        result_root = self.root / "用户生成结果"
        video_path = result_root / "video" / "restored" / "demo.mp4"
        metadata_path = result_root / ".restored-meta" / "video" / "restored" / "demo.mp4.json"
        video_path.parent.mkdir(parents=True)
        metadata_path.parent.mkdir(parents=True)
        video_path.write_bytes(b"video")
        metadata_path.write_text(
            json.dumps({
                "videoTitle": "恢复视频",
                "generationMeta": {
                    "segmentRecords": [{
                        "narrationText": "归档台词。",
                        "segmentPrompt": "恢复后的原始视频提示词。",
                    }],
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            items = ai8video_web._user_generated_result_items(limit=10)
            body = ai8video_web._tts_narration_text_payload_for_user_generated_video(
                "video/restored/demo.mp4"
            )
            saved = ai8video_web._save_tts_narration_text_for_user_generated_video(
                "video/restored/demo.mp4",
                "用户修改后的台词。",
            )
            updated = ai8video_web._tts_narration_text_payload_for_user_generated_video(
                "video/restored/demo.mp4"
            )
            prompt, _record, source = ai8video_web._video_prompt_for_user_generated_video(
                "video/restored/demo.mp4",
                video_path,
            )

        self.assertEqual(items[0]["videoTitle"], "恢复视频")
        self.assertEqual(body["text"], "归档台词")
        self.assertTrue(saved["ok"])
        self.assertEqual(updated["text"], "用户修改后的台词")
        self.assertTrue(updated["manual"])
        self.assertEqual(prompt, "恢复后的原始视频提示词。")
        self.assertEqual(source, "asset.generationMeta.segmentPrompt")

    def test_empty_saved_tts_narration_returns_deleted_state(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        video_path = video_dir / "demo.mp4"
        video_path.write_bytes(b"video")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all(
            [{
                "archiveKey": "video/demo.mp4",
                "archiveLocalPath": str(video_path),
                "generationMeta": {"localTtsNarrationText": "旧台词。"},
            }]
        )

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            saved = ai8video_web._save_tts_narration_text_for_user_generated_video("video/demo.mp4", "")
            self.assertTrue(saved["deleted"])
            regenerated = ai8video_web._regenerate_user_generated_tts("video/demo.mp4")

        self.assertTrue(regenerated["ok"])
        self.assertTrue(regenerated["deleted"])
        self.assertEqual(regenerated["textChars"], 0)

    def test_regenerate_user_generated_tts_uses_asset_narration_text(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        video_path = video_dir / "demo.mp4"
        video_path.write_bytes(b"video")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all(
            [{
                "archiveKey": "video/demo.mp4",
                "archiveLocalPath": str(video_path),
                "videoIndex": 2,
                "jobId": "job-demo",
                "generationMeta": {
                    "localTtsNarrationText": "第一句台词。第二句台词。",
                },
            }]
        )

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "attach_local_tts_to_video",
            return_value={"status": "mixed", "textChars": 12, "audioPath": "/tmp/demo.m4a"},
        ) as attach_tts, patch.object(
            ai8video_web,
            "mix_background_music",
            return_value={"enabled": True, "status": "mixed", "musicName": "BGM.mp3"},
        ) as mix_bgm, patch.object(
            ai8video_web,
            "sync_html_motion_review_audio",
            return_value={"status": "synced", "reviewId": "review-demo"},
        ) as sync_review_audio:
            body = ai8video_web._regenerate_user_generated_tts("video/demo.mp4")

        self.assertTrue(body["ok"])
        self.assertEqual(body["backgroundMusic"]["status"], "mixed")
        attach_tts.assert_called_once()
        self.assertEqual(attach_tts.call_args.kwargs["narration_text"], "第一句台词。第二句台词")
        self.assertFalse(attach_tts.call_args.kwargs["preserve_original_audio"])
        mix_bgm.assert_called_once()
        self.assertEqual(Path(mix_bgm.call_args.args[0]).resolve(), video_path.resolve())
        self.assertTrue(mix_bgm.call_args.kwargs["preserve_original_audio_override"])
        self.assertEqual(mix_bgm.call_args.kwargs["preserved_audio_volume_override"], 1.0)
        sync_review_audio.assert_called_once()
        self.assertEqual(Path(sync_review_audio.call_args.args[0]).resolve(), video_path.resolve())
        self.assertEqual(sync_review_audio.call_args.args[1], "video/demo.mp4")
        self.assertEqual(body["htmlMotionReviewAudio"]["status"], "synced")

    def test_regenerate_user_generated_tts_returns_deleted_payload_when_asset_missing(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        (video_dir / "demo.mp4").write_bytes(b"video")

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            result = ai8video_web._regenerate_user_generated_tts("video/demo.mp4")

        self.assertTrue(result["ok"])
        self.assertTrue(result["deleted"])
        self.assertEqual(result["textChars"], 0)

    def test_regenerate_user_generated_tts_fails_when_bgm_remix_fails(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        video_path = video_dir / "demo.mp4"
        video_path.write_bytes(b"video")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all(
            [{
                "archiveKey": "video/demo.mp4",
                "archiveLocalPath": str(video_path),
                "generationMeta": {"localTtsNarrationText": "保留背景音乐"},
            }]
        )

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "attach_local_tts_to_video",
            return_value={"status": "mixed", "textChars": 6},
        ), patch.object(
            ai8video_web,
            "mix_background_music",
            return_value={"enabled": True, "status": "failed", "reason": "BGM 混音失败"},
        ):
            with self.assertRaisesRegex(RuntimeError, "BGM 混音失败"):
                ai8video_web._regenerate_user_generated_tts("video/demo.mp4")

    def test_regenerate_html_motion_uses_retained_video_prompt(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        video_path = video_dir / "demo.mp4"
        video_path.write_bytes(b"video")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all(
            [{
                "archiveKey": "video/demo.mp4",
                "archiveLocalPath": str(video_path),
                "videoIndex": 2,
                "videoTitle": "演示视频",
                "jobId": "job-demo",
                "prompt": "留存的最终视频提示词",
                "generationMeta": {"userTtsNarrationText": "用户修改后的最新台词。"},
                "request": {"ratio": "9:16", "resolution": "720p", "durationSeconds": 10},
            }]
        )

        with patch("ai8video.media.motion.html_motion_review.HTML_MOTION_REVIEW_ROOT", self.root / "html-motion-reviews"), patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "build_html_motion_llm",
            return_value=lambda prompt: prompt,
        ), patch.object(
            ai8video_web,
            "apply_html_motion_overlay",
            return_value={"status": "applied", "reason": "HTML 动效已叠加"},
        ) as apply_overlay, patch.object(
            ai8video_web,
            "generate_preview_for_video",
            return_value={"ok": True, "previewKey": "preview/demo.jpg"},
        ):
            body = ai8video_web._regenerate_user_generated_html_motion("video/demo.mp4")

        self.assertTrue(body["ok"])
        request_snapshot = apply_overlay.call_args.args[1]
        video = apply_overlay.call_args.args[2]
        self.assertTrue(request_snapshot.html_motion_overlay_enabled)
        self.assertEqual(apply_overlay.call_args.kwargs["trigger"], "video_playback")
        self.assertEqual(video.prompt, "留存的最终视频提示词")
        self.assertEqual(video.source_summary, "用户修改后的最新台词")
        self.assertEqual(body["htmlMotionOverlay"]["dialogueChars"], 10)
        stored = JsonlAssetStore(self.root / "assets.jsonl").read_all()[0]
        self.assertEqual(body["htmlMotionOverlay"]["status"], "preview_ready")
        self.assertEqual(
            stored["generationMeta"]["htmlMotionOverlayRegeneration"]["status"],
            "preview_ready",
        )
        self.assertNotIn("htmlMotionOverlay", stored)

    def test_regenerate_html_motion_falls_back_to_segment_prompts(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        video_path = video_dir / "merge.mp4"
        video_path.write_bytes(b"video")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all(
            [{
                "archiveKey": "video/merge.mp4",
                "archiveLocalPath": str(video_path),
                "generationMeta": {
                    "segmentRecords": [
                        {"segmentPrompt": "片段一视频提示词"},
                        {"segmentPrompt": "片段二视频提示词"},
                    ],
                },
            }]
        )

        with patch("ai8video.media.motion.html_motion_review.HTML_MOTION_REVIEW_ROOT", self.root / "html-motion-reviews"), patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "build_html_motion_llm",
            return_value=lambda prompt: prompt,
        ), patch.object(
            ai8video_web,
            "apply_html_motion_overlay",
            return_value={"status": "degraded", "reason": "测试降级"},
        ) as apply_overlay:
            body = ai8video_web._regenerate_user_generated_html_motion("video/merge.mp4")

        self.assertEqual(body["htmlMotionOverlay"]["status"], "preview_failed")
        self.assertEqual(
            apply_overlay.call_args.args[2].prompt,
            "片段一视频提示词\n\n片段二视频提示词",
        )

    def test_regenerate_html_motion_reads_and_updates_manifest_prompt(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        video_path = video_dir / "manifest.mp4"
        video_path.write_bytes(b"video")
        manifest_path = self.root / "job-manifest.json"
        manifest_path.write_text(
            json.dumps({"video": {"prompt": "manifest 留存的视频提示词"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all(
            [{
                "archiveKey": "video/manifest.mp4",
                "archiveLocalPath": str(video_path),
                "archiveManifestPath": str(manifest_path),
            }]
        )

        with patch("ai8video.media.motion.html_motion_review.HTML_MOTION_REVIEW_ROOT", self.root / "html-motion-reviews"), patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "build_html_motion_llm",
            return_value=lambda prompt: prompt,
        ), patch.object(
            ai8video_web,
            "apply_html_motion_overlay",
            return_value={"status": "applied", "reason": "HTML 动效已叠加"},
        ) as apply_overlay, patch.object(
            ai8video_web,
            "generate_preview_for_video",
            return_value={"ok": True},
        ):
            body = ai8video_web._regenerate_user_generated_html_motion("video/manifest.mp4")

        self.assertEqual(apply_overlay.call_args.args[2].prompt, "manifest 留存的视频提示词")
        self.assertEqual(body["manifestUpdate"]["status"], "updated")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["htmlMotionOverlayRegeneration"]["status"], "preview_ready")
        self.assertNotIn("htmlMotionOverlay", manifest)

    def test_confirm_html_motion_publishes_prepared_preview_once(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        video_path = video_dir / "confirm.mp4"
        video_path.write_bytes(b"official")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all(
            [{"archiveKey": "video/confirm.mp4", "archiveLocalPath": str(video_path)}]
        )
        review_root = self.root / "html-motion-reviews"

        def render(candidate: Path) -> dict:
            candidate.write_bytes(b"prepared-preview")
            return {"status": "applied", "reason": "rendered"}

        with patch("ai8video.media.motion.html_motion_review.HTML_MOTION_REVIEW_ROOT", review_root), patch.object(
            ai8video_web,
            "ensure_user_generated_result_dir",
            return_value=result_root,
        ), patch.object(
            ai8video_web,
            "generate_preview_for_video",
            return_value={"ok": True, "previewKey": "preview/confirm.jpg"},
        ) as generate_preview:
            ai8video_web.prepare_html_motion_review(
                video_path,
                "video/confirm.mp4",
                render,
            )
            body = ai8video_web._confirm_user_generated_html_motion("video/confirm.mp4")

        self.assertTrue(body["ok"])
        self.assertEqual(video_path.read_bytes(), b"prepared-preview")
        self.assertEqual(body["htmlMotionOverlay"]["status"], "applied")
        stored = JsonlAssetStore(self.root / "assets.jsonl").read_all()[0]
        self.assertEqual(stored["htmlMotionOverlay"]["status"], "applied")
        generate_preview.assert_called_once()

    def test_regenerate_html_motion_reports_deleted_video_prompt(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        video_path = video_dir / "demo.mp4"
        video_path.write_bytes(b"video")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all(
            [{"archiveKey": "video/demo.mp4", "archiveLocalPath": str(video_path)}]
        )

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            with self.assertRaisesRegex(LookupError, "台词已删除"):
                ai8video_web._regenerate_user_generated_html_motion("video/demo.mp4")

    def test_regenerate_html_motion_uses_narration_when_video_prompt_is_missing(self) -> None:
        result_root = self.root / "用户生成结果"
        video_path = result_root / "video" / "merged.mp4"
        video_path.parent.mkdir(parents=True)
        video_path.write_bytes(b"video")
        record = {"generationMeta": {"userTtsNarrationText": "保留左侧台词"}}

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web, "_video_prompt_for_user_generated_video", return_value=("", record, "")
        ), patch.object(
            ai8video_web, "_tts_narration_text_for_user_generated_video", return_value=("保留左侧台词", record)
        ), patch.object(
            ai8video_web, "prepare_html_motion_review", return_value={"status": "preview_ready"}
        ) as prepare_review, patch.object(
            ai8video_web, "save_restored_result_html_motion_overlay", return_value=record
        ), patch.object(ai8video_web, "_update_html_motion_manifest", return_value={}):
            body = ai8video_web._regenerate_user_generated_html_motion("video/merged.mp4")

        self.assertTrue(body["ok"])
        self.assertEqual(prepare_review.call_args.args[3]["promptSource"], "tts_narration")

    def test_static_archive_tab_exposes_intermediate_artifact_cleanup_actions(self) -> None:
        html = read_static_source()

        self.assertIn("data-open-archive-artifact", html)
        self.assertIn("data-cleanup-archive-artifact", html)
        self.assertIn("/api/archive-artifacts/open", html)
        self.assertIn("/api/archive-artifacts/cleanup", html)
        self.assertIn("AI8VIDEO_ARCHIVE_TTS_OUTPUT_DIR: '清理配音输出'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_MERGE_TEMP_DIR: '清理临时媒体'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_REFERENCE_TEMP_DIR: '清理临时图片'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_MANIFEST_DIR: '清理孤儿元数据'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_ASSET_INDEX: '压缩孤儿记录'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_RECYCLE_BIN_DIR: '清空回收站'", html)

    def test_archive_paths_normalize_legacy_flat_key_to_video_root(self) -> None:
        result_root = self.root / "用户生成结果"
        flat_video = result_root / "video" / "demo.mp4"
        flat_video.parent.mkdir(parents=True)
        flat_video.write_bytes(b"video")

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            resolved = ai8video_web._manifest_video_path({
                "localVideo": str(result_root / "demo.mp4"),
            })

        self.assertEqual(resolved, flat_video.resolve())

    def test_auth_settings_archive_tab_lists_intermediate_artifacts(self) -> None:
        tts_status = {
            "enabled": False,
            "engine": "mimo",
            "apiBaseUrl": "",
            "apiKey": "",
            "model": "",
            "cloneModel": "",
            "voice": "",
            "voiceLabel": "",
            "voiceCount": 0,
            "voiceOptions": [],
            "voiceCloneCount": 0,
            "voiceCloneItems": [],
            "voiceCloneDir": str(self.root / "voice-clones"),
            "volume": 1,
            "available": False,
            "availabilityReason": "",
            "outputDir": str(self.root / "tts-output"),
            "outputFileCount": 0,
            "outputSizeBytes": 0,
            "outputSizeDisplay": "0 B",
        }
        with patch.object(ai8video_web, "local_tts_status", return_value=tts_status), patch.object(
            ai8video_web,
            "video_merge_mode_status",
            return_value={"mergeMode": "none"},
        ), patch.object(
            ai8video_web,
            "load_model_catalogs",
            return_value={},
        ), patch.object(
            ai8video_web,
            "pull_video_model_catalog",
        ):
            body = ai8video_web.api_auth_settings()

        env_names = {field["envName"] for field in body["fields"]}
        self.assertIn("archiveArtifacts", body)
        self.assertIn("AI8VIDEO_ARCHIVE_RESULT_VIDEO_DIR", env_names)
        result_field = next(field for field in body["fields"] if field["envName"] == "AI8VIDEO_ARCHIVE_RESULT_VIDEO_DIR")
        self.assertEqual(result_field["source"], "用户文件夹/用户生成结果/video")
        self.assertIn("AI8VIDEO_ARCHIVE_TTS_OUTPUT_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_MERGE_TEMP_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_REFERENCE_TEMP_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_MANIFEST_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_ASSET_INDEX", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_RECYCLE_BIN_DIR", env_names)

    def test_archive_artifact_cleanup_clears_tts_and_merge_temp_files(self) -> None:
        tts_dir = self.root / "tts-output"
        merge_dir = self.root / "merge-temp"
        tts_dir.mkdir(parents=True)
        merge_dir.mkdir(parents=True)
        (tts_dir / "voice.m4a").write_bytes(b"audio")
        (tts_dir / "note.txt").write_text("keep", encoding="utf-8")
        (merge_dir / "clip.mp4").write_bytes(b"video")
        (merge_dir / "nested").mkdir()
        (merge_dir / "nested" / "part.tmp").write_bytes(b"temp")

        with patch.object(ai8video_web, "local_tts_output_dir", return_value=tts_dir):
            tts_result = ai8video_web._cleanup_archive_artifacts("tts-output")
        with patch.object(ai8video_web, "MERGE_TEMP_MEDIA_DIR", merge_dir):
            merge_result = ai8video_web._cleanup_archive_artifacts("merge-temp")

        self.assertEqual(tts_result["deletedCount"], 1)
        self.assertFalse((tts_dir / "voice.m4a").exists())
        self.assertTrue((tts_dir / "note.txt").exists())
        self.assertEqual(merge_result["deletedCount"], 2)
        self.assertFalse((merge_dir / "clip.mp4").exists())
        self.assertFalse((merge_dir / "nested" / "part.tmp").exists())

    def test_archive_artifact_cleanup_orphan_covers_keeps_matching_video_cover(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        cover_dir = result_root / "cover"
        video_dir.mkdir(parents=True)
        cover_dir.mkdir(parents=True)
        (video_dir / "alive.mp4").write_bytes(b"video")
        (cover_dir / "alive.jpg").write_bytes(b"cover")
        (cover_dir / "orphan.jpg").write_bytes(b"orphan")

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            result = ai8video_web._cleanup_archive_artifacts("covers")

        self.assertEqual(result["deletedCount"], 1)
        self.assertTrue((cover_dir / "alive.jpg").exists())
        self.assertFalse((cover_dir / "orphan.jpg").exists())

    def test_static_settings_modal_uses_template_status_hidden_secret_toggle_and_no_watermark(self) -> None:
        html = read_static_source()

        self.assertIn("function currentVideoTemplateStatusText(settings)", html)
        self.assertIn("单个${Number(videoSettings.seconds || 10) || 10}秒", html)
        self.assertIn("const ratioField = resolutionMode === 'ratio' ?", html)
        self.assertIn("data-toggle-setting-secret", html)
        self.assertIn("settings-secret-toggle", html)
        self.assertNotIn("真实生成已就绪", html)
        self.assertNotIn("checkboxMarkup('watermark', '加水印', settings.watermark)", html)

    def test_static_deleted_progress_card_uses_failed_cross_style(self) -> None:
        html = read_static_source()

        self.assertIn(".result-notify-play.terminal-placeholder[aria-hidden=\"true\"] span::before", html)
        self.assertIn(".result-notify-play.processing-placeholder[aria-hidden=\"true\"] span", html)
        self.assertIn("animation: none;", html)
        self.assertIn("status: 'deleted'", html)
        self.assertIn("deletedCount: items.filter((item) => item?.status === 'deleted').length", html)
        self.assertIn("function scrubMissingUserGeneratedProgressFromSessions()", html)
        self.assertIn("const isDeletedOrMissing = status === 'deleted' || (status === 'succeeded' && !item?.hasLocalAsset);", html)
        self.assertIn("function isPostProcessingProgressItem(item)", html)
        self.assertIn("deletedUserGeneratedJobIds: []", html)
        self.assertIn("function collectProgressItemJobIds(item)", html)
        self.assertIn("function scrubDeletedGenerationProgress(progress, identity)", html)
        self.assertIn("generationProgress: scrubProgress(", html)
        self.assertIn("'deleted'].includes(", html)
        self.assertIn("return '后台处理中';", html)
        self.assertIn("processingClass = isPostProcessingProgressStatus(status)", html)
        self.assertIn("if (isDeletedOrMissing) {", html)
        self.assertIn('class="result-notify-card failed ${resultNotifyRatioClass(item)}"', html)
        self.assertIn("<div class=\"result-notify-failed-mark\" aria-hidden=\"true\">×</div>", html)
        self.assertIn("result-notify-play${isTerminal ? ' terminal-placeholder' : processingClass}", html)
        self.assertIn("已生成，文件已删除", html)
        self.assertNotIn("已生成，文件已删除或未落盘", html)
        self.assertNotIn("deleted-placeholder", html)

    def test_static_brand_uses_round_webp_avatar(self) -> None:
        from PIL import Image, features

        html = read_static_source()
        avatar_path = STATIC_ROOT / "images" / "ai8video-avatar.webp"

        self.assertIn("/static/images/ai8video-avatar.webp?v=20260723-0739", html)
        self.assertIn("alt=\"AI8video 头像\"", html)
        self.assertTrue(avatar_path.is_file())
        avatar_bytes = avatar_path.read_bytes()
        self.assertEqual(avatar_bytes[:4], b"RIFF")
        self.assertEqual(avatar_bytes[8:16], b"WEBPVP8L")
        vp8l_bits = int.from_bytes(avatar_bytes[21:25], "little")
        self.assertEqual(((vp8l_bits & 0x3FFF) + 1, ((vp8l_bits >> 14) & 0x3FFF) + 1), (512, 512))
        self.assertEqual((vp8l_bits >> 28) & 1, 1)
        if not features.check("webp"):
            return
        with Image.open(avatar_path) as avatar:
            self.assertEqual(avatar.format, "WEBP")
            self.assertEqual(avatar.size, (512, 512))
            rgba = avatar.convert("RGBA")
        self.assertEqual(rgba.getpixel((0, 0))[3], 0)
        self.assertEqual(rgba.getpixel((256, 256))[3], 255)

    def test_static_failed_result_card_uses_humanized_reason_badge(self) -> None:
        html = read_static_source()

        self.assertIn("function humanizeGenerationFailureReason(value)", html)
        self.assertIn("function summarizeGenerationFailureReason(value)", html)
        self.assertIn("function buildResultNotifyContext(items)", html)
        self.assertIn("function getGenerationFailureRawReason(item, fallback = '')", html)
        self.assertIn("function isGenericGenerationFailureText(value)", html)
        self.assertIn("const sharedFailureReason = sourceItems", html)
        self.assertIn("function isNoUpstreamFailureReason(value)", html)
        self.assertIn("if (reason.includes('没有上游返回')) return '未提交，无上游返回';", html)
        self.assertIn("if (reason.includes('请设置图片模型')) return '请设置图片模型';", html)
        self.assertIn("class=\"result-notify-failed-mark reason\"", html)
        self.assertIn("title=\"${escapeHtml(reason)}\"", html)
        self.assertIn("title=\"${escapeHtml(tooltipReason)}\"", html)
        self.assertIn("const isSkipped = status === 'skipped';", html)
        self.assertIn("videoIndex,", html)
        self.assertIn("error: item?.error || '',", html)
        self.assertIn("generationReasons: item?.generationReasons || '',", html)
        self.assertIn("statusLabel: item?.statusLabel || '',", html)
        self.assertIn("error: card?.error || card?.generationReasons || '',", html)
        self.assertIn("generationReasons: card?.generationReasons || '',", html)
        self.assertIn("const primary = cancelled ? rawLabel : '生成失败';", html)
        self.assertIn("const rawReason = getGenerationFailureRawReason(item);", html)
        self.assertIn("const inheritedReason = !cancelled && isNoUpstreamFailureReason(rawReason)", html)
        self.assertIn("const effectiveReason = inheritedReason || rawReason;", html)
        self.assertIn("const fallbackReason = cancelled ? primary : '这条未提交给生成服务；没有上游返回。';", html)
        self.assertIn("const tooltipReason = friendlyReason || '生成失败';", html)
        self.assertIn("const badgeReason = summarizeGenerationFailureReason(tooltipReason);", html)
        self.assertNotIn("前面失败，没生成", html)
        self.assertNotIn("片段${number}${statusText}", html)
        self.assertIn("<div class=\"result-notify-sub\">${escapeHtml(failureStageLabel)}</div>", html)
        self.assertIn("当前模型只支持 4、6 或 8 秒", html)

    def test_static_progress_modal_does_not_truncate_backend_items(self) -> None:
        html = read_static_source()

        self.assertIn("const boundedExpected = backendItems.length", html)
        self.assertIn("? Math.max(1, expectedCount || backendItems.length)", html)
        self.assertIn(": Math.max(1, Math.min(12, expectedCount || 2));", html)
        self.assertNotIn("backendItems.slice(0, boundedExpected).map", html)

    def test_static_clear_conversation_removes_all_chat_messages_only_locally(self) -> None:
        html = read_static_source()

        self.assertIn('id="clearConversationButton"', html)
        self.assertIn('id="clearConversationConfirmModal"', html)
        self.assertIn("确认清空对话？", html)
        self.assertIn("只会清空当前窗口里的文字对话，不会删除任务、结果、素材或媒体文件。", html)
        self.assertIn("function openClearConversationConfirmModal()", html)
        self.assertIn("function closeClearConversationConfirmModal()", html)
        self.assertIn("openClearConversationConfirmModal();", html)
        self.assertIn("clearConversationConfirmSubmitButton?.addEventListener('click'", html)
        self.assertIn("function clearActiveConversationTextMessages()", html)
        self.assertIn("return (session?.messages || []).length;", html)
        self.assertIn("session.messages = [];", html)
        self.assertIn("session.title = NEW_SESSION_TITLE;", html)
        self.assertIn("persistSessions();", html)
        self.assertNotIn("/api/clear-conversation", html)

    def test_static_session_cache_compacts_and_never_breaks_user_actions(self) -> None:
        html = read_static_source()

        self.assertIn("const SESSION_STORAGE_MAX_CHARS = 900000;", html)
        self.assertIn("function sessionStorageReplacer(aggressive = false)", html)
        self.assertIn("if (/^(data:|blob:)/i.test(value)) return undefined;", html)
        self.assertIn("function tryPersistSessionSnapshot(serialized)", html)
        self.assertIn("console.warn('会话缓存空间不足，正在自动精简', error);", html)
        self.assertIn("[8, 80, false]", html)
        self.assertIn("[1, 20, true]", html)
        self.assertIn("localStorage.removeItem(SESSION_STORAGE_KEY);", html)
        self.assertIn("pruneSettledPendingProgressFromSessions();\n      persistSessions();", html)
        self.assertIn("return false;", html)

    def test_static_force_cancel_without_index_targets_latest_pending_message(self) -> None:
        html = read_static_source()

        self.assertIn("forceCancelTrigger.hasAttribute('data-force-cancel-index')", html)
        self.assertIn("const hasMessageIndex = messageIndex !== null && messageIndex !== undefined", html)
        self.assertIn("const targetIndex = hasMessageIndex ? Number(messageIndex) : NaN", html)
        self.assertNotIn("const targetIndex = Number(messageIndex);", html)

    def test_static_main_background_is_transparent_not_green_gradient(self) -> None:
        html = read_static_source()

        self.assertIn("--bg: transparent;", html)
        self.assertIn("--bg-accent: transparent;", html)
        self.assertIn("body {\n      color: var(--text);\n      background: transparent;", html)
        self.assertNotIn("linear-gradient(180deg, var(--bg), var(--bg-accent))", html)
        self.assertNotIn("--bg-accent: #e8f0ea;", html)

    def test_static_flower_watermark_upload_button_matches_watermark_toggle_style(self) -> None:
        html = read_static_source()

        self.assertIn("上传水印图", html)
        self.assertIn(".flower-text-watermark-control", html)
        self.assertIn("gap: 4px;", html)
        self.assertIn("padding: 4px;", html)
        self.assertIn("background: rgba(238, 245, 255, 0.64);", html)
        self.assertIn("border: 1px solid rgba(37, 99, 235, 0.14);", html)
        self.assertNotIn("flower-text-watermark-upload-icon", html)
        self.assertNotIn(".flower-text-watermark-upload:hover", html)
        self.assertNotIn("background: linear-gradient(180deg, #f8fbff, #dfeaff);", html)
        self.assertIn(">更换纯色背景<", html)
        self.assertIn(">上传背景图<", html)
        self.assertNotIn("预览纯色背景", html)
        self.assertNotIn("上传预览背景", html)

    def test_static_flower_text_color_picker_supports_white_via_saturation(self) -> None:
        html = read_static_source()

        self.assertIn('data-flower-color-row="saturation"', html)
        self.assertIn('data-flower-text-color-channel="s"', html)
        self.assertIn("const channel = ['h', 's', 'v'].includes", html)
        self.assertIn("if (channel === 's') current.s =", html)
        self.assertIn("linear-gradient(90deg, #ffffff, var(--flower-text-hue-color, #ffee43))", html)
        self.assertNotIn("current.s = current.s <= 2 ? 100 : current.s;", html)

    def test_static_flower_text_drag_handle_stays_on_top_border_inside_preview(self) -> None:
        html = read_static_source()

        self.assertIn(
            ".flower-text-drag-handle {\n"
            "      position: absolute;\n"
            "      left: 50%;\n"
            "      top: 50%;\n"
            "      transform: translate(-50%, -50%);",
            html,
        )
        self.assertIn("const editorHeight = Math.max(1, editor.offsetHeight || editor.scrollHeight || 1);", html)
        self.assertIn("const handleHalfWidth = Math.max(12, (handle.offsetWidth || 24) / 2);", html)
        self.assertIn("const topBorderY = centerY - editorHeight / 2;", html)
        self.assertIn("const topBorderHandleY = topBorderY - handleHalfHeight;", html)
        self.assertIn("const handleX = Math.min(wrapWidth - handleHalfWidth, Math.max(handleHalfWidth, centerX));", html)
        self.assertIn("Math.max(handleHalfHeight, topBorderHandleY)", html)
        self.assertIn("handle.style.left = `${Math.round(handleX)}px`;", html)
        self.assertNotIn("centerX - editorWidth / 2 - 20", html)

    def test_static_flower_text_drag_uses_live_text_until_preview_refreshes(self) -> None:
        html = read_static_source()

        self.assertIn(".flower-text-editor-wrap.has-render-preview.is-dragging .flower-text-rendered-preview", html)
        self.assertIn(".flower-text-editor-wrap.has-render-preview.is-dragging .flower-text-editor", html)
        self.assertIn("editorWrap?.classList.add('is-dragging');", html)
        self.assertIn("wrap.classList.add('is-dragging');", html)
        self.assertGreaterEqual(html.count("await refreshFlowerTextRenderedPreview();"), 2)
        self.assertGreaterEqual(html.count("classList.remove('is-dragging')"), 4)
        self.assertIn("if (!drag.target) scheduleFlowerTextPositionSave();", html)

    def test_static_flower_text_drag_matches_rendered_style_without_editor_frame(self) -> None:
        html = read_static_source()

        self.assertIn("-webkit-text-stroke: var(--flower-text-live-stroke-width, 0px)", html)
        self.assertIn("background: transparent !important;\n      padding: 0 !important;", html)
        self.assertIn("border: 0 !important;\n      border-radius: 0;", html)
        self.assertIn("text-shadow: none;\n      box-shadow: none;", html)
        self.assertIn("editor.style.setProperty('--flower-text-live-stroke-width'", html)

    def test_static_flower_text_drawer_has_editable_html_motion_safe_zone(self) -> None:
        html = read_static_source()
        toolbar_start = html.index('<div class="flower-text-background-controls" role="group"')
        toolbar_end = html.index('<div id="flowerTextEditorWrap"', toolbar_start)
        background_toolbar = html[toolbar_start:toolbar_end]

        self.assertIn("data-html-motion-safe-zone-toggle", html)
        self.assertIn("data-html-motion-safe-zone-save", html)
        self.assertIn('id="htmlMotionSafeZoneBox"', html)
        self.assertIn("data-html-motion-safe-zone-resize", html)
        self.assertIn("/api/html-motion-safe-zone", html)
        self.assertIn("state.htmlMotionSafeZone.drag", html)
        self.assertNotIn("data-html-motion-safe-zone-toggle", background_toolbar)
        self.assertIn("html-motion-safe-zone-setting", html)
        self.assertLess(html.index('id="flowerTextSaveStatus"'), html.index('class="html-motion-safe-zone-setting"'))
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);", html)

    def test_static_selected_material_background_keeps_selected_tint(self) -> None:
        html = read_static_source()

        self.assertIn(".material-option.selected {\n      background: rgba(17, 138, 88, 0.12);", html)
        self.assertIn(".material-option.selected:hover {\n      background: rgba(17, 138, 88, 0.16);", html)
        self.assertIn(".material-selected-badge", html)
        self.assertIn("background: rgba(17, 138, 88, 0.16);\n      color: var(--ok);", html)
        self.assertIn(".report-badge.ok {\n      color: var(--ok);\n      background: transparent;", html)
        self.assertIn(".asset-chip.archived {\n      color: var(--ok);\n      background: transparent;", html)
        self.assertIn(".asset-chip.ok {\n      color: var(--ok);\n      background: transparent;", html)
        self.assertIn(".pill.ok { background: #0f7a55; color: #fff; border-color: #0f7a55; }", html)
        self.assertNotIn(".material-option.selected {\n      background: transparent;", html)
        self.assertNotIn(".material-option.selected:hover {\n      background: transparent;", html)
        self.assertNotIn(".report-badge.ok {\n      color: var(--ok);\n      background: #e8f7f0;", html)
        self.assertNotIn(".asset-chip.archived {\n      color: var(--ok);\n      background: #e8f7f0;", html)
        self.assertNotIn(".asset-chip.ok {\n      color: var(--ok);\n      background: #e8f7f0;", html)

    def test_static_composer_surface_keeps_tab_style(self) -> None:
        html = read_static_source()

        self.assertIn(".system-prompt-drawer {", html)
        self.assertIn("border-radius: 0 8px 0 0;\n      background: transparent;", html)
        self.assertIn("border-width: 1px 1px 0;\n      border-top-right-radius: 0;\n      background: #fff;", html)
        self.assertIn(".system-prompt-entry-button {", html)
        self.assertIn("scheduleSystemPromptAutoSave(event.target.value);", html)
        self.assertIn("await saveSystemPromptContent(event.target.value);", html)
        self.assertIn("if (value == null) {", html)
        self.assertIn("border: 1px solid rgba(37, 99, 235, 0.28);\n      background: rgba(255, 255, 255, 0.96);\n      color: #24549f;", html)
        self.assertIn(".system-prompt-entry-button:hover {\n      background: rgba(255, 255, 255, 0.98);", html)
        self.assertIn(".system-prompt-entry-button.is-open {\n      background: #fff;", html)
        self.assertIn("border-bottom: 0;\n      box-shadow: none;\n    }", html)
        self.assertIn(".system-prompt-entry-button.is-open::before {", html)
        self.assertNotIn(".system-prompt-entry-button::before {", html)
        self.assertIn("bottom: -1px;\n      height: 1px;\n      background: inherit;", html)
        self.assertIn("padding: 6px 28px 190px;", html)
        self.assertIn(".composer-wrap {\n      position: absolute;\n      left: 0;\n      right: 0;\n      bottom: 0;\n      padding: 0 28px 28px;\n      pointer-events: none;", html)
        self.assertIn("--composer-tool-gap: 16px;", html)
        self.assertIn("align-items: flex-end;\n      gap: var(--composer-tool-gap);", html)
        self.assertIn("box-shadow: none !important;\n      pointer-events: auto;\n    }\n\n    .composer {", html)
        self.assertIn("border-top-right-radius: 0;\n      background: #fff;\n      box-shadow: none;\n      pointer-events: auto;", html)
        self.assertIn("border-color: rgba(37, 99, 235, 0.22);\n      border-top-left-radius: 0;", html)
        self.assertNotIn("border-top-color: transparent;\n      border-top-left-radius: 0;", html)
        self.assertIn("border-top: 0 solid rgba(37, 99, 235, 0.22);\n      padding: 0 12px;", html)
        self.assertIn("border-top-width: 1px;\n      padding-block: 12px;", html)
        self.assertIn(".composer-wrap:has(#backgroundMusicButton.is-open) .system-prompt-drawer.open,", html)
        self.assertIn(".composer-wrap:has(#defaultReferenceButton.is-open) .system-prompt-drawer.open,", html)
        self.assertIn(".composer-wrap:has(#scriptReferenceButton.is-open) .system-prompt-drawer.open,", html)
        self.assertIn(".composer-wrap:has(#flowerTextButton.is-open) .system-prompt-drawer.open,", html)
        self.assertIn(".composer-wrap:has(#generationModeButton.is-open) .system-prompt-drawer.open,", html)
        self.assertIn(".composer-wrap:has(#htmlMotionOverlayButton.is-open) .system-prompt-drawer.open {\n      border-top-right-radius: 0;", html)
        self.assertIn(".generation-mode-entry-button.is-open {\n      border-top-right-radius: 8px;", html)
        self.assertIn("#flowerTextDrawer.open {\n      position: relative;\n      overflow: visible;\n      z-index: 300;", html)
        self.assertNotIn("#flowerTextDrawer.open {\n      position: relative;\n      overflow: visible;\n      z-index: 80;", html)
        self.assertNotIn(".system-prompt-entry-button:not(:last-child)::after {", html)
        self.assertNotIn(".system-prompt-entry-button:hover {\n      background: rgba(255, 255, 255, 0.98);\n      border-color: rgba(37, 99, 235, 0.42);\n      border-bottom: 0;", html)
        self.assertNotIn("box-shadow: var(--composer-tool-gap) 0 0 rgba(232, 240, 251, 0.62);", html)
        self.assertNotIn("var(--composer-tool-gap) 0 0 rgba(232, 240, 251, 0.62),", html)
        self.assertNotIn(".system-prompt-entry-button + .system-prompt-entry-button {\n      margin-left: -1px;", html)
        self.assertNotIn(".system-prompt-entry-button:not(:first-child) {\n      border-top-left-radius: 0;", html)
        self.assertNotIn(".system-prompt-entry-button:not(:last-child) {\n      border-top-right-radius: 0;", html)
        self.assertNotIn("border-radius: 8px 8px 0 0;\n      background: rgba(255, 255, 255, 0.86) !important;", html)
        self.assertNotIn("border-radius: 8px;\n      border: 1px solid transparent;\n      background: transparent;", html)
        self.assertNotIn("align-items: flex-end;\n      gap: 0;", html)

    def test_static_surface_contains_user_recycle_bin_entry(self) -> None:
        html = read_static_source()

        self.assertIn('id="recycleBinList"', html)
        self.assertIn('id="recycleBinModal"', html)
        self.assertIn("async function refreshRecycleBin()", html)
        self.assertIn("function renderRecycleBin()", html)
        self.assertIn("function renderRecycleBinModal()", html)
        self.assertIn("function buildRecycleBinCardMarkup(item)", html)
        self.assertIn("function humanizeRecycleBinReason(value)", html)
        self.assertIn("async function openUserRecycleBinFolder(trigger)", html)
        self.assertIn("fetch('/api/user-recycle-bin?limit=100')", html)
        self.assertIn("fetch('/api/open-user-recycle-bin-folder'", html)
        self.assertIn(
            "视频后处理失败，背景音乐或原声音轨合成没有完成。请重新生成，或先关闭背景音乐后再试。",
            html,
        )
        self.assertIn("<summary>技术详情</summary>", html)
        self.assertNotIn("item?.jobId ? `任务 ${item.jobId}` : ''", html)
        self.assertIn(
            "els.recycleBinSub.textContent = `${Number(bin.count || items.length || 0)} 个失败任务。失败但已产出视频的任务会放到这里。`;",
            html,
        )
        self.assertIn(
            '<div class="material-meta">${escapeHtml(`${count} 个失败任务`)}</div>',
            html,
        )
        self.assertNotIn(
            "count ? `${count} 个失败任务` : '失败但已产出视频的任务会放到这里。'",
            html,
        )
        self.assertNotIn(
            '<button type="button" class="material-add-button" data-open-user-recycle-bin-folder>打开文件夹</button>',
            html,
        )
        self.assertNotIn(
            "还没有失败片段。只有已经生成出至少一个视频但整条任务失败时，才会进入这里。",
            html,
        )
        self.assertNotIn(
            "真实视频任务仍在后台执行，完成后会自动回填到当前对话和资产库。",
            html,
        )
        self.assertNotIn(
            "已接入后端真实生成状态：共",
            html,
        )

    def test_static_material_library_cards_include_delete_action(self) -> None:
        html = read_static_source()

        self.assertIn(".material-wall-entry-actions {", html)
        self.assertIn(".material-wall-delete-button {", html)
        self.assertIn("data-delete-user-material-kind", html)
        self.assertIn("data-delete-user-material-path", html)
        self.assertIn("data-delete-user-material-name", html)
        self.assertIn("fetch('/api/delete-user-material'", html)
        self.assertIn("event.stopPropagation();", html)
        self.assertIn("确定删除素材“${materialName}”？删除后会立刻从素材库移除。", html)
        self.assertIn("剧本知识库", html)
        self.assertIn("data-script-knowledge-document", html)
        self.assertIn("/api/script-knowledge", html)
        self.assertIn("PostgreSQL 词法检索 · pg_trgm + tsvector · 无 Embedding", html)
        self.assertNotIn(
            "? '后台真实进度'",
            html,
        )


























if __name__ == "__main__":
    unittest.main()
