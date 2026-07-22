from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai8video.media.ffmpeg_utils import resolve_ffmpeg_bin
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root


HTML_MOTION_REVIEW_ROOT = (USER_FILE_ROOT / "HTML动效" / "reviews").resolve()


def prepare_html_motion_review(
    video_path: Path,
    relative_key: str,
    render_candidate: Callable[[Path], dict[str, Any]],
    result_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = video_path.resolve()
    review_dir = _review_dir(relative_key)
    review_dir.mkdir(parents=True, exist_ok=True)
    base = review_dir / f"base{source.suffix or '.mp4'}"
    candidate = review_dir / f"candidate{source.suffix or '.mp4'}"
    temporary = review_dir / f"candidate.generating{source.suffix or '.mp4'}"
    if not base.is_file():
        shutil.copy2(source, base)
    # 新任务必须使旧候选失效，避免失败后仍可确认上一轮的动效。
    candidate.unlink(missing_ok=True)
    (review_dir / "review.json").unlink(missing_ok=True)
    temporary.unlink(missing_ok=True)
    shutil.copy2(base, temporary)
    try:
        result = {**render_candidate(temporary), **(result_metadata or {})}
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    if result.get("status") != "applied":
        temporary.unlink(missing_ok=True)
        return {
            **result,
            "status": "preview_failed",
            "reviewReady": False,
        }
    temporary.replace(candidate)
    composition_html = result.pop("compositionHtml", None)
    if isinstance(composition_html, str) and composition_html.strip():
        (review_dir / "composition.html").write_text(composition_html, encoding="utf-8")
    else:
        (review_dir / "composition.html").unlink(missing_ok=True)
    review_id = review_dir.name
    prepared_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "reviewId": review_id,
        "relativeKey": relative_key,
        "candidateName": candidate.name,
        "preparedAt": prepared_at,
        "renderResult": result,
    }
    _write_json(review_dir / "review.json", payload)
    return {
        **result,
        "status": "preview_ready",
        "reason": "HTML 动效预览已生成，等待确认烧录",
        "reviewReady": True,
        "reviewId": review_id,
        "preparedAt": prepared_at,
        "previewUrl": f"/api/user-generated-results/html-motion-preview/{review_id}",
    }


def confirm_html_motion_review(video_path: Path, relative_key: str) -> dict[str, Any]:
    source = video_path.resolve()
    review_dir = _review_dir(relative_key)
    payload = _load_json(review_dir / "review.json")
    candidate = review_dir / f"candidate{source.suffix or '.mp4'}"
    if payload.get("relativeKey") != relative_key or not candidate.is_file():
        raise LookupError("请先重新生成 HTML 动效预览")
    temporary = source.with_name(f".{source.name}.html-motion-confirming")
    shutil.copy2(candidate, temporary)
    temporary.replace(source)
    render_result = payload.get("renderResult")
    result = dict(render_result) if isinstance(render_result, dict) else {}
    result.update(
        {
            "status": "applied",
            "reason": "HTML 动效已确认烧录",
            "reviewReady": False,
            "reviewId": review_dir.name,
            "confirmedAt": datetime.now(timezone.utc).isoformat(),
        }
    )
    payload["confirmedAt"] = result["confirmedAt"]
    _write_json(review_dir / "review.json", payload)
    return result


def html_motion_review_status(relative_key: str) -> dict[str, Any]:
    review_dir = _review_dir(relative_key)
    payload = _load_json(review_dir / "review.json")
    candidate = review_dir / str(payload.get("candidateName") or "")
    ready = bool(
        payload.get("relativeKey") == relative_key
        and not payload.get("confirmedAt")
        and candidate.is_file()
    )
    return {
        "ok": True,
        "reviewReady": ready,
        "reviewId": review_dir.name if ready else "",
        "previewUrl": (
            f"/api/user-generated-results/html-motion-preview/{review_dir.name}" if ready else ""
        ),
        "preparedAt": payload.get("preparedAt") if ready else None,
    }


def sync_html_motion_review_audio(
    video_path: Path,
    relative_key: str,
    *,
    ffmpeg_bin: str | None = None,
) -> dict[str, Any]:
    source = video_path.resolve()
    review_dir = _review_dir(relative_key)
    payload = _load_json(review_dir / "review.json")
    candidate = review_dir / str(payload.get("candidateName") or "")
    if payload.get("relativeKey") != relative_key or not candidate.is_file():
        return {"status": "skipped", "reason": "HTML 动效候选不存在"}
    base = review_dir / f"base{source.suffix or '.mp4'}"
    targets = [target for target in (base, candidate) if target.is_file()]
    ffmpeg = resolve_ffmpeg_bin(ffmpeg_bin)
    try:
        for target in targets:
            _sync_video_audio_from_source(target, source, ffmpeg)
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)[-500:]}
    return {
        "status": "synced",
        "reviewId": review_dir.name,
        "previewUrl": f"/api/user-generated-results/html-motion-preview/{review_dir.name}",
        "syncedTargets": len(targets),
    }


def _sync_video_audio_from_source(target: Path, source: Path, ffmpeg: str) -> None:
    temporary = target.with_name(f"{target.stem}.audio-syncing{target.suffix}")
    temporary.unlink(missing_ok=True)
    cmd = [
        ffmpeg, "-y",
        "-i", str(target),
        "-i", str(source),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "copy",
        "-shortest",
        "-movflags", "+faststart",
        str(temporary),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def resolve_html_motion_review_video(review_id: str) -> Path:
    normalized = str(review_id or "").strip().lower()
    if len(normalized) != 32 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("动效预览标识不合法")
    review_dir = (_review_root() / normalized).resolve()
    _assert_within_review_root(review_dir)
    payload = _load_json(review_dir / "review.json")
    candidate = review_dir / str(payload.get("candidateName") or "")
    if not candidate.is_file():
        raise FileNotFoundError("动效预览不存在")
    return candidate


def _review_dir(relative_key: str) -> Path:
    ensure_user_file_root()
    root = _review_root()
    root.mkdir(parents=True, exist_ok=True)
    review_id = hashlib.sha256(relative_key.encode("utf-8")).hexdigest()[:32]
    path = (root / review_id).resolve()
    _assert_within_review_root(path)
    return path


def _assert_within_review_root(path: Path) -> None:
    if not path.is_relative_to(_review_root()):
        raise ValueError("HTML 动效预览目录越界")


def _review_root() -> Path:
    return HTML_MOTION_REVIEW_ROOT.resolve()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.writing")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
