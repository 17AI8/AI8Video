from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from ai8video.core.models import ArchivedAsset, GenerationOutcome, ParsedRequest, QuickVideoJob, VideoPrompt
from ai8video.media.video_segment_postprocess import extract_tail_frame


TAIL_FRAME_CHAIN_PROMPT_SUFFIX = "最后一秒主体必须正对镜头。"
_FIRST_TIMELINE_BLOCK_PATTERN = re.compile(
    r"(?P<header>【\s*0\s*[-~至]\s*[^】]+秒\s*】)"
    r"(?P<body>.*?)"
    r"(?=(?:\n\s*【\s*\d)|\Z)",
    re.DOTALL,
)
_SPEECH_FIELD_PATTERN = re.compile(
    r"^\s*[-•]?\s*(?P<label>语气|台词/口播)\s*[：:]\s*(?P<value>.*)$",
)


def append_tail_frame_chain_prompt(video: VideoPrompt) -> VideoPrompt:
    prompt = _replace_first_timeline_block(str(video.prompt or "").strip())
    if TAIL_FRAME_CHAIN_PROMPT_SUFFIX not in prompt:
        prompt = f"{prompt}\n{TAIL_FRAME_CHAIN_PROMPT_SUFFIX}".strip()
    return replace(video, prompt=prompt)


def _replace_first_timeline_block(prompt: str) -> str:
    first_block = _FIRST_TIMELINE_BLOCK_PATTERN.search(prompt)
    if first_block is None:
        return prompt

    speech_fields: dict[str, str] = {}
    for raw_line in first_block.group("body").splitlines():
        field_match = _SPEECH_FIELD_PATTERN.match(raw_line)
        if field_match is None:
            continue
        label = field_match.group("label")
        value = _strip_repeated_field_label(field_match.group("value"), label)
        if value and label not in speech_fields:
            speech_fields[label] = value

    replacement_lines = [
        first_block.group("header"),
        "画面从参考图开始",
    ]
    if speech_fields.get("语气"):
        replacement_lines.append(f"- 语气：{speech_fields['语气']}")
    if speech_fields.get("台词/口播"):
        replacement_lines.append(f"- 台词/口播：{speech_fields['台词/口播']}")

    return (
        prompt[:first_block.start()]
        + "\n".join(replacement_lines)
        + prompt[first_block.end():]
    ).strip()


def _strip_repeated_field_label(value: str, label: str) -> str:
    normalized_value = str(value or "").strip()
    repeated_label_pattern = re.compile(
        rf"^(?:{re.escape(label)})\s*[：:]\s*",
    )
    while repeated_label_pattern.match(normalized_value):
        normalized_value = repeated_label_pattern.sub("", normalized_value, count=1).strip()
    return normalized_value


def tail_frame_chain_result_succeeded(
    job: QuickVideoJob,
    outcome: GenerationOutcome,
    archive: ArchivedAsset,
) -> bool:
    return (
        str(job.status or "").strip().lower() in {"succeeded", "completed"}
        and str(outcome.status or "").strip().lower() in {"succeeded", "completed"}
        and str(archive.status or "").strip().lower() in {
            "archived",
            "stored",
            "simulated",
            "disabled",
            "succeeded",
            "completed",
        }
    )


def build_next_tail_frame_request(
    request: ParsedRequest,
    job: QuickVideoJob,
    archive: ArchivedAsset,
    output_path: Path,
) -> ParsedRequest:
    source = str(archive.local_path or job.local_video_path or "").strip()
    if not source:
        raise RuntimeError("传尾帧模式无法继续：上一条视频没有本地成片")
    tail_frame = extract_tail_frame(Path(source), output_path)
    return replace(
        request,
        reference_image=str(tail_frame),
        reference_image_custom_prompt=None,
        reference_image_transform_options=None,
        concurrent_generation=False,
    )
