from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.application import runtime as application_runtime
from ai8video.media.motion import html_motion_overlay


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRY_PATH = PROJECT_ROOT / "desktop" / "runtime" / "backend_entry.py"
STAGE_RELEASE_PATH = PROJECT_ROOT / "desktop" / "runtime" / "stage_release.py"


def load_python_module(name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块：{module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesktopRuntimeTests(unittest.TestCase):
    def test_runtime_seeds_defaults_without_overwriting_user_files(self) -> None:
        entry = load_python_module("ai8video_backend_entry", ENTRY_PATH)
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as home_dir:
            source = Path(source_dir)
            home = Path(home_dir)
            default_font = source / "runtime-defaults" / "用户字体" / "内置字体" / "font.otf"
            default_font.parent.mkdir(parents=True)
            default_font.write_bytes(b"first")
            with patch.dict(os.environ, {"AI8VIDEO_HOME": str(home)}, clear=False):
                try:
                    self.assertEqual(entry.configure_runtime(source), home.resolve())
                    installed_font = home / "用户字体" / "内置字体" / "font.otf"
                    self.assertEqual(installed_font.read_bytes(), b"first")
                    installed_font.write_bytes(b"user-version")
                    default_font.write_bytes(b"second")
                    entry.configure_runtime(source)
                    self.assertEqual(installed_font.read_bytes(), b"user-version")
                    self.assertEqual(os.environ["AI8VIDEO_DISABLE_MYKEY"], "1")
                finally:
                    os.chdir(original_cwd)

    @unittest.skipIf(os.name == "nt", "Windows 默认测试权限不保证可创建符号链接")
    def test_release_staging_preserves_backend_symlinks(self) -> None:
        stage_release = load_python_module("ai8video_stage_release", STAGE_RELEASE_PATH)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            library = source / "library.dylib"
            library.write_bytes(b"library")
            (source / "library-current.dylib").symlink_to(library.name)

            stage_release.replace_directory(source, target)

            copied_link = target / "library-current.dylib"
            self.assertTrue(copied_link.is_symlink())
            self.assertEqual(copied_link.readlink(), Path(library.name))

    def test_hyperframes_cli_can_come_from_packaged_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cli_path = Path(temp_dir) / "cli.js"
            cli_path.write_text("", encoding="utf-8")
            with patch.dict(os.environ, {"AI8VIDEO_HYPERFRAMES_CLI": str(cli_path)}):
                self.assertEqual(html_motion_overlay._hyperframes_cli_path(), cli_path)

    def test_runtime_identity_comes_from_environment(self) -> None:
        values = {
            "AI8VIDEO_RUNTIME_MODE": "desktop",
            "AI8VIDEO_RUNTIME_INSTANCE": "desktop-instance",
        }
        with patch.dict(os.environ, values):
            self.assertEqual(application_runtime._runtime_mode(), "desktop")
            self.assertEqual(application_runtime._runtime_instance(), "desktop-instance")


if __name__ == "__main__":
    unittest.main()
