from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path

from ai8video.core.paths import PROJECT_ROOT
from ai8video.integrations.model_overrides import load_model_overrides


@dataclass(frozen=True)
class AI8VideoConfig:
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    multimodal_base_url: str | None = None
    multimodal_api_key: str | None = None
    multimodal_model: str | None = None
    image_base_url: str | None = None
    image_api_key: str | None = None
    image_model: str | None = None
    timeout_seconds: int = 180
    poll_interval_seconds: float = 8.0
    max_poll_attempts: int = 90
    dry_run: bool = True
    asset_store_path: str = "temp/ai8video/assets.jsonl"
    archive_backend: str = "local"
    archive_local_dir: str = "media_resources/ai8video/archive"
    archive_s3_endpoint: str | None = None
    archive_s3_bucket: str | None = None
    archive_s3_region: str | None = None
    archive_s3_access_key: str | None = None
    archive_s3_secret_key: str | None = None
    archive_s3_prefix: str = "ai8video"
    archive_public_base_url: str | None = None
    archive_download_timeout_seconds: int = 180
    real_job_max_count: int = 0
    real_job_window_seconds: int = 3600
    real_job_force_duration_seconds: int = 0
    real_job_audit_path: str = "temp/ai8video/real_generation_jobs.jsonl"
    batch_report_dir: str = "media_resources/ai8video/batch_reports"
    batch_alert_dir: str = "media_resources/ai8video/batch_alerts"
    batch_supervisor_state_path: str = "temp/ai8video/batch_supervisor_state.json"
    batch_supervisor_admin_state_path: str = "temp/ai8video/batch_supervisor_admin_state.json"
    batch_supervisor_lock_path: str = "temp/ai8video/batch_supervisor.lock"
    batch_schedule_times: str = ""
    batch_seed_file: str | None = None
    batch_target_pass_count: int = 30
    batch_style_hint: str | None = None
    batch_alert_min_pass_rate: float = 0.7
    batch_alert_consecutive_low_pass_runs: int = 2
    llm_source: str = "missing"
    multimodal_source: str = "missing"
    image_source: str = "missing"

    @classmethod
    def from_env(cls) -> "AI8VideoConfig":
        dry_value = os.getenv("AI8VIDEO_DRY_RUN", "0").strip().lower()
        model_overrides = load_model_overrides()

        llm_base_url = (os.getenv("AI8VIDEO_LLM_BASE_URL") or "").rstrip("/") or None
        llm_api_key = os.getenv("AI8VIDEO_LLM_API_KEY")
        llm_model = os.getenv("AI8VIDEO_LLM_MODEL")
        llm_source = "env" if (llm_base_url and llm_api_key and llm_model) else "missing"
        if not (llm_base_url and llm_api_key and llm_model):
            fallback = _load_ai8video_llm_fallback()
            if fallback:
                llm_base_url = llm_base_url or fallback.get("apibase")
                llm_api_key = llm_api_key or fallback.get("apikey")
                llm_model = llm_model or fallback.get("model")
                if llm_base_url and llm_api_key and llm_model:
                    llm_source = _merge_source(llm_source, "mykey.py")
        if model_overrides.get("AI8VIDEO_LLM_MODEL") and llm_base_url and llm_api_key:
            llm_model = model_overrides["AI8VIDEO_LLM_MODEL"]
            llm_source = _merge_source(llm_source, "user_file")
        multimodal_base_url = (os.getenv("AI8VIDEO_MULTIMODAL_BASE_URL") or "").rstrip("/") or None
        multimodal_api_key = os.getenv("AI8VIDEO_MULTIMODAL_API_KEY")
        multimodal_model = os.getenv("AI8VIDEO_MULTIMODAL_MODEL")
        multimodal_source = "env" if (multimodal_base_url and multimodal_api_key and multimodal_model) else "missing"
        if not multimodal_base_url and llm_base_url:
            multimodal_base_url = llm_base_url
            multimodal_source = _merge_source(multimodal_source, llm_source)
        if not multimodal_api_key and llm_api_key:
            multimodal_api_key = llm_api_key
            multimodal_source = _merge_source(multimodal_source, llm_source)
        if not multimodal_model and llm_model:
            multimodal_model = llm_model
            multimodal_source = _merge_source(multimodal_source, llm_source)
        if model_overrides.get("AI8VIDEO_MULTIMODAL_MODEL") and multimodal_base_url and multimodal_api_key:
            multimodal_model = model_overrides["AI8VIDEO_MULTIMODAL_MODEL"]
            multimodal_source = _merge_source(multimodal_source, "user_file")
        image_base_url = (os.getenv("AI8VIDEO_IMAGE_BASE_URL") or "").rstrip("/") or None
        image_api_key = os.getenv("AI8VIDEO_IMAGE_API_KEY")
        image_model = os.getenv("AI8VIDEO_IMAGE_MODEL")
        image_source = "env" if (image_base_url and image_api_key and image_model) else "missing"
        if not (image_base_url and image_api_key) and llm_base_url and llm_api_key:
            image_base_url = image_base_url or llm_base_url
            image_api_key = image_api_key or llm_api_key
            image_source = _merge_source(image_source, "shared_llm_credentials")
        if model_overrides.get("AI8VIDEO_IMAGE_MODEL") and image_base_url and image_api_key:
            image_model = model_overrides["AI8VIDEO_IMAGE_MODEL"]
            image_source = _merge_source(image_source, "user_file")
        archive_backend = (os.getenv("AI8VIDEO_ARCHIVE_BACKEND") or "local").strip().lower()
        archive_s3_endpoint = (os.getenv("AI8VIDEO_ARCHIVE_S3_ENDPOINT") or "").rstrip("/") or None
        archive_public_base_url = (os.getenv("AI8VIDEO_ARCHIVE_PUBLIC_BASE_URL") or "").rstrip("/") or None
        return cls(
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            multimodal_base_url=multimodal_base_url,
            multimodal_api_key=multimodal_api_key,
            multimodal_model=multimodal_model,
            image_base_url=image_base_url,
            image_api_key=image_api_key,
            image_model=image_model,
            timeout_seconds=int(os.getenv("AI8VIDEO_TIMEOUT_SECONDS", "180")),
            poll_interval_seconds=float(os.getenv("AI8VIDEO_POLL_INTERVAL_SECONDS", "8")),
            max_poll_attempts=int(os.getenv("AI8VIDEO_MAX_POLL_ATTEMPTS", "90")),
            dry_run=dry_value not in {"0", "false", "no", "real"},
            asset_store_path=_project_path(
                os.getenv("AI8VIDEO_ASSET_STORE_PATH"),
                "temp/ai8video/assets.jsonl",
            ),
            archive_backend=archive_backend,
            archive_local_dir=_project_path(
                os.getenv("AI8VIDEO_ARCHIVE_LOCAL_DIR"),
                "media_resources/ai8video/archive",
            ),
            archive_s3_endpoint=archive_s3_endpoint,
            archive_s3_bucket=os.getenv("AI8VIDEO_ARCHIVE_S3_BUCKET"),
            archive_s3_region=os.getenv("AI8VIDEO_ARCHIVE_S3_REGION"),
            archive_s3_access_key=os.getenv("AI8VIDEO_ARCHIVE_S3_ACCESS_KEY"),
            archive_s3_secret_key=os.getenv("AI8VIDEO_ARCHIVE_S3_SECRET_KEY"),
            archive_s3_prefix=(os.getenv("AI8VIDEO_ARCHIVE_S3_PREFIX") or "ai8video").strip("/"),
            archive_public_base_url=archive_public_base_url,
            archive_download_timeout_seconds=max(10, int(os.getenv("AI8VIDEO_ARCHIVE_DOWNLOAD_TIMEOUT_SECONDS", "180"))),
            real_job_max_count=max(0, int(os.getenv("AI8VIDEO_REAL_JOB_MAX_COUNT", "0"))),
            real_job_window_seconds=max(0, int(os.getenv("AI8VIDEO_REAL_JOB_WINDOW_SECONDS", "3600"))),
            real_job_force_duration_seconds=max(0, int(os.getenv("AI8VIDEO_REAL_JOB_FORCE_DURATION_SECONDS", "0"))),
            real_job_audit_path=_project_path(
                os.getenv("AI8VIDEO_REAL_JOB_AUDIT_PATH"),
                "temp/ai8video/real_generation_jobs.jsonl",
            ),
            batch_report_dir=_project_path(
                os.getenv("AI8VIDEO_BATCH_REPORT_DIR"),
                "media_resources/ai8video/batch_reports",
            ),
            batch_alert_dir=_project_path(
                os.getenv("AI8VIDEO_BATCH_ALERT_DIR"),
                "media_resources/ai8video/batch_alerts",
            ),
            batch_supervisor_state_path=_project_path(
                os.getenv("AI8VIDEO_BATCH_SUPERVISOR_STATE_PATH"),
                "temp/ai8video/batch_supervisor_state.json",
            ),
            batch_supervisor_admin_state_path=_project_path(
                os.getenv("AI8VIDEO_BATCH_SUPERVISOR_ADMIN_STATE_PATH"),
                "temp/ai8video/batch_supervisor_admin_state.json",
            ),
            batch_supervisor_lock_path=_project_path(
                os.getenv("AI8VIDEO_BATCH_SUPERVISOR_LOCK_PATH"),
                "temp/ai8video/batch_supervisor.lock",
            ),
            batch_schedule_times=os.getenv("AI8VIDEO_BATCH_SCHEDULE_TIMES", "").strip(),
            batch_seed_file=_optional_project_path(os.getenv("AI8VIDEO_BATCH_SEED_FILE")),
            batch_target_pass_count=max(1, int(os.getenv("AI8VIDEO_BATCH_TARGET_PASS_COUNT", "30"))),
            batch_style_hint=(os.getenv("AI8VIDEO_BATCH_STYLE_HINT") or "").strip() or None,
            batch_alert_min_pass_rate=min(1.0, max(0.0, float(os.getenv("AI8VIDEO_BATCH_ALERT_MIN_PASS_RATE", "0.7")))),
            batch_alert_consecutive_low_pass_runs=max(
                1,
                int(os.getenv("AI8VIDEO_BATCH_ALERT_CONSECUTIVE_LOW_PASS_RUNS", "2")),
            ),
            llm_source=llm_source,
            multimodal_source=multimodal_source,
            image_source=image_source,
        )

    def has_llm(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    def has_image_model(self) -> bool:
        return bool(self.image_base_url and self.image_api_key and self.image_model)

    def archive_enabled(self) -> bool:
        return self.archive_backend not in {"", "none", "off", "disabled"}

    def has_archive_s3(self) -> bool:
        return bool(
            self.archive_s3_bucket
            and self.archive_s3_endpoint
            and self.archive_s3_access_key
            and self.archive_s3_secret_key
        )

    def resolved_archive_backend(self) -> str:
        backend = (self.archive_backend or "").strip().lower()
        if backend in {"", "auto"}:
            return "s3" if self.has_archive_s3() else "local"
        if backend == "s3" and not self.has_archive_s3():
            return "local"
        if backend in {"off", "disabled"}:
            return "none"
        return backend

    def archive_public_enabled(self) -> bool:
        return self.resolved_archive_backend() == "s3" and bool(self.archive_public_base_url)


def _project_path(value: str | None, default: str) -> str:
    raw_path = (value or default).strip()
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return str(path)
    return str((PROJECT_ROOT / path).resolve())


def _optional_project_path(value: str | None) -> str | None:
    raw_path = (value or "").strip()
    if not raw_path:
        return None
    return _project_path(raw_path, raw_path)


def _load_ai8video_llm_fallback() -> dict[str, str] | None:
    if os.getenv("AI8VIDEO_DISABLE_MYKEY", "").strip().lower() in {"1", "true", "yes", "on"}:
        return None
    return load_ai8video_core_model_settings()


def load_ai8video_core_model_settings() -> dict[str, str] | None:
    try:
        import mykey

        importlib.reload(mykey)
    except Exception:
        return None

    candidates: list[tuple[int, dict[str, str]]] = []
    for name, value in vars(mykey).items():
        if name.startswith("_") or not isinstance(value, dict):
            continue
        if not all(value.get(field) for field in ("apibase", "apikey", "model")):
            continue
        score = 0
        lowered = name.lower()
        if "native" in lowered:
            score += 2
        if "oai" in lowered or "openai" in lowered:
            score += 2
        candidates.append((score, value))
    if not candidates:
        return None
    best = sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]
    result = {
        "apibase": str(best.get("apibase") or "").rstrip("/"),
        "apikey": str(best.get("apikey") or ""),
        "model": str(best.get("model") or ""),
        "name": str(best.get("name") or ""),
        "source": "mykey.py",
    }
    overrides = load_model_overrides()
    if overrides.get("mykey.py model"):
        result["model"] = overrides["mykey.py model"]
        result["source"] = _merge_source(result["source"], "user_file")
    return result


def _merge_source(current: str, fallback: str) -> str:
    if current in {"", "missing"}:
        return fallback
    if current == fallback:
        return current
    return f"{current}+{fallback}"
