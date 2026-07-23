from __future__ import annotations

from typing import Any


# 仅用于读取历史本地结果；业务代码和新写入必须使用 video 领域字段。
_LEGACY_KEY_ALIASES = {
    "episode": "video",
    "episodes": "videos",
    "episodeCount": "videoCount",
    "episodeIndex": "videoIndex",
    "episodeTitle": "videoTitle",
    "episodePrompt": "videoPrompt",
    "episode_count": "video_count",
    "episode_index": "video_index",
    "episode_title": "video_title",
    "episode_prompt": "video_prompt",
    "rewrittenEpisodeIndex": "rewrittenVideoIndex",
    "rewriteEpisodeIndex": "rewriteVideoIndex",
    "rewrite_episode_index": "rewrite_video_index",
}

_LEGACY_MODES = {
    "multi_episode_script": "batch_videos",
    "single_prompt": "single_video",
}

_LEGACY_EVENTS = {
    "split_model_input": "video_planning_model_input",
    "split_model_output": "video_planning_model_output",
    "split_model_json_parse_error": "video_planning_model_json_parse_error",
    "split_model_json_repair_output": "video_planning_model_json_repair_output",
}


def normalize_legacy_video_payload(value: Any) -> Any:
    """Read old payloads into the current video-task schema without rewriting source files."""
    if isinstance(value, list):
        return [normalize_legacy_video_payload(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized = {
        key: normalize_legacy_video_payload(item)
        for key, item in value.items()
        if key not in _LEGACY_KEY_ALIASES
    }
    for key, item in value.items():
        current_key = _LEGACY_KEY_ALIASES.get(key)
        if not current_key or current_key in normalized:
            continue
        normalized[current_key] = normalize_legacy_video_payload(item)

    mode = normalized.get("mode")
    if isinstance(mode, str) and mode in _LEGACY_MODES:
        normalized["mode"] = _LEGACY_MODES[mode]
    event = normalized.get("event")
    if isinstance(event, str) and event in _LEGACY_EVENTS:
        normalized["event"] = _LEGACY_EVENTS[event]
    return normalized
