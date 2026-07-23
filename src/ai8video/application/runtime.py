from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

from ai8video.batch.batch_alert_store import BatchAlertStore
from ai8video.batch.batch_seed_file import (
    build_batch_seed_file_from_recent_reports,
    inspect_batch_seed_file,
)
from ai8video.assets.asset_store import JsonlAssetStore
from ai8video.batch.batch_report_store import BatchReportStore
from ai8video.application.conversation_controller import AI8VideoConversationController
from ai8video.core.config import AI8VideoConfig
from ai8video.core.legacy_payload import normalize_legacy_video_payload
from ai8video.batch.daily_batch_runner import DailyBatchRunner
from ai8video.generation.pipeline import AI8VideoPipeline
from ai8video.application.summaries import build_batch_summary, build_pipeline_summary, build_summary
from ai8video.batch.supervisor_launchd import inspect_launchd_deployment
from ai8video.core.paths import PROJECT_ROOT
from ai8video.media.video_text_overlay import video_text_overlay_runtime_status
from ai8video.integrations.video_model_settings import load_video_model_settings

CHAT_BACKEND = "ai8video-runtime"


class AI8VideoRuntime:
    def __init__(self, config: AI8VideoConfig | None = None):
        self.config = config or AI8VideoConfig.from_env()
        self.pipeline = AI8VideoPipeline(config=self.config)
        self.store = JsonlAssetStore(self.config.asset_store_path)
        self.batch_report_store = BatchReportStore(self.config.batch_report_dir)
        self.batch_alert_store = BatchAlertStore(self.config.batch_alert_dir)
        self.conversation_controller = AI8VideoConversationController(self.pipeline)

    def health(self) -> dict:
        generation_guard = self.pipeline.client.guard.snapshot()
        supervisor_state_path = Path(self.config.batch_supervisor_state_path)
        supervisor_admin_state_path = Path(self.config.batch_supervisor_admin_state_path)
        supervisor_lock_path = Path(self.config.batch_supervisor_lock_path)
        supervisor_state = _read_json_dict(supervisor_state_path)
        supervisor_admin_state = _read_json_dict(supervisor_admin_state_path)
        latest_alert = _read_latest_alert_summary(self.batch_alert_store)
        deployment = inspect_launchd_deployment()
        configured_schedule_times = _parse_schedule_times_safe(self.config.batch_schedule_times)
        deployment_schedule_times = _parse_schedule_times_safe(deployment.get("scheduleTimes") or [])
        schedule_times = configured_schedule_times or deployment_schedule_times
        seed_file_status = inspect_batch_seed_file(self.config)
        latest_failure_reason = _extract_latest_failure_reason(supervisor_state, latest_alert)
        video_model_settings = load_video_model_settings(
            llm_base_url=self.config.llm_base_url,
            llm_api_key=self.config.llm_api_key,
        )
        text_overlay_runtime = video_text_overlay_runtime_status()
        return {
            "ok": True,
            "chatBackend": CHAT_BACKEND,
            "dryRun": self.config.dry_run,
            "videoGenerationProvider": "direct-video-model",
            "videoModelSettings": video_model_settings.public_dict(),
            "videoTextOverlayRuntime": text_overlay_runtime,
            "hasVideoModel": video_model_settings.configured(),
            "hasLLM": self.config.has_llm(),
            "llmSource": self.config.llm_source,
            "hasImageModel": self.config.has_image_model(),
            "imageModelSource": self.config.image_source,
            "assetStorePath": self.config.asset_store_path,
            "batchReportDir": self.config.batch_report_dir,
            "batchAlertDir": self.config.batch_alert_dir,
            "batchSupervisorStatePath": self.config.batch_supervisor_state_path,
            "batchSupervisorAdminStatePath": self.config.batch_supervisor_admin_state_path,
            "batchSupervisorLockPath": self.config.batch_supervisor_lock_path,
            "batchSupervisorState": supervisor_state,
            "batchSupervisorAdminResult": supervisor_admin_state,
            "batchSupervisorLockExists": supervisor_lock_path.exists(),
            "batchScheduleTimes": schedule_times,
            "batchConfiguredScheduleTimes": configured_schedule_times,
            "batchScheduleTimesRaw": self.config.batch_schedule_times,
            "batchNextScheduledSlot": _compute_next_scheduled_slot(schedule_times, now=_now_localtime()),
            "batchLatestAlert": latest_alert,
            "batchLatestFailureReason": latest_failure_reason,
            "batchSupervisorDeployment": deployment,
            "batchSupervisorSuggestions": _build_supervisor_suggestions(
                supervisor_state=supervisor_state,
                schedule_times=schedule_times,
                deployment=deployment,
                seed_file_status=seed_file_status,
                latest_failure_reason=latest_failure_reason,
                latest_alert=latest_alert,
                low_pass_threshold=self.config.batch_alert_consecutive_low_pass_runs,
            ),
            "batchSeedFile": seed_file_status["path"],
            "batchSeedFileConfigured": self.config.batch_seed_file,
            "batchSeedFileStatus": seed_file_status,
            "batchTargetPassCount": self.config.batch_target_pass_count,
            "batchStyleHint": self.config.batch_style_hint,
            "batchAlertMinPassRate": self.config.batch_alert_min_pass_rate,
            "batchAlertConsecutiveLowPassRuns": self.config.batch_alert_consecutive_low_pass_runs,
            "archiveEnabled": self.config.archive_enabled(),
            "archiveBackend": self.config.archive_backend,
            "archiveResolvedBackend": self.config.resolved_archive_backend(),
            "archiveLocalDir": self.config.archive_local_dir,
            "hasArchiveS3": self.config.has_archive_s3(),
            "archivePublicEnabled": self.config.archive_public_enabled(),
            "archivePublicBaseUrl": self.config.archive_public_base_url,
            "archiveS3Bucket": self.config.archive_s3_bucket,
            "archiveS3Endpoint": self.config.archive_s3_endpoint,
            "realGenerationGuard": generation_guard,
        }

    def assets(self, limit: int = 12) -> dict:
        records = self.store.read_all()
        limit = max(1, min(50, int(limit or 12)))
        return {"items": list(reversed(records[-limit:]))}

    def batch_reports(self, limit: int = 10) -> dict:
        return {"items": self.batch_report_store.read_recent(limit=limit)}

    def batch_alerts(self, limit: int = 10) -> dict:
        return {"items": self.batch_alert_store.read_recent(limit=limit)}

    def build_batch_seed_file(self, *, report_limit: int = 8, max_messages: int = 40) -> dict:
        return build_batch_seed_file_from_recent_reports(
            config=self.config,
            report_store=self.batch_report_store,
            report_limit=report_limit,
            max_messages=max_messages,
        )

    def run_batch(
        self,
        seed_messages: list[str],
        *,
        target_pass_count: int = 30,
        style_hint: str | None = None,
        trigger: str = "runtime",
        source: str = "runtime",
        session_id: str | None = None,
    ) -> dict:
        runner = DailyBatchRunner(
            pipeline=self.pipeline,
            config=self.config,
            target_pass_count=target_pass_count,
            initial_candidate_budget=max(len(seed_messages), int(target_pass_count * 1.5)),
            max_candidate_budget=max(max(len(seed_messages), int(target_pass_count * 1.5)), int(target_pass_count * 2)),
            report_store=self.batch_report_store,
        )
        report = runner.run(
            seed_messages,
            style_hint=style_hint,
            trigger=trigger,
            source=source,
            session_id=session_id,
        )
        return {
            "result": report,
            "summary": build_batch_summary(report),
        }

    def chat(self, session_id: str, message: str) -> dict:
        try:
            reply = self.conversation_controller.handle_message(session_id=session_id, message=message)
            reply_data = reply.to_dict()
            body = {"reply": reply_data, "chatBackend": CHAT_BACKEND}
            result_data = reply_data.get("result")
            if isinstance(result_data, dict):
                summary = build_summary(result_data, reply_data.get("meta") or {})
                if summary is not None:
                    body["summary"] = summary
                body["result"] = result_data
            return body
        except Exception as exc:
            message_text = _friendly_chat_error(exc)
            return {
                "reply": {
                    "text": message_text,
                    "stage": "error",
                    "awaiting": None,
                    "draft": None,
                    "meta": {
                        "operation": "error",
                        "errorType": exc.__class__.__name__,
                    },
                    "result": None,
                },
                "error": {
                    "type": exc.__class__.__name__,
                    "message": message_text,
                },
                "chatBackend": CHAT_BACKEND,
            }


