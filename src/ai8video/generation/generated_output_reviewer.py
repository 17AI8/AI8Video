from __future__ import annotations

import base64
import json
import mimetypes
import tempfile
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps

from ai8video.core.config import AI8VideoConfig
from ai8video.core.models import VideoPrompt
from ai8video.generation.prompt_trace import append_prompt_trace
from ai8video.generation.video_prompt_support import parse_json_object
from ai8video.integrations.http_client import api_request
from ai8video.integrations.llm_provider import normalize_chat_completions_url
from ai8video.media.ffmpeg_utils import probe_media_duration_seconds
from ai8video.media.video_segment_postprocess import extract_frame_at_time


MultimodalReviewCall = Callable[[Path, VideoPrompt, int, list[str]], dict[str, Any]]


class GeneratedOutputReviewer:
    def __init__(
        self,
        config: AI8VideoConfig,
        *,
        multimodal_call: MultimodalReviewCall | None = None,
    ) -> None:
        self.config = config
        self._multimodal_call = multimodal_call or self._call_multimodal

    def review(
        self,
        video_path: str | Path | None,
        video: VideoPrompt,
        *,
        expected_duration_seconds: int = 10,
        trace_session_id: str | None = None,
    ) -> dict[str, Any]:
        if self.config.dry_run:
            return _simulated_review(expected_duration_seconds)
        path = Path(str(video_path or "")).expanduser()
        if not path.is_file():
            return _unavailable_review(
                expected_duration_seconds,
                issues=["成片未落到可审查的本地文件"],
            )
        duration, technical_issues = _technical_review(path, expected_duration_seconds)
        if not self._multimodal_ready():
            return _unavailable_review(
                expected_duration_seconds,
                duration_seconds=duration,
                issues=technical_issues,
            )
        try:
            with tempfile.TemporaryDirectory(prefix="ai8video-output-review-") as tempdir:
                contact_sheet = _build_contact_sheet(path, Path(tempdir), duration or expected_duration_seconds)
                review = self._multimodal_call(contact_sheet, video, expected_duration_seconds, technical_issues)
        except Exception as exc:
            append_prompt_trace(
                "generated_output_review_error",
                session_id=trace_session_id,
                payload={"videoIndex": video.index, "errorType": exc.__class__.__name__, "error": str(exc)},
            )
            return _unavailable_review(
                expected_duration_seconds,
                duration_seconds=duration,
                issues=technical_issues,
            )
        normalized = _normalize_review(review, expected_duration_seconds, duration, technical_issues)
        append_prompt_trace(
            "generated_output_review_result",
            session_id=trace_session_id,
            payload={"videoIndex": video.index, "review": normalized},
        )
        return normalized

    def _multimodal_ready(self) -> bool:
        return bool(
            self.config.multimodal_base_url
            and self.config.multimodal_api_key
            and self.config.multimodal_model
        )

    def _call_multimodal(
        self,
        contact_sheet: Path,
        video: VideoPrompt,
        expected_duration_seconds: int,
        technical_issues: list[str],
    ) -> dict[str, Any]:
        prompt = _build_review_prompt(video, expected_duration_seconds, technical_issues)
        append_prompt_trace(
            "generated_output_review_model_input",
            payload={"videoIndex": video.index, "prompt": prompt},
        )
        response = api_request(
            "POST",
            normalize_chat_completions_url(self.config.multimodal_base_url or ""),
            headers={
                "Authorization": f"Bearer {self.config.multimodal_api_key}",
                "Content-Type": "application/json",
            },
            json=_multimodal_payload(self.config.multimodal_model or "", prompt, contact_sheet),
            timeout=self.config.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"成片审查模型请求失败（HTTP {response.status_code}）")
        raw = _response_text(response.json())
        append_prompt_trace(
            "generated_output_review_model_output",
            payload={"videoIndex": video.index, "raw": raw},
        )
        return parse_json_object(raw)


def _technical_review(path: Path, expected_duration_seconds: int) -> tuple[float | None, list[str]]:
    issues: list[str] = []
    if path.stat().st_size <= 0:
        issues.append("成片文件为空")
    try:
        duration = float(probe_media_duration_seconds(path) or 0)
    except Exception:
        duration = 0.0
    if duration <= 0:
        issues.append("无法读取成片时长")
        return None, issues
    tolerance = 1.5
    if abs(duration - expected_duration_seconds) > tolerance:
        issues.append(f"成片时长为 {duration:.1f} 秒，与目标 {expected_duration_seconds} 秒偏差过大")
    return duration, issues


