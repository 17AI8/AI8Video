from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ai8video.core.config import AI8VideoConfig
from ai8video.integrations.llm_provider import build_openai_compat_splitter
from ai8video.integrations.video_model_settings import load_video_model_settings

PREFLIGHT_CHECK_CHOICES = ("llm", "video_model", "archive_config", "archive_probe")
SAFE_PREFLIGHT_CHECKS = ("llm", "archive_config")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI8Video short-video live preflight checks")
    parser.add_argument(
        "--checks",
        nargs="*",
        default=["llm", "video_model", "archive_config"],
        choices=list(PREFLIGHT_CHECK_CHOICES),
        help="Which checks to run",
    )
    args = parser.parse_args()

    config = AI8VideoConfig.from_env()
    report = run_preflight_checks(config, args.checks or [])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def run_preflight_checks(config: AI8VideoConfig, checks: list[str] | tuple[str, ...]) -> dict:
    requested = []
    for name in checks or []:
        text = str(name or "").strip()
        if text and text in PREFLIGHT_CHECK_CHOICES and text not in requested:
            requested.append(text)
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dryRun": config.dry_run,
        "hasLLM": config.has_llm(),
        "llmSource": config.llm_source,
        "archiveRequestedBackend": config.archive_backend,
        "archiveResolvedBackend": config.resolved_archive_backend(),
        "hasArchiveS3": config.has_archive_s3(),
        "archivePublicEnabled": config.archive_public_enabled(),
        "archivePublicBaseUrl": config.archive_public_base_url,
        "checks": {},
    }

    if "llm" in requested:
        report["checks"]["llm"] = run_llm_check(config)
    if "video_model" in requested:
        report["checks"]["video_model"] = run_video_model_check(config)
    if "archive_config" in requested:
        report["checks"]["archive_config"] = run_archive_config_check(config)
    if "archive_probe" in requested:
        report["checks"]["archive_probe"] = run_archive_probe_check(config)
    return report


def run_llm_check(config: AI8VideoConfig) -> dict:
    if not config.has_llm():
        return {
            "status": "error",
            "error": "未配置 AI8VIDEO_LLM_*，AI8video 核心模型不可用。",
        }
    llm = build_openai_compat_splitter(config)
    if llm is None:
        return {
            "status": "error",
            "error": "未能构建对话模型调用器，AI8video 核心不可用。",
        }
    started = time.time()
    try:
        result = llm("请只输出三个字：调用成功")
        return {
            "status": "ok",
            "elapsedMs": int((time.time() - started) * 1000),
            "preview": result.strip()[:120],
        }
    except Exception as exc:
        return {
            "status": "error",
            "elapsedMs": int((time.time() - started) * 1000),
            "error": str(exc),
        }


def run_video_model_check(config: AI8VideoConfig) -> dict:
    settings = load_video_model_settings(
        llm_base_url=config.llm_base_url,
        llm_api_key=config.llm_api_key,
    )
    return {
        "status": "ok" if settings.configured() else "error",
        "configured": settings.configured(),
        "template": settings.template,
        "model": settings.model,
        "baseUrl": settings.base_url,
        "source": settings.source,
        "error": "" if settings.configured() else "未配置视频鉴权，请在设置里补齐视频模型地址、密钥、模型名和模板。",
    }


