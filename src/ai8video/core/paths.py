from __future__ import annotations

import os
from pathlib import Path


def discover_project_root() -> Path:
    configured = (os.getenv("AI8VIDEO_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    candidates = (Path.cwd(), *Path(__file__).resolve().parents)
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file():
            return candidate.resolve()
    return Path.cwd().resolve()


PROJECT_ROOT = discover_project_root()
SRC_ROOT = (PROJECT_ROOT / "src").resolve()
