from __future__ import annotations

import hashlib
import inspect
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ai8video.core.config import AI8VideoConfig
from ai8video.media.ffmpeg_utils import resolve_ffmpeg_bin
from ai8video.core.models import ArchivedAsset, VideoPrompt, ParsedRequest, QuickVideoJob, GenerationOutcome
from ai8video.assets.user_generated_results import ensure_user_generated_result_dir
from ai8video.assets.user_generated_previews import generate_preview_for_video
from ai8video.assets.user_recycle_bin import save_failed_video_task
from ai8video.media.background_music import (
    background_music_volume,
    file_meta,
    mix_background_music,
    preserve_original_audio_enabled,
)
from ai8video.generation.business_prompt import sanitize_internal_fidelity_notes
from ai8video.media.local_tts import attach_local_tts_to_video, extract_dialogue_text, prepare_narration_text
from ai8video.media.video_encoding import append_video_postprocess_encoding_args
from ai8video.media.motion.html_motion_overlay import manual_only_html_motion_result
from ai8video.media.video_text_overlay import apply_video_text_overlay


DEFAULT_VIDEO_START_TRIM_SECONDS = 0.1


def archive_with_progress(
    callback: Any,
    *args: Any,
    progress_session_id: str | None = None,
    **kwargs: Any,
) -> ArchivedAsset:
    """仅向支持新参数的归档器传递进度会话，兼容既有扩展实现。"""
    if progress_session_id and _accepts_keyword(callback, "progress_session_id"):
        kwargs["progress_session_id"] = progress_session_id
    return callback(*args, **kwargs)


