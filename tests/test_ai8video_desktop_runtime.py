from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.application import runtime as application_runtime
from ai8video.media.motion import html_motion_overlay


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRY_PATH = PROJECT_ROOT / "desktop" / "runtime" / "backend_entry.py"
STAGE_RELEASE_PATH = PROJECT_ROOT / "desktop" / "runtime" / "stage_release.py"
STAGE_NODE_RUNTIME_PATH = PROJECT_ROOT / "desktop" / "runtime" / "stage_node_runtime.mjs"
SIGNING_CONFIG_PATH = PROJECT_ROOT / "desktop" / "runtime" / "check_signing_config.mjs"
VERIFY_PACKAGED_RUNTIME_PATH = PROJECT_ROOT / "desktop" / "runtime" / "verify_packaged_runtime.mjs"
ELECTRON_PACKAGE_PATH = PROJECT_ROOT / "desktop" / "electron" / "package.json"
BACKEND_SPEC_PATH = PROJECT_ROOT / "desktop" / "runtime" / "ai8video_backend.spec"
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
SIGNING_ENV_NAMES = (
    "CSC_LINK",
    "CSC_KEY_PASSWORD",
    "APPLE_ID",
    "APPLE_APP_SPECIFIC_PASSWORD",
    "APPLE_TEAM_ID",
    "WIN_CSC_LINK",
    "WIN_CSC_KEY_PASSWORD",
    "GITHUB_OUTPUT",
    "GITHUB_STEP_SUMMARY",
)


