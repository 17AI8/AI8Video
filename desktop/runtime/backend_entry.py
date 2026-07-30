from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


DEFAULTS_DIR_NAME = "runtime-defaults"


def bundle_root() -> Path:
    bundled = getattr(sys, "_MEIPASS", "")
    if bundled:
        return Path(bundled).resolve()
    return Path(__file__).resolve().parent


def runtime_home() -> Path:
    configured = str(os.getenv("AI8VIDEO_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / "AI8video").resolve()


def copy_missing_defaults(source_root: Path, target_root: Path) -> None:
    if not source_root.is_dir():
        return
    for source in source_root.rglob("*"):
        if not source.is_file():
            continue
        target = target_root / source.relative_to(source_root)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def configure_runtime(source_root: Path | None = None) -> Path:
    home = runtime_home()
    home.mkdir(parents=True, exist_ok=True)
    defaults = (source_root or bundle_root()) / DEFAULTS_DIR_NAME
    copy_missing_defaults(defaults, home)
    os.environ["AI8VIDEO_HOME"] = str(home)
    os.environ.setdefault("AI8VIDEO_DISABLE_MYKEY", "1")
    os.chdir(home)
    return home


def runtime_self_check() -> dict[str, object]:
    import av
    import boto3
    import ctranslate2
    import onnxruntime
    import psycopg
    import sherpa_onnx
    from PIL import Image
    from faster_whisper import WhisperModel

    return {
        "ok": True,
        "modules": [
            av.__name__,
            boto3.__name__,
            ctranslate2.__name__,
            Image.__name__.split(".", 1)[0],
            onnxruntime.__name__,
            psycopg.__name__,
            sherpa_onnx.__name__,
            WhisperModel.__module__.split(".", 1)[0],
        ],
    }


def main() -> int:
    configure_runtime()
    if sys.argv[1:] == ["--runtime-self-check"]:
        print(json.dumps(runtime_self_check(), ensure_ascii=False))
        return 0
    from ai8video.interfaces.web.app import main as web_main

    return web_main()


if __name__ == "__main__":
    raise SystemExit(main())
