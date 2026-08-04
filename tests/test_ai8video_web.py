from __future__ import annotations

import io
import os
import struct
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
from ai8video.interfaces.web.routes import smart_image_editor as smart_image_routes
from ai8video.generation import generation_progress
from ai8video.generation.reference_image_preprocessor import (
    ReferenceImagePreprocessError,
    build_smart_image_edit_prompt,
)
from ai8video.radar import hot_topic
from ai8video.radar import hot_topic_feeds
from ai8video.application import runtime as ai8video_runtime
from ai8video.assets import user_materials as ai8video_user_materials
from ai8video.assets.asset_store import JsonlAssetStore
from ai8video.core.models import VideoPrompt
from ai8video.interfaces.web.static_bundle import read_workbench_script, workbench_script_paths
from ai8video.media import tts_timeline_review
from ai8video.media import timeline_boundary
from ai8video.media import tts_waveform
from ai8video.media import video_timeline_review
from ai8video.media.background_music_track import _merged_bgm_command, build_hidden_bgm_timeline
from ai8video.media.merged_preview_tracks import (
    edited_video_durations,
    merged_edited_video_chunks,
    merged_tts_chunks,
    merged_video_chunks,
)
from ai8video.media.motion import html_motion_review
from ai8video.media.motion import html_motion_merge
from ai8video.media.motion import hyperframes_overlay_renderer


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
    def test_rollback_latest_tail_frame_result_restores_wait_and_resets_downstream(self) -> None:
        progress = {
            "items": [
                {"videoIndex": 1, "status": "succeeded"},
                {"videoIndex": 2, "status": "succeeded"},
                {
                    "videoIndex": 3,
                    "status": "succeeded",
                    "videoPrompt": "third",
                    "assetRecord": {"archiveKey": "video/third.mp4"},
                },
                {"videoIndex": 4, "status": "awaiting_tail_frame_continue", "tailFramePreviewUrl": "/old.png"},
                {"videoIndex": 5, "status": "pending_submission"},
            ]
        }
        checkpoint = SimpleNamespace(next_video_index=3, preview_url=lambda: "/video-3-reference.png")
        ledger_writer = Mock()
        with patch.object(
            ai8video_web, "get_generation_batch_family_snapshot",
            return_value={"progress": progress},
        ), patch.object(
            ai8video_web, "_delete_user_generated_video",
            return_value={"deleted": ["video/third.mp4"]},
        ), patch.object(JsonlAssetStore, "read_all", return_value=[]), patch.object(
            ai8video_web, "prepare_rollback_tail_frame_resume", return_value=checkpoint,
        ), patch.object(ai8video_web, "TaskLedger", return_value=ledger_writer):
            result = ai8video_web._rollback_latest_tail_frame_result({
                "sessionId": "session-1",
                "generationBatchId": "batch-1",
                "videoIndex": 3,
                "userGeneratedKey": "video/third.mp4",
            })

        items = result["generationProgress"]["items"]
        self.assertEqual(items[2]["status"], "awaiting_tail_frame_continue")
        self.assertEqual(items[2]["tailFramePreviewUrl"], "/video-3-reference.png")
        self.assertEqual(items[3]["status"], "pending_submission")
        self.assertNotIn("tailFramePreviewUrl", items[3])
        self.assertEqual(items[4]["status"], "pending_submission")
        ledger_writer.upsert_generation_batch.assert_called_once()

    def test_rollback_accepts_latest_result_from_child_batch(self) -> None:
        progress = {
            "items": [
                {"videoIndex": 1, "status": "succeeded"},
                {
                    "videoIndex": 2,
                    "status": "succeeded",
                    "childGenerationBatchId": "child-2",
                    "assetRecord": {"archiveKey": "video/second.mp4"},
                },
                {"videoIndex": 3, "status": "pending_submission"},
            ]
        }
        child_ledger = {
            "status": "completed",
            "phase": "completed",
            "progress": {"items": [{"videoIndex": 2, "status": "succeeded"}]},
        }
        checkpoint = SimpleNamespace(next_video_index=2, preview_url=lambda: "/video-2-reference.png")
        ledger_writer = Mock()
        with patch.object(
            ai8video_web, "get_generation_batch_family_snapshot", return_value={"progress": progress},
        ), patch.object(
            ai8video_web, "get_generation_ledger_snapshot", return_value=child_ledger,
        ), patch.object(
            ai8video_web, "_delete_user_generated_video", return_value={"deleted": ["video/second.mp4"]},
        ), patch.object(JsonlAssetStore, "read_all", return_value=[]), patch.object(
            ai8video_web, "prepare_rollback_tail_frame_resume", return_value=checkpoint,
        ), patch.object(ai8video_web, "TaskLedger", return_value=ledger_writer):
            result = ai8video_web._rollback_latest_tail_frame_result({
                "sessionId": "session-1",
                "generationBatchId": "root-1",
                "videoIndex": 2,
                "userGeneratedKey": "video/second.mp4",
            })

        self.assertEqual(result["generationProgress"]["items"][1]["status"], "awaiting_tail_frame_continue")
        child_write = ledger_writer.upsert_generation_batch.call_args_list[1].kwargs
        self.assertEqual(child_write["generation_batch_id"], "child-2")
        self.assertEqual(child_write["progress"]["items"][0]["status"], "awaiting_tail_frame_continue")

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

    def test_smart_split_feedback_only_applies_to_replan_action(self) -> None:
        source = read_static_source()
        user_message_start = source.index(".message.user .bubble > p {")
        user_message_end = source.index("}", user_message_start)
        user_message_style = source[user_message_start:user_message_end]

        self.assertIn("data-smart-split-feedback", source)
        self.assertIn("重新分集意见（可选）", source)
        self.assertIn("data-smart-split-feedback-drawer hidden", source)
        self.assertIn("data-smart-split-feedback-toggle aria-expanded=\"false\"", source)
        self.assertIn("data-smart-split-feedback-submit", source)
        self.assertNotIn("填写后提交；“确认并继续”不会使用这里的内容。", source)
        self.assertIn("smart-split-confirmation-card", source)
        self.assertIn("renderSmartSplitPlanOverview", source)
        self.assertIn('class="smart-split-plan-node"', source)
        self.assertIn("data-smart-split-plan-toggle", source)
        self.assertIn("smart-split-plan-prompt", source)
        self.assertIn("index === 0 && !isSmartSplitConfirmation", source)
        self.assertIn("data-smart-split-confirm-action", source)
        self.assertIn("data-smart-split-cancel-action", source)
        self.assertIn("data-smart-split-hide-on-feedback", source)
        self.assertIn("action.kind === 'dismiss-plan'", source)
        self.assertIn("function smartSplitActionRank(action)", source)
        self.assertIn("if (value === '重新分集') return 0", source)
        self.assertIn("if (value === '确认分集') return 1", source)
        self.assertIn("if (String(action?.kind || '').trim() === 'dismiss-plan') return 2", source)
        self.assertIn(".smart-split-confirmation-card .guide-actions .guide-action-button", source)
        self.assertIn("flex: 0 0 108px;", source)
        self.assertIn("[data-smart-split-cancel-action]", source)
        self.assertIn("flex: 0 0 54px;", source)
        self.assertIn("width: 54px;", source)
        self.assertIn("margin-left: auto;", source)
        self.assertIn("opacity 180ms ease", source)
        self.assertIn("visibility 0s linear 180ms", source)
        self.assertIn("data-smart-split-feedback-toggle][aria-expanded=\"true\"]", source)
        self.assertIn(".smart-split-feedback-drawer .smart-split-feedback-submit", source)
        self.assertIn("width: 100%;", source)
        self.assertIn("if (actionKind !== 'send')", source)
        self.assertIn("if (text !== '重新分集')", source)
        self.assertIn("setSmartSplitFeedbackMode(card, drawer.hidden)", source)
        self.assertIn("action.disabled = shouldOpen", source)
        self.assertIn("action.setAttribute('aria-hidden'", source)
        self.assertIn("block: 'center'", source)
        self.assertIn("const replanText = /^(?:重新分集|重分|重新规划)\\s*[：:]?/.test(feedback)", source)
        self.assertIn("? feedback", source)
        self.assertIn(": (feedback ? `重新分集：${feedback}` : text);", source)
        self.assertIn("const isReplanMessage = /^(?:重新分集|重分|重新规划)(?:[：:].*)?$/.test(compactMessage);", source)
        self.assertIn("if (!confirmationMessages.has(compactMessage) && !isReplanMessage) return null;", source)
        self.assertIn("trigger?.closest?.('.guide-card')", source)
        self.assertIn("if (actionKind === 'dismiss-plan')", source)
        self.assertIn("fetch('/api/chat-plan-cancel'", source)
        self.assertIn("function dismissSmartSplitMessage(trigger, options = {})", source)
        self.assertIn("function getSmartSplitDismissRange(session, targetMessage)", source)
        self.assertIn("session.messages[startIndex - 1]?.role === 'user'", source)
        self.assertIn("function fadeSmartSplitMessages(session, targetMessage)", source)
        self.assertIn("function removeSmartSplitMessages(session, targetMessage)", source)
        self.assertIn("range.currentIndex - range.startIndex + 1", source)
        self.assertIn("continuationClosed: true", source)
        self.assertIn("if (data.cancelled !== true && !options.allowResetSession)", source)
        self.assertIn("function isConversationContinuationClosed(payload)", source)
        self.assertIn("!isSessionPending(targetSession)", source)
        self.assertIn("is-smart-split-dismissing", source)
        self.assertIn("animation: smart-split-message-fade 220ms ease-in forwards", source)
        self.assertIn("@keyframes smart-split-message-fade", source)
        self.assertNotIn("smart-split-dismiss-particle", source)
        self.assertIn("prefers-reduced-motion: reduce", source)
        self.assertIn("wrap.dataset.messageIndex = String(messageIndex)", source)
        self.assertIn("@keyframes smart-split-feedback-drawer-open", source)
        self.assertIn("white-space: pre-wrap;", user_message_style)
        self.assertIn("overflow-wrap: anywhere;", user_message_style)

    def test_pending_only_bubble_uses_compact_content_width(self) -> None:
        source = read_static_source()
        rule_start = source.index(".message:not(.user) .bubble.pending-only {")
        rule_end = source.index("}", rule_start)
        rule = source[rule_start:rule_end]

        self.assertIn("width: fit-content;", rule)
        self.assertIn("max-width: min(100%, 760px);", rule)

    def test_agent_result_thumbnail_is_visually_separated_from_progress_card(self) -> None:
        source = read_static_source()
        rule_start = source.index(".message:not(.user) .bubble.agent-run-with-results {")
        rule_end = source.index("}", rule_start)
        rule = source[rule_start:rule_end]

        self.assertIn("const pendingThumbnails = renderAgentVideoThumbnails(displayedPending);", source)
        self.assertIn("${pendingThumbnails}", source)
        self.assertIn("const hasPendingCard = directChildren.some", source)
        self.assertIn("const hasAgentVideoResults = directChildren.some", source)
        self.assertIn("'agent-run-with-results',", source)
        self.assertIn("directChildren.length === 2 && hasPendingCard && hasAgentVideoResults", source)
        self.assertIn("display: grid;", rule)
        self.assertIn("width: fit-content;", rule)
        self.assertIn("padding: 0;", rule)
        self.assertIn("background: transparent;", rule)
        self.assertIn(".bubble.agent-run-with-results > .agent-video-results", source)

    def test_historical_completion_guide_is_not_rendered(self) -> None:
        source = read_static_source()

        self.assertIn(
            "const isHistoricalMessage = Number(context.messageIndex) < Number(context.messageCount) - 1;",
            source,
        )
        self.assertIn("function getActiveConversationAwaiting(session)", source)
        self.assertIn("const activeAwaiting = getActiveConversationAwaiting(session);", source)
        self.assertIn("const guideAwaiting = String(payload.awaiting || '').trim();", source)
        self.assertIn("guideAwaiting === activeAwaiting", source)
        self.assertIn("const historicalPending = isHistoricalMessage;", source)
        self.assertIn("if (payload.meta?.guide && isActiveGuide)", source)

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
        self.assertNotIn('id="hotRadarSelectedTopic"', source)
        self.assertNotIn('class="hot-radar-detail-panel"', modal_source)
        self.assertIn('hot-radar-topic-preview', source)
        self.assertIn('data-hot-radar-action="summary"', source)
        self.assertIn('data-hot-radar-action="prompt"', source)
        self.assertIn('data-hot-radar-action="fill"', source)
        self.assertIn('data-hot-radar-summary-output', source)
        self.assertIn("#hotRadarModal #hotRadarTopicList .hot-radar-topic-meta", source)
        self.assertIn('class="hot-radar-topic-meta-item"', source)
        self.assertIn("flex-wrap: wrap", source)
        self.assertIn("#hotRadarModal #hotRadarTopicList .hot-radar-topic-card > *", source)
        self.assertIn("min-inline-size: 0", source)
        self.assertIn("overflow-wrap: anywhere", source)
        self.assertIn("统一热点雷达实际控件的蓝紫玻璃状态", source)
        self.assertIn('id="hotRadarSourceSelect"', source)
        self.assertIn('class="hot-radar-filter-toolbar"', source)
        self.assertIn('hot-radar-topic-details', source)
        self.assertIn('function selectHotRadarTopicCard(topicCard)', source)
        self.assertIn('function buildHotRadarTopicListMarkup(items, hotRadar, twoColumns)', source)
        self.assertIn('hot-radar-topic-column', source)
        self.assertIn('data-hot-radar-column="0"', source)
        self.assertIn("卡片作 grid 直子项时 overflow:hidden 会把标题行压成 0 高", source)
        self.assertIn("window.matchMedia('(min-width: 901px)').matches", source)
        self.assertIn("min-height: min-content", source)
        self.assertIn("grid-template-rows: auto auto", source)
        self.assertIn('is-expanded', source)
        self.assertIn("#hotRadarModal #hotRadarTopicList .hot-radar-topic-details {\n      max-height: 0;\n      opacity: 0;\n      overflow: hidden;", source)
        self.assertIn("#hotRadarModal #hotRadarTopicList .hot-radar-topic-preview {\n      max-height: 220px;\n      overflow: auto;", source)
        self.assertIn("#hotRadarModal .hot-radar-content-grid {\n      min-height: 0;\n      display: grid;\n      grid-template-columns: minmax(0, 1fr);", source)
        self.assertIn("#hotRadarModal .hot-radar-hotlist-panel {\n      min-height: 0;\n      display: flex;\n      flex-direction: column;\n      overflow: hidden;", source)
        self.assertIn("#hotRadarModal #hotRadarTopicList {\n        flex: 1 1 auto;\n        min-block-size: 0;\n        overflow-x: hidden;\n        overflow-y: auto;\n      }", source)
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
        self.assertIn("#videoPreviewBody .video-preview-stage-grid.extension-active .video-preview-extension-close-button { display: inline-flex; }", source)
        self.assertNotIn("extension-has-video", source)
        self.assertNotIn("if (!String(savedState.rightVideoKey || '').trim() || !String(savedState.rightVideoUrl || '').trim()) return;", source)
        self.assertIn('aria-label="删除右侧延长内容"', source)
        self.assertIn("function hasActiveVideoPreviewExtensionState(userGeneratedKey)", source)
        self.assertIn("async function discardDetachedVideoPreviewExtensionResult(leftKey, rightKey, preserveFrameState = false)", source)
        self.assertIn("function collectVideoPreviewExtensionResultKeys(savedState = {})", source)
        self.assertIn("savedState.batchFrames.map((frame) => frame?.userGeneratedKey)", source)
        self.assertIn("await deleteVideoPreviewExtensionAssets(key, savedState)", source)
        self.assertIn("if (deleteExtensionButton) deleteExtensionButton.disabled = false;", source)
        self.assertIn("function setVideoPreviewMainControlsDisabled(disabled)", source)
        self.assertIn("data-extension-disabled-before", source)
        self.assertIn("messageCount: session.messages.length", source)
        self.assertIn("const historicalPending = isHistoricalMessage;", source)
        self.assertIn("这是较早消息的进度记录，不再显示为执行中。", source)
        self.assertIn("pending-card${staticPending ? ' is-history' : ''}", source)
        self.assertIn("function buildHistoricalPendingSnapshot(pending = {}, statusLabel = '历史进度快照')", source)
        self.assertIn("isActive: !staticPending", source)
        self.assertIn("historicalSnapshot: true", source)
        self.assertIn("if (itemWithBatch.historicalSnapshot) return buildProgressStatusResultItem(itemWithBatch, index, progressItems);", source)
        self.assertIn('data-video-preview-action="edit-video-prompt"', source)
        self.assertIn("async function openVideoPromptEditor(userGeneratedKey)", source)
        self.assertIn("async function generateVideoPreviewExtension(userGeneratedKey, button)", source)
        self.assertIn("async function syncVideoPreviewExtensionGenerateButton(userGeneratedKey)", source)
        self.assertIn("function updateVideoPreviewExtensionState(userGeneratedKey, patch)", source)
        self.assertIn("generationStartedAt", source)
        self.assertIn("const generationSessionId = `extension-${parentSessionId}-${generationNonce}`;", source)
        self.assertNotIn("const generationSessionId = String(state.activeId || '').trim();", source)
        self.assertIn("sessionId: generationSessionId", source)
        self.assertIn("mode === 'replace' ? '重新生成视频' : '延长视频'", source)
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
        self.assertIn("async function prepareVideoExtensionPreview(userGeneratedKey, button, savedState = null, options = {})", source)
        self.assertIn("if (mode === 'replace' && !savedState)", source)
        self.assertIn("postVideoPrompt(key, undefined, 'original')", source)
        self.assertIn("data-icon=\"regenerate\"", source)
        self.assertIn(".video-preview-regenerate-button", source)
        self.assertIn("width: 92px", source)
        self.assertIn("#resultModalBody", source)
        self.assertIn("padding: 4px", source)
        self.assertIn("height: 36px", source)
        self.assertIn("border: 1px solid rgba(77, 116, 255, 0.28)", source)
        self.assertIn(".result-notify-card.is-batch-selected", source)
        self.assertIn("border-radius: 12px", source)
        self.assertIn('id="videoPreviewStatus"', source)
        self.assertIn("function setVideoPreviewHeaderStatus(message = '', tone = '')", source)
        self.assertIn("'已生成替换视频' : '已生成延长视频'", source)
        self.assertNotIn("setVideoPreviewButtonLabel(activeButton, mode === 'replace' ? '已生成替换视频'", source)
        self.assertIn("fontawesome-free-7.3.1-desktop/svgs-full/solid/arrows-rotate.svg", source)
        self.assertIn("fontawesome-free-7.3.1-desktop/svgs-full/solid/arrow-right-long.svg", source)
        self.assertIn("VIDEO_PREVIEW_EXTENSION_STORAGE_KEY", source)
        self.assertIn("async function saveVideoPreviewExtensionFrame(userGeneratedKey, frameTime)", source)
        self.assertIn("fetch('/api/user-generated-results/extension-frame'", source)
        self.assertIn("function restoreVideoPreviewExtensionState(video, userGeneratedKey, extendButton, regenerateButton)", source)
        self.assertIn("video.addEventListener('loadeddata', seekSavedFrame", source)
        self.assertIn("video.addEventListener('seeked', () => void renderSavedFrame()", source)
        self.assertIn("videoOutputTimeToSourceTime(previewTime)", source)
        self.assertIn("const previewTime = Math.max(0, Number(savedState?.previewTime ?? video.currentTime));", source)
        self.assertNotIn("const previewTime = mode === 'replace'", source)
        self.assertIn('alt="原视频当前时间点截图"', source)
        self.assertIn("video.pause()", source)
        self.assertIn("mode === 'replace' ? '重新生成' : '延长视频'", source)
        self.assertIn(".video-preview-merge-control", source)
        self.assertIn("data-video-preview-merge disabled>待生成", source)
        self.assertIn("function videoPreviewExtensionActionLabel(mode, phase = 'ready')", source)

        self.assertIn("function syncVideoPreviewMergeAvailability()", source)
        self.assertIn("async function confirmVideoPreviewExtensionBatch()", source)
        self.assertIn("stage.dataset.videoKey = selected.userGeneratedKey || ''", source)
        self.assertIn("if (actionBar) actionBar.hidden = true", source)
        self.assertIn("await discardDetachedVideoPreviewExtensionResult(key, rightKey, true);", source)
        self.assertIn("async function mergeExtendedPreviewVideos(leftKey, button)", source)
        self.assertIn("'/api/user-generated-results/replace'", source)
        self.assertIn("'/api/user-generated-results/merge'", source)
        self.assertIn('data-video-preview-action="regenerate-video"', source)
        self.assertIn("data-video-preview-merge disabled>待生成", source)
        self.assertIn("data-video-preview-cut-guide", source)
        self.assertIn("function bindTimelinePlayheadDrag", source)
        self.assertIn("function handleVideoTimelineSpacePlayback", source)
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
        self.assertIn("grid-template-columns: minmax(0, 1fr)", source)
        self.assertIn("HOT_RADAR_SNAPSHOT_STORAGE_KEY", source)
        self.assertIn("function loadHotRadarSnapshot()", source)
        self.assertIn("function persistHotRadarSnapshot(hotRadar)", source)
        self.assertIn('点击“刷新”获取最新热榜', source)
        self.assertIn('<h1 id="hotRadarTitle">热点雷达</h1>', modal_source)
        self.assertIn('class="hot-radar-brand-glyph"', modal_source)
        self.assertNotIn('id="hotRadarMarkGradient"', modal_source)
        self.assertNotIn('<header class="hot-radar-header">', modal_source)
        self.assertLess(
            modal_source.index('id="hotRadarRefreshButton"'),
            modal_source.index('class="hot-radar-app-shell"'),
        )
        self.assertLess(
            modal_source.index('id="hotRadarCloseButton"'),
            modal_source.index('class="hot-radar-app-shell"'),
        )
        self.assertNotIn('<div id="hotRadarDetailMeta" class="hot-radar-detail-sub">热点摘要</div>', modal_source)
        self.assertIn("公开热点聚合与选题工作台", modal_source)
        self.assertIn("热点来源", modal_source)
        self.assertNotIn('class="hot-radar-sidebar-footer"', modal_source)
        self.assertNotIn('id="hotRadarFetchRouteBadge"', modal_source)
        self.assertIn('id="hotRadarSourceManagerModal"', source)
        self.assertIn('value="__add_source__">新增数据源…', source)
        self.assertNotIn('value="__add_source__">＋', source)
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

    def test_smart_image_editor_uses_configured_image_model_and_precedes_hot_radar(self) -> None:
        source = read_static_source()
        editor_source = (STATIC_ROOT / "scripts" / "17a-smart-image-editor.js").read_text(encoding="utf-8")
        canvas_source = (STATIC_ROOT / "scripts" / "17b-smart-image-canvas.js").read_text(encoding="utf-8")
        history_source = (STATIC_ROOT / "scripts" / "17c-smart-image-history-export.js").read_text(encoding="utf-8")
        smart_image_style = (STATIC_ROOT / "styles" / "20-smart-image-editor.css").read_text(encoding="utf-8")

        self.assertLess(
            source.index('data-open-smart-image-editor-entry'),
            source.index('data-open-hot-radar-entry'),
        )
        self.assertIn("导入、描述、对比并导出副本", source)
        self.assertIn('data-smart-image-action="enqueue"', source)
        self.assertIn('aria-label="生成结果数量"', editor_source)
        self.assertIn("id: 'portrait'", editor_source)
        self.assertNotIn('data-smart-image-preset="${preset.id}"', editor_source)
        self.assertIn('id="smartImageCompareRange"', source)
        self.assertIn('<option value="jpeg">JPEG</option>', editor_source)
        self.assertIn('<option value="webp">WebP</option>', editor_source)
        self.assertIn('data-smart-image-ratio="9:16"', source)
        self.assertIn("SMART_IMAGE_MAX_EDGE = 4096", source)
        self.assertIn("canvas.toBlob(resolve, 'image/png')", source)
        self.assertIn("fetch('/api/smart-image-editor/render'", source)
        self.assertIn("fetch('/api/smart-image-editor/optimize-prompt'", source)
        self.assertIn("form.append('prompt'", source)
        self.assertIn("-智能修图.png", source)
        self.assertIn("AI8VIDEO_IMAGE_MODEL", editor_source)
        self.assertNotIn("去水印", editor_source)
        self.assertIn("const AI8SmartImage", source)
        self.assertNotIn('id="smartImageSourceCard"', source)
        self.assertNotIn('id="smartImageUploadInput"', source)
        self.assertIn('id="smartImageLibraryList"', source)
        self.assertIn('data-smart-image-action="manage-library"', source)
        self.assertIn('data-smart-image-action="save-library"', source)
        self.assertIn("data-edit-smart-image-material", source)
        self.assertIn("fetch('/api/upload-user-material'", source)
        self.assertIn("refreshUserMaterials()", source)
        self.assertNotIn("smartImageToolButton('mask'", source)
        self.assertNotIn('aria-label="无限画布"', source)
        self.assertIn("任务队列", editor_source)
        self.assertIn("前后对比", editor_source)
        self.assertIn("选择图片", editor_source)
        self.assertIn("描述并生成", editor_source)
        self.assertNotIn("想把图片修成什么样？", editor_source)
        self.assertIn('id="smartImagePrompt" aria-label="图片修改描述"', editor_source)
        self.assertNotIn("smartImageCallHint", editor_source)
        self.assertNotIn("本任务预计调用图片模型", canvas_source)
        self.assertIn("对比并导出", editor_source)
        self.assertIn("可选：快速微调", editor_source)
        self.assertNotIn("选择修图方案", editor_source)
        self.assertNotIn("smart-image-preset-grid", editor_source)
        self.assertIn('id="smartImageJobSection" class="smart-image-job-section smart-image-sidebar-job-section"', editor_source)
        self.assertEqual(editor_source.count('id="smartImageJobSection"'), 1)
        self.assertLess(editor_source.index('smart-image-library-section'), editor_source.index('id="smartImageJobSection"'))
        self.assertNotIn('id="smartImageJobOwner"', editor_source)
        self.assertIn('class="smart-image-brand-mark" aria-hidden="true"', editor_source)
        self.assertIn("fontawesome-free-7.3.1-desktop/svgs-full/solid/wand-magic-sparkles.svg", smart_image_style)
        self.assertIn("最近选择 · 最多 6 张", editor_source)
        self.assertEqual(editor_source.count("smart-image-step-heading smart-image-step-heading--inline"), 3)
        self.assertIn(".smart-image-step-heading--inline > div { display: flex; align-items: baseline; gap: 6px; }", smart_image_style)
        self.assertIn("SMART_IMAGE_RECENT_LIBRARY_LIMIT = 6", editor_source)
        self.assertIn("recentLibraryHistory", source)
        self.assertIn("smart-image-library-slot", source)
        self.assertIn(".smart-image-library-card img { width: 100%; aspect-ratio: 1; object-fit: contain;", smart_image_style)
        self.assertIn(".smart-image-result-thumb img { width: 100%; height: 49px; display: block; object-fit: contain;", smart_image_style)
        self.assertNotIn("smartImageLibraryItems().slice(0, 8)", source)
        self.assertNotIn("smart-image-source-ready", source)
        self.assertNotIn("is-file-dragging", source)
        self.assertIn("当前任务结果", editor_source)
        self.assertIn("修图任务 · 目标", canvas_source)
        self.assertNotIn("const label = { queued:", canvas_source)
        self.assertNotIn("escapeHtml(job.error || job.prompt)", canvas_source)
        self.assertNotIn(".smart-image-job-copy > span", smart_image_style)
        self.assertNotIn(".smart-image-job small", smart_image_style)
        self.assertNotIn(".smart-image-sidebar-job-section .smart-image-job-copy { grid-template-columns: 1fr;", smart_image_style)
        self.assertIn(".smart-image-sidebar-job-section .smart-image-job { align-items: center; }", smart_image_style)
        self.assertIn("grid-template-columns: 24px minmax(0, 1fr); align-items: center; gap: 6px;", smart_image_style)
        self.assertIn(".smart-image-job-actions { align-self: stretch; display: flex; align-items: center;", smart_image_style)
        self.assertIn(".smart-image-sidebar-job-section .smart-image-job-actions { align-self: stretch; }", smart_image_style)
        self.assertNotIn(".smart-image-sidebar-job-section .smart-image-job-actions { align-self: start; }", smart_image_style)
        self.assertIn("display: inline-grid; place-items: center;", smart_image_style)
        self.assertNotIn("-webkit-line-clamp: 2", smart_image_style)
        self.assertIn("AI8SmartImage.state.jobs.length", canvas_source)
        self.assertIn('data-smart-image-job="${job.id}"', canvas_source)
        self.assertIn("smartImageVisibleResults()", canvas_source)
        self.assertEqual(editor_source.count('data-smart-image-action="export-current"'), 1)
        self.assertEqual(editor_source.count('data-smart-image-action="save-library"'), 1)
        self.assertNotIn('data-smart-image-result="source"', canvas_source)
        self.assertIn("导出为副本，不覆盖原图", editor_source)
        self.assertNotIn("smart-image-rights-notice", editor_source)
        self.assertNotIn("仅处理你有权使用的图片", editor_source)
        self.assertIn("container-type: size", smart_image_style)
        self.assertIn("grid-template-columns: 286px minmax(390px, 1fr) 304px", smart_image_style)
        self.assertIn("grid-template-columns: 273px minmax(360px, 1fr) 292px", smart_image_style)
        self.assertIn("grid-template-columns: 255px minmax(340px, 1fr) 280px", smart_image_style)
        self.assertIn("grid-template-rows: 40px minmax(280px, 1fr) auto; gap: 6px;", smart_image_style)
        self.assertIn("aspect-ratio: var(--preview-ratio)", smart_image_style)
        self.assertIn("width: min(92cqw, calc(92cqh * var(--preview-ratio)))", smart_image_style)
        self.assertIn(".smart-image-preview-head > div:first-child { min-width: 0; display: flex; align-items: center; gap: 8px;", smart_image_style)
        self.assertIn("min-height: 29px; flex: 0 1 auto; display: inline-flex; align-items: center;", smart_image_style)
        self.assertIn("font-size: 18px; font-weight: 800; line-height: 32px;", smart_image_style)
        self.assertNotIn("smart-image-eyebrow", editor_source)
        self.assertNotIn("smart-image-preview-head .smart-image-eyebrow", smart_image_style)
        self.assertNotIn("smartImagePreviewMeta", editor_source)
        self.assertNotIn("previewMeta", canvas_source)
        self.assertNotIn("height: min(92%, calc(92% / var(--preview-ratio)))", smart_image_style)
        self.assertIn("SIDEBAR_NAV_ICON_NAMES", source)
        self.assertIn('data-icon="${iconName}"', source)
        self.assertIn('id="sidebarBrandToggle"', source)
        self.assertIn("[button, brandButton].filter(Boolean)", source)
        self.assertIn("title: '查看结果'", source)
        self.assertIn("meta: `${resultCount} 个结果`", source)
        self.assertIn("count: resultCount", source)
        sidebar_style = (STATIC_ROOT / "styles" / "21-sidebar-nav.css").read_text(encoding="utf-8")
        self.assertIn("fontawesome-free-7.3.1-desktop/svgs-full/solid/wand-magic-sparkles.svg", sidebar_style)
        self.assertIn("fontawesome-free-7.3.1-desktop/svgs-full/solid/tower-broadcast.svg", sidebar_style)
        self.assertIn("fontawesome-free-7.3.1-desktop/svgs-full/solid/eye.svg", sidebar_style)
        self.assertIn("fontawesome-free-7.3.1-desktop/svgs-full/solid/angles-left.svg", sidebar_style)
        self.assertNotIn("fontawesome-free-7.3.1-desktop/svgs-full/solid/chart-line.svg", sidebar_style)
        self.assertIn("--sidebar-motion-duration: 280ms", sidebar_style)
        self.assertIn("transform var(--sidebar-motion-duration) var(--sidebar-motion-ease)", sidebar_style)
        self.assertIn("transform-origin: left center", sidebar_style)
        self.assertIn("max-width: none", sidebar_style)
        self.assertIn("transform: translateX(4px) scale(0.6667)", sidebar_style)
        self.assertIn("transform: rotate(180deg)", sidebar_style)
        self.assertNotIn("transform: scaleX(-1)", sidebar_style)
        self.assertIn(".shell.is-sidebar-collapsed .sidebar-section-label {\n  visibility: hidden;", sidebar_style)
        self.assertIn(".shell.is-sidebar-collapsed .sidebar-collapse-button {\n  display: none;", sidebar_style)
        self.assertIn(".sidebar-nav-action {\n  display: none;", sidebar_style)
        self.assertIn("fontAwesomeIconMarkup(iconNames[name] || 'wand-magic-sparkles'", editor_source)
        self.assertNotIn("M12 3l7 3v5c0 4.5", editor_source)
        self.assertIn('data-smart-image-action="export-current"', source)
        self.assertIn("ai8video-smart-image-studio-v1", source)
        self.assertIn("jobs: AI8SmartImage.state.jobs.slice(-24)", source)
        self.assertIn("上次关闭时任务尚未完成，请手动重试", source)
        self.assertIn("job.remaining", source)
        self.assertIn("sourceSessions: {}", editor_source)
        self.assertIn("selectedJobId: ''", editor_source)
        self.assertIn("deletedResultKeys: []", editor_source)
        self.assertIn("deletedJobIds: []", editor_source)
        self.assertIn("source.sourceKey = smartImageSourceKey(source)", canvas_source)
        self.assertIn("smartImageRememberSourceSession();", canvas_source)
        self.assertIn("smartImageActivateSourceSession(source)", canvas_source)
        self.assertIn("已切回 ${source.sourceName}，恢复", canvas_source)
        self.assertNotIn("deleteSmartImageResultFiles(AI8SmartImage.state.results", canvas_source)
        self.assertIn("function smartImageRememberSourceSession()", history_source)
        self.assertIn("sourceSessions: smartImageSerializableSessions(AI8SmartImage.state.sourceSessions)", history_source)
        self.assertIn("AI8SmartImage.state.jobs = restoreSmartImageJobs(session.jobs, source)", history_source)
        self.assertIn("resultIds: smartImageSerializableStringList(job.resultIds, 64)", history_source)
        self.assertIn("selectedJobId: hierarchy.selectedJobId", history_source)
        self.assertIn("jobId: job.id", source)
        self.assertIn("删除这个任务及其", source)
        self.assertIn("deletedResultKeys", source)
        self.assertIn("deletedJobIds", source)

        canvas_style = (STATIC_ROOT / "styles" / "20a-smart-image-canvas.css").read_text(encoding="utf-8")
        self.assertIn("已替换为单图任务工作台", canvas_style)
        editor_style = (STATIC_ROOT / "styles" / "20-smart-image-editor.css").read_text(encoding="utf-8")
        self.assertIn("#smartImageEditorModal .smart-image-studio-body", editor_style)
        self.assertIn("#smartImageEditorModal .smart-image-compare", editor_style)

    def test_static_settings_entry_uses_gear_icon(self) -> None:
        source = read_static_source()
        sidebar_style = (STATIC_ROOT / "styles" / "21-sidebar-nav.css").read_text(encoding="utf-8")
        workbench_style = (STATIC_ROOT / "workbench.css").read_text(encoding="utf-8")

        self.assertIn('aria-label="打开设置"', source)
        self.assertIn('data-icon="settings"', source)
        self.assertIn('sidebar-nav-icon-glyph', source)
        self.assertIn('<span class="sidebar-nav-copy"><span class="sidebar-nav-title">设置</span></span>', source)
        self.assertNotIn("模型、接口与系统偏好", source)
        self.assertIn("fontawesome-free-7.3.1-desktop/svgs-full/solid/gear.svg", sidebar_style)
        self.assertIn("#settingsEntryButton.settings-entry-button", sidebar_style)
        self.assertIn("min-height: 44px !important", sidebar_style)
        self.assertIn("grid-template-columns: 32px minmax(0, 1fr) !important", sidebar_style)
        self.assertIn("padding: 4px 10px !important", sidebar_style)
        self.assertIn("border-radius: 14px !important", sidebar_style)
        self.assertIn("#settingsEntryButton.settings-entry-button .sidebar-nav-icon", sidebar_style)
        self.assertIn("#settingsEntryButton.settings-entry-button .sidebar-nav-icon-glyph", sidebar_style)
        self.assertIn("width: 26px;\n  height: 26px;", sidebar_style)
        self.assertIn('21-sidebar-nav.css?v=20260804-3', workbench_style)
        self.assertTrue(
            (STATIC_ROOT / "vendor" / "fontawesome-free-7.3.1-desktop" / "svgs-full" / "solid" / "gear.svg").is_file()
        )
        self.assertNotIn('M12 1v2M12 21v2', source)

    def test_sidebar_resource_counts_use_numeric_badges(self) -> None:
        nav_source = (STATIC_ROOT / "scripts" / "01a-sidebar-nav.js").read_text(encoding="utf-8")
        material_source = (STATIC_ROOT / "scripts" / "19-render-viral-breakdown-workbench.js").read_text(encoding="utf-8")
        recycle_source = (STATIC_ROOT / "scripts" / "17-close-html-motion-overlay-drawer.js").read_text(encoding="utf-8")
        badge_style = (STATIC_ROOT / "styles" / "21a-sidebar-count-badge.css").read_text(encoding="utf-8")
        workbench_style = (STATIC_ROOT / "workbench.css").read_text(encoding="utf-8")

        self.assertIn("count = null", nav_source)
        self.assertIn('class="sidebar-nav-count"', nav_source)
        self.assertIn('aria-hidden="true">${safeCount}</span>', nav_source)
        self.assertIn("meta: `${items.length} 个文件`,", material_source)
        self.assertIn("count: items.length,", material_source)
        self.assertNotIn("'暂无文件'", material_source)
        self.assertIn("meta: `${count} 个失败任务`,", recycle_source)
        self.assertNotIn("countTone", nav_source)
        self.assertNotIn("countTone", recycle_source)
        self.assertIn('class="sidebar-nav-title-row"', nav_source)
        self.assertLess(nav_source.index('class="sidebar-nav-title"'), nav_source.index('${countMarkup}'))
        self.assertIn(".sidebar-nav-title-row", badge_style)
        self.assertIn("display: flex", badge_style)
        self.assertIn("align-items: center", badge_style)
        self.assertIn("justify-content: space-between", badge_style)
        self.assertIn("margin-left: auto", badge_style)
        self.assertIn("#sidebarResourceList .sidebar-nav-item--counted", badge_style)
        self.assertIn("min-height: 44px !important", badge_style)
        self.assertIn("padding: 4px 10px !important", badge_style)
        self.assertIn("width: 32px", badge_style)
        self.assertIn("color: #fff", badge_style)
        self.assertIn("background: #2f9e6f", badge_style)
        self.assertNotIn('data-tone="danger"', badge_style)
        self.assertIn("clip-path: inset(50%)", badge_style)
        self.assertNotIn("position: absolute;\n  top: 4px", badge_style)
        self.assertIn("width: 26px;\n  height: 26px;", badge_style)
        self.assertIn('21a-sidebar-count-badge.css?v=20260804-7', workbench_style)

    def test_sidebar_tools_hide_meta_and_use_compact_rows(self) -> None:
        tool_source = (
            STATIC_ROOT / "scripts" / "17-close-html-motion-overlay-drawer.js"
        ).read_text(encoding="utf-8")
        nav_source = (STATIC_ROOT / "scripts" / "01a-sidebar-nav.js").read_text(encoding="utf-8")
        tool_style = (STATIC_ROOT / "styles" / "21b-sidebar-tool-compact.css").read_text(encoding="utf-8")
        workbench_style = (STATIC_ROOT / "workbench.css").read_text(encoding="utf-8")

        for description in (
            "导入、描述、对比并导出副本",
            "聚合公开热点数据并生成选题摘要",
            "一键预填拆解提示词，直接进入对话分析",
        ):
            self.assertIn(description, tool_source)
        self.assertIn("const tooltip = safeMeta ? `${safeTitle}，${safeMeta}` : safeTitle;", nav_source)
        self.assertIn("#assistantToolsList > .sidebar-nav-item", tool_style)
        self.assertIn("min-height: 44px !important", tool_style)
        self.assertIn("padding: 4px 10px !important", tool_style)
        self.assertIn("width: 32px", tool_style)
        self.assertIn("clip-path: inset(50%)", tool_style)
        self.assertIn("width: 26px;\n  height: 26px;", tool_style)
        self.assertIn('21b-sidebar-tool-compact.css?v=20260804-3', workbench_style)

    def test_sidebar_progress_uses_single_title_compact_row(self) -> None:
        progress_source = (
            STATIC_ROOT / "scripts" / "10-render-flower-text-color-control.js"
        ).read_text(encoding="utf-8")
        progress_style = (
            STATIC_ROOT / "styles" / "21c-sidebar-progress-inline.css"
        ).read_text(encoding="utf-8")
        workbench_style = (STATIC_ROOT / "workbench.css").read_text(encoding="utf-8")

        self.assertIn("title: '查看结果'", progress_source)
        self.assertNotIn("查看所有结果", progress_source)
        self.assertIn("function getResultFolderCompletedCount(gallery)", progress_source)
        self.assertIn("return getPlayableResultItems(gallery).length", progress_source)
        self.assertIn(
            "const resultCount = getResultFolderCompletedCount(buildResultFolderGalleryModel(session))",
            progress_source,
        )
        self.assertIn("const completedCount = getResultFolderCompletedCount(gallery)", progress_source)
        self.assertNotIn("function getProgressResultCount", progress_source)
        self.assertNotIn("generatedMetric", progress_source)
        self.assertIn("meta: `${resultCount} 个结果`", progress_source)
        self.assertIn("count: resultCount", progress_source)
        self.assertIn("#progressPanel > .sidebar-nav-item", progress_style)
        self.assertIn("min-height: 44px !important", progress_style)
        self.assertIn("padding: 4px 10px !important", progress_style)
        self.assertIn("width: 32px", progress_style)
        self.assertIn(".sidebar-nav-copy", progress_style)
        self.assertIn("gap: 0", progress_style)
        self.assertNotIn("clip-path", progress_style)
        self.assertNotIn("display: none !important", progress_style)
        self.assertIn("width: 26px;\n  height: 26px;", progress_style)
        self.assertIn('21c-sidebar-progress-inline.css?v=20260804-5', workbench_style)

    def test_smart_image_editor_calls_configured_image_model(self) -> None:
        output_root = self.root / "smart-image-results"
        output_root.mkdir()
        output = output_root / "reference-i2i-test.png"
        output.write_bytes(b"model-image")
        upload = SimpleNamespace(
            raw_filename="portrait.png",
            filename="portrait.png",
            file=io.BytesIO(b"input-image"),
        )
        fake_request = SimpleNamespace(
            method="POST",
            files={"file": upload},
            forms={"prompt": "自然增强人物照片"},
        )
        fake_response = SimpleNamespace(status=200)
        fake_config = SimpleNamespace(image_model="GPT-image2")
        editor = Mock()
        editor.edit_image.return_value = str(output)
        request_backup = smart_image_routes.request
        response_backup = smart_image_routes.response
        smart_image_routes.request = fake_request
        smart_image_routes.response = fake_response
        try:
            with (
                patch.object(smart_image_routes, "TRANSFORMED_REFERENCE_DIR", output_root),
                patch.object(smart_image_routes.AI8VideoConfig, "from_env", return_value=fake_config),
                patch.object(smart_image_routes, "ReferenceImagePreprocessor", return_value=editor),
            ):
                body = smart_image_routes.api_render_smart_image()
        finally:
            smart_image_routes.request = request_backup
            smart_image_routes.response = response_backup

        self.assertEqual(body["ok"], True)
        self.assertEqual(body["model"], "GPT-image2")
        self.assertEqual(body["resultUrl"], "/smart-image-results/reference-i2i-test.png")
        self.assertEqual(body["fileName"], "portrait-AI修图.png")
        editor.edit_image.assert_called_once()
        self.assertEqual(
            editor.edit_image.call_args.kwargs,
            {"custom_prompt": "自然增强人物照片", "max_concurrency": 1},
        )

    def test_smart_image_project_rejects_stale_schema_overwrite(self) -> None:
        project_path = self.root / "智能修图画布.json"
        project_path.write_text(
            json.dumps({"version": 7, "project": {"source": None, "results": [{"id": "kept"}]}}),
            encoding="utf-8",
        )
        stale_payload = json.dumps({"version": 6, "project": {"source": None, "results": []}}).encode("utf-8")
        fake_request = SimpleNamespace(method="PUT", body=io.BytesIO(stale_payload))
        fake_response = SimpleNamespace(status=200)
        request_backup = smart_image_routes.request
        response_backup = smart_image_routes.response
        smart_image_routes.request = fake_request
        smart_image_routes.response = fake_response
        try:
            with patch.object(smart_image_routes, "SMART_IMAGE_PROJECT_PATH", project_path):
                body = smart_image_routes.api_smart_image_project()
        finally:
            smart_image_routes.request = request_backup
            smart_image_routes.response = response_backup

        self.assertFalse(body["ok"])
        self.assertEqual(fake_response.status, 409)
        self.assertEqual(body["currentVersion"], 7)
        self.assertEqual(json.loads(project_path.read_text(encoding="utf-8"))["project"]["results"][0]["id"], "kept")

    def test_smart_image_project_preserves_results_from_same_version_stale_tab(self) -> None:
        project_path = self.root / "智能修图画布.json"
        source = {"sourceKey": "library:portrait.png", "sourceRelativePath": "portrait.png", "edits": {}}
        kept_result = {"id": "kept", "url": "/smart-image-results/kept.png"}
        current_session = {
            "results": [kept_result],
            "jobs": [{"id": "kept-job"}],
            "selectedResultId": "kept",
            "prompt": "拟人化",
            "batchCount": 3,
            "viewMode": "result",
        }
        project_path.write_text(
            json.dumps(
                {
                    "version": 7,
                    "project": {
                        "source": source,
                        "results": [kept_result],
                        "sourceSessions": {"library:portrait.png": current_session},
                    },
                }
            ),
            encoding="utf-8",
        )
        stale_payload = json.dumps(
            {
                "version": 7,
                "project": {
                    "source": source,
                    "results": [],
                    "jobs": [],
                    "prompt": "默认描述",
                    "sourceSessions": {"library:portrait.png": {"results": [], "jobs": [], "prompt": "默认描述"}},
                },
            }
        ).encode("utf-8")
        fake_request = SimpleNamespace(method="PUT", body=io.BytesIO(stale_payload))
        fake_response = SimpleNamespace(status=200)
        request_backup = smart_image_routes.request
        response_backup = smart_image_routes.response
        smart_image_routes.request = fake_request
        smart_image_routes.response = fake_response
        try:
            with patch.object(smart_image_routes, "SMART_IMAGE_PROJECT_PATH", project_path):
                body = smart_image_routes.api_smart_image_project()
        finally:
            smart_image_routes.request = request_backup
            smart_image_routes.response = response_backup

        saved = json.loads(project_path.read_text(encoding="utf-8"))["project"]
        self.assertTrue(body["ok"])
        self.assertEqual(saved["results"][0]["id"], "kept")
        self.assertEqual(saved["prompt"], "拟人化")
        self.assertEqual(saved["sourceSessions"]["library:portrait.png"]["jobs"][0]["id"], "kept-job")

    def test_smart_image_project_merges_recent_library_history_by_latest_selection(self) -> None:
        project_path = self.root / "智能修图画布.json"
        current_history = [
            {"path": "a.png", "selectedAt": "2026-08-02T12:00:00.000Z"},
            {"path": "b.png", "selectedAt": "2026-08-02T11:00:00.000Z"},
            {"path": "c.png", "selectedAt": "2026-08-02T10:00:00.000Z"},
            {"path": "d.png", "selectedAt": "2026-08-02T09:00:00.000Z"},
            {"path": "e.png", "selectedAt": "2026-08-02T08:00:00.000Z"},
            {"path": "f.png", "selectedAt": "2026-08-02T07:00:00.000Z"},
        ]
        project_path.write_text(
            json.dumps({"version": 7, "project": {"source": None, "recentLibraryHistory": current_history}}),
            encoding="utf-8",
        )
        incoming_payload = json.dumps(
            {
                "version": 7,
                "project": {
                    "source": None,
                    "recentLibraryHistory": [
                        {"path": "g.png", "selectedAt": "2026-08-02T13:00:00.000Z"},
                        {"path": "a.png", "selectedAt": "2026-08-02T06:00:00.000Z"},
                    ],
                },
            }
        ).encode("utf-8")
        fake_request = SimpleNamespace(method="PUT", body=io.BytesIO(incoming_payload))
        fake_response = SimpleNamespace(status=200)
        request_backup = smart_image_routes.request
        response_backup = smart_image_routes.response
        smart_image_routes.request = fake_request
        smart_image_routes.response = fake_response
        try:
            with patch.object(smart_image_routes, "SMART_IMAGE_PROJECT_PATH", project_path):
                body = smart_image_routes.api_smart_image_project()
        finally:
            smart_image_routes.request = request_backup
            smart_image_routes.response = response_backup

        saved_history = json.loads(project_path.read_text(encoding="utf-8"))["project"]["recentLibraryHistory"]
        self.assertTrue(body["ok"])
        self.assertEqual([item["path"] for item in saved_history], ["g.png", "a.png", "b.png", "c.png", "d.png", "e.png"])
        self.assertEqual(saved_history[1]["selectedAt"], "2026-08-02T12:00:00.000Z")

    def test_smart_image_project_task_deletion_tombstones_prevent_stale_result_restore(self) -> None:
        project_path = self.root / "智能修图画布.json"
        source = {"sourceKey": "library:portrait.png", "sourceRelativePath": "portrait.png", "edits": {}}
        kept_result = {"id": "kept", "jobId": "kept-job", "url": "/smart-image-results/kept.png"}
        removed_result = {"id": "removed", "jobId": "removed-job", "url": "/smart-image-results/removed.png"}
        current_session = {
            "results": [kept_result, removed_result],
            "jobs": [
                {"id": "kept-job", "resultIds": ["kept"]},
                {"id": "removed-job", "resultIds": ["removed"]},
            ],
            "selectedJobId": "removed-job",
            "selectedResultId": "removed",
        }
        project_path.write_text(
            json.dumps(
                {
                    "version": 7,
                    "project": {
                        "source": source,
                        "results": [kept_result, removed_result],
                        "jobs": current_session["jobs"],
                        "sourceSessions": {"library:portrait.png": current_session},
                    },
                }
            ),
            encoding="utf-8",
        )
        deletion_session = {
            "results": [kept_result],
            "jobs": [{"id": "kept-job", "resultIds": ["kept"]}],
            "selectedJobId": "kept-job",
            "selectedResultId": "kept",
            "deletedResultKeys": [removed_result["url"]],
            "deletedJobIds": ["removed-job"],
        }
        deletion_payload = json.dumps(
            {
                "version": 7,
                "project": {
                    "source": source,
                    **deletion_session,
                    "sourceSessions": {"library:portrait.png": deletion_session},
                },
            }
        ).encode("utf-8")
        stale_payload = json.dumps(
            {
                "version": 7,
                "project": {
                    "source": source,
                    "results": [kept_result, removed_result],
                    "jobs": current_session["jobs"],
                    "sourceSessions": {"library:portrait.png": current_session},
                },
            }
        ).encode("utf-8")
        fake_response = SimpleNamespace(status=200)
        request_backup = smart_image_routes.request
        response_backup = smart_image_routes.response
        smart_image_routes.response = fake_response
        try:
            with patch.object(smart_image_routes, "SMART_IMAGE_PROJECT_PATH", project_path):
                smart_image_routes.request = SimpleNamespace(method="PUT", body=io.BytesIO(deletion_payload))
                self.assertTrue(smart_image_routes.api_smart_image_project()["ok"])
                smart_image_routes.request = SimpleNamespace(method="PUT", body=io.BytesIO(stale_payload))
                self.assertTrue(smart_image_routes.api_smart_image_project()["ok"])
        finally:
            smart_image_routes.request = request_backup
            smart_image_routes.response = response_backup

        saved = json.loads(project_path.read_text(encoding="utf-8"))["project"]
        self.assertEqual([item["id"] for item in saved["results"]], ["kept"])
        self.assertEqual([item["id"] for item in saved["jobs"]], ["kept-job"])
        self.assertEqual(saved["selectedJobId"], "kept-job")
        self.assertIn(removed_result["url"], saved["deletedResultKeys"])
        self.assertIn("removed-job", saved["deletedJobIds"])

    def test_smart_image_editor_submits_optional_local_mask(self) -> None:
        output_root = self.root / "smart-image-mask-results"
        output_root.mkdir()
        output = output_root / "reference-i2i-mask.png"
        output.write_bytes(b"model-image")
        fake_request = SimpleNamespace(
            method="POST",
            files={
                "file": SimpleNamespace(raw_filename="portrait.png", filename="portrait.png", file=io.BytesIO(b"input-image")),
                "mask": SimpleNamespace(raw_filename="mask.png", filename="mask.png", file=io.BytesIO(b"mask-image")),
            },
            forms={"prompt": "只修改人物衣服颜色"},
        )
        fake_response = SimpleNamespace(status=200)
        fake_config = SimpleNamespace(image_model="GPT-image2")
        editor = Mock()
        editor.edit_image_with_mask.return_value = str(output)
        request_backup = smart_image_routes.request
        response_backup = smart_image_routes.response
        smart_image_routes.request = fake_request
        smart_image_routes.response = fake_response
        try:
            with (
                patch.object(smart_image_routes, "TRANSFORMED_REFERENCE_DIR", output_root),
                patch.object(smart_image_routes.AI8VideoConfig, "from_env", return_value=fake_config),
                patch.object(smart_image_routes, "ReferenceImagePreprocessor", return_value=editor),
            ):
                body = smart_image_routes.api_render_smart_image()
        finally:
            smart_image_routes.request = request_backup
            smart_image_routes.response = response_backup

        self.assertEqual(body["ok"], True)
        editor.edit_image_with_mask.assert_called_once()
        source_path, mask_path = editor.edit_image_with_mask.call_args.args
        self.assertTrue(source_path.endswith("source.png"))
        self.assertTrue(mask_path.endswith("mask.png"))
        self.assertEqual(
            editor.edit_image_with_mask.call_args.kwargs,
            {"custom_prompt": "只修改人物衣服颜色", "max_concurrency": 1},
        )

    def test_smart_image_edit_prompt_preserves_rights_marks(self) -> None:
        prompt = build_smart_image_edit_prompt("自然提亮并改善肤色")

        self.assertIn("必须完整保留原图已有的署名、水印、版权标识", prompt)
        for requirement in ("请去水印", "把右下角水印去掉", "P掉品牌 Logo"):
            with self.subTest(requirement=requirement), self.assertRaisesRegex(
                ReferenceImagePreprocessError,
                "不支持移除",
            ):
                build_smart_image_edit_prompt(requirement)

    def test_smart_image_prompt_optimizer_uses_llm_and_rechecks_rights(self) -> None:
        llm = Mock(return_value="优化后的修图提示词：自然提亮人物，保留真实肤质、构图、Logo 与已有版权标识。")

        optimized = smart_image_routes.optimize_smart_image_prompt("自然提亮人物", llm)

        self.assertEqual(optimized, "自然提亮人物，保留真实肤质、构图、Logo 与已有版权标识。")
        llm.assert_called_once()
        with self.assertRaisesRegex(ReferenceImagePreprocessError, "不支持移除"):
            smart_image_routes.optimize_smart_image_prompt(
                "自然提亮人物",
                Mock(return_value="自然提亮人物，并移除右下角水印。"),
            )

    def test_smart_image_prompt_optimizer_route_uses_configured_text_model(self) -> None:
        fake_request = SimpleNamespace(method="POST", json={"prompt": "自然增强商品图"})
        fake_response = SimpleNamespace(status=200)
        fake_config = SimpleNamespace(
            has_llm=lambda: True,
            timeout_seconds=30,
            llm_model="text-model-test",
        )
        llm = Mock(return_value="自然增强商品材质与光影，保留包装文字、Logo 和原始构图。")
        request_backup = smart_image_routes.request
        response_backup = smart_image_routes.response
        smart_image_routes.request = fake_request
        smart_image_routes.response = fake_response
        try:
            with (
                patch.object(smart_image_routes.AI8VideoConfig, "from_env", return_value=fake_config),
                patch.object(smart_image_routes, "build_openai_compat_llm", return_value=llm) as build_llm,
            ):
                body = smart_image_routes.api_optimize_smart_image_prompt()
        finally:
            smart_image_routes.request = request_backup
            smart_image_routes.response = response_backup

        self.assertTrue(body["ok"])
        self.assertEqual(body["model"], "text-model-test")
        self.assertIn("保留包装文字", body["prompt"])
        build_llm.assert_called_once_with(
            fake_config,
            timeout_seconds=90,
            system_prompt="你是专业图片后期提示词编辑器，只改写用户要求，不执行图片编辑。",
            stream=False,
            transport_retry_count=1,
        )

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

    def test_retry_inputs_regenerate_missing_first_frame(self) -> None:
        record = {
            "videoIndex": 3,
            "videoTitle": "布局窗口期",
            "prompt": "复用现有最终方案",
            "request": {"durationSeconds": 10, "ratio": "9:16", "resolution": "480p", "preset": "custom"},
            "firstFrame": None,
        }
        retry_request, video, first_frame = ai8video_web._build_retry_inputs(record)
        self.assertEqual(retry_request.ratio, "9:16")
        self.assertEqual(video.index, 3)
        self.assertIsNone(first_frame)

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

    def test_html_motion_progress_expands_to_stream_drawer(self) -> None:
        source = read_static_source()

        self.assertIn('data-video-preview-html-motion-toggle', source)
        self.assertIn('data-video-preview-html-motion-detail', source)
        self.assertIn('data-video-preview-html-motion-status', source)
        self.assertIn('video-preview-html-motion-summary', source)
        self.assertIn('function updateHtmlMotionPreviewTaskSnapshot(data, options = {})', source)
        self.assertIn('updateHtmlMotionPreviewTaskSnapshot(data, { render: false })', source)
        self.assertIn('避免轮询用接口原文盖掉导致数字闪烁', source)
        self.assertIn('streamText', source)
        self.assertIn('height: 260px;', source)
        self.assertIn('resize: vertical;', source)
        self.assertIn('transition: height 220ms ease', source)
        self.assertIn('.video-preview-html-motion-drawer.has-task,', source)
        self.assertIn('video-preview-html-motion-drawer-slot', source)
        self.assertIn('grid-template-rows: 0fr;', source)
        self.assertIn('grid-template-rows: 1fr;', source)
        self.assertIn('function syncHtmlMotionDrawerWidth()', source)
        self.assertIn('confirm-burn', source)
        self.assertIn("data-video-preview-action=\"confirm-burn\"", source)
        self.assertIn('overflow-wrap: anywhere;', source)
        self.assertIn('border-radius: 12px 12px 0 0;', source)
        self.assertIn('border-radius: 0 0 12px 12px;', source)
        self.assertIn('border-bottom-width: 0;', source)
        self.assertIn('video-preview-controls-row', source)
        self.assertNotIn('border-radius: 8px 8px 0 0;', source)
        self.assertNotIn('width: 196px;', source)

    def test_confirm_burn_waits_for_html_motion_timeline_persistence(self) -> None:
        source = read_static_source()

        self.assertIn("await state.videoPreviewModal?.htmlMotionPersistChain", source)
        self.assertLess(
            source.index("await state.videoPreviewModal?.htmlMotionPersistChain"),
            source.index("const data = await requestConfirmedBurn(key)"),
        )

    def test_html_motion_timeline_chunk_click_seeks_video(self) -> None:
        source = read_static_source()

        self.assertIn('function seekVideoPreviewToHtmlMotionChunk(index)', source)
        self.assertIn('setHtmlMotionSelectedChunkIndex(index)', source)
        self.assertIn('function setVideoSelectedChunkIndex(index, exclusive = true)', source)
        self.assertIn('function setTtsSelectedChunkIndex(index, exclusive = true)', source)
        self.assertIn('function setHtmlMotionSelectedChunkIndex(index, exclusive = true)', source)
        self.assertNotIn('data-video-preview-action="toggle-video-seek"', source)
        self.assertNotIn('videoTimelineSeekMode', source)
        self.assertIn('seekVideoTimelineToTime(Number(chunk.startSeconds || 0), index);', source)
        self.assertIn('seekVideoTimelineToTime(timelineSecondsAtPointer(event, lane, scaleDuration), index);', source)
        self.assertIn('setVideoSelectedChunkIndex(selectedIndex);', source)
        self.assertIn('setVideoSelectedChunkIndex(null, false)', source)
        self.assertIn('setTtsSelectedChunkIndex(null, false)', source)
        self.assertIn('setHtmlMotionSelectedChunkIndex(null, false)', source)
        self.assertIn('video.pause();', source)
        self.assertIn('function handleHtmlMotionChunkClick(event, element, duration)', source)
        self.assertIn('splitHtmlMotionTimelineAtPointer(event, element, duration)', source)
        self.assertNotIn('剪刀可切块；关闭剪刀后', source)

    def test_html_motion_timeline_supports_marquee_and_batch_actions(self) -> None:
        source = read_static_source()

        self.assertIn('function bindHtmlMotionMarqueeSelection(lane)', source)
        self.assertIn('data-video-preview-html-motion-marquee', source)
        self.assertIn('function currentHtmlMotionSelectedChunkIndexes()', source)
        self.assertIn('excludeIndexes: selectedIndexes', source)
        self.assertIn('已移动 ${selectedItems.length} 个动效片段', source)
        self.assertIn("chunkElement.title = `起点 ${entry.item.startSeconds.toFixed(1)} 秒，释放后保存`;", source)
        html_drag_start = source.index('function beginHtmlMotionChunkDrag')
        html_drag_end = source.index('function bindHtmlMotionChunkEndTrim', html_drag_start)
        self.assertNotIn("chunkElement.querySelector('small').textContent", source[html_drag_start:html_drag_end])
        self.assertIn("element.dataset.suppressHtmlMotionClick = 'true';", source)
        self.assertIn("if (element.dataset.suppressHtmlMotionClick === 'true') {", source)
        self.assertIn("if (lane?.dataset.timelineIgnoreClick === 'true') {", source)

    def test_html_motion_text_position_editing_uses_visible_selected_anchor_and_batch_scope(self) -> None:
        source = read_static_source()
        runtime = (Path(html_motion_review.__file__).parent / "waapi_timeline_runtime.js").read_text(
            encoding="utf-8",
        )

        self.assertIn("function currentHtmlMotionVisibleSelectedChunks(video)", source)
        self.assertIn("selectedChunkIds: editing.selectedChunkIds", source)
        self.assertIn("editableChunkIds: editing.editableChunkIds", source)
        self.assertIn("frame.classList.toggle('is-text-position-editable', editable)", source)
        self.assertIn("if (!currentHtmlMotionVisibleSelectedChunks(video).length)", source)
        self.assertIn("selectedIndexes.forEach((index) => {", source)
        self.assertIn("chunk.textPosition = { ...position };", source)
        self.assertIn("已同步 ${selectedIndexes.length} 个动效片段文字位置", source)
        self.assertIn("syncLiveHtmlMotionPreview(els.videoPreviewBody?.querySelector('video'))", source)
        self.assertIn("anchorScene.dataset.ai8TextEditable !== 'true'", runtime)
        self.assertIn("type: 'ai8-motion-text-position-change'", runtime)
        self.assertIn("selectedChunkIds: global.__ai8MotionSelectedChunkIds || []", runtime)
        self.assertIn("data-chunk-id=", source)
        self.assertIn("is-text-position-editable", source)

    def test_tts_timeline_supports_marquee_and_batch_selection(self) -> None:
        source = read_static_source()

        self.assertIn('function bindTtsMarqueeSelection(lane)', source)
        self.assertIn('data-video-preview-tts-marquee', source)
        self.assertIn('function currentTtsSelectedChunkIndexes()', source)
        self.assertIn('setTtsSelectedChunkIndexes([...initial, ...hits])', source)
        self.assertIn('删除 ${selectedIndexes.length} 个配音块', source)
        self.assertIn('excludeIndexes: selectedIndexes', source)
        self.assertIn('已移动 ${selectedItems.length} 个配音块', source)
        self.assertIn('const selectedIndexes = new Set(currentTtsSelectedChunkIndexes());', source)
        self.assertIn('state.videoPreviewModal.ttsSelectedChunkIndexes = selectedIndexes;', source)
        self.assertIn("lane.addEventListener('click', (event) => {", source)
        self.assertIn("lane.addEventListener('lostpointercapture', end);", source)
        self.assertIn("if (moved) lane.dataset.timelineIgnoreClick = 'true';", source)
        self.assertIn("if (lane.dataset.timelineIgnoreClick === 'true') {", source)
        self.assertIn('delete element.dataset.suppressTtsClick;', source)
        self.assertIn("'/api/user-generated-results/delete-html-motion-review'", source)
        self.assertIn('selected.length === chunks.length', source)

    def test_confirm_burn_does_not_replace_open_preview_source(self) -> None:
        source = (STATIC_ROOT / "scripts" / "11d-timeline-boundary-and-burn.js").read_text(encoding="utf-8")
        start = source.index("async function applyConfirmedBurn(data, button)")
        end = source.index("async function confirmBurnFromVideoPreview", start)
        apply_source = source[start:end]
        self.assertNotIn("video.src", apply_source)
        self.assertNotIn("officialSrc", apply_source)
        self.assertNotIn("refreshUserGeneratedResults", apply_source)
        self.assertNotIn("renderResultModal", apply_source)
        self.assertNotIn("renderStatus", apply_source)

    def test_html_motion_live_preview_uses_clean_base_instead_of_composited_candidate(self) -> None:
        source = read_static_source()

        self.assertIn("overlay?.basePreviewUrl", source)
        self.assertIn("/api/user-generated-results/html-motion-base/${encodeURIComponent(reviewId)}", source)
        self.assertIn("video.src = `${basePreviewUrl}", source)

    def test_timeline_ruler_click_moves_shared_playhead(self) -> None:
        source = read_static_source()

        self.assertIn("querySelector('[data-video-preview-timeline-ruler]')?.addEventListener('click', seekAtClick)", source)
        self.assertRegex(
            source,
            r"\.video-preview-timeline-ruler\s*\{[^}]*cursor: pointer;[^}]*pointer-events: auto;",
        )
        self.assertIn('timelineSecondsAtPointer(event, lane, duration)', source)
        self.assertIn('syncVideoTimelinePlayhead();', source)
        self.assertIn('syncTtsTimelinePlayhead();', source)
        self.assertIn('syncHtmlMotionTimelinePlayhead();', source)
        self.assertIn("if (element.dataset.suppressTtsClick === 'true')", source)
        self.assertIn("element.dataset.suppressTtsClick = 'true';", source)

    def test_timeline_playhead_updates_smoothly_during_video_playback(self) -> None:
        source = read_static_source()

        self.assertIn('function bindSmoothTimelinePlayheadSync(video)', source)
        self.assertIn('animationFrameId = requestAnimationFrame(updateDuringPlayback);', source)
        self.assertIn('if (animationFrameId) cancelAnimationFrame(animationFrameId);', source)
        self.assertIn("video.addEventListener('play', startAnimation);", source)
        self.assertIn("video.addEventListener('pause', stopAnimation);", source)
        self.assertIn("video.addEventListener('timeupdate', syncAllTimelinePlayheads);", source)
        self.assertIn('bindSmoothTimelinePlayheadSync(video);', source)

    def test_html_motion_chunks_do_not_show_redundant_start_seconds(self) -> None:
        source = read_static_source()

        self.assertNotIn(
            '<small>${start.toFixed(1)}s</small>${timelineTrimHandleMarkup(label)}',
            source,
        )
        self.assertNotIn('.video-preview-html-motion-chunk > small', source)

    def test_frame_repair_requires_prompt_but_not_optional_reference_image(self) -> None:
        source = read_static_source()

        self.assertIn("data-frame-repair-start ${customPrompt.trim() && !busy ? '' : 'disabled'}", source)
        self.assertIn("function frameRepairActionLabel(stageGrid)", source)
        self.assertIn("${actionLabel}", source)
        self.assertIn('if (!frameKey || !customPrompt || button.disabled) return;', source)
        self.assertIn('if (!frames.length || !customPrompt || button.disabled) return;', source)
        self.assertNotIn('if (!frameKey || !referencePaths.length || button.disabled) return;', source)
        self.assertIn('function currentFrameRepairPrompt()', source)
        self.assertIn("addEventListener('input', (event) => {", source)
        self.assertIn("startButton.disabled = !prompt || isVideoPreviewExtensionBatchBusy(stageGrid)", source)
        self.assertIn("persistVideoPreviewExtensionState(key, { ...(loadVideoPreviewExtensionStates()[key] || {}), frameRepairPrompt: prompt });\n        renderVideoPreviewFrameRepairActions();", source)
        self.assertIn("const nextFrames = updateVideoPreviewExtensionBatchFrame(stageGrid, index, repairedFrame);", source)
        self.assertIn("applyVideoPreviewExtensionBatchStage(stageGrid, nextFrames);", source)
        self.assertIn("setVideoPreviewBatchVariantLoading(stageGrid, index, true, '修图中')", source)
        self.assertIn("const completedFrame = {", source)
        self.assertIn("userGeneratedKey: data.userGeneratedKey", source)
        self.assertIn("videoUrl: data.videoUrl", source)
        self.assertIn("if (signal.done) return;", source)
        self.assertIn("signal.done = true;\n            const completedFrame", source)
        self.assertIn("if (['failed', 'cancelled', 'canceled'].includes(String(item?.status || '').toLowerCase())) return '生成失败';", source)
        self.assertIn("${failed ? ' is-failed' : ''}", source)
        self.assertIn("status: 'failed', progressLabel: '', error: error?.message || '生成视频失败'", source)
        self.assertIn("queued: '排队中'", source)
        self.assertIn("percent && !/\\d+(?:\\.\\d+)?%/.test(status)", source)
        self.assertIn("const generationNonce = globalThis.crypto?.randomUUID?.()", source)
        self.assertIn("const sessionIdBase = `extension-${parentSessionId}-${generationNonce}`;", source)
        self.assertNotIn("videoSessionId: `${String(state.activeId || '').trim()}-batch-${index + 1}`", source)

    def test_extension_batch_status_sits_below_center_spinner(self) -> None:
        source = read_static_source()

        self.assertIn('top: calc(50% + 22px);', source)
        self.assertIn('transform: translateX(-50%);', source)

    def test_extension_batch_refresh_resumes_existing_polling(self) -> None:
        source = read_static_source()

        self.assertIn('function resumeVideoPreviewExtensionBatchPolling(stageGrid)', source)
        self.assertIn('void resumeVideoPreviewExtensionBatchPolling(stageGrid);', source)
        self.assertIn('if (!isPendingBatchVideoFrame(frame)) return;', source)
        self.assertNotIn("return { ...frame, status: 'completed', progressLabel: '' };", source)

    def test_extension_batch_status_normalizes_variant_frame_key(self) -> None:
        source = Path(ai8video_web.__file__).read_text(encoding="utf-8")

        self.assertIn('base_stem = re.sub(r"(?:-batch-[1-4])+$", "", source.stem)', source)
        self.assertIn('source = source.with_name(f"{base_stem}{source.suffix}")', source)

    def test_extension_batch_restore_never_mounts_stale_single_video(self) -> None:
        source = read_static_source()

        self.assertIn("...(batchMode ? { rightVideoKey: '', rightVideoUrl: '' } : {}),", source)
        self.assertIn('if (savedState.batchMode === true) return;', source)

    def test_extension_batch_restore_recovers_lost_pending_status(self) -> None:
        source = read_static_source()

        self.assertIn('function isPendingBatchVideoFrame(frame)', source)
        self.assertIn("frame?.videoSessionId && !frame?.videoUrl && frame?.status !== 'failed'", source)
        self.assertIn("{ ...frame, status: 'video-generating', progressLabel: frame.progressLabel || '视频生成中' }", source)

    def test_replace_action_does_not_cover_batch_checkbox(self) -> None:
        source = read_static_source()

        self.assertIn('.video-preview-merge-control.video-preview-replace-control', source)
        self.assertIn('left: calc(50% - 28px);', source)

    def test_batch_refresh_preserves_only_explicit_selection(self) -> None:
        source = read_static_source()

        self.assertIn('const selectedIndex = readVideoPreviewExtensionBatchFrames(stageGrid).findIndex((frame) => frame.selected);', source)
        self.assertIn('selected: selectedIndex >= 0 && index === selectedIndex,', source)
        self.assertNotIn('selected: frame.frameKey === selectedKey,', source)
        self.assertIn('fontawesome-free-7.3.1-desktop/svgs-full/solid/check.svg', source)
        self.assertIn('padding: 0;', source)
        self.assertIn('width: 14px;', source)
        self.assertIn('height: 14px;', source)

    def test_html_motion_timeline_drawer_animates_open_and_closed(self) -> None:
        source = read_static_source()

        self.assertIn("panel.classList.toggle('is-open', open)", source)
        self.assertIn("button?.setAttribute('aria-expanded', open ? 'true' : 'false')", source)
        self.assertIn('.video-preview-html-motion-timeline.is-open', source)
        self.assertIn('max-height 260ms cubic-bezier(0.22, 1, 0.36, 1)', source)
        self.assertIn('visibility 0s linear 260ms', source)
        self.assertNotIn('panel.hidden = !panel.hidden', source)

    def test_tts_ai_working_status_uses_green(self) -> None:
        source = read_static_source()

        self.assertIn(".video-preview-tts-status.is-working", source)
        self.assertIn("color: #9ff3cb;", source)
        self.assertIn("background: rgba(15, 28, 43, 0.96);", source)
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

        update.assert_called_once_with(
            concurrent_generation=True,
            smart_split=False,
            confirm_smart_split=False,
            tail_frame_chaining=False,
            tail_frame_chaining_mode="auto",
        )
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

    def test_api_open_user_generated_results_folder_opens_burned_video_dir(self) -> None:
        generated_root = self.root / "用户生成结果"
        burned_video_dir = generated_root / "burned" / "video"
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
                "schedule_missing_burned_result_copies",
                return_value=[],
            ), patch.object(
                ai8video_web,
                "_open_in_file_manager",
            ) as open_dir:
                body = ai8video_web.api_open_user_generated_results_folder()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_dir.assert_called_once_with(burned_video_dir.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(Path(body["path"]).resolve(), burned_video_dir.resolve())
        self.assertTrue(burned_video_dir.is_dir())

    def test_api_open_user_generated_burned_video_reveals_exact_file(self) -> None:
        generated_root = self.root / "用户生成结果"
        burned_video = generated_root / "burned" / "video" / "demo.mp4"
        burned_video.parent.mkdir(parents=True, exist_ok=True)
        burned_video.write_bytes(b"burned")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"userGeneratedKey": "video/demo.mp4"},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(
                ai8video_web,
                "ensure_user_generated_result_dir",
                return_value=generated_root.resolve(),
            ), patch.object(ai8video_web, "_reveal_in_file_manager") as reveal_file:
                body = ai8video_web.api_open_user_generated_burned_video_in_folder()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        reveal_file.assert_called_once_with(burned_video.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["userGeneratedKey"], "burned/video/demo.mp4")
        self.assertEqual(Path(body["path"]).resolve(), burned_video.resolve())

    def test_api_open_user_generated_burned_video_repairs_missing_initial_copy(self) -> None:
        generated_root = self.root / "用户生成结果"
        source_video = generated_root / "video" / "demo.mp4"
        burned_video = generated_root / "burned" / "video" / "demo.mp4"
        source_video.parent.mkdir(parents=True, exist_ok=True)
        source_video.write_bytes(b"source")
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"userGeneratedKey": "video/demo.mp4"},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(
                ai8video_web,
                "ensure_user_generated_result_dir",
                return_value=generated_root.resolve(),
            ), patch.object(ai8video_web, "_reveal_in_file_manager") as reveal_file:
                body = ai8video_web.api_open_user_generated_burned_video_in_folder()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        reveal_file.assert_called_once_with(burned_video.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(body["userGeneratedKey"], "burned/video/demo.mp4")
        self.assertEqual(burned_video.read_bytes(), b"source")

    def test_reveal_in_file_manager_selects_file_in_macos_finder(self) -> None:
        video = self.root / "burned.mp4"
        video.write_bytes(b"video")
        with patch.object(ai8video_web.sys, "platform", "darwin"), patch.object(
            ai8video_web.subprocess,
            "Popen",
        ) as popen:
            ai8video_web._reveal_in_file_manager(video)

        popen.assert_called_once_with(["open", "-R", str(video.resolve())])

    def test_api_open_user_generated_results_folder_ignores_stale_local_path(self) -> None:
        generated_root = self.root / "用户生成结果"
        burned_video_dir = generated_root / "burned" / "video"
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
            ), patch.object(
                ai8video_web,
                "schedule_missing_burned_result_copies",
                return_value=[],
            ), patch.object(ai8video_web, "_open_in_file_manager") as open_dir:
                body = ai8video_web.api_open_user_generated_results_folder()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        open_dir.assert_called_once_with(burned_video_dir.resolve())
        self.assertTrue(body["ok"])
        self.assertEqual(Path(body["path"]).resolve(), burned_video_dir.resolve())

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

    def test_api_user_generated_results_hides_dry_run_placeholder_videos(self) -> None:
        generated_root = self.root / "用户生成结果"
        real_video = generated_root / "video" / "real-result.mp4"
        dry_video = generated_root / "video" / "01-demo-merge2-dry-model-1-a-dry-model-1-b.mp4"
        real_video.parent.mkdir(parents=True, exist_ok=True)
        real_video.write_bytes(b"real")
        dry_video.write_bytes(b"placeholder")

        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="GET",
            query=SimpleNamespace(get=lambda key, default="200": default),
        )
        try:
            with patch.object(
                ai8video_web,
                "ensure_user_generated_result_dir",
                return_value=generated_root.resolve(),
            ):
                body = ai8video_web.api_user_generated_results()
        finally:
            ai8video_web.request = request_backup

        self.assertEqual([item["userGeneratedKey"] for item in body["items"]], ["video/real-result.mp4"])

    def test_result_modal_identifies_global_result_folder_view(self) -> None:
        source = read_static_source()

        self.assertIn("els.resultModalTitle.textContent = '全部生成结果';", source)
        self.assertIn("结果目录中 ${completedCount} 个成片", source)

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

    def test_delete_hidden_bgm_result_also_removes_visible_extension_assets(self) -> None:
        generated_root = self.root / "用户生成结果"
        media_rel = Path("extensions/video/demo.mp4")
        hidden_rel = Path(".media-tracks/bgm-base") / media_rel
        preview_rel = Path("extensions/preview/demo.jpg")
        cover_rel = Path("extensions/cover/demo.jpg")
        for relative_path in (media_rel, hidden_rel, preview_rel, cover_rel):
            target = generated_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"asset")

        with patch.object(ai8video_web, "USER_GENERATED_RESULT_ROOT", generated_root.resolve()), patch.object(
            ai8video_web,
            "ensure_user_generated_result_dir",
            return_value=generated_root.resolve(),
        ):
            body = ai8video_web._delete_user_generated_video(hidden_rel.as_posix())

        self.assertTrue(body["ok"])
        for relative_path in (media_rel, hidden_rel, preview_rel, cover_rel):
            self.assertFalse((generated_root / relative_path).exists())

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

    def test_in_memory_progress_does_not_mark_active_retry_as_deleted(self) -> None:
        video_path = self.root / "用户生成结果" / "video" / "old-retry.mp4"
        body = {
            "generationProgress": {
                "items": [
                    {
                        "videoIndex": 6,
                        "status": "polling",
                        "statusLabel": "视频生成中",
                        "jobId": "old-job-id",
                        "assetRecord": {
                            "jobId": "old-job-id",
                            "archiveStatus": "archived",
                            "archiveLocalPath": str(video_path),
                        },
                    }
                ]
            }
        }

        ai8video_web._apply_deleted_asset_progress_state(body)

        self.assertEqual(body["generationProgress"]["items"][0]["status"], "polling")

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
        self.assertEqual(selected[0]["previewUrl"], "/api/background-music/preview/second")

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
        ai8video_web.request = SimpleNamespace(method="POST", json={})
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
            json={
                "sessionId": "session-cancel",
                "generationBatchId": "gb-session-cancel",
                "reason": "用户强行终止",
            },
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
        cancel_mock.assert_called_once_with(
            session_id="session-cancel",
            reason="用户强行终止",
            generation_batch_id="gb-session-cancel",
        )

    def test_api_chat_plan_cancel_discards_pending_confirmation(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        fake_response = SimpleNamespace(status=200)
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"sessionId": "session-plan-cancel"},
        )
        ai8video_web.response = fake_response
        try:
            with patch.object(
                ai8video_web,
                "cancel_smart_split_confirmation_via_ai8video",
                return_value={
                    "ok": True,
                    "cancelled": True,
                    "sessionId": "session-plan-cancel",
                },
            ) as cancel_mock:
                body = ai8video_web.api_chat_plan_cancel()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(fake_response.status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["cancelled"])
        cancel_mock.assert_called_once_with(session_id="session-plan-cancel")

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

        self.assertIn("function renderAgentStepChain(pending = {}, options = {})", html)
        self.assertIn("function buildAgentStepChainModel(pending = {})", html)
        self.assertIn("${renderAgentStepChain(displayedPending, { messageIndex: context.messageIndex })}", html)
        self.assertIn("理解需求", html)
        self.assertIn("规划任务", html)
        self.assertIn("提交生成", html)
        self.assertIn("生成视频", html)
        self.assertIn("归档结果", html)
        self.assertIn(".agent-step-chain", html)
        self.assertIn("flex: 0 0 20px;", html)
        self.assertIn("justify-content: center;", html)
        self.assertIn("line-height: 1;", html)
        self.assertIn("const AGENT_STEP_ORBIT_DURATION_MS = 1200;", html)
        self.assertIn("const orbitDelayMs = -(Date.now() % AGENT_STEP_ORBIT_DURATION_MS);", html)
        self.assertIn("--agent-step-orbit-delay:${orbitDelayMs}ms", html)
        self.assertIn("animation-delay: var(--agent-step-orbit-delay, 0ms);", html)
        self.assertIn("index === activeStepIndex ? 'active'", html)
        self.assertIn(".agent-step-details", html)
        self.assertIn("agent-step-detail-marker", html)
        self.assertIn(".agent-step-details-drawer", html)
        self.assertIn("grid-template-rows: 0fr", html)
        self.assertIn(".agent-step-details.is-expanded .agent-step-details-drawer", html)
        self.assertIn("grid-template-rows: 1fr", html)
        self.assertIn("max-height: 180px", html)
        self.assertIn("agent-step-details-toggle", html)
        self.assertIn("data-agent-step-details-toggle", html)
        self.assertIn("展开全部 · ${events.length}", html)
        self.assertIn("agentStepDetailsExpanded", html)
        self.assertIn("function buildAgentStepDetailsKey(sessionId, messageIndex)", html)
        self.assertIn("function toggleAgentStepDetailsExpanded(detailsKey)", html)
        self.assertIn("function applyAgentStepDetailsExpanded(detailsKey, rootEl = null)", html)
        self.assertIn("toggle.closest('.agent-step-details')", html)
        self.assertIn("agent-step-details-drawer-slot", html)
        self.assertIn("const historyEvents = events.slice(1);", html)
        self.assertIn("renderAgentStepChain(displayedPending, { messageIndex: context.messageIndex })", html)
        self.assertIn("${drawer}", html)
        self.assertIn("${toggle}", html)
        self.assertIn("toggle.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' })", html)
        self.assertIn("overflow-anchor: none", html)
        self.assertNotIn("scroller.scrollTop += delta", html)
        self.assertIn(".agent-step-details::before", html)
        self.assertIn(".message:not(.user) .bubble", html)
        self.assertIn("width: 70%;", html)
        self.assertIn("function renderAgentExecutionEvents(pending = {}, options = {})", html)
        self.assertIn("function renderAgentVideoResultActionButton(items = [])", html)
        self.assertIn("const firstRowItems = items.slice(0, 6);", html)
        self.assertIn("const secondRowItems = items.slice(6);", html)
        self.assertIn("renderAgentVideoResultActionButton(items)", html)
        self.assertIn("agent-video-results-secondary", html)
        self.assertIn("data-toggle-agent-batch-merge", html)
        self.assertIn("toggleAgentResultBatchMergeMode()", html)
        self.assertIn("confirmAgentResultBatchMerge()", html)
        self.assertIn("function humanizeAgentEventMessage(value)", html)
        self.assertIn("queued: '排队中'", html)
        self.assertIn("humanizeAgentEventMessage(event?.message || '状态已更新')", html)
        self.assertIn("function collapseAgentPollingEvents(rawEvents)", html)
        self.assertIn("const latestStatusIndex = new Map();", html)
        self.assertIn("const eventKey = status ? `${videoIndex}:${segmentIndex}:${status}:${eventKind}` : '';", html)
        self.assertIn("function buildTerminalAgentPendingStatus(payload, resultGroups, summary, sessionId)", html)
        self.assertIn("function isLocalVideoPostprocessFailure(value)", html)
        self.assertIn("function getGenerationFailureStageLabel(itemOrReason = {})", html)
        self.assertIn("const hasAgentProgress = !!renderedPendingStatus?.generationProgress;", html)
        self.assertIn("if (payload.meta?.operation === 'pending' || hasAgentProgress)", html)
        self.assertIn("if (isGeneratedResult && summary && !hasAgentProgress)", html)
        self.assertIn("const submittingCount = countStatuses(new Set(['preparing_first_frame', 'preparing_tail_frame', 'submitting']));", html)
        self.assertIn("const generatingCount = countStatuses(new Set(['submitted', 'polling']));", html)
        self.assertIn("status === 'polling' && Number.isFinite(Number(event?.providerProgress))", html)
        self.assertIn("index === 0 && !['succeeded', 'completed'].includes(status)", html)
        self.assertIn("本轮已结束：已生成 ${done}/${total}，失败 ${failed} 条。", html)
        self.assertIn("本机视频后处理编码器不兼容，开头裁剪失败", html)
        self.assertIn("hasLocalPostprocessFailure ? '本地后处理失败' : '视频生成失败'", html)
        self.assertIn("last.payload.pendingStatus = normalizePendingStatusProgress({", html)
        self.assertIn('class="pending-card-status"', html)
        self.assertIn("function renderAgentVideoThumbnails(pending = {})", html)
        self.assertIn("String(progress.status || '').trim() === 'planning'", html)
        self.assertIn("if (planning) return '';", html)
        self.assertIn("if (!submitted) return '';", html)
        self.assertIn("${renderProgressResultStrip([], pendingCount)}", html)
        self.assertIn("return buildProgressStatusResultItem(itemWithBatch, index, progressItems);", html)
        self.assertIn("function humanizePublicExecutionStatus(value)", html)
        self.assertIn("后台真实执行事件", html)
        self.assertIn(".agent-video-results", html)
        self.assertIn("历史任务已结束", html)

    def test_failed_generation_retry_button_does_not_require_batch_id(self) -> None:
        html = read_static_source()
        status_source = (
            STATIC_ROOT / "scripts" / "06-refresh-generation-mode.js"
        ).read_text(encoding="utf-8")

        self.assertIn("if (videoIndex < 1) return '';", html)
        self.assertIn("if (!sessionId || videoIndex < 1 || button.disabled) return;", html)
        self.assertNotIn("if (videoIndex < 1 || !generationBatchId) return '';", html)
        self.assertIn("function showGenerationRetryPendingCard(button)", html)
        self.assertIn("card.classList.remove('failed');", html)
        self.assertIn("preview.title = '正在重新生成';", html)
        self.assertIn("const restoreFailedCard = showGenerationRetryPendingCard(button);", html)
        self.assertIn("restoreFailedCard();", html)
        self.assertIn("function persistGenerationRetryPendingState(sessionId, videoIndex, generationBatchId, displayProgress = null)", html)
        self.assertIn("operation: 'pending', continuationClosed: false", html)
        self.assertIn("last.payload.generationBatchId = generationBatchId;", html)
        self.assertIn("schedulePendingPoll(sessionId, 200);", html)
        self.assertIn("async function reconcilePendingSessionAfterReload(session, targetMessage = null, statusMessage = null)", html)
        self.assertIn("&& !isConversationContinuationClosed(message.payload)", html)
        self.assertIn("function mergeGenerationProgressSnapshot(previousProgress = {}, nextProgress = {})", html)
        self.assertIn("function mergeGenerationStatusPayload(payload = {}, data = {}, sessionId = '')", html)
        self.assertIn("continuationClosed: true", html)
        self.assertIn("messageToUpdate.payload = mergeGenerationStatusPayload(messageToUpdate.payload, data, sessionId);", html)
        self.assertIn("pendingStatus.generationBatchId = String(data.generationBatchId).trim();", html)
        self.assertIn("tailFrameChaining: !!state.generationMode?.tailFrameChaining", html)
        self.assertLess(
            status_source.index("payload?.pendingStatus?.generationBatchId"),
            status_source.index("payload?.generationBatchId"),
        )

    def test_retry_generation_starts_background_worker_and_returns_pending_batch(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"sessionId": "session-1", "generationBatchId": "batch-old", "videoIndex": 1},
        )
        ai8video_web.response = SimpleNamespace(status=200)
        video = VideoPrompt(1, "视频 1", "生成方案")
        retry_request = SimpleNamespace(tail_frame_chaining=False)
        try:
            with patch.object(ai8video_web.AI8VideoConfig, "from_env", return_value=SimpleNamespace()), \
                 patch.object(ai8video_web, "_find_retryable_asset_record", return_value={}), \
                 patch.object(ai8video_web, "_build_retry_inputs", return_value=(retry_request, video, None)), \
                 patch.object(ai8video_web, "get_generation_ledger_snapshot", return_value={
                     "generationBatchId": "batch-old",
                     "rootGenerationBatchId": "batch-old",
                     "progress": {"items": [{"videoIndex": 1, "status": "failed"}]},
                 }), \
                 patch.object(ai8video_web, "create_generation_batch_id", return_value="batch-retry"), \
                 patch.object(ai8video_web, "register_generation_child_batch") as register_child, \
                 patch.object(ai8video_web, "clear_generation_progress") as clear_progress, \
                 patch.object(ai8video_web, "claim_generation_batch") as claim_batch, \
                 patch.object(ai8video_web, "start_generation_progress") as start_progress, \
                 patch.object(ai8video_web, "record_generation_execution"), \
                 patch.object(ai8video_web, "start_external_generation_task", return_value=SimpleNamespace(worker_id="worker-1")) as start_worker:
                body = ai8video_web.api_retry_failed_generation()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        clear_progress.assert_called_once_with("session-1")
        claim_batch.assert_called_once_with("session-1", "batch-retry")
        start_progress.assert_called_once_with("session-1", [video], generation_batch_id="batch-retry")
        register_child.assert_called_once()
        start_worker.assert_called_once()
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["generationBatchId"], "batch-old")
        self.assertEqual(body["childGenerationBatchId"], "batch-retry")

    def test_retry_inputs_restore_tail_frame_mode(self) -> None:
        record = {
            "videoIndex": 2,
            "videoTitle": "第二条",
            "prompt": "继续动作",
            "request": {
                "mode": "batch_videos",
                "videoCount": 3,
                "tailFrameChaining": True,
                "concurrentGeneration": False,
            },
        }

        retry_request, video, _ = ai8video_web._build_retry_inputs(record)

        self.assertTrue(retry_request.tail_frame_chaining)
        self.assertFalse(retry_request.concurrent_generation)
        self.assertEqual(video.index, 2)

    def test_retry_recovers_pre_submission_failure_from_progress_ledger(self) -> None:
        config = SimpleNamespace(asset_store_path=self.root / "assets.jsonl")
        JsonlAssetStore(config.asset_store_path).rewrite_all([{
            "sessionId": "session-1",
            "generationBatchId": "batch-1",
            "videoIndex": 1,
            "generationStatus": "generated",
            "request": {
                "mode": "batch_videos",
                "durationSeconds": 10,
                "ratio": "9:16",
                "resolution": "720p",
                "preset": "custom",
                "tailFrameChaining": True,
            },
        }])
        ledger = {"progress": {"items": [{
            "videoIndex": 3,
            "title": "第三条",
            "videoPrompt": "已规划的第三条提示词",
            "status": "failed",
            "error": "上游连接中断",
        }]}}

        with patch.object(ai8video_web, "get_generation_ledger_snapshot", return_value=ledger):
            record = ai8video_web._find_retryable_asset_record(
                config, "session-1", "batch-1", 3,
            )

        self.assertEqual(record["videoIndex"], 3)
        self.assertEqual(record["videoTitle"], "第三条")
        self.assertEqual(record["prompt"], "已规划的第三条提示词")
        self.assertEqual(record["request"]["resolution"], "720p")

    def test_tail_frame_retry_requires_successful_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset_path = Path(tmp) / "assets.jsonl"
            asset_path.write_text("", encoding="utf-8")
            config = SimpleNamespace(asset_store_path=asset_path)
            retry_request = SimpleNamespace(tail_frame_chaining=True)
            ledger = {"progress": {"items": [{"videoIndex": 1, "status": "failed"}]}}

            with patch.object(ai8video_web, "get_generation_ledger_snapshot", return_value=ledger):
                with self.assertRaisesRegex(ValueError, "请先成功生成第 1 条视频"):
                    ai8video_web._resolve_retry_tail_frame_source(config, "session-1", 2, retry_request)

    def test_tail_frame_retry_uses_latest_successful_predecessor_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            predecessor = Path(tmp) / "video-1.mp4"
            predecessor.write_bytes(b"video")
            config = SimpleNamespace(asset_store_path=Path(tmp) / "assets.jsonl")
            config.asset_store_path.write_text("", encoding="utf-8")
            retry_request = SimpleNamespace(tail_frame_chaining=True)
            ledger = {"progress": {"items": [{
                "videoIndex": 1,
                "status": "succeeded",
                "assetRecord": {"archiveLocalPath": str(predecessor)},
            }]}}

            with patch.object(ai8video_web, "get_generation_ledger_snapshot", return_value=ledger):
                source = ai8video_web._resolve_retry_tail_frame_source(
                    config,
                    "session-1",
                    2,
                    retry_request,
                )
            self.assertEqual(source, str(predecessor))

    def test_tail_frame_refresh_prefers_latest_video_timeline_candidate(self) -> None:
        current = self.root / "current.mp4"
        candidate = self.root / "candidate.mp4"
        current.write_bytes(b"current")
        candidate.write_bytes(b"edited-tail")
        records = [{
            "sessionId": "session-1",
            "videoIndex": 1,
            "userGeneratedKey": "video/current.mp4",
            "archiveLocalPath": str(self.root / "archived.mp4"),
        }]

        with patch.object(
            ai8video_web,
            "_resolve_user_generated_video_key",
            return_value=(current, "video/current.mp4"),
        ), patch.object(
            ai8video_web,
            "_current_video_timeline_status",
            return_value={"reviewId": "review-current", "pending": False, "timelineChunks": [{"index": 0}]},
        ), patch.object(
            ai8video_web,
            "resolve_video_timeline_review_video",
            return_value=candidate,
        ):
            source = ai8video_web._latest_edited_tail_frame_source(records, "session-1", 1)

        self.assertEqual(source, candidate)

    def test_tail_frame_refresh_does_not_fall_back_to_archived_video(self) -> None:
        archived = self.root / "archived.mp4"
        archived.write_bytes(b"initial-archive")
        records = [{
            "sessionId": "session-1",
            "videoIndex": 1,
            "archiveLocalPath": str(archived),
        }]

        source = ai8video_web._latest_edited_tail_frame_source(records, "session-1", 1)

        self.assertIsNone(source)

    def test_tail_frame_retry_falls_back_to_archived_asset_when_ledger_status_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            predecessor = Path(tmp) / "video-1.mp4"
            predecessor.write_bytes(b"video")
            asset_path = Path(tmp) / "assets.jsonl"
            asset_path.write_text(
                f'{json.dumps({"sessionId": "session-1", "videoIndex": 1, "generationStatus": "generated", "archiveLocalPath": str(predecessor)}, ensure_ascii=False)}\n',
                encoding="utf-8",
            )
            config = SimpleNamespace(asset_store_path=asset_path)
            retry_request = SimpleNamespace(tail_frame_chaining=True)
            ledger = {"progress": {"items": [{"videoIndex": 1, "status": "archiving"}]}}

            with patch.object(ai8video_web, "get_generation_ledger_snapshot", return_value=ledger):
                source = ai8video_web._resolve_retry_tail_frame_source(
                    config,
                    "session-1",
                    2,
                    retry_request,
                )

            self.assertEqual(source, str(predecessor))

    def test_tail_frame_retry_rejects_older_success_after_latest_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            predecessor = Path(tmp) / "video-1.mp4"
            predecessor.write_bytes(b"video")
            asset_path = Path(tmp) / "assets.jsonl"
            records = [
                {
                    "sessionId": "session-1",
                    "videoIndex": 1,
                    "generationStatus": "generated",
                    "archiveLocalPath": str(predecessor),
                },
                {
                    "sessionId": "session-1",
                    "videoIndex": 1,
                    "generationStatus": "failed",
                },
            ]
            asset_path.write_text(
                "".join(f"{json.dumps(record, ensure_ascii=False)}\n" for record in records),
                encoding="utf-8",
            )
            config = SimpleNamespace(asset_store_path=asset_path)
            retry_request = SimpleNamespace(tail_frame_chaining=True)

            with patch.object(ai8video_web, "get_generation_ledger_snapshot", return_value=None):
                with self.assertRaisesRegex(ValueError, "请先成功生成第 1 条视频"):
                    ai8video_web._resolve_retry_tail_frame_source(
                        config,
                        "session-1",
                        2,
                        retry_request,
                    )

    def test_retryable_asset_falls_back_to_latest_session_failure_without_batch_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset_path = Path(tmp) / "assets.jsonl"
            records = [
                {
                    "sessionId": "session-1",
                    "generationBatchId": "batch-old",
                    "videoIndex": 1,
                    "generationStatus": "failed",
                    "prompt": "旧方案",
                },
                {
                    "sessionId": "session-1",
                    "generationBatchId": "batch-new",
                    "videoIndex": 1,
                    "generationStatus": "failed",
                    "prompt": "新方案",
                },
            ]
            asset_path.write_text(
                "".join(f"{json.dumps(record, ensure_ascii=False)}\n" for record in records),
                encoding="utf-8",
            )
            config = SimpleNamespace(asset_store_path=asset_path)

            latest = ai8video_web._find_retryable_asset_record(config, "session-1", "", 1)
            exact = ai8video_web._find_retryable_asset_record(config, "session-1", "batch-old", 1)

            self.assertEqual(latest["prompt"], "新方案")
            self.assertEqual(exact["prompt"], "旧方案")

    def test_static_video_preview_derives_delete_key_from_user_generated_url(self) -> None:
        html = read_static_source()

        self.assertIn("function deriveUserGeneratedKeyFromMediaUrl(value)", html)
        self.assertIn("const prefix = '/user-generated-results/';", html)
        self.assertIn("const userGeneratedKey = item.userGeneratedKey || deriveUserGeneratedKeyFromMediaUrl(videoSrc);", html)
        self.assertIn("const explicitKey = trigger?.getAttribute?.('data-video-user-generated-key') || '';", html)
        self.assertIn("const userGeneratedKey = explicitKey || deriveUserGeneratedKeyFromMediaUrl(src);", html)

    def test_static_video_preview_can_open_current_burned_result_in_folder(self) -> None:
        html = read_static_source()

        self.assertIn('id="resultModalOpenFolderButton"', html)
        self.assertIn('title="打开最终烧录成片文件夹">打开成片文件夹</button>', html)
        self.assertIn('id="videoPreviewOpenBurnedFolderButton"', html)
        self.assertIn('>在文件夹中打开</button>', html)
        self.assertIn("currentVideoPreviewUserGeneratedKey()", html)
        self.assertIn("/api/user-generated-results/open-burned-in-folder", html)
        self.assertIn("JSON.stringify({ userGeneratedKey })", html)
        self.assertIn("已在文件夹中选中", html)

    def test_agent_batch_merge_collapses_cards_and_reconciles_merged_preview(self) -> None:
        html = read_static_source()

        self.assertIn("await animateAgentResultBatchMerge(keys);", html)
        self.assertIn("function reconcileBatchMergedProgress(progress)", html)
        self.assertIn("function resolveBatchMergedProgressSourceKey(item)", html)
        self.assertIn("function findBatchMergedProgressMatch(item, groups)", html)
        self.assertIn("item?.assetRecord?.archiveKey", html)
        self.assertIn("historicalSnapshot: false", html)
        self.assertIn("const progress = reconcileBatchMergedProgress(pending.generationProgress || {});", html)
        self.assertIn("batchMergedSourceKeys", html)
        self.assertIn("agentBatchMergeFoldIntoAnchor", html)
        self.assertIn("outline-offset: 0;", html)
        self.assertIn("batchSubmitting: batchState.submitting", html)
        self.assertIn("options.batchSubmitting ? 'disabled aria-disabled=\"true\"'", html)
        self.assertIn("is-batch-merge-submitting .result-notify-play", html)

    def test_hidden_bgm_merge_stops_at_video_duration(self) -> None:
        command = _merged_bgm_command(
            "ffmpeg",
            Path("input.mp4"),
            Path("output.mp4"),
            [{
                "musicPath": "music.mp3",
                "startSeconds": 0,
                "durationSeconds": 3,
                "sourceOffsetSeconds": 0,
                "volume": 0.3,
            }],
        )

        self.assertIn("-shortest", command)

    def test_viral_breakdown_interval_shows_live_frame_estimate(self) -> None:
        html = read_static_source()

        self.assertIn('id="viralBreakdownFrameEstimate"', html)
        self.assertIn('#viralBreakdownModal .area-transcript .viral-breakdown-empty', html)
        self.assertIn('function moveViralBreakdownTranscriptChunks(segments, fromIndex, toIndex)', html)
        self.assertIn("segment.audioUrl || segment.sourceAudioUrl", html)
        self.assertNotIn('function moveViralBreakdownTranscriptContent(', html)
        target_ratio_position = html.index('id="viralBreakdownTargetRatio"')
        interval_position = html.index('id="viralBreakdownIntervalInput"')
        grid_meta_position = html.index('id="viralBreakdownGridMeta"')
        self.assertLess(target_ratio_position, interval_position)
        self.assertLess(interval_position, grid_meta_position)
        self.assertNotIn('class="viral-breakdown-toolbar-main"', html)
        self.assertIn("function openViralBreakdownGridFrame(event, image, item)", html)
        self.assertIn("function showViralBreakdownFrameLightbox(item, frameIndex)", html)
        self.assertIn("data-viral-grid-preview", html)
        self.assertIn("viral-breakdown-lightbox-fade-in", html)
        self.assertNotIn("pendingProgressSweep", html)
        self.assertIn("return Boolean(String(item?.transcriptJsonKey || '').trim());", html)
        self.assertIn("const VIRAL_BREAKDOWN_MAX_FRAME_COUNT = 188;", html)
        self.assertIn("function minimumViralBreakdownInterval(item)", html)
        self.assertIn("function isViralBreakdownGenerateReady(item)", html)
        self.assertNotIn("if (!String(transcriptText || '').trim()) missing.push('识别台词');", html)
        self.assertIn("Math.ceil((duration / VIRAL_BREAKDOWN_MAX_FRAME_COUNT) * 10) / 10", html)
        self.assertIn("intervalInput.min = String(minimumInterval);", html)
        self.assertIn("intervalVideoKey: ''", html)
        self.assertIn("function estimateViralBreakdownFrameCount(item, intervalSeconds)", html)
        self.assertIn("Math.ceil(duration / clampViralBreakdownInterval(item, intervalSeconds))", html)
        self.assertIn("addEventListener('input', (event) =>", html)
        self.assertIn("function resolvePlayablePreviewSrc(item)", html)
        self.assertIn("function deriveLocalPreviewKey(videoKey)", html)
        self.assertIn("data-video-user-generated-preview-key", html)
        self.assertIn("data-regenerate-user-generated-previews", html)
        self.assertIn("/api/user-generated-previews/regenerate", html)
        self.assertIn("data-video-preview-action=\"delete-video\"", html)
        self.assertIn("data-video-preview-action=\"regenerate-tts\"", html)
        self.assertIn("data-video-preview-action=\"edit-video-timeline\"", html)
        self.assertIn("裁剪视频", html)
        self.assertIn("setVideoPreviewButtonLabel(button, '正在渲染…')", html)
        self.assertIn("setVideoPreviewButtonLabel(button, '裁剪视频')", html)
        self.assertLess(
            html.index('data-video-preview-action="edit-video-timeline"'),
            html.index('data-video-preview-action="regenerate-tts"'),
        )
        self.assertIn("data-video-preview-video-timeline", html)
        self.assertIn("data-video-preview-video-chunks", html)
        self.assertIn('data-video-preview-action="toggle-background-music"', html)
        self.assertIn("data-video-preview-background-music-drawer", html)
        self.assertIn("function bindVideoPreviewBackgroundMusic(video)", html)
        self.assertIn("selected?.previewUrl", html)
        self.assertIn("function positionVideoPreviewBackgroundMusicDrawer()", html)
        self.assertIn("max-height: min(320px, 52vh);", html)
        self.assertIn('data-full-label="${escapeHtml(label)}"', html)
        self.assertIn("content: attr(data-full-label)", html)
        self.assertIn("video-preview-timeline-toolbar", html)
        self.assertIn("video-preview-timeline-duration", html)
        self.assertNotIn("video-preview-html-motion-ruler", html)
        self.assertNotIn("data-video-preview-video-output-duration", html)
        self.assertIn(".video-preview-tts-timeline:not(.video-preview-video-timeline) > .video-preview-tts-chunks", html)
        self.assertIn("padding: 6px 10px;", html)
        self.assertIn("padding-block: 5px;", html)
        self.assertIn("height: 44px;", html)
        self.assertNotIn("width: 90%;", html)
        self.assertNotIn("width: 80%;", html)
        self.assertIn("function syncVideoTimelineDurationLabels()", html)
        self.assertIn("videoTimelineOutputDuration || 0", html)
        self.assertNotIn("videoTimelineSourceDuration.toFixed(1)} 秒", html)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);", html)
        self.assertIn("data-video-preview-action=\"toggle-video-scissors\"", html)
        self.assertIn("data-video-preview-action=\"delete-selected-video-chunk\"", html)
        self.assertIn("/api/user-generated-results/video-timeline-review", html)
        self.assertIn("/api/user-generated-results/video-timeline-preview", html)
        self.assertIn("videoTimelineFilmstripUrl", html)
        self.assertNotIn("开启剪刀后点击画面切块", html)
        self.assertNotIn("裁剪后仍可编辑配音和动效", html)
        self.assertIn("timelineOverflowZoneMarkup", html)
        self.assertIn("boundary.ttsOverflowIndexes", html)
        self.assertIn("boundary.htmlMotionOverflowIndexes", html)
        self.assertIn("video-preview-timeline-overflow-zone", html)
        self.assertNotIn("video-preview-tts-boundary-warning", html)
        self.assertNotIn("video-preview-html-motion-boundary-warning", html)
        self.assertIn("超出 ${boundary.videoDurationSeconds.toFixed(1)} 秒，请先调整", html)
        self.assertIn("button.setAttribute('aria-label', reason", html)
        self.assertIn("is-out-of-bounds", html)
        self.assertIn("has-timeline-blocker", html)
        self.assertIn(".video-preview-button.primary:disabled", html)
        self.assertNotIn(".video-preview-button.primary.has-timeline-blocker:disabled", html)
        self.assertNotIn("请先恢复完整视频，再微调 TTS 时间轴", html)
        self.assertIn('.video-preview-controls-row .video-preview-button[aria-expanded="true"]', html)
        self.assertNotIn("data-video-preview-action=\"edit-tts-timeline\"", html)
        self.assertIn("data-video-preview-action=\"edit-tts-text\"", html)
        self.assertIn("video-preview-split-button", html)
        self.assertIn("/api/user-generated-results/tts-narration", html)
        self.assertIn("/api/user-generated-results/tts-narration/polish", html)
        self.assertIn("/api/user-generated-results/tts-narration/expand", html)
        self.assertIn("persistOpenTtsEditorBeforeHtmlMotion", html)
        self.assertIn("await persistOpenTtsEditorBeforeHtmlMotion(key)", html)
        self.assertIn("/api/user-generated-results/regenerate-tts", html)
        self.assertIn("/api/user-generated-results/tts-timeline-preview", html)
        self.assertIn("/api/user-generated-results/burn-review", html)
        self.assertIn("/api/user-generated-results/confirm-burn", html)
        self.assertIn("button.classList.add('is-spinning')", html)
        self.assertIn("button.classList.remove('is-spinning')", html)
        self.assertIn("/api/user-generated-results/regenerate-html-motion", html)
        self.assertIn("/api/user-generated-results/confirm-burn", html)
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
        self.assertIn("video-preview-controls-row", html)
        self.assertIn(".video-preview-controls {\n      position: relative;\n      display: flex;\n      flex-direction: column;", html)
        self.assertIn(".video-preview-controls-row {\n      display: flex;\n      align-items: flex-end;", html)
        self.assertIn(".video-preview-side-actions {", html)
        self.assertIn("min-height: 32px;", html)
        self.assertNotIn('aria-label="播放控制"', html)
        self.assertNotIn('data-video-preview-action="toggle-play"', html)
        self.assertNotIn('data-video-preview-action="restart"', html)
        self.assertNotIn('data-video-preview-action="toggle-mute"', html)
        self.assertIn("重新生成TTS配音", html)
        self.assertNotIn("videoPreviewButtonInnerHtml('edit', '编辑TTS')", html)
        self.assertIn("video-preview-tts-waveform", html)
        self.assertIn("buildTtsWaveformPath", html)
        self.assertIn("waveformPeaks", html)
        self.assertIn("修改台词", html)
        self.assertIn("导出 MP3", html)
        self.assertIn('data-video-preview-action="export-tts-mp3"', html)
        self.assertIn("exportTtsMp3FromVideoPreview", html)
        self.assertIn("/api/user-generated-results/export-tts-mp3", html)
        self.assertIn("保存为…", html)
        self.assertIn("请设置 MP3 文件名和保存位置", html)
        self.assertIn('id="resultModalBatchMergeButton"', html)
        self.assertIn('data-result-batch-merge-select', html)
        self.assertIn("/api/user-generated-results/batch-merge", html)
        self.assertIn("重新生成 HTML 动效", html)
        self.assertIn("强行停止", html)
        self.assertIn("setHtmlMotionButtonBusy(button, true)", html)
        self.assertIn("video-preview-button-spin 0.85s linear infinite", html)
        self.assertIn("cancelHtmlMotionFromVideoPreview", html)
        self.assertIn("确认烧录", html)
        self.assertNotIn("videoPreviewButtonInnerHtml('edit', '微调时间轴')", html)
        self.assertIn("function toggleAllTimelineEditors", html)
        self.assertIn('data-video-preview-action="confirm-burn"', html)
        self.assertNotIn('data-video-preview-action="confirm-html-motion"', html)
        self.assertIn('data-video-preview-action="toggle-tts-scissors"', html)
        self.assertIn('aria-pressed="false"', html)
        self.assertIn("videoPreviewIconSvg('scissors')", html)
        self.assertIn("toggleTtsScissorMode", html)
        self.assertIn("splitTtsTimelineAtPointer", html)
        self.assertIn("is-scissor-mode", html)
        self.assertIn("ttsScissorMode", html)
        self.assertIn('data-video-preview-action="delete-selected-tts-chunk"', html)
        self.assertIn('aria-label="删除所选配音块"', html)
        self.assertIn("deleteSelectedTtsChunk", html)
        self.assertIn("syncTtsDeleteButton", html)
        self.assertIn("ttsSelectedChunkIndex", html)
        self.assertIn("video-preview-tts-chunk.is-selected", html)
        self.assertNotIn("删除会在原位置保留静音", html)
        self.assertIn("const shouldPlay = !video.paused", html)
        self.assertIn("video.autoplay = shouldPlay", html)
        self.assertIn("splitTtsTimelineAtPlayhead", html)
        self.assertIn("beginTtsChunkDrag", html)
        self.assertIn("选择${label}，起点 ${start.toFixed(1)}秒", html)
        self.assertIn("setTtsSelectedChunkIndex(Number(element.dataset.chunkIndex))", html)
        self.assertNotIn("if (!dragged) seekVideoPreviewToHtmlMotionChunk(index)", html)
        self.assertIn("function resetTimelinePointerInteractions()", html)
        self.assertIn("document.addEventListener('pointercancel', resetTimelinePointerInteractions, true)", html)
        self.assertIn("window.addEventListener('blur', resetTimelinePointerInteractions)", html)
        self.assertNotIn("所有修改在确认烧录前都只是预览", html)
        self.assertIn(".video-preview-tts-timeline.is-open", html)
        self.assertIn('data-video-preview-action="toggle-html-motion-scissors"', html)
        self.assertIn('data-video-preview-action="delete-selected-html-motion-chunk"', html)
        self.assertIn('data-video-preview-action="reset-html-motion-timeline"', html)
        self.assertIn('aria-label="删除所选动效片段"', html)
        self.assertIn("toggleHtmlMotionScissorMode", html)
        self.assertIn("splitHtmlMotionTimelineAtPointer", html)
        self.assertIn("deleteSelectedHtmlMotionChunk", html)
        self.assertIn("htmlMotionLivePreviewUrl", html)
        self.assertIn("if (previewUrl) {\n          video.src =", html)
        self.assertIn("preserveVideoSource: true", html)
        self.assertIn("frame.addEventListener('load', sync, { once: true });\n      frame.src =", html)
        self.assertIn("mountPendingHtmlMotionPreview", html)
        self.assertIn("ai8-motion-ready", html)
        self.assertIn("video.addEventListener(eventName, () => syncLiveHtmlMotionPreview(video))", html)
        self.assertIn("/api/user-generated-results/save-html-motion-timeline", html)
        self.assertIn("originalTimelineChunks", html)
        self.assertIn("resetHtmlMotionTimeline", html)
        self.assertIn("htmlMotionSelectedChunkIndex", html)
        self.assertIn("恢复完整动效", html)
        self.assertIn('data-video-preview-action="regenerate-html-motion"', html)
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
        split_end = html.index("</span>", html_motion_button)
        confirm_button = html.index('data-video-preview-action="confirm-burn"', split_end)
        self.assertGreater(confirm_button, split_end)
        self.assertIn("AI 润色", html)
        self.assertIn("AI 扩写", html)
        self.assertIn("video-preview-tts-ai-group", html)
        self.assertIn('class="video-preview-tts-heading"', html)
        self.assertIn('id="viralBreakdownLibraryButton"', html)
        self.assertIn('svgs-full/solid/photo-film.svg', html)
        self.assertIn('id="viralBreakdownLibraryModal"', html)
        self.assertIn('data-delete-viral-library-video', html)
        self.assertIn("fetch('/api/viral-breakdown/delete'", html)
        self.assertIn('原视频、截图、宫格图、台词、镜头语言、猜剧本、生成会话及爆款拆解成片副本都会一并删除', html)
        self.assertIn('grid-template-columns: auto minmax(0, 1fr) auto;', html)
        self.assertIn("if (event.key === 'Escape' && state.viralBreakdown.libraryVisible)", html)
        self.assertIn("if (state.viralBreakdown.libraryVisible) closeViralBreakdownLibraryModal();", html)
        library_modal = html.index('id="viralBreakdownLibraryModal"')
        self.assertLess(html.index('id="viralBreakdownUploadButton"'), library_modal)
        self.assertGreater(html.index('id="viralBreakdownOpenFolderButton"'), library_modal)
        self.assertEqual(html.count('id="viralBreakdownContextActionButton"'), 1)
        self.assertNotIn('id="viralBreakdownProcessFramesButton"', html)
        self.assertNotIn('id="viralBreakdownTranscribeButton"', html)
        self.assertNotIn('id="viralBreakdownAnalyzeShotLanguageButton"', html)
        self.assertNotIn('id="viralBreakdownGuessScriptButton"', html)
        self.assertIn("tab === 'generated'", html)
        self.assertIn("label: state.viralBreakdown.transcriptProcessing ? '识别中...' : '分析台词'", html)
        self.assertIn("else if (tab === 'transcript') await transcribeSelectedViralBreakdownVideo();", html)
        self.assertIn("else if (tab === 'shot-language') await analyzeSelectedViralBreakdownShotLanguage();", html)
        self.assertIn("else if (tab === 'script') await guessSelectedViralBreakdownScript", html)
        self.assertIn("hasAnalysis ? '重新分析镜头' : '分析镜头'", html)

    def test_api_delete_viral_breakdown_videos_forwards_batch_keys(self) -> None:
        request_backup = ai8video_web.request
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"videoKeys": ["原视频/one.mp4", "原视频/two.mp4"]},
        )
        try:
            with patch.object(
                ai8video_web,
                "delete_viral_breakdown_videos",
                return_value={"ok": True, "deletedCount": 2, "deletedBytes": 10, "items": []},
            ) as delete_videos, patch.object(
                ai8video_web,
                "list_viral_breakdown_items",
                return_value={"items": [], "itemCount": 0},
            ):
                body = ai8video_web.api_delete_viral_breakdown_videos()
        finally:
            ai8video_web.request = request_backup

        delete_videos.assert_called_once_with(["原视频/one.mp4", "原视频/two.mp4"])
        self.assertEqual(body["deletedCount"], 2)
        self.assertEqual(body["summary"]["itemCount"], 0)

    def test_viral_transcript_export_uses_reordered_source_audio_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.m4a"
            second = root / "second.m4a"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            paths = {
                "台词音频/demo/first.m4a": first,
                "台词音频/demo/second.m4a": second,
            }

            with patch.object(
                ai8video_web,
                "resolve_viral_breakdown_asset_path",
                side_effect=lambda key: (paths[key], key),
            ), patch.object(ai8video_web, "_ensure_viral_transcript_audio") as synthesize:
                prepared, duration = ai8video_web._prepare_viral_transcript_export_segments([
                    {"text": "第二段", "sourceAudioKey": "台词音频/demo/second.m4a", "durationSeconds": 1.0},
                    {"text": "第一段", "sourceAudioKey": "台词音频/demo/first.m4a", "durationSeconds": 1.2},
                ])

            self.assertEqual([Path(item["audioPath"]).name for item in prepared], ["second.m4a", "first.m4a"])
            self.assertEqual([(item["start"], item["end"]) for item in prepared], [(0.0, 1.0), (1.0, 2.2)])
            self.assertEqual(duration, 2.2)
            synthesize.assert_not_called()

    def test_polish_tts_narration_uses_text_model(self) -> None:
        prompts: list[str] = []
        progress: list[dict[str, str]] = []
        responses = iter(("更顺口的新台词。", '{"passes":true,"issues":[],"approved_text":"更顺口的新台词。"}'))
        with patch.object(ai8video_web, "build_openai_compat_llm", return_value=lambda prompt: prompts.append(prompt) or next(responses)) as build_llm:
            body = ai8video_web._polish_tts_narration_text("旧台词。", 14, progress.append)

        self.assertTrue(body["ok"])
        self.assertEqual(body["text"], "更顺口的新台词")
        self.assertIn("当前视频时长：14.00 秒", prompts[0])
        self.assertEqual(build_llm.call_count, 2)
        self.assertEqual([item["stage"] for item in progress], ["knowledge", "rewrite", "review"])

    def test_polish_tts_narration_accepts_json_model_output(self) -> None:
        responses = iter(('{"text":"JSON 润色台词。"}', '{"passes":true,"issues":[],"approved_text":"JSON 润色台词。"}'))
        with patch.object(
            ai8video_web,
            "build_openai_compat_llm",
            return_value=lambda prompt: next(responses),
        ):
            body = ai8video_web._polish_tts_narration_text("旧台词。", 10)

        self.assertEqual(body["text"], "JSON 润色台词")

    def test_polish_tts_narration_injects_top_k_script_knowledge(self) -> None:
        prompts: list[str] = []
        responses = iter(("知识库增强后的台词。", '{"passes":true,"issues":[],"approved_text":"知识库增强后的台词。"}'))

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return next(responses)

        knowledge = {
            "contextText": "[知识段 1｜私域资产]\n客户资产才是真正的资产。",
            "meta": {"used": True, "query": "私域资产", "recallCount": 20, "topK": 5, "rerankApplied": True},
        }
        with patch.object(ai8video_web, "_tts_script_knowledge", return_value=knowledge), patch.object(
            ai8video_web,
            "build_openai_compat_llm",
            return_value=fake_llm,
        ):
            body = ai8video_web._polish_tts_narration_text("客户不能流失。", 10)

        self.assertIn("[知识段 1｜私域资产]", prompts[0])
        self.assertIn("用户系统提示词", prompts[0])
        self.assertEqual(body["knowledge"]["topK"], 5)

    def test_polish_tts_narration_requires_text_model(self) -> None:
        with patch.object(ai8video_web, "build_openai_compat_llm", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "文本/视频规划模型"):
                ai8video_web._polish_tts_narration_text("旧台词。")

    def test_expand_tts_narration_uses_text_model(self) -> None:
        prompts: list[str] = []
        responses = iter(("扩写后的新台词，节奏更完整。", '{"passes":true,"issues":[],"approved_text":"扩写后的新台词，节奏更完整。"}'))
        with patch.object(ai8video_web, "build_openai_compat_llm", return_value=lambda prompt: prompts.append(prompt) or next(responses)) as build_llm:
            body = ai8video_web._expand_tts_narration_text("旧台词。", 10)

        self.assertTrue(body["ok"])
        self.assertEqual(body["text"], "扩写后的新台词，节奏更完整")
        self.assertEqual(body["targetChars"], 13)
        self.assertIn("当前台词：3 字。本次目标：约 13 字", prompts[0])
        self.assertEqual(build_llm.call_count, 2)

    def test_expand_tts_narration_targets_ten_more_chars_each_time(self) -> None:
        prompts: list[str] = []
        text = "这是一段刚好三十个字左右的当前台词内容用于验证连续扩写目标。"
        responses = iter((text + "继续补充内容。", json.dumps({"passes": True, "issues": [], "approved_text": text + "继续补充内容。"}, ensure_ascii=False)))
        with patch.object(ai8video_web, "build_openai_compat_llm", return_value=lambda prompt: prompts.append(prompt) or next(responses)):
            body = ai8video_web._expand_tts_narration_text(text, 20)

        current_chars = ai8video_web.narration_spoken_char_count(text)
        self.assertEqual(body["targetChars"], current_chars + 10)
        self.assertIn(f"当前台词：{current_chars} 字。本次目标：约 {current_chars + 10} 字", prompts[0])

    def test_tts_character_counts_ignore_punctuation(self) -> None:
        responses = iter(("你好，世界！", '{"passes":true,"issues":[],"approved_text":"你好，世界！"}'))
        with patch.object(ai8video_web, "build_openai_compat_llm", return_value=lambda prompt: next(responses)):
            body = ai8video_web._expand_tts_narration_text("你好，世界！", 10)

        self.assertEqual(body["textChars"], 4)
        self.assertEqual(body["targetChars"], 14)

    def test_tts_transform_reviewer_rejects_text_over_duration_limit(self) -> None:
        long_text = "这是明显超过十秒自然口播容量的台词" * 8
        review = json.dumps({"passes": True, "issues": [], "approved_text": long_text}, ensure_ascii=False)
        responses = iter((long_text, review, review))
        with patch.object(ai8video_web, "build_openai_compat_llm", return_value=lambda prompt: next(responses)):
            with self.assertRaisesRegex(RuntimeError, "Reviewer 未通过"):
                ai8video_web._polish_tts_narration_text("旧台词。", 10)

    def test_tts_duration_limit_uses_slightly_fast_speaking_rate(self) -> None:
        self.assertEqual(ai8video_web.narration_char_limit(10), 55)

    def test_expand_tts_narration_accepts_json_model_output(self) -> None:
        responses = iter(('{"text":"JSON 扩写台词。"}', '{"passes":true,"issues":[],"approved_text":"JSON 扩写台词。"}'))
        with patch.object(
            ai8video_web,
            "build_openai_compat_llm",
            return_value=lambda prompt: next(responses),
        ):
            body = ai8video_web._expand_tts_narration_text("旧台词。", 10)

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

    def test_tts_narration_payload_recovers_dialogue_from_archived_prompt(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        video_path = video_dir / "demo.mp4"
        video_path.write_bytes(b"video")
        manifest_path = self.root / "demo-manifest.json"
        manifest_path.write_text(json.dumps({
            "video": {
                "prompt": "8-10秒：人物面对镜头说：‘走，带你们去看面料样品，工厂到了！’语气期待。",
            },
        }, ensure_ascii=False), encoding="utf-8")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all([{
            "archiveKey": "video/demo.mp4",
            "archiveLocalPath": str(video_path),
            "archiveManifestPath": str(manifest_path),
        }])

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            body = ai8video_web._tts_narration_text_payload_for_user_generated_video("video/demo.mp4")

        self.assertEqual(body["text"], "走，带你们去看面料样品，工厂到了！")
        self.assertFalse(body["manual"])

    def test_batch_merge_keeps_selection_order_and_removes_selected_sources(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        preview_dir = result_root / "preview"
        extension_video_dir = result_root / "extensions" / "video"
        extension_preview_dir = result_root / "extensions" / "preview"
        extension_cover_dir = result_root / "extensions" / "cover"
        video_dir.mkdir(parents=True)
        preview_dir.mkdir(parents=True)
        extension_video_dir.mkdir(parents=True)
        extension_preview_dir.mkdir(parents=True)
        extension_cover_dir.mkdir(parents=True)
        first = video_dir / "first.mp4"
        second = video_dir / "second.mp4"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        (preview_dir / "first.jpg").write_bytes(b"first-preview")
        (preview_dir / "second.jpg").write_bytes(b"second-preview")
        frame_name = hashlib.sha256(b"video/second.mp4").hexdigest()[:24]
        frame_path = result_root / "extension-frame" / f"{frame_name}.png"
        frame_path.parent.mkdir(parents=True)
        frame_path.write_bytes(b"frame")
        (frame_path.parent / f"{frame_name}-batch-1.state.json").write_text("{}", encoding="utf-8")
        review_root = self.root / "html-motion-reviews"
        review_id = hashlib.sha256(b"video/second.mp4").hexdigest()[:32]
        review_dir = review_root / review_id
        review_dir.mkdir(parents=True)
        (review_dir / "candidate.mp4").write_bytes(b"candidate")
        derived_video = extension_video_dir / "derived.mp4"
        derived_preview = extension_preview_dir / "derived.jpg"
        derived_cover = extension_cover_dir / "derived.jpg"
        derived_video.write_bytes(b"derived")
        derived_preview.write_bytes(b"derived-preview")
        derived_cover.write_bytes(b"derived-cover")
        archive_root = self.root / "archive"
        archive_root.mkdir()
        manifest_path = archive_root / "derived-manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")
        tts_dir = self.root / "tts"
        tts_dir.mkdir()
        audio_path = tts_dir / "derived.m4a"
        audio_path.write_bytes(b"audio")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all([
            {"archiveKey": "video/first.mp4", "archiveLocalPath": str(first)},
            {"archiveKey": "video/second.mp4", "archiveLocalPath": str(second)},
            {
                "archiveKey": "extensions/video/derived.mp4",
                "archiveLocalPath": str(derived_video),
                "userGeneratedPreviewKey": "extensions/preview/derived.jpg",
                "archiveCoverKey": "extensions/cover/derived.jpg",
                "archiveManifestPath": str(manifest_path),
                "archiveMeta": {"localTts": {"audioPath": str(audio_path)}},
                "firstFrame": {"source": str(frame_path)},
            },
        ])
        captured: list[str] = []

        def fake_concat(paths, target):
            captured.extend(path.name for path in paths)
            target.write_bytes(b"merged")
            return {"status": "merged", "method": "test"}

        with patch.dict(os.environ, {"AI8VIDEO_ARCHIVE_LOCAL_DIR": str(archive_root)}), patch.object(
            ai8video_web,
            "HTML_MOTION_REVIEW_ROOT",
            review_root,
        ), patch.object(
            ai8video_web,
            "local_tts_output_dir",
            return_value=tts_dir,
        ), patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "concat_videos",
            side_effect=fake_concat,
        ), patch.object(
            ai8video_web,
            "_tts_narration_text_for_user_generated_video",
            side_effect=lambda key, path: (path.stem, {}),
        ), patch.object(
            ai8video_web,
            "save_video_timeline_review",
            return_value={"ok": True},
        ), patch.object(
            ai8video_web,
            "merge_html_motion_reviews",
            return_value={"ok": True, "reviewReady": False},
        ), patch.object(
            ai8video_web,
            "_current_video_timeline_status",
            return_value={},
        ), patch.object(
            ai8video_web,
            "_current_tts_timeline_status",
            return_value={},
        ), patch.object(ai8video_web, "schedule_burned_result_copy") as schedule_copy:
            body = ai8video_web._batch_merge_user_generated_videos(["video/second.mp4", "video/first.mp4"])

        self.assertEqual(captured, ["second.mp4", "first.mp4"])
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertFalse((preview_dir / "first.jpg").exists())
        self.assertFalse((preview_dir / "second.jpg").exists())
        self.assertFalse(frame_path.exists())
        self.assertFalse(review_dir.exists())
        self.assertFalse(derived_video.exists())
        self.assertFalse(derived_preview.exists())
        self.assertFalse(derived_cover.exists())
        self.assertFalse(manifest_path.exists())
        self.assertFalse(audio_path.exists())
        self.assertEqual(JsonlAssetStore(self.root / "assets.jsonl").read_all(), [])
        merged = result_root / body["userGeneratedKey"]
        schedule_copy.assert_called_once_with(
            merged.resolve(),
            result_root=result_root.resolve(),
            overwrite=True,
        )
        self.assertEqual(merged.read_bytes(), b"merged")
        self.assertEqual((result_root / body["previewKey"]).read_bytes(), b"second-preview")

    def test_hidden_bgm_timeline_continues_same_music_and_resets_different_music(self) -> None:
        first_music = self.root / "first.mp3"
        second_music = self.root / "second.mp3"
        first_music.write_bytes(b"first")
        second_music.write_bytes(b"second")
        tracks = [
            {"musicPath": str(first_music), "musicName": "first", "volume": 0.3},
            {"musicPath": str(first_music), "musicName": "first", "volume": 0.3},
            {"musicPath": str(second_music), "musicName": "second", "volume": 0.4},
        ]

        timeline = build_hidden_bgm_timeline(tracks, [8.0, 7.0, 6.0])

        self.assertEqual([item["startSeconds"] for item in timeline], [0.0, 8.0, 15.0])
        self.assertEqual([item["sourceOffsetSeconds"] for item in timeline], [0.0, 8.0, 0.0])

    def test_replaced_bgm_track_uses_one_full_duration_segment(self) -> None:
        result_root = self.root / "用户生成结果"
        video = result_root / "video" / "merged.mp4"
        music = self.root / "music.mp3"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"video")
        music.write_bytes(b"music")
        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web, "track_duration", return_value=24.5,
        ):
            ai8video_web._replace_user_generated_background_music_track(
                "video/merged.mp4",
                {"enabled": True, "path": str(music), "name": "new.mp3", "volume": 0.4},
            )

        metadata = json.loads(
            (result_root / ".restored-meta" / "video" / "merged.mp4.json").read_text(encoding="utf-8")
        )
        segments = metadata["backgroundMusicTrack"]["segments"]
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["startSeconds"], 0.0)
        self.assertEqual(segments[0]["durationSeconds"], 24.5)

    def test_batch_merge_treats_legacy_baked_background_music_as_empty_track(self) -> None:
        with patch.object(ai8video_web, "load_restored_result_metadata", return_value={}), patch.object(
            ai8video_web,
            "_asset_record_for_user_generated_key",
            return_value={"archiveMeta": {"backgroundMusic": {"enabled": True, "status": "mixed"}}},
        ):
            track = ai8video_web._background_music_track_for_user_generated_video(
                "video/legacy.mp4",
                self.root / "legacy.mp4",
            )

        self.assertEqual(track, {})

    def test_merged_preview_tracks_shift_each_source_to_merged_timeline(self) -> None:
        video_chunks = merged_video_chunks([8.0, 7.0])
        statuses = [
            {"audioDurationSeconds": 8.5, "timelineChunks": [
                {"sourceStartSeconds": 1.0, "sourceEndSeconds": 3.0, "startSeconds": 0.5},
            ]},
            {"audioDurationSeconds": 7.5, "timelineChunks": [
                {"sourceStartSeconds": 0.0, "sourceEndSeconds": 2.0, "startSeconds": 1.0},
            ]},
        ]

        tts_chunks = merged_tts_chunks(statuses, [8.0, 7.0])

        self.assertEqual(video_chunks, [
            {"sourceStartSeconds": 0.0, "sourceEndSeconds": 8.0},
            {"sourceStartSeconds": 8.0, "sourceEndSeconds": 15.0},
        ])
        self.assertEqual(tts_chunks[1]["sourceStartSeconds"], 8.5)
        self.assertEqual(tts_chunks[1]["startSeconds"], 9.0)

    def test_merged_preview_preserves_source_video_cut_chunks(self) -> None:
        statuses = [
            {"timelineChunks": [
                {"sourceStartSeconds": 1.0, "sourceEndSeconds": 3.0},
                {"sourceStartSeconds": 5.0, "sourceEndSeconds": 7.0},
            ]},
            {"timelineChunks": [{"sourceStartSeconds": 2.0, "sourceEndSeconds": 6.0}]},
        ]

        chunks = merged_edited_video_chunks(statuses, [8.0, 10.0])

        self.assertEqual(chunks, [
            {"sourceStartSeconds": 1.0, "sourceEndSeconds": 3.0},
            {"sourceStartSeconds": 5.0, "sourceEndSeconds": 7.0},
            {"sourceStartSeconds": 10.0, "sourceEndSeconds": 14.0},
        ])
        self.assertEqual(edited_video_durations(statuses, [8.0, 10.0]), [4.0, 4.0])

    def test_merged_html_motion_offsets_scenes_and_renames_dom_ids(self) -> None:
        artifact = {"scenes": [{
            "start": 1.0,
            "end": 2.5,
            "ids": ["scene-title"],
            "html": '<div id="scene-title"></div>',
            "css": "#scene-title{opacity:1}",
            "animations": [{"target": "#scene-title"}],
        }]}

        scenes = html_motion_merge._offset_scenes(artifact, 1, 8.0)

        self.assertEqual(scenes[0]["start"], 9.0)
        self.assertEqual(scenes[0]["end"], 10.5)
        self.assertEqual(scenes[0]["_timelineSourceIndex"], 0)
        self.assertIn("m2-1-scene-title", scenes[0]["html"])
        self.assertEqual(scenes[0]["animations"][0]["target"], "#m2-1-scene-title")

        next_scenes = html_motion_merge._offset_scenes(artifact, 2, 16.0, output_start_index=4)

        self.assertEqual(next_scenes[0]["_timelineSourceIndex"], 4)

    def test_batch_merge_uses_clean_html_motion_base_when_available(self) -> None:
        video_path = self.root / "burned.mp4"
        clean_base = self.root / "clean.mp4"
        fallback = self.root / "bgm-base.mp4"

        with patch.object(
            ai8video_web,
            "html_motion_review_base_path",
            return_value=clean_base,
        ):
            source = ai8video_web._batch_merge_visual_source(
                video_path,
                "video/demo.mp4",
                fallback,
            )

        self.assertEqual(source, clean_base)

    def test_batch_merge_falls_back_when_clean_html_motion_base_is_missing(self) -> None:
        video_path = self.root / "burned.mp4"
        fallback = self.root / "bgm-base.mp4"

        with patch.object(
            ai8video_web,
            "html_motion_review_base_path",
            side_effect=LookupError("missing"),
        ):
            source = ai8video_web._batch_merge_visual_source(
                video_path,
                "video/demo.mp4",
                fallback,
            )

        self.assertEqual(source, fallback)

    def test_batch_merge_restores_sources_when_install_fails(self) -> None:
        result_root = self.root / "用户生成结果"
        video_dir = result_root / "video"
        video_dir.mkdir(parents=True)
        first = video_dir / "first.mp4"
        second = video_dir / "second.mp4"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        frame_name = hashlib.sha256(b"video/first.mp4").hexdigest()[:24]
        frame_path = result_root / "extension-frame" / f"{frame_name}.png"
        frame_path.parent.mkdir(parents=True)
        frame_path.write_bytes(b"frame")
        review_root = self.root / "html-motion-reviews"
        review_id = hashlib.sha256(b"video/first.mp4").hexdigest()[:32]
        review_dir = review_root / review_id
        review_dir.mkdir(parents=True)
        (review_dir / "candidate.mp4").write_bytes(b"candidate")

        def fake_concat(paths, target):
            target.write_bytes(b"merged")
            return {"status": "merged", "method": "test"}

        with patch.object(ai8video_web, "HTML_MOTION_REVIEW_ROOT", review_root), patch.object(
            ai8video_web,
            "ensure_user_generated_result_dir",
            return_value=result_root,
        ), patch.object(
            ai8video_web,
            "concat_videos",
            side_effect=fake_concat,
        ), patch.object(
            ai8video_web,
            "_tts_narration_text_for_user_generated_video",
            return_value=("", {}),
        ), patch.object(
            ai8video_web,
            "save_video_timeline_review",
            return_value={"ok": True},
        ), patch.object(
            ai8video_web,
            "merge_html_motion_reviews",
            return_value={"ok": True, "reviewReady": False},
        ), patch.object(
            ai8video_web,
            "generate_preview_for_video",
            return_value={"ok": False},
        ):
            with self.assertRaisesRegex(RuntimeError, "预览图生成失败"):
                ai8video_web._batch_merge_user_generated_videos(["video/first.mp4", "video/second.mp4"])

        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(second.read_bytes(), b"second")
        self.assertEqual(frame_path.read_bytes(), b"frame")
        self.assertEqual((review_dir / "candidate.mp4").read_bytes(), b"candidate")
        self.assertEqual(list(video_dir.glob("批量合并-*.mp4")), [])

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

        audio_path = self.root / "tts" / "demo.m4a"
        audio_path.parent.mkdir()
        audio_path.write_bytes(b"audio")
        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "attach_local_tts_to_video",
            return_value={"status": "mixed", "textChars": 3, "audioPath": str(audio_path), "ttsVolume": 1.0},
        ) as attach_tts, patch.object(
            ai8video_web,
            "_safe_local_tts_audio_path",
            return_value=audio_path,
        ), patch.object(ai8video_web, "save_tts_timeline_review"), patch.object(
            ai8video_web,
            "_burn_review_payload",
            return_value={"reviewReady": True, "previewUrl": "/preview/tts.mp4"},
        ):
            ai8video_web._regenerate_user_generated_tts("video/demo.mp4")

        self.assertEqual(attach_tts.call_args.kwargs["narration_text"], "新台词")
        self.assertNotEqual(Path(attach_tts.call_args.args[0]).resolve(), video_path.resolve())
        self.assertEqual(video_path.read_bytes(), b"video")

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

    def test_legacy_extension_results_move_out_of_main_result_list(self) -> None:
        result_root = self.root / "用户生成结果"
        video_path = result_root / "video" / "demo-extension.mp4"
        preview_path = result_root / "preview" / "demo-extension.jpg"
        video_path.parent.mkdir(parents=True)
        preview_path.parent.mkdir(parents=True)
        video_path.write_bytes(b"video")
        preview_path.write_bytes(b"preview")
        JsonlAssetStore(self.root / "assets.jsonl").rewrite_all([{
            "archiveKey": "video/demo-extension.mp4",
            "archiveLocalPath": str(video_path),
            "videoTitle": "演示视频-延长",
        }])

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            items = ai8video_web._user_generated_result_items(limit=10)

        self.assertEqual(items, [])
        self.assertTrue((result_root / "extensions" / "video" / "demo-extension.mp4").is_file())
        self.assertTrue((result_root / "extensions" / "preview" / "demo-extension.jpg").is_file())
        record = JsonlAssetStore(self.root / "assets.jsonl").read_all()[0]
        self.assertEqual(record["archiveKey"], "extensions/video/demo-extension.mp4")
        self.assertEqual(record["archiveMeta"]["artifactKind"], "extension")

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

        audio_path = self.root / "tts" / "demo.m4a"
        audio_path.parent.mkdir()
        audio_path.write_bytes(b"audio")
        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "attach_local_tts_to_video",
            return_value={"status": "mixed", "textChars": 12, "audioPath": str(audio_path), "ttsVolume": 1.2},
        ) as attach_tts, patch.object(
            ai8video_web,
            "_safe_local_tts_audio_path",
            return_value=audio_path,
        ), patch.object(
            ai8video_web,
            "save_tts_timeline_review",
            return_value={"pending": True},
        ) as save_review, patch.object(
            ai8video_web,
            "_burn_review_payload",
            return_value={
                "reviewReady": True,
                "pendingKinds": ["tts"],
                "previewUrl": "/api/user-generated-results/tts-timeline-preview/review-demo",
            },
        ):
            body = ai8video_web._regenerate_user_generated_tts("video/demo.mp4")

        self.assertTrue(body["ok"])
        self.assertTrue(body["burnReview"]["reviewReady"])
        attach_tts.assert_called_once()
        self.assertEqual(attach_tts.call_args.kwargs["narration_text"], "第一句台词。第二句台词")
        self.assertFalse(attach_tts.call_args.kwargs["preserve_original_audio"])
        self.assertNotEqual(Path(attach_tts.call_args.args[0]).resolve(), video_path.resolve())
        self.assertEqual(video_path.read_bytes(), b"video")
        save_review.assert_called_once()

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

    def test_regenerate_user_generated_tts_fails_when_preview_remix_fails(self) -> None:
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

        audio_path = self.root / "tts" / "demo.m4a"
        audio_path.parent.mkdir()
        audio_path.write_bytes(b"audio")
        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "attach_local_tts_to_video",
            return_value={"status": "mixed", "textChars": 6, "audioPath": str(audio_path), "ttsVolume": 1.0},
        ), patch.object(
            ai8video_web,
            "_safe_local_tts_audio_path",
            return_value=audio_path,
        ), patch.object(ai8video_web, "save_tts_timeline_review"), patch.object(
            ai8video_web,
            "_burn_review_payload",
            side_effect=RuntimeError("BGM 混音失败"),
        ):
            with self.assertRaisesRegex(RuntimeError, "BGM 混音失败"):
                ai8video_web._regenerate_user_generated_tts("video/demo.mp4")

    def test_api_export_user_generated_tts_mp3_passes_video_key(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={"userGeneratedKey": "video/demo.mp4"},
        )
        ai8video_web.response = SimpleNamespace(status=200)
        expected = {"ok": True, "canceled": False, "fileName": "demo-TTS配音.mp3"}
        try:
            with patch.object(
                ai8video_web,
                "_export_user_generated_tts_mp3",
                return_value=expected,
            ) as export_mp3:
                body = ai8video_web.api_export_user_generated_tts_mp3()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(body, expected)
        export_mp3.assert_called_once_with("video/demo.mp4")

    def test_tts_timeline_starts_complete_and_preserves_source_order(self) -> None:
        complete = tts_timeline_review.normalize_tts_timeline_chunks(
            [],
            audio_duration_seconds=10,
            video_duration_seconds=12,
        )
        split = tts_timeline_review.normalize_tts_timeline_chunks(
            [
                {"sourceStartSeconds": 0, "sourceEndSeconds": 4, "startSeconds": 0},
                {"sourceStartSeconds": 4, "sourceEndSeconds": 10, "startSeconds": 5},
            ],
            audio_duration_seconds=10,
            video_duration_seconds=12,
        )
        deleted_middle = tts_timeline_review.normalize_tts_timeline_chunks(
            [
                {"sourceStartSeconds": 0, "sourceEndSeconds": 4, "startSeconds": 0},
                {"sourceStartSeconds": 6, "sourceEndSeconds": 10, "startSeconds": 6},
            ],
            audio_duration_seconds=10,
            video_duration_seconds=12,
        )
        single_remaining = tts_timeline_review.normalize_tts_timeline_chunks(
            [{"sourceStartSeconds": 2, "sourceEndSeconds": 8, "startSeconds": 2}],
            audio_duration_seconds=10,
            video_duration_seconds=12,
        )

        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]["label"], "完整配音")
        self.assertEqual(complete[0]["originalSourceEndSeconds"], 10.0)
        self.assertEqual([item["label"] for item in split], ["配音 1", "配音 2"])
        self.assertEqual(single_remaining[0]["label"], "配音 1")
        self.assertEqual(split[1]["startSeconds"], 5)
        self.assertEqual(
            [(item["sourceStartSeconds"], item["sourceEndSeconds"]) for item in deleted_middle],
            [(0.0, 4.0), (6.0, 10.0)],
        )
        with self.assertRaisesRegex(ValueError, "不能重复"):
            tts_timeline_review.normalize_tts_timeline_chunks(
                [
                    {"sourceStartSeconds": 0, "sourceEndSeconds": 6, "startSeconds": 0},
                    {"sourceStartSeconds": 5, "sourceEndSeconds": 10, "startSeconds": 6},
                ],
                audio_duration_seconds=10,
                video_duration_seconds=12,
            )

    def test_tts_timeline_keeps_full_audio_restore_bound_past_video_end(self) -> None:
        chunks = tts_timeline_review.normalize_tts_timeline_chunks(
            [],
            audio_duration_seconds=12,
            video_duration_seconds=8,
        )

        self.assertEqual(chunks[0]["sourceEndSeconds"], 8.0)
        self.assertEqual(chunks[0]["originalSourceEndSeconds"], 12.0)
        self.assertEqual(chunks[0]["label"], "完整配音")
        with self.assertRaisesRegex(ValueError, "不能重叠"):
            tts_timeline_review.normalize_tts_timeline_chunks(
                [
                    {"sourceStartSeconds": 0, "sourceEndSeconds": 6, "startSeconds": 0},
                    {"sourceStartSeconds": 6, "sourceEndSeconds": 10, "startSeconds": 5},
                ],
                audio_duration_seconds=10,
                video_duration_seconds=12,
            )

    def test_tts_timeline_ffmpeg_command_splits_audio_and_rebuilds_timestamps(self) -> None:
        command = tts_timeline_review._tts_timeline_ffmpeg_command(
            Path("source.mp4"),
            Path("voice.m4a"),
            Path("target.mp4"),
            [
                {"sourceStartSeconds": 0, "sourceEndSeconds": 4, "startSeconds": 0},
                {"sourceStartSeconds": 4, "sourceEndSeconds": 10, "startSeconds": 5},
            ],
            12,
            1.2,
            "ffmpeg",
        )
        filter_complex = command[command.index("-filter_complex") + 1]

        self.assertIn("[1:a:0]asplit=2[source0][source1]", filter_complex)
        self.assertIn("[source0]atrim=start=0:end=4", filter_complex)
        self.assertIn("[source1]atrim=start=4:end=10", filter_complex)
        self.assertIn("adelay=5000:all=1,asetpts=N/SR/TB[tts1]", filter_complex)
        self.assertIn("apad=whole_dur=12.000", filter_complex)
        self.assertIn("atrim=end=12.000", filter_complex)
        self.assertIn("asetpts=N/SR/TB[aout]", filter_complex)
        self.assertEqual(tts_timeline_review.TTS_TIMELINE_PREVIEW_AUDIO_MODE, "tts-only-v3")

    def test_video_timeline_ripple_packs_chunks_and_remaps_tts(self) -> None:
        chunks = video_timeline_review.normalize_video_timeline_chunks(
            [
                {"sourceStartSeconds": 0, "sourceEndSeconds": 3, "durationSeconds": 3},
                {"sourceStartSeconds": 5, "sourceEndSeconds": 10, "durationSeconds": 5},
            ],
            video_duration_seconds=10,
        )
        remapped = video_timeline_review.remap_timeline_chunks_through_video_cuts(
            [{"sourceStartSeconds": 0, "sourceEndSeconds": 10, "startSeconds": 0}],
            chunks,
        )

        self.assertEqual([item["startSeconds"] for item in chunks], [0.0, 3.0])
        self.assertEqual([item["durationSeconds"] for item in chunks], [3.0, 5.0])
        self.assertEqual(chunks[0]["originalSourceEndSeconds"], 3.0)
        self.assertEqual([item["startSeconds"] for item in remapped], [0.0, 3.0])
        self.assertEqual(
            [(item["sourceStartSeconds"], item["sourceEndSeconds"]) for item in remapped],
            [(0.0, 3.0), (5.0, 10.0)],
        )
        with self.assertRaisesRegex(ValueError, "不能重叠"):
            video_timeline_review.normalize_video_timeline_chunks(
                [
                    {"sourceStartSeconds": 0, "sourceEndSeconds": 6},
                    {"sourceStartSeconds": 5, "sourceEndSeconds": 10},
                ],
                video_duration_seconds=10,
            )

    def test_video_timeline_save_discards_stale_revision_before_conflict_check(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            video_path = root / "source.mp4"
            video_path.write_bytes(b"current-video")
            relative_key = "batch/source.mp4"
            review_root = root / "reviews"
            review_dir = review_root / hashlib.sha256(relative_key.encode("utf-8")).hexdigest()[:32]
            review_dir.mkdir(parents=True)
            (review_dir / "review.json").write_text(json.dumps({
                "revision": 4,
                "pending": True,
                "relativeKey": relative_key,
                "sourceSignature": {"path": str(video_path), "sizeBytes": 1, "mtimeNs": 1},
            }), encoding="utf-8")

            with patch.object(video_timeline_review, "VIDEO_TIMELINE_REVIEW_ROOT", review_root), patch.object(
                video_timeline_review, "_video_duration", return_value=8.0,
            ):
                review = video_timeline_review.save_video_timeline_review(
                    video_path,
                    relative_key,
                    [{"sourceStartSeconds": 0, "sourceEndSeconds": 6.6}],
                    expected_revision=0,
                )

            self.assertEqual(review["revision"], 1)
            self.assertEqual(review["outputDurationSeconds"], 6.6)

    def test_timeline_left_trim_preserves_restore_start_bounds(self) -> None:
        video = video_timeline_review.normalize_video_timeline_chunks(
            [{
                "sourceStartSeconds": 2,
                "sourceEndSeconds": 8,
                "originalSourceStartSeconds": 0,
                "originalSourceEndSeconds": 10,
            }],
            video_duration_seconds=10,
        )
        tts = tts_timeline_review.normalize_tts_timeline_chunks(
            [{
                "sourceStartSeconds": 2,
                "sourceEndSeconds": 8,
                "originalSourceStartSeconds": 0,
                "originalSourceEndSeconds": 10,
                "startSeconds": 2,
            }],
            audio_duration_seconds=10,
            video_duration_seconds=12,
        )

        self.assertEqual(video[0]["startSeconds"], 0.0)
        self.assertEqual(video[0]["durationSeconds"], 6.0)
        self.assertEqual(video[0]["originalSourceStartSeconds"], 0.0)
        self.assertEqual(tts[0]["startSeconds"], 2.0)
        self.assertEqual(tts[0]["durationSeconds"], 6.0)
        self.assertEqual(tts[0]["originalSourceStartSeconds"], 0.0)

    def test_video_timeline_ffmpeg_command_trims_audio_with_video_chunks(self) -> None:
        command = video_timeline_review._video_timeline_ffmpeg_command(
            Path("source.mp4"),
            Path("target.mp4"),
            [
                {"sourceStartSeconds": 0, "sourceEndSeconds": 3, "durationSeconds": 3},
                {"sourceStartSeconds": 5, "sourceEndSeconds": 10, "durationSeconds": 5},
            ],
            True,
            "ffmpeg",
        )
        filter_complex = command[command.index("-filter_complex") + 1]

        self.assertIn("[0:v:0]split=2[videoSource0][videoSource1]", filter_complex)
        self.assertIn("[0:a:0]asplit=2[audioSource0][audioSource1]", filter_complex)
        self.assertIn("trim=start=0:end=3", filter_complex)
        self.assertIn("atrim=start=0:end=3", filter_complex)
        self.assertIn("atrim=start=5:end=10", filter_complex)
        self.assertIn("concat=n=2:v=1:a=1[videoOut][audioOut]", filter_complex)
        self.assertEqual(command[command.index("-map", command.index("-map") + 1) + 1], "[audioOut]")
        self.assertNotIn("-t", command)

    def test_video_timeline_tts_preview_clips_copy_without_mutating_source(self) -> None:
        chunks = [{"sourceStartSeconds": 0, "sourceEndSeconds": 9.9, "startSeconds": 0}]

        clipped = video_timeline_review._clip_tts_chunks_for_preview(chunks, 4.1)

        self.assertEqual(clipped, [{
            "sourceStartSeconds": 0.0,
            "sourceEndSeconds": 4.1,
            "startSeconds": 0.0,
        }])
        self.assertEqual(chunks[0]["sourceEndSeconds"], 9.9)

    def test_static_video_timeline_precision_and_regeneration_controls(self) -> None:
        source = read_static_source()

        self.assertIn("function bindTimelinePlayheadDrag", source)
        self.assertIn("data-video-preview-cut-guide", source)
        self.assertIn("function handleVideoTimelineSpacePlayback", source)
        self.assertIn("function videoOutputTimeToSourceTime(outputSeconds)", source)
        self.assertIn("function bindTimelineEndTrimHandle", source)
        self.assertIn("function bindTimelineStartTrimHandle", source)
        self.assertIn("function timelineFixedContentGeometry", source)
        self.assertIn("function videoTimelineEditScaleDuration", source)
        self.assertIn('data-video-preview-timeline-trim-handle="end"', source)
        self.assertIn('data-video-preview-timeline-trim-handle="start"', source)
        self.assertIn("originalSourceEndSeconds", source)
        self.assertIn("--video-preview-tts-waveform-scale", source)
        self.assertIn("--video-preview-video-content-scale", source)
        self.assertIn("--video-preview-tts-waveform-offset", source)
        self.assertIn("--video-preview-video-content-offset", source)
        self.assertRegex(
            source,
            r"\.video-preview-tts-waveform\s*\{[^}]*left: calc\(5px - var\(--video-preview-tts-waveform-offset",
        )
        self.assertRegex(
            source,
            r"\.video-preview-tts-playhead::after\s*\{[^}]*left: 5px;",
        )
        self.assertIn("拖动左右边缘可裁剪或恢复", source)
        self.assertIn("function bindTtsChunkTrimHandles", source)
        self.assertIn("function bindVideoChunkTrimHandles", source)
        self.assertIn("function bindHtmlMotionChunkTrimHandles", source)
        self.assertIn("finalize: finalizeVideoChunkStartTrim", source)
        self.assertIn("videoOutputTimeToSourceTime(previewTime)", source)
        self.assertIn('data-video-preview-action="regenerate-video"', source)
        self.assertIn("videoPreviewButtonInnerHtml('regenerate', '重新生成')", source)
        self.assertIn("videoPreviewExtensionMergeMarkup(mode, savedState)", source)
        self.assertIn("'/api/user-generated-results/replace'", source)
        self.assertIn("if (data?.needsLoad === true) return;", source)
        self.assertIn("configureVideoTimeline(data.videoTimeline || {});", source)
        self.assertIn("refreshVideoTimelineRevision(key)", source)
        self.assertIn("requestVideoTimelinePreview(key, requestedChunks, options)", source)
        self.assertNotIn("state.videoPreviewModal.videoTimelineOutputDuration = 0;", source)
        self.assertIn("let videoPreviewBackdropPointerDown = false;", source)
        self.assertIn("videoPreviewBackdropPointerDown && event.target === els.videoPreviewModal", source)

    def test_static_multi_chunk_restore_and_cut_boundary_regressions(self) -> None:
        source = read_static_source()

        self.assertIn("let remaining = timelineChunkWithRestoreBounds(chunk);", source)
        self.assertIn("splitTimelineRestoreBounds(remaining, end)", source)
        self.assertIn("const chunk = chunks.slice().reverse().find(", source)
        self.assertIn("output >= Number(item.startSeconds || 0)", source)

    def test_static_timeline_history_covers_all_edit_tracks(self) -> None:
        source = read_static_source()

        self.assertIn('data-video-preview-action="undo-timeline"', source)
        self.assertIn('data-video-preview-action="redo-timeline"', source)
        self.assertIn("const TIMELINE_HISTORY_LIMIT = 50;", source)
        self.assertIn("function recordTimelineHistory(track, label, before)", source)
        self.assertIn("async function applyTimelineHistory(direction)", source)
        self.assertIn("recordTimelineHistory('video'", source)
        self.assertIn("recordTimelineHistory('tts'", source)
        self.assertIn("recordTimelineHistory('html'", source)
        self.assertIn("if (key === 'y' || event.shiftKey) redoTimelineHistory();", source)
        self.assertIn("if (!changed) return setTtsTimelineStatus('配音位置未变化');", source)
        self.assertIn("if (!changed) return setHtmlMotionTimelineStatus('动效位置未变化');", source)
        self.assertNotIn("function updateConfirmedBurnVideo", source)
        self.assertNotIn("function closeBurnTimelinePanels", source)
        self.assertNotIn("clearTimelineHistory();\n      closeBurnTimelinePanels();", source)
        self.assertIn("await refreshUserGeneratedResults();", source)

    def test_video_preview_expands_when_a_timeline_is_open(self) -> None:
        source = read_static_source()

        self.assertIn("#videoPreviewModal .video-preview-panel:has(", source)
        self.assertIn("[data-video-preview-tts-timeline].is-open", source)
        self.assertIn("width: calc(100vw - 32px);", source)

    def test_open_timeline_borders_have_inner_spacing(self) -> None:
        source = read_static_source()

        self.assertGreaterEqual(source.count("padding: 6px 8px;"), 2)

    def test_html_motion_chunk_hides_metadata_when_narrow(self) -> None:
        source = read_static_source()

        self.assertIn(".video-preview-html-motion-chunk small", source)
        self.assertIn(".video-preview-html-motion-chunk span", source)
        self.assertIn("text-overflow: clip;", source)
        self.assertNotIn("@container (max-width: 36px)", source)
        self.assertNotIn("@container (max-width: 42px)", source)
        self.assertIn("position: static;\n      flex: 0 0 auto;", source)
        self.assertIn('data-compact-label="${escapeHtml(compactLabel)}"', source)
        self.assertIn("@container (max-width: 52px)", source)
        self.assertIn("content: attr(data-compact-label);", source)
        self.assertIn("@container (max-width: 10px)", source)
        self.assertNotIn("@container (max-width: 22px)", source)

    def test_tts_chunk_hides_trim_handles_when_too_narrow(self) -> None:
        source = read_static_source()

        self.assertIn("@container (max-width: 28px)", source)
        self.assertIn(
            ".video-preview-tts-chunk > .video-preview-timeline-trim-handle",
            source,
        )

    def test_static_timeline_ruler_and_snapping_cover_all_tracks(self) -> None:
        source = read_static_source()
        script_names = [path.name for path in workbench_script_paths(STATIC_ROOT)]

        self.assertIn("const TIMELINE_SNAP_TOLERANCE_PX = 8;", source)
        self.assertIn("function timelineBuildSnapPoints(duration, options = {})", source)
        self.assertIn("function timelineResolveSnap(seconds, lane, duration, event, options = {})", source)
        self.assertIn("function timelineResolveTrimSnap(sourceSeconds, lane, duration, event, options = {})", source)
        self.assertIn("function timelineResolveChunkMoveSnap(startSeconds, chunkDuration, lane, duration, event, options = {})", source)
        self.assertIn("function timelineChunkBoundaryTime(chunk, edge, sourceSeconds)", source)
        self.assertIn("function timelineChunkSourceAtBoundary(chunk, edge, seconds)", source)
        self.assertIn("event?.shiftKey", source)
        self.assertGreaterEqual(source.count("timelineRulerMarkup("), 4)
        self.assertGreaterEqual(source.count("timelineSnapGuideMarkup()"), 4)
        self.assertIn("data-video-preview-video-playhead", source)
        self.assertIn("data-video-preview-tts-playhead", source)
        self.assertIn("data-video-preview-html-motion-playhead", source)
        self.assertIn("syncHtmlMotionTimelinePlayhead();", source)
        self.assertIn("lostpointercapture", source)
        self.assertIn("timelineInteractionCount", source)
        self.assertIn(".video-preview-timeline-ruler", source)
        self.assertIn(".video-preview-timeline-snap-guide", source)
        self.assertLess(
            script_names.index("11abb-timeline-ruler-snap.js"),
            script_names.index("11ac-timeline-history.js"),
        )
        self.assertLess(
            script_names.index("11abc-timeline-drag-snap.js"),
            script_names.index("11ac-timeline-history.js"),
        )

    def test_result_cards_expose_video_prompt_modal_and_copy_action(self) -> None:
        source = read_static_source()
        result_styles = (STATIC_ROOT / "styles" / "03-results.css").read_text(encoding="utf-8")
        prompt_button_rule = result_styles.split(".result-video-prompt-button {", 1)[1].split("}", 1)[0]

        self.assertIn('class="result-video-prompt-button"', source)
        self.assertIn("height: 20px;", prompt_button_rule)
        self.assertIn("display: inline-flex;", prompt_button_rule)
        self.assertIn("align-items: center;", prompt_button_rule)
        self.assertIn("justify-content: center;", prompt_button_rule)
        self.assertIn("padding: 0;", prompt_button_rule)
        self.assertIn('id="resultVideoPromptModal"', source)
        self.assertIn("async function openResultVideoPromptModal(button)", source)
        self.assertIn("/api/generation/video-prompt?sessionId=", source)
        self.assertIn('data-result-video-index="${videoIndex}"', source)
        self.assertNotIn("function storedSessionVideoPrompt(videoIndex)", source)
        self.assertIn("navigator.clipboard.writeText(text)", source)
        self.assertIn('"videoPrompt": video.prompt', Path(generation_progress.__file__).read_text(encoding="utf-8"))

        video = VideoPrompt(index=1, title="测试视频", source_summary="摘要", prompt="最终视频提示词")
        generation_progress.start_generation_progress("prompt-visible-session", [video])
        try:
            item = generation_progress.get_generation_progress("prompt-visible-session")["items"][0]
            self.assertEqual(item["videoPrompt"], "最终视频提示词")
        finally:
            generation_progress.clear_generation_progress("prompt-visible-session")

    def test_old_progress_cards_recover_final_prompt_from_trace(self) -> None:
        body = {"generationProgress": {"items": [{"videoIndex": 1, "status": "failed"}]}}
        records = [{
            "event": "final_video_prompt",
            "payload": {"videoIndex": 1, "prompt": "已经提交给远端模型的最终提示词"},
        }]

        with patch.object(ai8video_web, "_iter_prompt_trace_records", return_value=iter(records)):
            ai8video_web._apply_trace_video_prompts(body, "old-session")

        self.assertEqual(
            body["generationProgress"]["items"][0]["videoPrompt"],
            "已经提交给远端模型的最终提示词",
        )

    def test_retry_progress_recovers_original_final_prompt_before_pending_since(self) -> None:
        body = {"generationProgress": {"items": [{"videoIndex": 1, "status": "failed"}]}}
        records = [{
            "event": "final_video_prompt",
            "payload": {"videoIndex": 1, "prompt": "原始批次实际提交的视频提示词"},
        }]

        with patch.object(
            ai8video_web,
            "_iter_prompt_trace_records",
            side_effect=[iter([]), iter(records)],
        ):
            ai8video_web._apply_trace_video_prompts(
                body,
                "retry-session",
                pending_since=datetime.now(timezone.utc),
            )

        self.assertEqual(
            body["generationProgress"]["items"][0]["videoPrompt"],
            "原始批次实际提交的视频提示词",
        )

    def test_video_prompt_lookup_scans_full_trace_for_old_video(self) -> None:
        trace_path = self.root / "old-prompt-trace.jsonl"
        trace_path.write_text(
            json.dumps({
                "event": "final_video_prompt",
                "sessionId": "old-session",
                "payload": {"videoIndex": 1, "prompt": "旧任务最终提示词"},
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        with patch.object(ai8video_web, "PROMPT_TRACE_PATH", trace_path):
            prompt = ai8video_web._final_video_prompt_from_full_trace("old-session", 1)

        self.assertEqual(prompt, "旧任务最终提示词")

    def test_timeline_boundary_blocks_chunks_past_cropped_video_end(self) -> None:
        status = timeline_boundary.timeline_boundary_status(
            8,
            tts_chunks=[
                {"index": 0, "startSeconds": 0, "durationSeconds": 4},
                {"index": 1, "startSeconds": 6, "durationSeconds": 3},
            ],
            html_motion_chunks=[
                {"index": 2, "startSeconds": 5, "endSeconds": 7.5},
            ],
        )

        self.assertFalse(status["valid"])
        self.assertEqual(status["ttsOverflowIndexes"], [1])
        self.assertEqual(status["htmlMotionOverflowIndexes"], [])
        self.assertIn("1 个配音片段", status["reason"])
        with self.assertRaisesRegex(ValueError, "超出裁剪后视频"):
            timeline_boundary.ensure_timeline_chunks_within_video(
                8,
                tts_chunks=[{"startSeconds": 7, "durationSeconds": 2}],
            )

    def test_html_motion_timeline_supports_split_delete_and_scene_reindex(self) -> None:
        def artifact() -> dict:
            return {"scenes": [
                {
                    "start": 0,
                    "end": 4,
                    "zone": "top-left",
                    "html": '<div id="scene-1-title">第一段</div>',
                    "css": "#hf-scene-1 #scene-1-title{}",
                    "animations": [{"target": "#scene-1-title"}],
                    "ids": ["scene-1-title"],
                },
                {
                    "start": 4,
                    "end": 8,
                    "zone": "top-right",
                    "html": '<div id="scene-2-title">第二段</div>',
                    "css": "#hf-scene-2 #scene-2-title{}",
                    "animations": [{"target": "#scene-2-title"}],
                    "ids": ["scene-2-title"],
                },
            ]}

        split_artifact = artifact()
        split = html_motion_review._apply_timeline_chunks(
            split_artifact,
            [
                {"sourceIndex": 0, "startSeconds": 0, "durationSeconds": 2},
                {"sourceIndex": 0, "startSeconds": 2, "durationSeconds": 2},
                {"sourceIndex": 1, "startSeconds": 6, "durationSeconds": 2},
            ],
            10,
        )

        self.assertEqual([item["sourceIndex"] for item in split], [0, 0, 1])
        self.assertEqual([item["index"] for item in split], [0, 1, 2])
        self.assertIn('id="scene-2-title"', split_artifact["scenes"][1]["html"])
        self.assertIn("#hf-scene-3", split_artifact["scenes"][2]["css"])
        deleted_artifact = artifact()
        deleted = html_motion_review._apply_timeline_chunks(
            deleted_artifact,
            [{"sourceIndex": 1, "startSeconds": 6, "durationSeconds": 2}],
            10,
        )
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0]["sourceIndex"], 1)
        self.assertIn('id="scene-1-title"', deleted_artifact["scenes"][0]["html"])
        markup = hyperframes_overlay_renderer._scene_markup(deleted_artifact["scenes"][0], 0)
        self.assertIn('data-timeline-source-index="1"', markup)
        runtime = (Path(html_motion_review.__file__).parent / "waapi_timeline_runtime.js").read_text(encoding="utf-8")
        self.assertIn("updateChunks(Array.from(document.querySelectorAll('.hf-scene'))", runtime)
        self.assertIn("const delaySeconds = number(item.at, 0) + staggerSeconds * index;", runtime)
        self.assertIn("if (global.__ai8MotionManageSceneVisibility)", runtime)
        self.assertIn("global.__ai8MotionManageSceneVisibility = true;", runtime)

    def test_html_motion_trim_preserves_source_phase_and_restore_bound(self) -> None:
        artifact = {"scenes": [{
            "start": 0,
            "end": 4,
            "zone": "top-left",
            "html": '<div id="scene-1-title">第一段</div>',
            "css": "#hf-scene-1 #scene-1-title{}",
            "animations": [{
                "target": "#scene-1-title",
                "kind": "entrance",
                "at": 1,
                "duration": 1,
                "from": {},
                "to": {},
            }],
            "ids": ["scene-1-title"],
        }]}

        chunks = html_motion_review._apply_timeline_chunks(
            artifact,
            [{
                "sourceIndex": 0,
                "sourceStartSeconds": 1,
                "sourceEndSeconds": 3,
                "originalSourceStartSeconds": 0,
                "originalSourceEndSeconds": 4,
                "startSeconds": 0,
            }],
            4,
        )
        plan = hyperframes_overlay_renderer._motion_plan(artifact, 4)

        self.assertEqual(chunks[0]["durationSeconds"], 2.0)
        self.assertEqual(chunks[0]["originalSourceEndSeconds"], 4.0)
        self.assertEqual(plan["animations"][0]["at"], 0.0)
        self.assertEqual(plan["animations"][0]["localAt"], 1)

    def test_html_motion_timeline_accepts_exact_tenth_second_chunk(self) -> None:
        artifact = {"scenes": [{
            "start": 0,
            "end": 1,
            "html": "<div>短动效</div>",
            "css": "",
            "animations": [],
            "ids": [],
        }]}

        chunks = html_motion_review._apply_timeline_chunks(
            artifact,
            [{
                "sourceIndex": 0,
                "sourceStartSeconds": 0.7,
                "sourceEndSeconds": 0.8,
                "startSeconds": 0,
            }],
            1,
        )

        self.assertEqual(chunks[0]["durationSeconds"], 0.1)

    def test_pending_burn_context_rejects_tts_past_cropped_video_end(self) -> None:
        video_state = {"outputDurationSeconds": 8.0}
        tts_state = {
            "available": True,
            "timelineChunks": [{"startSeconds": 0, "durationSeconds": 10}],
        }
        with patch.object(
            ai8video_web, "html_motion_review_status", return_value={"reviewReady": False},
        ), patch.object(
            ai8video_web, "pending_video_timeline_review", return_value=video_state,
        ), patch.object(
            ai8video_web, "pending_tts_timeline_review", return_value=tts_state,
        ):
            with self.assertRaisesRegex(ValueError, "配音片段"):
                ai8video_web._pending_burn_context(Path("demo.mp4"), "video/demo.mp4", None)

    def test_pending_burn_context_uses_current_tts_when_only_video_crop_is_pending(self) -> None:
        video_state = {"outputDurationSeconds": 8.0}
        current_tts = {
            "available": True,
            "audioPath": "/tmp/voice.m4a",
            "durationSeconds": 10.0,
            "timelineChunks": [{"startSeconds": 0, "durationSeconds": 8}],
        }
        with patch.object(
            ai8video_web, "html_motion_review_status", return_value={"reviewReady": False},
        ), patch.object(
            ai8video_web, "pending_video_timeline_review", return_value=video_state,
        ), patch.object(
            ai8video_web, "pending_tts_timeline_review", return_value={},
        ), patch.object(
            ai8video_web, "_current_tts_timeline_status", return_value=current_tts,
        ):
            tts_state, saved_video_state, html_pending, tts_review_pending = (
                ai8video_web._pending_burn_context(Path("demo.mp4"), "video/demo.mp4", None)
            )

        self.assertIs(tts_state, current_tts)
        self.assertIs(saved_video_state, video_state)
        self.assertFalse(html_pending)
        self.assertFalse(tts_review_pending)

    def test_video_crop_preview_replaces_embedded_audio_with_independent_tts(self) -> None:
        video_path = self.root / "demo.mp4"
        composite_source = self.root / "visual.mp4"
        audio_path = self.root / "voice.m4a"
        for path in (video_path, composite_source, audio_path):
            path.write_bytes(b"data")
        tts_status = {
            "available": True,
            "audioPath": str(audio_path),
            "ttsVolume": 1.1,
            "timelineChunks": [{"sourceStartSeconds": 0, "sourceEndSeconds": 4, "startSeconds": 0}],
        }
        with patch.object(
            ai8video_web, "pending_video_timeline_review", return_value={"outputDurationSeconds": 4.0},
        ), patch.object(
            ai8video_web, "video_timeline_candidate_needs_render", return_value=True,
        ), patch.object(
            ai8video_web, "render_video_timeline_candidate",
        ) as render_video, patch.object(
            ai8video_web,
            "render_video_timeline_tts_preview",
            return_value={"pending": True, "previewUrl": "/crop-with-tts.mp4"},
        ) as render_tts:
            result = ai8video_web._render_pending_video_burn_preview(
                video_path,
                "video/demo.mp4",
                composite_source,
                tts_status,
            )

        self.assertEqual(result["previewUrl"], "/crop-with-tts.mp4")
        render_video.assert_called_once_with(
            composite_source,
            "video/demo.mp4",
            preserve_source_audio=False,
        )
        render_tts.assert_called_once_with(
            "video/demo.mp4",
            audio_path,
            tts_status["timelineChunks"],
            tts_volume=1.1,
        )

    def test_burn_review_keeps_tts_and_html_preview_layers_independent(self) -> None:
        video_path = self.root / "demo.mp4"
        video_path.write_bytes(b"video")
        html_candidate = self.root / "html-candidate.mp4"
        video_candidate = self.root / "video-candidate.mp4"
        with patch.object(
            ai8video_web,
            "_pending_burn_visual_source",
            return_value=(html_candidate, {"reviewReady": True, "livePreviewUrl": "/live/composition.html"}),
        ), patch.object(
            ai8video_web,
            "_current_tts_timeline_status",
            return_value={"available": True, "timelineChunks": []},
        ), patch.object(
            ai8video_web,
            "_render_pending_tts_burn_preview",
            return_value={"pending": True, "previewUrl": "/tts.mp4", "reviewId": "tts-review"},
        ) as render_tts, patch.object(
            ai8video_web, "resolve_video_timeline_review_video", return_value=video_candidate,
        ), patch.object(
            ai8video_web,
            "_render_pending_video_burn_preview",
            return_value={
                "pending": True,
                "previewUrl": "/video.mp4",
                "reviewId": "video-review",
                "outputDurationSeconds": 8.0,
            },
        ), patch.object(
            ai8video_web, "timeline_boundary_status", return_value={"valid": True},
        ):
            review = ai8video_web._burn_review_payload(video_path, "video/demo.mp4")

        self.assertEqual(render_tts.call_args.kwargs["visual_source"], video_candidate)
        self.assertEqual(render_tts.call_args.kwargs["duration_seconds"], 8.0)
        self.assertEqual(review["previewUrl"], "/tts.mp4")
        self.assertEqual(review["livePreviewUrl"], "/live/composition.html")

    def test_tts_waveform_extracts_and_reuses_cached_peaks(self) -> None:
        audio_path = self.root / "voice.m4a"
        cache_path = self.root / "review" / "waveform.json"
        audio_path.write_bytes(b"audio")
        pcm = struct.pack(
            "<16h",
            0, 1000, -2000, 500,
            0, 4000, -8000, 1000,
            200, 100, -50, 0,
            32767, -32768, 100, 0,
        ) * 4
        completed = SimpleNamespace(stdout=pcm, stderr=b"")

        with patch.object(tts_waveform.subprocess, "run", return_value=completed) as run_ffmpeg:
            first = tts_waveform.cached_audio_waveform(audio_path, cache_path, point_count=4)
            second = tts_waveform.cached_audio_waveform(audio_path, cache_path, point_count=4)

        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(first["peaks"]), 32)
        self.assertEqual(max(first["peaks"]), 1.0)
        run_ffmpeg.assert_called_once()

    def test_confirm_burn_combines_pending_tts_and_html_into_extra_video(self) -> None:
        result_root = self.root / "用户生成结果"
        video_path = result_root / "video" / "demo.mp4"
        html_base = self.root / "html-base.mp4"
        audio_path = self.root / "tts.m4a"
        video_path.parent.mkdir(parents=True)
        video_path.write_bytes(b"official")
        html_base.write_bytes(b"clean")
        audio_path.write_bytes(b"audio")
        state = {
            "audioPath": str(audio_path),
            "durationSeconds": 10.0,
            "ttsVolume": 1.2,
            "timelineChunks": [{
                "sourceStartSeconds": 0,
                "sourceEndSeconds": 8,
                "startSeconds": 1,
                "durationSeconds": 8,
            }],
        }
        rendered_from: list[bytes] = []

        def fake_render(visual_source, _audio, target, _chunks, **_kwargs):
            rendered_from.append(Path(visual_source).read_bytes())
            Path(target).write_bytes(b"combined")
            return {"status": "rendered"}

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "html_motion_review_status",
            return_value={"reviewReady": True},
        ), patch.object(
            ai8video_web,
            "adjust_html_motion_review_timeline",
            return_value={"reviewReady": True, "renderReused": True},
        ) as adjust_html, patch.object(
            ai8video_web,
            "html_motion_review_base_path",
            return_value=html_base,
        ), patch.object(
            ai8video_web,
            "pending_tts_timeline_review",
            return_value=state,
        ), patch.object(
            ai8video_web,
            "pending_video_timeline_review",
            return_value={},
        ), patch.object(
            ai8video_web,
            "render_tts_timeline_video",
            side_effect=fake_render,
        ), patch.object(
            ai8video_web,
            "mix_background_music",
            return_value={"enabled": False, "status": "skipped"},
        ), patch.object(
            ai8video_web,
            "probe_media_video_info",
            return_value={"width": 720, "height": 1280, "durationSeconds": 10.0},
        ), patch.object(
            ai8video_web,
            "resolve_ffmpeg_bin",
            return_value="ffmpeg",
        ), patch.object(
            ai8video_web,
            "composite_transparent_layer",
        ), patch.object(
            ai8video_web,
            "finalize_html_motion_review",
            return_value={"status": "applied"},
        ), patch.object(
            ai8video_web,
            "mark_tts_timeline_review_confirmed",
            return_value={"status": "applied"},
        ), patch.object(
            ai8video_web,
            "save_restored_result_html_motion_overlay",
            return_value={"archiveManifestPath": ""},
        ), patch.object(
            ai8video_web,
            "_update_html_motion_manifest",
            return_value={"status": "skipped"},
        ), patch.object(
            ai8video_web,
            "_confirmed_tts_result",
            return_value=({"status": "mixed"}, {"status": "skipped"}),
        ), patch.object(
            ai8video_web,
            "generate_preview_for_video",
            return_value={"ok": True},
        ), patch.object(
            ai8video_web,
            "sync_html_motion_review_audio",
            return_value={"status": "synced"},
        ), patch.object(
            ai8video_web,
            "_burn_review_payload",
            return_value={"reviewReady": False},
        ):
            body = ai8video_web._confirm_user_generated_burn("video/demo.mp4")

        self.assertTrue(body["ok"])
        self.assertEqual(adjust_html.call_count, 1)
        self.assertEqual(adjust_html.call_args.args, (video_path.resolve(), "video/demo.mp4", None))
        self.assertEqual(rendered_from, [b"clean"])
        self.assertEqual(video_path.read_bytes(), b"official")
        self.assertEqual((result_root / "burned" / "video" / "demo.mp4").read_bytes(), b"combined")
        self.assertEqual(body["burnedUserGeneratedKey"], "burned/video/demo.mp4")

    def test_confirm_burn_applies_video_crop_before_tts_composite(self) -> None:
        result_root = self.root / "用户生成结果"
        video_path = result_root / "video" / "crop.mp4"
        audio_path = self.root / "tts.m4a"
        video_path.parent.mkdir(parents=True)
        video_path.write_bytes(b"official")
        audio_path.write_bytes(b"audio")
        tts_state = {
            "audioPath": str(audio_path),
            "durationSeconds": 10.0,
            "ttsVolume": 1.0,
            "timelineChunks": [{
                "sourceStartSeconds": 0,
                "sourceEndSeconds": 8,
                "startSeconds": 0,
                "durationSeconds": 8,
            }],
        }
        video_state = {
            "sourceDurationSeconds": 10.0,
            "outputDurationSeconds": 8.0,
            "timelineChunks": [
                {"sourceStartSeconds": 0, "sourceEndSeconds": 3, "startSeconds": 0},
                {"sourceStartSeconds": 5, "sourceEndSeconds": 10, "startSeconds": 3},
            ],
        }
        render_order: list[str] = []

        def render_tts(_source, _audio, target, _chunks, **_kwargs):
            render_order.append("tts")
            self.assertEqual(Path(_source).read_bytes(), b"cropped")
            Path(target).write_bytes(b"composite")
            return {"status": "rendered"}

        def render_crop(source, target, _chunks, **_kwargs):
            render_order.append("crop")
            self.assertEqual(Path(source).read_bytes(), b"official")
            Path(target).write_bytes(b"cropped")
            return {"status": "rendered"}

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web, "html_motion_review_status", return_value={"reviewReady": False},
        ), patch.object(
            ai8video_web, "pending_tts_timeline_review", return_value=tts_state,
        ), patch.object(
            ai8video_web, "pending_video_timeline_review", return_value=video_state,
        ), patch.object(
            ai8video_web, "render_tts_timeline_video", side_effect=render_tts,
        ), patch.object(
            ai8video_web, "render_video_timeline_video", side_effect=render_crop,
        ), patch.object(
            ai8video_web, "mix_background_music", return_value={"enabled": False, "status": "skipped"},
        ), patch.object(
            ai8video_web, "mark_tts_timeline_review_confirmed", return_value={"status": "applied"},
        ), patch.object(
            ai8video_web, "mark_video_timeline_review_confirmed", return_value={"status": "applied"},
        ) as mark_crop, patch.object(
            ai8video_web, "_confirmed_tts_result", return_value=({"status": "mixed"}, {"status": "skipped"}),
        ) as save_tts, patch.object(
            ai8video_web, "generate_preview_for_video", return_value={"ok": True},
        ), patch.object(
            ai8video_web, "sync_html_motion_review_audio", return_value={"status": "synced"},
        ), patch.object(
            ai8video_web, "_burn_review_payload", return_value={"reviewReady": False},
        ):
            body = ai8video_web._confirm_user_generated_burn("video/crop.mp4")

        self.assertTrue(body["ok"])
        self.assertEqual(render_order, ["crop", "tts"])
        self.assertEqual(video_path.read_bytes(), b"official")
        self.assertEqual((result_root / "burned" / "video" / "crop.mp4").read_bytes(), b"composite")
        mark_crop.assert_not_called()
        save_tts.assert_not_called()

    def test_confirm_burn_with_tts_uses_current_replaced_video_instead_of_stale_bgm_base(self) -> None:
        result_root = self.root / "用户生成结果"
        video_path = result_root / "video" / "current.mp4"
        stale_base = result_root / ".media-tracks" / "bgm-base" / "video" / "current.mp4"
        video_path.parent.mkdir(parents=True)
        stale_base.parent.mkdir(parents=True)
        video_path.write_bytes(b"current-replaced-video")
        stale_base.write_bytes(b"stale-original-video")

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root):
            source = ai8video_web._confirmed_burn_source(
                video_path,
                "video/current.mp4",
                False,
                has_tts=True,
            )

        self.assertEqual(source, video_path)

    def test_replace_video_updates_hidden_bgm_base_to_selected_variant(self) -> None:
        result_root = self.root / "用户生成结果"
        left = result_root / "video" / "original.mp4"
        right = result_root / "extensions" / "video" / "selected.mp4"
        left.parent.mkdir(parents=True)
        right.parent.mkdir(parents=True)
        left.write_bytes(b"old-video")
        right.write_bytes(b"selected-variant")

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web, "generate_preview_for_video", return_value={"ok": True},
        ), patch.object(
            ai8video_web, "_delete_extension_state_assets", return_value={"ok": True, "deleted": []},
        ), patch.object(ai8video_web, "schedule_burned_result_copy") as schedule_copy:
            body = ai8video_web._replace_user_generated_video(
                "video/original.mp4",
                "extensions/video/selected.mp4",
            )

        base = ai8video_web.hidden_bgm_base_path(result_root, "video/original.mp4")
        schedule_copy.assert_called_once_with(
            left.resolve(),
            result_root=result_root,
            overwrite=True,
        )
        self.assertTrue(body["ok"])
        self.assertEqual(left.read_bytes(), b"selected-variant")
        self.assertEqual(base.read_bytes(), b"selected-variant")

    def test_replace_video_reapplies_current_tts_timeline_to_selected_visual(self) -> None:
        result_root = self.root / "用户生成结果"
        left = result_root / "video" / "original.mp4"
        right = result_root / "extensions" / "video" / "selected.mp4"
        audio = self.root / "tts.m4a"
        left.parent.mkdir(parents=True)
        right.parent.mkdir(parents=True)
        left.write_bytes(b"old-video")
        right.write_bytes(b"selected-visual")
        audio.write_bytes(b"tts")
        chunks = [{"startSeconds": 0.5, "durationSeconds": 1.0}]

        def render(visual, audio_path, target, timeline_chunks, **kwargs):
            self.assertEqual(visual.read_bytes(), b"selected-visual")
            self.assertEqual(audio_path, audio)
            self.assertEqual(timeline_chunks, chunks)
            target.write_bytes(b"selected-visual-with-current-tts")

        with patch.object(ai8video_web, "ensure_user_generated_result_dir", return_value=result_root), patch.object(
            ai8video_web,
            "_current_tts_timeline_status",
            return_value={
                "available": True,
                "audioPath": str(audio),
                "timelineChunks": chunks,
                "ttsVolume": 1.0,
            },
        ), patch.object(
            ai8video_web, "track_duration", return_value=7.7,
        ), patch.object(
            ai8video_web, "render_tts_timeline_video", side_effect=render,
        ), patch.object(
            ai8video_web, "generate_preview_for_video", return_value={"ok": True},
        ), patch.object(
            ai8video_web, "_delete_extension_state_assets", return_value={"ok": True, "deleted": []},
        ), patch.object(ai8video_web, "schedule_burned_result_copy") as schedule_copy:
            ai8video_web._replace_user_generated_video(
                "video/original.mp4",
                "extensions/video/selected.mp4",
            )

        base = ai8video_web.hidden_bgm_base_path(result_root, "video/original.mp4")
        schedule_copy.assert_called_once_with(
            left.resolve(),
            result_root=result_root,
            overwrite=True,
        )
        self.assertEqual(left.read_bytes(), b"selected-visual-with-current-tts")
        self.assertEqual(base.read_bytes(), b"selected-visual-with-current-tts")

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

    def test_confirm_html_motion_writes_extra_burned_video_once(self) -> None:
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

        def composite(base: Path, _layer: Path, _media: dict, _ffmpeg: str) -> None:
            self.assertEqual(base.read_bytes(), b"official")
            base.write_bytes(b"prepared-preview")

        with patch("ai8video.media.motion.html_motion_review.HTML_MOTION_REVIEW_ROOT", review_root), patch.object(
            ai8video_web,
            "ensure_user_generated_result_dir",
            return_value=result_root,
        ), patch.object(
            ai8video_web,
            "html_motion_review_status",
            return_value={"reviewReady": True},
        ), patch.object(
            ai8video_web,
            "adjust_html_motion_review_timeline",
            return_value={"reviewReady": True, "renderReused": True},
        ), patch.object(
            ai8video_web,
            "pending_tts_timeline_review",
            return_value={},
        ), patch.object(
            ai8video_web,
            "pending_video_timeline_review",
            return_value={},
        ), patch.object(
            ai8video_web,
            "probe_media_video_info",
            return_value={"durationSeconds": 1.0},
        ), patch.object(
            ai8video_web,
            "resolve_ffmpeg_bin",
            return_value="ffmpeg",
        ), patch.object(
            ai8video_web,
            "composite_transparent_layer",
            side_effect=composite,
        ) as composite_layer, patch.object(
            ai8video_web,
            "mix_background_music",
            return_value={"enabled": False, "status": "unchanged"},
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
        self.assertEqual(video_path.read_bytes(), b"official")
        self.assertEqual(body["burnedUserGeneratedKey"], "burned/video/confirm.mp4")
        self.assertEqual(
            (result_root / "burned" / "video" / "confirm.mp4").read_bytes(),
            b"prepared-preview",
        )
        stored = JsonlAssetStore(self.root / "assets.jsonl").read_all()[0]
        self.assertNotIn("htmlMotionOverlay", stored)
        composite_layer.assert_called_once()
        generate_preview.assert_not_called()

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
        self.assertIn("data-cleanup-archive-all", html)
        self.assertIn("总占用 ${escapeHtml(archiveTotal)}", html)
        self.assertIn("/api/archive-artifacts/open", html)
        self.assertIn("/api/archive-artifacts/cleanup", html)
        self.assertIn("AI8VIDEO_ARCHIVE_TTS_OUTPUT_DIR: '清理配音输出'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_MERGE_TEMP_DIR: '清理临时媒体'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_REFERENCE_TEMP_DIR: '清理临时图片'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_MANIFEST_DIR: '清理孤儿元数据'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_ASSET_INDEX: '压缩孤儿记录'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_RECYCLE_BIN_DIR: '清空回收站'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_EXTENSION_DIR: '清理延长视频'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_EXTENSION_FRAME_DIR: '清理延长截帧'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_HTML_MOTION_WORK_DIR: '清理失败工作目录'", html)
        self.assertIn("AI8VIDEO_ARCHIVE_HTML_MOTION_REVIEW_DIR: '清理审核缓存'", html)

    def test_archive_one_click_cleanup_runs_every_registered_cleanup(self) -> None:
        effects = [
            {"ok": True, "kind": kind, "deletedCount": 1, "removedBytes": 10}
            for kind in ai8video_web.ARCHIVE_ONE_CLICK_CLEANUP_KINDS
        ]

        with patch.object(ai8video_web, "_cleanup_archive_artifacts", side_effect=effects) as cleanup:
            result = ai8video_web._cleanup_all_archive_artifacts()

        self.assertEqual(
            [call.args[0] for call in cleanup.call_args_list],
            list(ai8video_web.ARCHIVE_ONE_CLICK_CLEANUP_KINDS),
        )
        self.assertEqual(result["deletedCount"], len(effects))
        self.assertEqual(result["removedBytes"], len(effects) * 10)

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
        self.assertEqual(body["agentArchitecture"]["controller"], "AI8VideoMainAgent")
        self.assertEqual(body["agentArchitecture"]["decisionPolicy"], "key_nodes")
        self.assertEqual(body["agentArchitecture"]["runtimeOwner"], "python")
        self.assertEqual(
            body["agentArchitecture"]["compositeTools"],
            [
                "prepare_video_plan",
                "review_video_plan",
                "generate_video_batch",
                "inspect_generation_result",
                "archive_and_deliver",
                "task_user",
            ],
        )
        self.assertTrue(body["agentArchitecture"]["standardMode"]["isolated"])
        self.assertEqual(
            body["agentArchitecture"]["modelBinding"]["strategy"],
            "first_message_snapshot",
        )
        self.assertEqual(body["agentSkills"]["totalAgents"], 15)
        self.assertEqual(body["agentSkills"]["totalSkills"], 15)
        self.assertEqual(body["agentSkills"]["enabledSkills"], 6)
        planner = next(
            agent
            for agent in body["agentSkills"]["agents"]
            if agent["agentId"] == "planner"
        )
        self.assertEqual(planner["skills"][0]["name"], "plan-video-content")
        self.assertTrue(planner["skills"][0]["enabled"])
        self.assertTrue(planner["skills"][0]["builtIn"])
        self.assertFalse(planner["skills"][0]["removable"])
        self.assertNotIn("path", planner["skills"][0])
        supervisor = next(
            agent
            for agent in body["agentSkills"]["agents"]
            if agent["agentId"] == "supervisor"
        )
        self.assertEqual(supervisor["skills"][0]["status"], "placeholder")
        self.assertIn("AI8VIDEO_ARCHIVE_RESULT_VIDEO_DIR", env_names)
        result_field = next(field for field in body["fields"] if field["envName"] == "AI8VIDEO_ARCHIVE_RESULT_VIDEO_DIR")
        self.assertEqual(result_field["source"], "用户文件夹/用户生成结果/video")
        self.assertIn("AI8VIDEO_ARCHIVE_TTS_OUTPUT_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_MERGE_TEMP_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_REFERENCE_TEMP_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_MANIFEST_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_ASSET_INDEX", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_RECYCLE_BIN_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_EXTENSION_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_EXTENSION_FRAME_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_HTML_MOTION_WORK_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_HTML_MOTION_REVIEW_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_RESTORED_METADATA_DIR", env_names)
        self.assertIn("AI8VIDEO_ARCHIVE_RESULT_JUNK", env_names)

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
        self.assertNotIn("settings-row-env", html)
        self.assertNotIn("field.source || 'missing'", html)
        self.assertNotIn("真实生成已就绪", html)
        self.assertNotIn("checkboxMarkup('watermark', '加水印', settings.watermark)", html)

    def test_static_agent_architecture_settings_matches_main_agent_runtime(self) -> None:
        html = read_static_source()

        self.assertIn("label: 'Main Agent'", html)
        self.assertIn("label: '复合工具'", html)
        self.assertIn("label: 'Runtime'", html)
        self.assertIn("label: '标准模式'", html)
        self.assertIn("label: '专项能力'", html)
        self.assertIn("label: '模型绑定'", html)
        self.assertIn("return label === 'AI8video' ? 'Agent 架构' : label;", html)
        self.assertIn("单一 Main Agent，关键节点决策", html)
        self.assertIn("两种模式只共享媒体资源和配置来源", html)
        self.assertIn("function buildAgentCompositeToolsPanel()", html)
        self.assertIn("prepare_video_plan", html)
        self.assertIn("archive_and_deliver", html)
        self.assertIn("task_user", html)
        self.assertNotIn("媒体审核仍为影子模式", html)
        self.assertNotIn("const multiAgentRoleDefinitions", html)

    def test_static_agent_architecture_separates_special_capabilities_and_model_binding(self) -> None:
        html = read_static_source()

        self.assertIn("function enabledAgentSkills(agentId)", html)
        self.assertIn("function buildAgentSpecialCapabilitiesPanel()", html)
        self.assertIn("agentId: 'knowledge-base'", html)
        self.assertIn("title: '知识建树'", html)
        self.assertIn("title: '镜头语言分析'", html)
        self.assertIn("title: '剧本重建'", html)
        self.assertIn("不属于主调度链", html)
        self.assertIn("对话在第一条消息提交时固化当前模型配置快照", html)
        self.assertIn("兼容模型回退", html)
        self.assertIn(".agent-capability-grid", html)
        self.assertIn(".agent-model-profile-grid", html)
        self.assertNotIn("label: '知识库 Agent'", html)
        self.assertNotIn("label: '镜头语言 Agent'", html)
        self.assertNotIn("label: '猜剧本 Agent'", html)

    def test_static_deleted_progress_card_uses_soft_deleted_style(self) -> None:
        html = read_static_source()

        self.assertIn(".result-notify-play.terminal-placeholder[aria-hidden=\"true\"] span::before", html)
        self.assertIn(".result-notify-play.processing-placeholder[aria-hidden=\"true\"] span", html)
        self.assertIn("animation: none;", html)
        self.assertIn("data-tail-frame-chaining-mode", html)
        self.assertIn("awaiting_tail_frame_continue", html)
        self.assertIn("data-tail-frame-continue", html)
        self.assertIn("data-tail-frame-refresh", html)
        self.assertIn("/api/tail-frame-chain/", html)
        self.assertIn("function isSessionRecoverableTailFrameFailure(session)", html)
        self.assertIn("tailFrameRecoveryPollAttempted", html)
        self.assertEqual(html.count("${renderGenerationRetryButton(item)}"), 1)
        self.assertIn("button.textContent = originalText;", html)
        self.assertIn("preview.src = data.tailFramePreviewUrl;", html)
        self.assertIn("manualTailFrameWait ? '等待继续' : '提交生成'", html)
        self.assertIn("时间轴仍在被其他操作更新，请稍后再试", html)
        self.assertIn("function fetchChatStatusWithBatchFallback(sessionId, session, options = {})", html)
        self.assertIn("function restoreSucceededProgressFromUserResults()", html)
        self.assertIn("data?.phase === 'unknown_generation_batch'", html)
        self.assertIn("omitGenerationBatchId: true", html)
        self.assertIn("尾帧已准备完成，点击继续后才会提交下一条视频。", html)
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
        self.assertIn('class="result-notify-card deleted ${resultNotifyRatioClass(item)}"', html)
        self.assertIn('class="result-notify-deleted-mark" aria-hidden="true">已删除</div>', html)
        self.assertIn(".result-notify-card.deleted .result-notify-preview", html)
        self.assertIn(".result-notify-deleted-mark", html)
        self.assertIn("result-notify-play${isTerminal ? ` terminal-placeholder${historicalClass}` : `${processingClass}${waitingClass}`}", html)
        self.assertIn("已生成，文件已删除", html)
        self.assertNotIn("已生成，文件已删除或未落盘", html)
        self.assertNotIn("deleted-placeholder", html)
        self.assertNotIn('<div class="result-notify-failed-mark" aria-hidden="true">×</div>', html)

    def test_manual_tail_frame_wait_keeps_chat_status_pending(self) -> None:
        body = {
            "status": "pending",
            "generationProgress": {
                "items": [
                    {"videoIndex": 1, "status": "succeeded"},
                    {"videoIndex": 2, "status": "awaiting_tail_frame_continue"},
                ]
            },
        }

        ai8video_web._refresh_generation_progress_summary(body)

        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["phase"], "awaiting_tail_frame_continue")
        self.assertEqual(body["generationProgress"]["waitingCount"], 1)

    def test_static_brand_uses_project_png_assets(self) -> None:
        from PIL import Image

        html = read_static_source()
        avatar_path = STATIC_ROOT / "images" / "ai8video-avatar.png"
        sidebar_brand_path = STATIC_ROOT / "images" / "ai8video-sidebar-brand.png"

        self.assertIn("/static/images/ai8video-avatar.png?v=20260727-1", html)
        self.assertIn('background: transparent url("/static/images/ai8video-sidebar-brand.png?v=20260728-1")', html)
        self.assertIn("const avatar = message.role === 'user' ? '我' : 'AI8video';", html)
        self.assertTrue(avatar_path.is_file())
        self.assertTrue(sidebar_brand_path.is_file())
        with Image.open(avatar_path) as avatar:
            self.assertEqual(avatar.format, "PNG")
            self.assertEqual(avatar.size, (512, 512))
        with Image.open(sidebar_brand_path) as sidebar_brand:
            self.assertEqual(sidebar_brand.format, "PNG")
            self.assertEqual(sidebar_brand.size, (384, 193))

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

    def test_static_conversation_shell_has_no_clear_or_background_switcher(self) -> None:
        html = read_static_source()

        self.assertNotIn('id="clearConversationButton"', html)
        self.assertNotIn('id="mainBackgroundButton"', html)
        self.assertNotIn('id="clearConversationConfirmModal"', html)
        self.assertNotIn("const MAIN_BACKGROUND_MODES", html)
        self.assertNotIn("bindMainBackgroundSwitcher", html)
        self.assertNotIn("clearActiveConversationTextMessages", html)
        self.assertNotIn("openClearConversationConfirmModal", html)
        self.assertNotIn(".main-background-button", html)
        self.assertIn('22-main-background.css?v=20260804-1', html)
        self.assertIn("linear-gradient(rgba(79, 109, 255, 0.14) 1px, transparent 1px)", html)
        self.assertIn("background-size: 24px 24px;", html)
        self.assertNotIn('id="executionWorkflowButton"', html)
        self.assertNotIn('id="executionAgentButton"', html)
        self.assertNotIn('id="statusBar"', html)
        self.assertNotIn('class="conversation-controlbar-title"', html)
        self.assertNotIn("AI8video 对话", html)
        self.assertNotIn("文本鉴权已配置", html)
        self.assertNotIn("视频鉴权已配置", html)
        self.assertNotIn("switchConversationExecutionMode", html)
        self.assertIn('id="newConversationWorkflowModeButton"', html)
        self.assertIn('id="newConversationAgentModeButton"', html)
        self.assertIn('id="newConversationButton"', html)
        self.assertIn('>标准模式</button>', html)
        self.assertIn('>Agent 模式 <span class="new-conversation-mode-badge">Beta</span></button>', html)
        self.assertIn(
            'class="new-conversation-button-plus fa-icon" data-fa-icon="plus" aria-hidden="true"></span>',
            html,
        )
        self.assertIn('class="new-conversation-button-label" data-new-conversation-label>新建标准对话</span>', html)
        self.assertIn('class="new-conversation-control sidebar-new-conversation-control"', html)
        self.assertIn('class="conversation-controlbar mobile-conversation-controlbar"', html)
        self.assertIn('id="mobileNewConversationButton"', html)
        self.assertIn('data-create-conversation', html)
        self.assertNotIn('id="conversationCount"', html)
        self.assertLess(html.index('id="newConversationButton"'), html.index('id="sessionList"'))
        self.assertIn(".mobile-conversation-controlbar {\n  display: none;", html)
        self.assertIn(".mobile-conversation-controlbar {\n    display: grid;", html)
        self.assertIn(".shell.is-sidebar-collapsed .sidebar-new-conversation-control .new-conversation-mode-switch", html)
        self.assertIn("newConversationButtons: Array.from(document.querySelectorAll('[data-create-conversation]'))", html)
        self.assertIn("newConversationModeButtons: Array.from(document.querySelectorAll('[data-new-conversation-mode]'))", html)
        self.assertIn("const createLabel = newConversationMode === 'agent' ? '新建 Agent 对话' : '新建标准对话';", html)
        self.assertIn("buttonLabel.textContent = visibleLabel;", html)
        self.assertIn("button.setAttribute('aria-disabled', String(state.conversationSyncing));", html)
        self.assertIn("if (!locked) {\n        renderStatus();\n        return;\n      }", html)
        self.assertIn(".sidebar-new-conversation-control {\n  width: 100%;\n  min-width: 0;\n  flex-direction: column;", html)
        self.assertIn(".sidebar-new-conversation-control .new-conversation-button {\n  width: calc(100% - 8px);", html)
        self.assertIn("margin: 4px;\n  padding-inline: 12px;\n  box-sizing: border-box;", html)
        self.assertIn("border: 1px solid rgba(255, 255, 255, 0.24);\n  border-radius: 10px;", html)
        self.assertIn("min-height: 44px;\n  margin: 0;\n  padding: 0;", html)
        self.assertIn(".shell.is-sidebar-collapsed .sidebar-new-conversation-control .new-conversation-button-label", html)
        self.assertIn("min-height: 44px;", html)
        self.assertIn("body: JSON.stringify({ title, executionMode: mode })", html)
        self.assertIn("return createConversation(title, 'workflow');", html)
        self.assertIn('id="sessionList"', html)
        self.assertIn('id="sidebarResultsSection"', html)
        self.assertIn('<h3 id="sidebarResultsLabel" class="sidebar-section-label">结果</h3>', html)
        self.assertLess(html.index('id="sidebarResultsLabel"'), html.index('id="progressPanel"'))
        self.assertLess(html.index('id="progressPanel"'), html.index('id="sidebarResourcesLabel"'))
        self.assertIn("sidebarResultsSection: document.getElementById('sidebarResultsSection')", html)
        self.assertIn("els.sidebarResultsSection.hidden = true;", html)
        self.assertIn("els.sidebarResultsSection.hidden = false;", html)
        self.assertIn("async function deleteConversation(conversationId)", html)
        self.assertIn("至少保留一个可用对话；最后一个对话不能删除。", html)
        self.assertIn('<span class="session-delete-icon" aria-hidden="true"></span>', html)
        self.assertIn('.session-delete-icon {', html)
        self.assertIn('width: 24px;\n  height: 24px;', html)
        self.assertIn('opacity: 0;\n  visibility: hidden;', html)
        self.assertIn('.session-item:hover .session-delete-button,', html)
        self.assertIn('.session-item:focus-within .session-delete-button {\n  opacity: 1;\n  visibility: visible;', html)
        self.assertIn('fontawesome-free-7.3.1-desktop/svgs-full/solid/trash-can.svg', html)
        self.assertNotIn('.session-delete-button svg {', html)
        self.assertNotIn('M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5', html)

    def test_collapsed_sidebar_sessions_show_clickable_conversation_bubbles(self) -> None:
        renderer_source = (
            STATIC_ROOT / "scripts" / "21-humanize-recycle-bin-reason.js"
        ).read_text(encoding="utf-8")
        conversation_state_source = (
            STATIC_ROOT / "scripts" / "01b-conversation-state.js"
        ).read_text(encoding="utf-8")
        controls_style = (
            STATIC_ROOT / "styles" / "22a-conversation-controls.css"
        ).read_text(encoding="utf-8")
        responsive_style = (
            STATIC_ROOT / "styles" / "22b-conversation-controls-responsive.css"
        ).read_text(encoding="utf-8")
        collapsed_icon_style = (
            STATIC_ROOT / "styles" / "21d-sidebar-collapsed-icon-scale.css"
        ).read_text(encoding="utf-8")
        workbench_style = (STATIC_ROOT / "workbench.css").read_text(encoding="utf-8")
        index_source = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn('data-select-conversation="${escapeHtml(session.id)}"', renderer_source)
        self.assertIn('class="session-conversation-icon" aria-hidden="true"', renderer_source)
        self.assertIn('aria-label="${escapeHtml(`切换到会话 ${sessionTitle}`)}"', renderer_source)
        self.assertIn('title="${escapeHtml(sessionTitle)}"', renderer_source)
        self.assertIn("fontAwesomeIconMarkup('comment-alt', 'session-conversation-glyph')", renderer_source)
        self.assertNotIn('viewBox="0 0 24 24"', renderer_source)
        self.assertIn("event.target.closest('[data-select-conversation]')", conversation_state_source)
        self.assertIn(
            "void setActiveConversation(selectButton.dataset.selectConversation || '');",
            conversation_state_source,
        )
        self.assertIn(".session-conversation-icon {", controls_style)
        self.assertIn(".session-item.active .session-conversation-icon", controls_style)
        self.assertIn(".session-item.active {\n  border-color: #3158e2;", controls_style)
        self.assertIn("inset 0 0 0 1px #3158e2", controls_style)
        self.assertIn(".new-conversation-button.is-limit:hover,", controls_style)
        self.assertIn(".new-conversation-button.is-limit:focus-visible {\n  color: #fff;", controls_style)
        self.assertNotIn("border: 1px solid rgba(79, 109, 255, 0.16);", controls_style)
        self.assertNotIn("background: linear-gradient(145deg, #5875ff, #3158e2);", controls_style)
        self.assertNotIn("box-shadow: 0 6px 14px rgba(49, 88, 226, 0.24);", controls_style)
        self.assertIn(".session-select > :not(.session-conversation-icon)", responsive_style)
        self.assertNotIn(".session-select > :not(.session-mode-badge)", responsive_style)
        self.assertIn("min-height: 44px;", responsive_style)
        self.assertIn(".session-conversation-icon {", responsive_style)
        self.assertIn("display: inline-grid;", responsive_style)
        self.assertIn("width: 44px;\n  height: 44px;", responsive_style)
        self.assertIn("width: 28px;\n  height: 28px;", responsive_style)
        self.assertIn("width: 26px;\n  height: 26px;", responsive_style)
        self.assertIn(".session-item.active {\n  border-color: #3158e2;\n  background: #3158e2;", responsive_style)
        self.assertIn(".session-item.active .session-conversation-icon {\n  color: #fff;", responsive_style)
        self.assertIn(".new-conversation-button:hover,\n.shell.is-sidebar-collapsed", responsive_style)
        self.assertIn(".new-conversation-button:focus-visible {\n  color: #fff;", responsive_style)
        self.assertIn("width: 44px !important;", collapsed_icon_style)
        self.assertIn("height: 44px !important;", collapsed_icon_style)
        self.assertIn("width: 26px !important;", collapsed_icon_style)
        self.assertIn("padding: 4px !important;", collapsed_icon_style)
        self.assertIn('22a-conversation-controls.css?v=20260804-14', workbench_style)
        self.assertIn('22b-conversation-controls-responsive.css?v=20260804-11', workbench_style)
        self.assertIn('21d-sidebar-collapsed-icon-scale.css?v=20260804-1', workbench_style)
        self.assertIn('/static/workbench.css?v=20260804-32', index_source)
        self.assertIn('/static/workbench.js?v=20260804-14', index_source)

    def test_runtime_ui_icons_use_local_fontawesome_svg_system(self) -> None:
        source = read_static_source()
        icon_style = (
            STATIC_ROOT / "styles" / "00a-fontawesome-icons.css"
        ).read_text(encoding="utf-8")
        icon_script = (
            STATIC_ROOT / "scripts" / "01aa-fontawesome-icons.js"
        ).read_text(encoding="utf-8")
        notices = (STATIC_ROOT.parents[4] / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

        self.assertIn('00a-fontawesome-icons.css?v=20260804-2', source)
        self.assertIn("function fontAwesomeIconMarkup(iconName, className = '')", icon_script)
        self.assertIn('.fa-icon {', icon_style)
        for icon_name in (
            'plus',
            'xmark',
            'magnifying-glass',
            'comment-alt',
            'up-down-left-right',
            'eye-slash',
            'microphone',
            'pen-to-square',
            'crop-simple',
            'scissors',
            'chevron-down',
            'rotate-left',
            'rotate-right',
            'download',
            'left-right',
            'floppy-disk',
            'music',
            'grip-vertical',
            'play',
            'chevron-right',
            'triangle-exclamation',
        ):
            self.assertIn(f'data-fa-icon="{icon_name}"', icon_style)
            self.assertTrue(
                (
                    STATIC_ROOT
                    / "vendor"
                    / "fontawesome-free-7.3.1-desktop"
                    / "svgs-full"
                    / "solid"
                    / f"{icon_name}.svg"
                ).is_file()
            )

        self.assertEqual(source.count('<svg'), 1)
        self.assertIn('class="video-preview-tts-waveform"', source)
        self.assertNotIn('const VIDEO_PREVIEW_ICONS =', source)
        self.assertNotIn('return `<svg viewBox', source)
        for character_icon in ('🎵', '⌛', '⌄', '⠿', '▶', '✓', '>×</button>', '>+</span>'):
            self.assertNotIn(character_icon, source)
        self.assertIn('37 个 Solid SVG', notices)

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

    def test_static_flower_text_and_watermark_active_states_are_independent(self) -> None:
        html = read_static_source()

        self.assertIn("const flowerTextReady = !!config.enabled && !!String(config.text || '').trim();", html)
        self.assertIn("const active = flowerTextReady || watermarkReady;", html)
        self.assertIn("const hasRenderableText = !!payload.enabled && !!payload.text.trim();", html)
        self.assertIn("enabled ? '' : ' is-flower-text-disabled'", html)
        self.assertIn(".flower-text-editor-wrap.is-flower-text-disabled .flower-text-rendered-preview", html)
        self.assertIn("function syncFlowerTextActivationControls()", html)
        watermark_save = html[html.index("async function saveFlowerWatermarkCheckbox"):html.index("async function uploadFlowerWatermarkFiles")]
        self.assertIn("syncFlowerTextActivationControls();", watermark_save)
        self.assertNotIn("renderFlowerTextDrawer();", watermark_save)
        self.assertIn("await saveFlowerText({ enabled }, { rerender: false });", html)
        self.assertNotIn("enabled: !!checked || !!state.flowerText?.enabled", html)
        self.assertNotIn("127.0.0.1:7352", html)

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
        self.assertNotIn("<summary>技术详情</summary>", html)
        self.assertNotIn("item?.jobId ? `任务 ${item.jobId}` : ''", html)
        self.assertIn(
            "els.recycleBinSub.textContent = `${Number(bin.count || items.length || 0)} 个失败任务。失败但已产出视频的任务会放到这里。`;",
            html,
        )
        self.assertIn(
            "meta: `${count} 个失败任务`,",
            html,
        )
        self.assertIn("attrs: 'data-show-recycle-bin',", html)
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
        self.assertIn("检索并引用本地剧本知识", html)
        self.assertIn("data-script-knowledge-tab", html)
        self.assertIn("data-script-knowledge-panel", html)
        self.assertIn("data-script-knowledge-ingest", html)
        self.assertIn("function formatScriptKnowledgeLeafContent(value)", html)
        self.assertIn("script-knowledge-tree-chevron", html)
        self.assertIn("script-knowledge-tree-leaf-body", html)
        self.assertIn("script-knowledge-tree-drawer", html)
        self.assertIn("data-script-knowledge-tree-toggle", html)
        self.assertIn("script-knowledge-tree-node", html)
        self.assertIn('data-last="${isLast ? \'true\' : \'false\'}"', html)
        self.assertIn("点叶节点展开正文", html)
        self.assertIn("script-knowledge-tree-meta", html)
        knowledge_css = (STATIC_ROOT / "script-knowledge.css").read_text(encoding="utf-8")
        self.assertIn("--tree-spine:", knowledge_css)
        self.assertIn("--tree-icon-gap:", knowledge_css)
        self.assertIn('data-depth="0"]::before', knowledge_css)
        self.assertIn(".script-knowledge-tree-node::before", knowledge_css)
        self.assertIn(".script-knowledge-tree-node::after", knowledge_css)
        self.assertIn(".script-knowledge-tree-drawer", knowledge_css)
        self.assertIn("grid-template-rows: 0fr", knowledge_css)
        self.assertNotIn("script-knowledge-tree-count-pill", html)
        self.assertIn("/api/script-knowledge/${id}/ingest", html)
        self.assertIn("正在知识入库", html)
        self.assertIn("await loadScriptKnowledgeIngestionStatus(id, { renderAfter: false });", html)
        self.assertIn("Number(job?.documentId || 0) === id ? job : null", html)
        self.assertIn("Number(state.scriptKnowledge.selectedId || 0) !== id) return null;", html)
        self.assertNotIn("${state.scriptKnowledge.ingesting ? '知识入库中' : '知识入库'}", html)
        self.assertNotIn('id="scriptKnowledgeSyncButton"', html)
        self.assertNotIn(
            "? '后台真实进度'",
            html,
        )


























if __name__ == "__main__":
    unittest.main()