def load_python_module(name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块：{module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_signing_check(platform: str, values: dict[str, str] | None = None):
    env = os.environ.copy()
    for name in SIGNING_ENV_NAMES:
        env.pop(name, None)
    env.update(values or {})
    return subprocess.run(
        ["node", str(SIGNING_CONFIG_PATH), platform],
        capture_output=True,
        text=True,
        env=env,
    )


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

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 验证发行 Node 运行时")
    def test_node_runtime_staging_prunes_development_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "node_modules"
            target = root / "runtime" / "node_modules"
            hyperframes = source / "hyperframes"
            (hyperframes / "dist").mkdir(parents=True)
            (hyperframes / "types").mkdir()
            (hyperframes / "dist" / "cli.js").write_text("export {};\n", encoding="utf-8")
            (hyperframes / "dist" / "cli.js.map").write_text("{}\n", encoding="utf-8")
            (hyperframes / "types" / "index.d.ts").write_text("export {};\n", encoding="utf-8")
            (hyperframes / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
            executable_shims = source / ".bin"
            executable_shims.mkdir()
            (executable_shims / "hyperframes").write_text("runner shim\n", encoding="utf-8")

            result = subprocess.run(
                ["node", str(STAGE_NODE_RUNTIME_PATH), str(source), str(target)],
                check=True,
                capture_output=True,
                text=True,
            )
            report = json.loads(result.stdout)

            self.assertEqual(report["prunedFiles"], 2)
            self.assertEqual(report["prunedBinEntries"], 1)
            self.assertTrue((target / "hyperframes" / "dist" / "cli.js").is_file())
            self.assertTrue((target / "hyperframes" / "package.json").is_file())
            self.assertFalse((target / ".bin").exists())
            self.assertFalse((target / "hyperframes" / "dist" / "cli.js.map").exists())
            self.assertFalse((target / "hyperframes" / "types" / "index.d.ts").exists())

    @unittest.skipUnless(
        shutil.which("node") and sys.platform in {"darwin", "win32"},
        "仅在桌面目标平台验证安装包运行时",
    )
    def test_packaged_runtime_rejects_executable_shims(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dist_root = Path(temp_dir) / "dist"
            if sys.platform == "darwin":
                runtime_root = (
                    dist_root / "mac-arm64" / "AI8video.app" / "Contents" / "Resources" / "runtime"
                )
                backend_name = "ai8video-backend"
            else:
                runtime_root = dist_root / "win-unpacked" / "resources" / "runtime"
                backend_name = "ai8video-backend.exe"

            (runtime_root / "backend").mkdir(parents=True)
            (runtime_root / "node_modules" / "hyperframes" / "dist").mkdir(parents=True)
            (runtime_root / "licenses").mkdir()
            (runtime_root / "backend" / backend_name).write_bytes(b"backend")
            (runtime_root / "node_modules" / "hyperframes" / "dist" / "cli.js").write_text(
                "export {};\n",
                encoding="utf-8",
            )
            (runtime_root / "licenses" / "FONT_LICENSES.md").write_text("licenses\n", encoding="utf-8")
            executable_shims = runtime_root / "node_modules" / ".bin"
            executable_shims.mkdir()
            (executable_shims / "hyperframes").write_text("runner shim\n", encoding="utf-8")

            rejected = subprocess.run(
                ["node", str(VERIFY_PACKAGED_RUNTIME_PATH), str(dist_root)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("npm .bin", rejected.stderr)

            shutil.rmtree(executable_shims)
            accepted = subprocess.run(
                ["node", str(VERIFY_PACKAGED_RUNTIME_PATH), str(dist_root)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

    @unittest.skipIf(os.name == "nt", "Windows 默认测试权限不保证可创建符号链接")
    @unittest.skipUnless(shutil.which("node") and sys.platform == "darwin", "仅在 macOS 验证符号链接边界")
    def test_packaged_runtime_rejects_external_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist_root = root / "dist"
            runtime_root = (
                dist_root / "mac-arm64" / "AI8video.app" / "Contents" / "Resources" / "runtime"
            )
            (runtime_root / "backend").mkdir(parents=True)
            hyperframes = runtime_root / "node_modules" / "hyperframes" / "dist"
            hyperframes.mkdir(parents=True)
            (runtime_root / "licenses").mkdir()
            (runtime_root / "backend" / "ai8video-backend").write_bytes(b"backend")
            (hyperframes / "cli.js").write_text("export {};\n", encoding="utf-8")
            (runtime_root / "licenses" / "FONT_LICENSES.md").write_text("licenses\n", encoding="utf-8")
            external_target = root / "github-runner-node_modules"
            external_target.mkdir()
            (hyperframes / "runner-link").symlink_to(external_target)

            result = subprocess.run(
                ["node", str(VERIFY_PACKAGED_RUNTIME_PATH), str(dist_root)],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("运行时外部", result.stderr)

    def test_electron_languages_cover_mac_and_windows_names(self) -> None:
        package = json.loads(ELECTRON_PACKAGE_PATH.read_text(encoding="utf-8"))
        languages = set(package["build"]["electronLanguages"])

        self.assertIn("en-US", languages)
        self.assertIn("zh-CN", languages)
        self.assertIn("zh_CN", languages)

    def test_electron_mac_build_keeps_adhoc_and_trusted_signing_lanes(self) -> None:
        package = json.loads(ELECTRON_PACKAGE_PATH.read_text(encoding="utf-8"))
        scripts = package["scripts"]

        self.assertIn("--config.mac.identity=-", scripts["dist:mac"])
        self.assertIn("--config.mac.hardenedRuntime=false", scripts["dist:mac"])
        self.assertNotIn("--config.mac.identity=-", scripts["dist:mac:signed"])
        self.assertIn("verify_packaged_runtime.mjs", scripts["dist:mac"])
        self.assertIn("verify_packaged_runtime.mjs", scripts["dist:mac:signed"])

    def test_desktop_release_excludes_removed_sherpa_runtime(self) -> None:
        project_config = PYPROJECT_PATH.read_text(encoding="utf-8")
        backend_spec = BACKEND_SPEC_PATH.read_text(encoding="utf-8")

        self.assertNotIn('"sherpa-onnx', project_config)
        for module in ("_pytest", "fsspec.conftest", "pytest", "sherpa_onnx"):
            self.assertIn(f'"{module}"', backend_spec)

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 验证签名配置")
    def test_signing_config_marks_empty_mac_build_unsigned(self) -> None:
        result = run_signing_check("mac")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "unsigned")

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 验证签名配置")
    def test_signing_config_rejects_partial_mac_credentials(self) -> None:
        result = run_signing_check("mac", {"CSC_LINK": "certificate"})

        self.assertEqual(result.returncode, 1)
        error = json.loads(result.stderr)
        self.assertEqual(error["group"], "signing")
        self.assertEqual(error["missing"], ["CSC_KEY_PASSWORD"])

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 验证签名配置")
    def test_signing_config_accepts_complete_mac_notarization(self) -> None:
        values = {
            "CSC_LINK": "certificate",
            "CSC_KEY_PASSWORD": "password",
            "APPLE_ID": "developer@example.com",
            "APPLE_APP_SPECIFIC_PASSWORD": "password",
            "APPLE_TEAM_ID": "TEAMID",
        }
        result = run_signing_check("mac", values)

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "signed-notarized")
        self.assertTrue(report["signing"])
        self.assertTrue(report["notarization"])

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 验证签名配置")
    def test_signing_config_writes_safe_github_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            summary = Path(temp_dir) / "summary"
            values = {
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            }
            result = run_signing_check("mac", values)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status=unsigned", output.read_text(encoding="utf-8"))
            self.assertIn("`unsigned`", summary.read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 验证签名配置")
    def test_signing_config_requires_signing_before_notarization(self) -> None:
        values = {
            "APPLE_ID": "developer@example.com",
            "APPLE_APP_SPECIFIC_PASSWORD": "password",
            "APPLE_TEAM_ID": "TEAMID",
        }
        result = run_signing_check("mac", values)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["group"], "signing-required-for-notarization")

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 验证签名配置")
    def test_signing_config_accepts_complete_windows_credentials(self) -> None:
        values = {
            "WIN_CSC_LINK": "certificate",
            "WIN_CSC_KEY_PASSWORD": "password",
        }
        result = run_signing_check("win", values)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "signed")

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