def _build_contact_sheet(video_path: Path, work_dir: Path, duration_seconds: float) -> Path:
    frame_paths: list[Path] = []
    for index, timestamp in enumerate(_review_timestamps(duration_seconds), 1):
        frame_paths.append(
            extract_frame_at_time(
                video_path,
                work_dir / f"frame-{index}.png",
                time_seconds=timestamp,
            )
        )
    canvas = Image.new("RGB", (960, 960), "black")
    for index, frame_path in enumerate(frame_paths):
        with Image.open(frame_path) as source:
            image = ImageOps.contain(source.convert("RGB"), (470, 470))
        x = (index % 2) * 480 + (480 - image.width) // 2
        y = (index // 2) * 480 + (480 - image.height) // 2
        canvas.paste(image, (x, y))
    target = work_dir / "contact-sheet.jpg"
    canvas.save(target, format="JPEG", quality=88, optimize=True)
    return target


def _review_timestamps(duration_seconds: float) -> list[float]:
    duration = max(1.0, float(duration_seconds or 10))
    latest = max(0.1, duration - 0.15)
    return [min(latest, max(0.1, duration * ratio)) for ratio in (0.08, 0.35, 0.65, 0.92)]


def _build_review_prompt(video: VideoPrompt, expected_duration_seconds: int, technical_issues: list[str]) -> str:
    return f"""你是 AI8video 的成片 Reviewer Specialist Agent。

根据四帧时间线宫格、目标提示词和技术检查，找出真实问题与下一条可复用的优化空间。
反馈只能优化下一条独立视频，不能要求重新生成当前条，也不能把当前条主题复制到下一条。

要求：
1. 检查主体一致性、动作连贯性、构图、镜头节奏、可读文字/畸形、视觉噪声和首秒吸引力。
2. issues 只写宫格中可见或技术检查确认的问题；没有问题可为空。
3. improvements 至少给出一条可迁移的优化方向。
4. next_prompt_constraints 写成下一条提示词可直接执行的约束，最多 5 条。
5. passes 仅表示当前成片是否达到可交付标准。
6. 只返回严格 JSON 对象：
{{"passes":true,"issues":[],"improvements":["..."],"next_prompt_constraints":["..."]}}

目标时长：{expected_duration_seconds} 秒
技术检查：{json.dumps(technical_issues, ensure_ascii=False)}
当前标题：{video.title}
当前提示词：{video.prompt[:8000]}
"""


def _multimodal_payload(model: str, prompt: str, contact_sheet: Path) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是短视频成片质量 Reviewer，只返回严格 JSON。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_data_url(contact_sheet)}},
                ],
            },
        ],
        "temperature": 0.1,
    }


def _normalize_review(
    review: dict[str, Any],
    expected_duration_seconds: int,
    duration_seconds: float | None,
    technical_issues: list[str],
) -> dict[str, Any]:
    if not isinstance(review.get("passes"), bool):
        raise ValueError("成片审查缺少 passes")
    issues = _dedupe(technical_issues + _string_list(review.get("issues")))
    improvements = _string_list(review.get("improvements"))
    constraints = _dedupe(_technical_constraints(technical_issues) + _string_list(review.get("next_prompt_constraints")))[:5]
    if not improvements and not constraints:
        raise ValueError("成片审查没有返回可迭代的优化项")
    return {
        "status": "completed",
        "passes": bool(review.get("passes")) and not issues,
        "issues": issues,
        "improvements": improvements,
        "nextPromptConstraints": constraints,
        "durationSeconds": None if duration_seconds is None else round(duration_seconds, 3),
        "expectedDurationSeconds": expected_duration_seconds,
        "reviewSource": "multimodal_contact_sheet",
    }


def _unavailable_review(
    expected_duration_seconds: int,
    *,
    duration_seconds: float | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    normalized_issues = _dedupe(issues or [])
    constraints = _technical_constraints(normalized_issues)
    return {
        "status": "partial" if constraints else "unavailable",
        "passes": False if constraints else None,
        "issues": normalized_issues,
        "improvements": [],
        "nextPromptConstraints": constraints,
        "durationSeconds": None if duration_seconds is None else round(duration_seconds, 3),
        "expectedDurationSeconds": expected_duration_seconds,
        "reviewSource": "technical_only" if constraints else "unavailable",
    }


def _simulated_review(expected_duration_seconds: int) -> dict[str, Any]:
    return {
        "status": "simulated",
        "passes": None,
        "issues": [],
        "improvements": [],
        "nextPromptConstraints": [],
        "durationSeconds": None,
        "expectedDurationSeconds": expected_duration_seconds,
        "reviewSource": "dry_run",
    }


def _technical_constraints(issues: list[str]) -> list[str]:
    constraints: list[str] = []
    if any("时长" in issue for issue in issues):
        constraints.append("下一条必须严格按 10 秒时间线组织镜头，避免成片过短或超时。")
    return constraints


def _response_text(data: Any) -> str:
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        raise ValueError("成片审查模型响应缺少 choices")
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    raise ValueError("成片审查模型响应缺少文本内容")


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:220] for item in value if str(item).strip()][:8]


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