def _accepts_keyword(callback: Any, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == keyword or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def trim_video_start(
    video_path: Path | str,
    *,
    start_seconds: float = DEFAULT_VIDEO_START_TRIM_SECONDS,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    source = Path(video_path)
    if not source.is_file():
        raise ValueError("视频文件不存在")

    seconds = float(start_seconds)
    if seconds <= 0:
        return {
            "enabled": False,
            "status": "skipped",
            "reason": "trim seconds <= 0",
        }

    temp_target = source.with_name(f"{source.stem}.trim-start.tmp{source.suffix or '.mp4'}")
    if temp_target.exists():
        temp_target.unlink()

    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{seconds:.3f}",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
    ]
    video_encoding = append_video_postprocess_encoding_args(cmd)
    cmd.extend([
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(temp_target),
    ])
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        if not temp_target.is_file() or temp_target.stat().st_size <= 0:
            raise RuntimeError("trim output is empty")
        os.replace(temp_target, source)
    except Exception as exc:
        if temp_target.exists():
            try:
                temp_target.unlink()
            except OSError:
                pass
        detail = _video_postprocess_error_detail(exc)
        raise RuntimeError(f"视频开头裁剪失败：{detail}") from exc

    return {
        "enabled": True,
        "status": "trimmed",
        "trimStartSeconds": seconds,
        "ffmpeg": ffmpeg,
        "videoEncoding": video_encoding,
    }


def _video_postprocess_error_detail(exc: Exception) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return "FFmpeg 处理超时"
    if isinstance(exc, FileNotFoundError):
        return "未找到 FFmpeg 可执行文件"
    if isinstance(exc, subprocess.CalledProcessError):
        output = str(exc.stderr or exc.stdout or "").strip()
        if output:
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            if lines:
                return " ".join(lines[-3:])[:500]
    return (str(exc).strip() or exc.__class__.__name__)[:500]


class VideoAssetArchiver:
    def __init__(self, config: AI8VideoConfig):
        self.config = config
        self.local_root = Path(config.archive_local_dir)

    def archive(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        job: QuickVideoJob,
        outcome: GenerationOutcome,
        *,
        progress_session_id: str | None = None,
    ) -> ArchivedAsset:
        backend = self._resolve_backend()
        if backend == "none":
            return ArchivedAsset(
                video_index=video.index,
                job_id=job.job_id,
                backend="none",
                status="disabled",
            )
        if not job.video_url or "example.invalid" in job.video_url:
            return self._simulate_archive(request, video, job, outcome, backend)
        if backend == "local":
            return self._archive_local(request, video, job, outcome, progress_session_id=progress_session_id)
        if backend == "s3":
            return self._archive_s3(request, video, job, outcome, progress_session_id=progress_session_id)
        raise RuntimeError(f"Unsupported archive backend: {backend}")

    def archive_local_file(
        self,
        source_video: Path | str,
        request: ParsedRequest,
        video: VideoPrompt,
        job: QuickVideoJob,
        outcome: GenerationOutcome,
        *,
        extra_meta: dict[str, Any] | None = None,
        progress_session_id: str | None = None,
    ) -> ArchivedAsset:
        source = Path(source_video)
        if not source.is_file():
            raise RuntimeError("本地合并视频不存在，无法归档")
        backend = self._resolve_backend()
        if backend == "none":
            return ArchivedAsset(
                video_index=video.index,
                job_id=job.job_id,
                backend="none",
                status="disabled",
                meta=extra_meta or {},
            )
        if backend == "s3":
            return self._archive_local_file_to_s3(
                source,
                request,
                video,
                job,
                outcome,
                extra_meta=extra_meta,
                progress_session_id=progress_session_id,
            )
        if backend != "local":
            raise RuntimeError(f"Unsupported archive backend: {backend}")

        self.local_root.mkdir(parents=True, exist_ok=True)
        result_root = ensure_user_generated_result_dir()
        video_name = Path(self._build_video_key(job, video)).name
        result_video_key = self._result_video_key(video, video_name)
        result_video = result_root / result_video_key
        result_video.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != result_video.resolve():
            try:
                video_meta, postprocess = self._apply_video_postprocess(
                    source,
                    request=request,
                    video=video,
                    job=job,
                    progress_session_id=progress_session_id,
                )
            except Exception as exc:
                save_failed_video_task(
                    video=video,
                    job=job,
                    reason=str(exc),
                    videos=[source],
                    meta={"source": "merged-local-file", **(extra_meta or {})},
                )
                raise
            shutil.move(str(source), str(result_video))
        else:
            video_meta, postprocess = self._apply_video_postprocess(
                result_video,
                request=request,
                video=video,
                job=job,
                progress_session_id=progress_session_id,
            )

        cover_name = Path(self._build_cover_key(job, video)).name
        cover_key = self._result_cover_key(result_video_key, cover_name)
        result_cover = result_root / cover_key
        result_cover.parent.mkdir(parents=True, exist_ok=True)
        cover_generation_meta = self._extract_cover_frame(result_video, result_cover)
        cover_url = self._display_url("local", cover_key) if cover_generation_meta.get("status") == "generated" else None
        if not cover_url:
            cover_key = None
            result_cover = None
        preview_generation_meta = generate_preview_for_video(result_video, result_root, result_video_key)

        manifest_path = self._write_manifest(
            request,
            video,
            job,
            outcome,
            "local",
            result_video,
            result_cover,
            background_music_result=postprocess["backgroundMusic"],
            text_overlay_result=postprocess["textOverlay"],
            start_trim_result=postprocess["startTrim"],
            html_motion_result=postprocess["htmlMotionOverlay"],
            local_tts_result=postprocess["localTts"],
            postprocess_meta=extra_meta,
        )
        return ArchivedAsset(
            video_index=video.index,
            job_id=job.job_id,
            backend="local",
            status="archived",
            archive_key=result_video_key,
            archive_url=self._display_url("local", result_video_key),
            archive_cover_key=cover_key,
            archive_cover_url=cover_url,
            local_path=str(result_video),
            local_cover_path=None if result_cover is None else str(result_cover),
            manifest_path=str(manifest_path),
            size_bytes=video_meta["size_bytes"],
            sha256=video_meta["sha256"],
            meta={
                "source": "merged-local-file",
                **postprocess,
                "coverGeneration": cover_generation_meta,
                "previewGeneration": preview_generation_meta,
                **(extra_meta or {}),
            },
        )

    @staticmethod
    def _result_video_key(video: VideoPrompt, filename: str) -> str:
        subdir = Path(str(video.archive_subdir or "video").strip() or "video")
        if subdir.is_absolute() or ".." in subdir.parts:
            raise ValueError("用户生成结果归档目录无效")
        return (subdir / filename).as_posix()

    @staticmethod
    def _result_cover_key(video_key: str, filename: str) -> str:
        parts = list(Path(video_key).parts)
        if "video" not in parts:
            return (Path("cover") / filename).as_posix()
        video_index = parts.index("video")
        return (Path(*parts[:video_index], "cover") / filename).as_posix()

    def _resolve_backend(self) -> str:
        return self.config.resolved_archive_backend()

    def _simulate_archive(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        job: QuickVideoJob,
        outcome: GenerationOutcome,
        backend: str,
    ) -> ArchivedAsset:
        archive_key = self._build_video_key(job, video)
        manifest_path = self.local_root / f"{job.job_id}-manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "mode": "simulated",
            "request": request.__dict__,
            "video": video.__dict__,
            "job": job.__dict__,
            "generation": outcome.__dict__,
            "archiveKey": archive_key,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        with manifest_path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
        return ArchivedAsset(
            video_index=video.index,
            job_id=job.job_id,
            backend=backend,
            status="simulated",
            archive_key=archive_key,
            archive_url=self._display_url(backend, archive_key),
            manifest_path=str(manifest_path),
            meta={
                "reason": "当前是 dry-run 或签名 videoUrl 不可用，已写入模拟归档清单",
            },
        )

    def _archive_local(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        job: QuickVideoJob,
        outcome: GenerationOutcome,
        *,
        progress_session_id: str | None = None,
    ) -> ArchivedAsset:
        self.local_root.mkdir(parents=True, exist_ok=True)
        video_temp, video_meta = self._download_to_tempfile(job.video_url, suffix=".mp4")
        video_temp_path = Path(video_temp)
        try:
            video_meta, postprocess = self._apply_video_postprocess(
                video_temp_path,
                request=request,
                video=video,
                job=job,
                progress_session_id=progress_session_id,
            )
        except Exception as exc:
            save_failed_video_task(
                video=video,
                job=job,
                reason=str(exc),
                videos=[video_temp_path],
                meta={"sourceStorageKey": job.storage_key},
            )
            raise
        result_root = ensure_user_generated_result_dir()
        video_name = Path(self._build_video_key(job, video)).name
        result_video_key = self._result_video_key(video, video_name)
        result_video = result_root / result_video_key
        result_video.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(video_temp_path), result_video)

        result_cover = None
        cover_key = None
        cover_url = None
        cover_generation_meta: dict[str, Any] | None = None
        if job.cover_image_url:
            cover_temp, _cover_meta = self._download_to_tempfile(job.cover_image_url, suffix=".jpg")
            cover_name = Path(self._build_cover_key(job, video)).name
            cover_key = self._result_cover_key(result_video_key, cover_name)
            result_cover = result_root / cover_key
            result_cover.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(cover_temp, result_cover)
            cover_url = self._display_url("local", cover_key)
        else:
            cover_name = Path(self._build_cover_key(job, video)).name
            cover_key = self._result_cover_key(result_video_key, cover_name)
            result_cover = result_root / cover_key
            result_cover.parent.mkdir(parents=True, exist_ok=True)
            cover_generation_meta = self._extract_cover_frame(result_video, result_cover)
            if cover_generation_meta.get("status") == "generated":
                cover_url = self._display_url("local", cover_key)
            else:
                cover_key = None
                result_cover = None
        preview_generation_meta = generate_preview_for_video(result_video, result_root, result_video_key)

        manifest_path = self._write_manifest(
            request,
            video,
            job,
            outcome,
            "local",
            result_video,
            result_cover,
            background_music_result=postprocess["backgroundMusic"],
            text_overlay_result=postprocess["textOverlay"],
            start_trim_result=postprocess["startTrim"],
            html_motion_result=postprocess["htmlMotionOverlay"],
            local_tts_result=postprocess["localTts"],
        )
        return ArchivedAsset(
            video_index=video.index,
            job_id=job.job_id,
            backend="local",
            status="archived",
            archive_key=result_video_key,
            archive_url=self._display_url("local", result_video_key),
            archive_cover_key=cover_key,
            archive_cover_url=cover_url,
            local_path=str(result_video),
            local_cover_path=None if result_cover is None else str(result_cover),
            manifest_path=str(manifest_path),
            size_bytes=video_meta["size_bytes"],
            sha256=video_meta["sha256"],
            meta={
                "sourceStorageKey": job.storage_key,
                **postprocess,
                "coverGeneration": cover_generation_meta,
                "previewGeneration": preview_generation_meta,
            },
        )

    def _archive_s3(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        job: QuickVideoJob,
        outcome: GenerationOutcome,
        *,
        progress_session_id: str | None = None,
    ) -> ArchivedAsset:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for AI8VIDEO_ARCHIVE_BACKEND=s3") from exc

        session = boto3.session.Session()
        client = session.client(
            "s3",
            endpoint_url=self.config.archive_s3_endpoint,
            region_name=self.config.archive_s3_region,
            aws_access_key_id=self.config.archive_s3_access_key,
            aws_secret_access_key=self.config.archive_s3_secret_key,
        )
        video_temp, video_meta = self._download_to_tempfile(job.video_url, suffix=".mp4")
        archive_key = self._build_video_key(job, video)
        try:
            video_meta, postprocess = self._apply_video_postprocess(
                video_temp,
                request=request,
                video=video,
                job=job,
                progress_session_id=progress_session_id,
            )
        except Exception as exc:
            save_failed_video_task(
                video=video,
                job=job,
                reason=str(exc),
                videos=[Path(video_temp)],
                meta={"sourceStorageKey": job.storage_key, "archiveBackend": "s3"},
            )
            raise
        try:
            self._upload_file_to_s3(client, video_temp, archive_key, content_type="video/mp4")
        finally:
            if os.path.exists(video_temp):
                os.unlink(video_temp)

        cover_key = None
        cover_url = None
        cover_generation_meta: dict[str, Any] | None = None
        if job.cover_image_url:
            suffix = Path(job.cover_image_url).suffix or ".jpg"
            cover_temp, _cover_meta = self._download_to_tempfile(job.cover_image_url, suffix=suffix)
            cover_key = self._build_cover_key(job, video, suffix=suffix)
            try:
                self._upload_file_to_s3(client, cover_temp, cover_key, content_type=_guess_content_type(cover_key))
            finally:
                if os.path.exists(cover_temp):
                    os.unlink(cover_temp)
            cover_url = self._display_url("s3", cover_key)
        else:
            cover_fd, cover_temp = tempfile.mkstemp(prefix="ai8video-cover-", suffix=".jpg")
            os.close(cover_fd)
            cover_path = Path(cover_temp)
            cover_generation_meta = self._extract_cover_frame(Path(video_temp), cover_path)
            if cover_generation_meta.get("status") == "generated":
                cover_key = self._build_cover_key(job, video, suffix=".jpg")
                try:
                    self._upload_file_to_s3(client, str(cover_path), cover_key, content_type="image/jpeg")
                finally:
                    if cover_path.exists():
                        cover_path.unlink()
                cover_url = self._display_url("s3", cover_key)
            elif cover_path.exists():
                cover_path.unlink()

        manifest_path = self._write_manifest(
            request,
            video,
            job,
            outcome,
            "s3",
            background_music_result=postprocess["backgroundMusic"],
            text_overlay_result=postprocess["textOverlay"],
            start_trim_result=postprocess["startTrim"],
            html_motion_result=postprocess["htmlMotionOverlay"],
            local_tts_result=postprocess["localTts"],
        )
        manifest_key = self._build_manifest_key(job, video)
        self._upload_file_to_s3(client, str(manifest_path), manifest_key, content_type="application/json")
        return ArchivedAsset(
            video_index=video.index,
            job_id=job.job_id,
            backend="s3",
            status="archived",
            archive_key=archive_key,
            archive_url=self._display_url("s3", archive_key),
            archive_cover_key=cover_key,
            archive_cover_url=cover_url,
            manifest_path=str(manifest_path),
            size_bytes=video_meta["size_bytes"],
            sha256=video_meta["sha256"],
            meta={
                "bucket": self.config.archive_s3_bucket,
                "endpoint": self.config.archive_s3_endpoint,
                "manifestKey": manifest_key,
                "sourceStorageKey": job.storage_key,
                **postprocess,
                "coverGeneration": cover_generation_meta,
            },
        )

    def _archive_local_file_to_s3(
        self,
        source: Path,
        request: ParsedRequest,
        video: VideoPrompt,
        job: QuickVideoJob,
        outcome: GenerationOutcome,
        *,
        extra_meta: dict[str, Any] | None = None,
        progress_session_id: str | None = None,
    ) -> ArchivedAsset:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for AI8VIDEO_ARCHIVE_BACKEND=s3") from exc

        session = boto3.session.Session()
        client = session.client(
            "s3",
            endpoint_url=self.config.archive_s3_endpoint,
            region_name=self.config.archive_s3_region,
            aws_access_key_id=self.config.archive_s3_access_key,
            aws_secret_access_key=self.config.archive_s3_secret_key,
        )
        archive_key = self._build_video_key(job, video)
        video_fd, video_temp = tempfile.mkstemp(prefix="ai8video-merged-archive-", suffix=".mp4")
        os.close(video_fd)
        video_path = Path(video_temp)
        try:
            shutil.copy2(source, video_path)
            try:
                video_meta, postprocess = self._apply_video_postprocess(
                    video_path,
                    request=request,
                    video=video,
                    job=job,
                    progress_session_id=progress_session_id,
                )
            except Exception as exc:
                save_failed_video_task(
                    video=video,
                    job=job,
                    reason=str(exc),
                    videos=[video_path],
                    meta={"source": "merged-local-file", "archiveBackend": "s3", **(extra_meta or {})},
                )
                raise
            self._upload_file_to_s3(client, str(video_path), archive_key, content_type="video/mp4")

            cover_key = None
            cover_url = None
            cover_generation_meta: dict[str, Any] | None = None
            cover_fd, cover_temp = tempfile.mkstemp(prefix="ai8video-merged-cover-", suffix=".jpg")
            os.close(cover_fd)
            cover_path = Path(cover_temp)
            try:
                cover_generation_meta = self._extract_cover_frame(video_path, cover_path)
                if cover_generation_meta.get("status") == "generated":
                    cover_key = self._build_cover_key(job, video, suffix=".jpg")
                    self._upload_file_to_s3(client, str(cover_path), cover_key, content_type="image/jpeg")
                    cover_url = self._display_url("s3", cover_key)
            finally:
                if cover_path.exists():
                    cover_path.unlink()

            manifest_path = self._write_manifest(
                request,
                video,
                job,
                outcome,
                "s3",
                background_music_result=postprocess["backgroundMusic"],
                text_overlay_result=postprocess["textOverlay"],
                start_trim_result=postprocess["startTrim"],
                html_motion_result=postprocess["htmlMotionOverlay"],
                local_tts_result=postprocess["localTts"],
                postprocess_meta=extra_meta,
            )
            manifest_key = self._build_manifest_key(job, video)
            self._upload_file_to_s3(client, str(manifest_path), manifest_key, content_type="application/json")
            return ArchivedAsset(
                video_index=video.index,
                job_id=job.job_id,
                backend="s3",
                status="archived",
                archive_key=archive_key,
                archive_url=self._display_url("s3", archive_key),
                archive_cover_key=cover_key,
                archive_cover_url=cover_url,
                manifest_path=str(manifest_path),
                size_bytes=video_meta["size_bytes"],
                sha256=video_meta["sha256"],
                meta={
                    "source": "merged-local-file",
                    "bucket": self.config.archive_s3_bucket,
                    "endpoint": self.config.archive_s3_endpoint,
                    "manifestKey": manifest_key,
                    **postprocess,
                    "coverGeneration": cover_generation_meta,
                    **(extra_meta or {}),
                },
            )
        finally:
            if video_path.exists():
                video_path.unlink()

    def _apply_video_postprocess(
        self,
        video_path: Path | str,
        *,
        request: ParsedRequest,
        video: VideoPrompt | None = None,
        job: QuickVideoJob | None = None,
        progress_session_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        target = Path(video_path)
        start_trim_result = trim_video_start(target)
        html_motion_result = manual_only_html_motion_result()
        text_overlay_result = apply_video_text_overlay(target)
        _raise_if_required_text_overlay_failed(text_overlay_result)
        narration_text = None
        if video is not None:
            narration_text = _local_tts_narration_text(job, video)
        preserve_original_audio = preserve_original_audio_enabled()
        local_tts_result = attach_local_tts_to_video(
            target,
            narration_text=narration_text,
            video_index=None if video is None else video.index,
            job_id=None if job is None else job.job_id,
            preserve_original_audio=preserve_original_audio,
        )
        if narration_text and local_tts_result.get("status") == "mixed":
            local_tts_result["narrationText"] = narration_text
        tts_replaced_audio = (
            local_tts_result.get("enabled") is True
            and local_tts_result.get("status") == "mixed"
            and local_tts_result.get("originalAudio") == "replaced"
        )
        background_music_result = mix_background_music(
            target,
            preserve_original_audio_override=True if tts_replaced_audio else None,
            preserved_audio_volume_override=(
                1.0 if tts_replaced_audio else None
            ),
        )
        return file_meta(target), {
            "startTrim": start_trim_result,
            "htmlMotionOverlay": html_motion_result,
            "textOverlay": text_overlay_result,
            "localTts": local_tts_result,
            "backgroundMusic": background_music_result,
        }

    def _upload_file_to_s3(self, client, file_path: str, key: str, content_type: str | None = None) -> None:
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        with open(file_path, "rb") as fh:
            if extra_args:
                client.upload_fileobj(fh, self.config.archive_s3_bucket, key, ExtraArgs=extra_args)
            else:
                client.upload_fileobj(fh, self.config.archive_s3_bucket, key)

    def _download_to_tempfile(self, url: str, suffix: str) -> tuple[str, dict[str, Any]]:
        fd, temp_path = tempfile.mkstemp(prefix="ai8video-archive-", suffix=suffix)
        os.close(fd)
        sha = hashlib.sha256()
        size_bytes = 0
        try:
            with requests.get(url, stream=True, timeout=self.config.archive_download_timeout_seconds) as response:
                response.raise_for_status()
                with open(temp_path, "wb") as fh:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        sha.update(chunk)
                        size_bytes += len(chunk)
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
        return temp_path, {"sha256": sha.hexdigest(), "size_bytes": size_bytes}

    def _write_manifest(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        job: QuickVideoJob,
        outcome: GenerationOutcome,
        backend: str,
        local_video: Path | None = None,
        local_cover: Path | None = None,
        background_music_result: dict[str, Any] | None = None,
        text_overlay_result: dict[str, Any] | None = None,
        start_trim_result: dict[str, Any] | None = None,
        html_motion_result: dict[str, Any] | None = None,
        local_tts_result: dict[str, Any] | None = None,
        postprocess_meta: dict[str, Any] | None = None,
    ) -> Path:
        self.local_root.mkdir(parents=True, exist_ok=True)
        manifest_path = self.local_root / f"{job.job_id}-manifest.json"
        payload = {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "backend": backend,
            "request": request.__dict__,
            "video": video.__dict__,
            "job": job.__dict__,
            "generation": outcome.__dict__,
            "localVideo": None if local_video is None else str(local_video),
            "localCover": None if local_cover is None else str(local_cover),
            "startTrim": start_trim_result,
            "htmlMotionOverlay": html_motion_result,
            "textOverlay": text_overlay_result,
            "localTts": local_tts_result,
            "backgroundMusic": background_music_result,
            "postprocess": postprocess_meta,
        }
        with manifest_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return manifest_path

    def _build_video_key(self, job: QuickVideoJob, video: VideoPrompt, suffix: str = ".mp4") -> str:
        day = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        prefix = self.config.archive_s3_prefix.strip("/")
        title = _slugify(sanitize_internal_fidelity_notes(video.title)) or f"video-{video.index:02d}"
        parts = [part for part in [prefix, day, "video", f"{job.video_index:02d}-{title}-{job.job_id}{suffix}"] if part]
        return "/".join(parts)

    def _build_cover_key(self, job: QuickVideoJob, video: VideoPrompt, suffix: str = ".jpg") -> str:
        day = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        prefix = self.config.archive_s3_prefix.strip("/")
        title = _slugify(sanitize_internal_fidelity_notes(video.title)) or f"video-{video.index:02d}"
        parts = [part for part in [prefix, day, "cover", f"{job.video_index:02d}-{title}-{job.job_id}{suffix}"] if part]
        return "/".join(parts)

    def _build_manifest_key(self, job: QuickVideoJob, video: VideoPrompt) -> str:
        day = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        prefix = self.config.archive_s3_prefix.strip("/")
        title = _slugify(sanitize_internal_fidelity_notes(video.title)) or f"video-{video.index:02d}"
        parts = [part for part in [prefix, day, "manifest", f"{job.video_index:02d}-{title}-{job.job_id}.json"] if part]
        return "/".join(parts)

    def _display_url(self, backend: str, key: str | None) -> str | None:
        if not key:
            return None
        if backend == "s3":
            if self.config.archive_public_base_url:
                return f"{self.config.archive_public_base_url.rstrip('/')}/{key}"
            return f"s3://{self.config.archive_s3_bucket}/{key}"
        return key

    def _extract_cover_frame(self, video_path: Path, cover_path: Path) -> dict[str, Any]:
        ffmpeg = resolve_ffmpeg_bin()
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "00:00:01",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(cover_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        except Exception as exc:
            if cover_path.exists():
                try:
                    cover_path.unlink()
                except OSError:
                    pass
            return {
                "status": "failed",
                "reason": str(exc)[:300],
            }
        if not cover_path.is_file() or cover_path.stat().st_size <= 0:
            return {
                "status": "failed",
                "reason": "cover frame output is empty",
            }
        return {
            "status": "generated",
            "source": "local_video_frame",
            "offsetSeconds": 1,
            "path": str(cover_path),
        }


def _slugify(value: str) -> str:
    cleaned = []
    for ch in value.lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif ch in {" ", "-", "_"}:
            cleaned.append("-")
    slug = "".join(cleaned).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:80]


def _guess_content_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _local_tts_narration_text(job: QuickVideoJob | None, video: VideoPrompt) -> str:
    if job is not None and isinstance(job.usage, dict):
        if "localTtsNarrationText" in job.usage:
            return str(job.usage.get("localTtsNarrationText") or "").strip()
    post_review = (video.keyword_guidance or {}).get("post_review")
    if isinstance(post_review, dict) and "narrationText" in post_review:
        return str(post_review.get("narrationText") or "").strip()
    dialogue = extract_dialogue_text(video.prompt)
    if dialogue:
        return prepare_narration_text(dialogue)
    return ""


def _tts_voiceover_volume_for_background_music() -> float:
    return 1.0


def _raise_if_required_text_overlay_failed(result: dict[str, Any]) -> None:
    if not result.get("enabled"):
        return
    status = str(result.get("status") or "").strip().lower()
    if status != "failed":
        return
    reason = str(result.get("reason") or "未知错误").strip()
    raise RuntimeError(f"花字烧录失败：{reason}")
