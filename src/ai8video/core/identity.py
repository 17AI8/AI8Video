from __future__ import annotations

import os
from collections.abc import MutableMapping


PRODUCT_NAME = "AI8video"
PRODUCT_SLUG = "ai8video"
ENV_PREFIX = "AI8VIDEO_"

# 旧名称只允许出现在这一兼容边界。新配置始终写入 ENV_PREFIX。
LEGACY_ENV_PREFIX = "AI8MINIVIDEO_"


def normalize_product_env_key(key: str) -> str:
    name = str(key or "").strip()
    if name.startswith(LEGACY_ENV_PREFIX):
        return f"{ENV_PREFIX}{name[len(LEGACY_ENV_PREFIX):]}"
    return name


def bridge_legacy_environment(
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    target = environ if environ is not None else os.environ
    migrated: dict[str, str] = {}
    for legacy_name, value in list(target.items()):
        current_name = normalize_product_env_key(legacy_name)
        if current_name == legacy_name or current_name in target:
            continue
        target[current_name] = value
        migrated[current_name] = legacy_name
    return migrated