def run_archive_config_check(config: AI8VideoConfig) -> dict:
    resolved = config.resolved_archive_backend()
    payload = {
        "status": "ok",
        "requestedBackend": config.archive_backend,
        "resolvedBackend": resolved,
        "archiveEnabled": config.archive_enabled(),
        "hasArchiveS3": config.has_archive_s3(),
        "archivePublicEnabled": config.archive_public_enabled(),
        "archivePublicBaseUrl": config.archive_public_base_url,
        "localDir": config.archive_local_dir,
    }
    if resolved == "s3":
        missing = []
        if not config.archive_s3_endpoint:
            missing.append("AI8VIDEO_ARCHIVE_S3_ENDPOINT")
        if not config.archive_s3_bucket:
            missing.append("AI8VIDEO_ARCHIVE_S3_BUCKET")
        if not config.archive_s3_access_key:
            missing.append("AI8VIDEO_ARCHIVE_S3_ACCESS_KEY")
        if not config.archive_s3_secret_key:
            missing.append("AI8VIDEO_ARCHIVE_S3_SECRET_KEY")
        payload.update({
            "bucket": config.archive_s3_bucket,
            "endpoint": config.archive_s3_endpoint,
            "region": config.archive_s3_region,
        })
        if missing:
            payload["status"] = "error"
            payload["missing"] = missing
            return payload
        try:
            import boto3  # noqa: F401
            payload["boto3Ready"] = True
        except ImportError as exc:
            payload["status"] = "error"
            payload["boto3Ready"] = False
            payload["error"] = str(exc)
        return payload
    if resolved == "local":
        payload["localDirExists"] = Path(config.archive_local_dir).exists()
        return payload
    payload["status"] = "skipped"
    payload["reason"] = "归档后端已关闭"
    return payload


def run_archive_probe_check(config: AI8VideoConfig) -> dict:
    resolved = config.resolved_archive_backend()
    started = time.time()
    if resolved == "none":
        return {"status": "skipped", "reason": "归档后端已关闭"}
    if resolved == "local":
        root = Path(config.archive_local_dir)
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".ai8video-archive-probe.txt"
        marker = f"archive probe {datetime.now(timezone.utc).isoformat()}"
        probe.write_text(marker, encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {
            "status": "ok",
            "elapsedMs": int((time.time() - started) * 1000),
            "resolvedBackend": resolved,
            "localDir": str(root),
        }
    if resolved != "s3":
        return {
            "status": "error",
            "elapsedMs": int((time.time() - started) * 1000),
            "error": f"unsupported archive backend: {resolved}",
        }
    if not config.has_archive_s3():
        return {
            "status": "skipped",
            "elapsedMs": int((time.time() - started) * 1000),
            "reason": "未配置完整 AI8VIDEO_ARCHIVE_S3_*",
        }
    try:
        import boto3
    except ImportError as exc:
        return {
            "status": "error",
            "elapsedMs": int((time.time() - started) * 1000),
            "error": f"boto3 not available: {exc}",
        }
    session = boto3.session.Session()
    client = session.client(
        "s3",
        endpoint_url=config.archive_s3_endpoint,
        region_name=config.archive_s3_region,
        aws_access_key_id=config.archive_s3_access_key,
        aws_secret_access_key=config.archive_s3_secret_key,
    )
    probe_key = build_archive_probe_key(config)
    body = f"AI8video archive probe {datetime.now(timezone.utc).isoformat()}".encode("utf-8")
    client.put_object(
        Bucket=config.archive_s3_bucket,
        Key=probe_key,
        Body=body,
        ContentType="text/plain; charset=utf-8",
    )
    client.delete_object(Bucket=config.archive_s3_bucket, Key=probe_key)
    response = {
        "status": "ok",
        "elapsedMs": int((time.time() - started) * 1000),
        "resolvedBackend": resolved,
        "bucket": config.archive_s3_bucket,
        "endpoint": config.archive_s3_endpoint,
        "probeKey": probe_key,
    }
    if config.archive_public_base_url:
        response["probeUrl"] = f"{config.archive_public_base_url.rstrip('/')}/{probe_key}"
    return response


def build_archive_probe_key(config: AI8VideoConfig) -> str:
    prefix = (config.archive_s3_prefix or "").strip("/")
    stamp = datetime.now(timezone.utc).strftime("%Y/%m/%d/%H%M%S")
    parts = [part for part in [prefix, "preflight", stamp, "ai8video-archive-probe.txt"] if part]
    return "/".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