def _friendly_chat_error(error: Exception) -> str:
    message = str(error).strip()
    lowered = message.lower()
    if "ssl module is not available" in lowered or "can't connect to https url" in lowered:
        return "本机安全连接组件不可用，暂时无法连接视频服务。请修复本机 Python 的 HTTPS 支持后再试。"
    return message or "本次任务未完成，请稍后重试。"


_RUNTIME: AI8VideoRuntime | None = None
_RUNTIME_LOCK = Lock()
_CHAT_SNAPSHOTS: dict[str, dict] = {}
_CHAT_SNAPSHOTS_LOCK = Lock()


def _read_json_dict(path: Path) -> dict | None:
    try:
        if not path.exists() or not path.is_file():
            return None
        data = normalize_legacy_video_payload(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_json_dict(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _read_latest_alert_summary(store: BatchAlertStore) -> dict | None:
    try:
        items = store.read_recent(limit=1)
    except Exception:
        return None
    if not items:
        return None
    latest = items[0]
    return latest if isinstance(latest, dict) else None


def _extract_latest_failure_reason(supervisor_state: dict | None, latest_alert: dict | None) -> str | None:
    if isinstance(supervisor_state, dict):
        last_error = str(supervisor_state.get("lastError") or "").strip()
        if last_error:
            return last_error
        if supervisor_state.get("lastGoalMet") is False and isinstance(latest_alert, dict):
            message = str(latest_alert.get("message") or "").strip()
            if message:
                return message
    if isinstance(latest_alert, dict):
        message = str(latest_alert.get("message") or "").strip()
        if message:
            return message
    return None


def _build_supervisor_suggestions(
    *,
    supervisor_state: dict | None,
    schedule_times: list[str],
    deployment: dict | None,
    seed_file_status: dict | None,
    latest_failure_reason: str | None,
    latest_alert: dict | None,
    low_pass_threshold: int,
) -> list[str]:
    suggestions: list[str] = []

    if not isinstance(seed_file_status, dict) or not seed_file_status.get("exists"):
        suggestions.append("先生成种子文件，让自动值守有可读候选内容。")

    if not schedule_times:
        suggestions.append("先补自动排期，例如 09:00,13:15。")

    platform_supported = True if not isinstance(deployment, dict) else deployment.get("platformSupported", True)
    deployment_exists = bool(deployment.get("exists")) if isinstance(deployment, dict) else False
    deployment_loaded = bool(deployment.get("loaded")) if isinstance(deployment, dict) else False
    if platform_supported is False:
        suggestions.append("当前机器不是 macOS，长期运行不要走 launchd。")
    elif schedule_times and isinstance(seed_file_status, dict) and seed_file_status.get("exists") and not deployment_exists:
        suggestions.append("排期和候选都已准备好，下一步可生成部署文件。")
    elif deployment_exists and not deployment_loaded:
        suggestions.append("部署文件已写好，确认后可直接安装值守。")

    if isinstance(supervisor_state, dict):
        last_error = str(supervisor_state.get("lastError") or "").strip()
        if last_error:
            suggestions.append("先打开状态文件，确认最近异常。")
        low_pass_runs = int(supervisor_state.get("consecutiveLowPassRuns") or 0)
        if low_pass_runs >= max(1, int(low_pass_threshold or 1)):
            suggestions.append("最近连续低成功，先看最近日报和告警，再补候选或下调目标生成数。")

    failure_text = str(latest_failure_reason or "").strip()
    alert_text = str((latest_alert or {}).get("message") or "").strip()
    combined_text = f"{failure_text} {alert_text}".strip()
    if combined_text and "未达标" in combined_text:
        suggestions.append("最近一轮没达标，先补候选内容，再考虑放宽目标生成数。")

    deduped: list[str] = []
    for item in suggestions:
        text = str(item or "").strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped[:3]


def _parse_schedule_times_safe(raw_value: str | list[str] | tuple[str, ...]) -> list[str]:
    try:
        from ai8video.batch.daily_batch_supervisor import parse_schedule_times

        return parse_schedule_times(raw_value or "")
    except Exception:
        return []


def _compute_next_scheduled_slot(schedule_times: list[str], *, now: datetime) -> str | None:
    if not schedule_times:
        return None
    local_now = now if now.tzinfo is not None else now.astimezone()
    for day_offset in range(2):
        current_date = (local_now + timedelta(days=day_offset)).date()
        for slot in schedule_times:
            hour_text, minute_text = slot.split(":")
            candidate = local_now.replace(
                year=current_date.year,
                month=current_date.month,
                day=current_date.day,
                hour=int(hour_text),
                minute=int(minute_text),
                second=0,
                microsecond=0,
            )
            if candidate > local_now:
                return candidate.isoformat()
    return None


def _now_localtime() -> datetime:
    return datetime.now().astimezone()


def _resolve_project_path(raw_path: str | Path) -> Path:
    target = Path(raw_path)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    return target.resolve()


def get_runtime(refresh: bool = False) -> AI8VideoRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if refresh or _RUNTIME is None:
            _RUNTIME = AI8VideoRuntime()
        return _RUNTIME


def get_health_payload(refresh: bool = False) -> dict:
    return get_runtime(refresh=refresh).health()


def get_supervisor_admin_result_path(refresh: bool = False) -> Path:
    runtime = get_runtime(refresh=refresh)
    return _resolve_project_path(runtime.config.batch_supervisor_admin_state_path)


def write_supervisor_admin_result_payload(
    *,
    action: str,
    path: str | None = None,
    seed_file: str | None = None,
    deployment: dict | None = None,
    keep_plist: bool | None = None,
    refresh: bool = False,
) -> dict:
    target = get_supervisor_admin_result_path(refresh=refresh)
    deployment_payload = to_safe_snapshot(deployment or {})
    payload = {
        "action": str(action or "").strip() or "write",
        "savedAt": _now_localtime().isoformat(),
        "path": str(path or deployment_payload.get("plistPath") or "").strip(),
        "seedFile": str(seed_file or "").strip(),
        "deployment": deployment_payload,
        "exists": bool(deployment_payload.get("exists")),
        "loaded": bool(deployment_payload.get("loaded")),
        "keepPlist": bool(keep_plist) if keep_plist is not None else None,
    }
    _write_json_dict(target, payload)
    return payload


def get_assets_payload(limit: int = 12, refresh: bool = False) -> dict:
    return get_runtime(refresh=refresh).assets(limit=limit)


def get_batch_reports_payload(limit: int = 10, refresh: bool = False) -> dict:
    return get_runtime(refresh=refresh).batch_reports(limit=limit)


def get_batch_alerts_payload(limit: int = 10, refresh: bool = False) -> dict:
    return get_runtime(refresh=refresh).batch_alerts(limit=limit)


def build_batch_seed_file_payload(
    *,
    report_limit: int = 8,
    max_messages: int = 40,
    refresh: bool = False,
) -> dict:
    return get_runtime(refresh=refresh).build_batch_seed_file(
        report_limit=report_limit,
        max_messages=max_messages,
    )


def run_batch_payload(
    seed_messages: list[str],
    *,
    target_pass_count: int = 30,
    style_hint: str | None = None,
    trigger: str = "runtime",
    source: str = "runtime",
    session_id: str | None = None,
    refresh: bool = False,
) -> dict:
    return get_runtime(refresh=refresh).run_batch(
        seed_messages,
        target_pass_count=target_pass_count,
        style_hint=style_hint,
        trigger=trigger,
        source=source,
        session_id=session_id,
    )


def handle_chat_message(session_id: str, message: str, refresh: bool = False) -> dict:
    body = get_runtime(refresh=refresh).chat(session_id=session_id, message=message)
    _remember_chat_snapshot(session_id, body)
    return body


def clear_chat_snapshot(session_id: str) -> None:
    key = str(session_id or "").strip()
    if not key:
        return
    with _CHAT_SNAPSHOTS_LOCK:
        _CHAT_SNAPSHOTS.pop(key, None)


def get_chat_snapshot(session_id: str) -> dict | None:
    with _CHAT_SNAPSHOTS_LOCK:
        snapshot = _CHAT_SNAPSHOTS.get(str(session_id or "").strip())
        if snapshot is None:
            return None
        return json.loads(json.dumps(snapshot, ensure_ascii=False))


def _remember_chat_snapshot(session_id: str, body: dict) -> None:
    key = str(session_id or "").strip()
    if not key or not isinstance(body, dict):
        return
    with _CHAT_SNAPSHOTS_LOCK:
        _CHAT_SNAPSHOTS[key] = json.loads(json.dumps(body, ensure_ascii=False))


def to_safe_snapshot(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"value": str(payload)}
    safe = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, list):
            safe[key] = value[:3]
        elif isinstance(value, dict):
            safe[key] = {k: v for k, v in list(value.items())[:8]}
        else:
            safe[key] = asdict(value) if hasattr(value, "__dataclass_fields__") else str(value)
    return safe
