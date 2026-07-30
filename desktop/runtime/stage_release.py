from __future__ import annotations

import argparse
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def remove_directory(target: Path) -> None:
    for _attempt in range(3):
        if not target.exists():
            return
        shutil.rmtree(target, ignore_errors=True)
    if target.exists():
        raise OSError(f"无法清理目录：{target}")


def replace_directory(source: Path, target: Path) -> None:
    remove_directory(target)
    shutil.copytree(source, target, symlinks=True)


def stage_release(backend_dir: Path, target: Path) -> Path:
    backend = backend_dir.resolve()
    runtime = target.resolve()
    if not backend.is_dir():
        raise FileNotFoundError(f"冻结后端目录不存在：{backend}")
    remove_directory(runtime)
    runtime.mkdir(parents=True)
    replace_directory(backend, runtime / "backend")
    licenses = runtime / "licenses"
    licenses.mkdir()
    shutil.copy2(PROJECT_ROOT / "FONT_LICENSES.md", licenses / "FONT_LICENSES.md")
    replace_directory(PROJECT_ROOT / "licenses", licenses / "fonts")
    return runtime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="暂存 Electron 发布运行时")
    parser.add_argument("--backend-dir", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime = stage_release(args.backend_dir, args.target)
    print(runtime)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
