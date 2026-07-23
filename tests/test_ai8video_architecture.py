from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from ai8video.core.legacy_payload import normalize_legacy_video_payload


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "ai8video"
CORE_DIRECTORIES = (
    "application",
    "assets",
    "batch",
    "breakdown",
    "core",
    "generation",
    "integrations",
    "knowledge",
    "media",
    "radar",
)
OLD_NAME_PATTERN = re.compile(r"mini[ _.-]*video", re.IGNORECASE)
COMPATIBILITY_FILES = {
    Path("src/ai8video/core/identity.py"),
    Path("src/ai8video/interfaces/web/static/scripts/01-bootstrap.js"),
    Path("desktop/electron/main.js"),
    Path("start_ai8video_web.sh"),
    Path("双击启动.bat"),
    Path("tests/test_ai8video_identity.py"),
    Path("tests/test_ai8video_architecture.py"),
}
SOURCE_ROOTS = (
    PACKAGE_ROOT,
    PROJECT_ROOT / "desktop",
    PROJECT_ROOT / "tests",
)
SERIES_DOMAIN_PATTERN = re.compile(
    r"Episode|episode|多集|集数|拆集|分集|第几集|每集|这一集|上集|下集|剧集"
)
SERIES_COMPATIBILITY_FILES = {
    Path("src/ai8video/core/legacy_payload.py"),
    Path("src/ai8video/interfaces/web/static/scripts/27b-migrate-legacy-video-schema.js"),
    Path("tests/test_ai8video_architecture.py"),
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class AI8VideoArchitectureTests(unittest.TestCase):
    def test_core_never_imports_entry_adapters(self) -> None:
        violations: list[str] = []
        for directory in CORE_DIRECTORIES:
            for path in (PACKAGE_ROOT / directory).rglob("*.py"):
                forbidden = sorted(
                    module
                    for module in imported_modules(path)
                    if module == "ai8video.interfaces" or module.startswith("ai8video.interfaces.")
                )
                if forbidden:
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}: {forbidden}")
        self.assertEqual(violations, [])

    def test_cli_uses_application_facade_for_core_use_cases(self) -> None:
        cli_path = PACKAGE_ROOT / "interfaces" / "cli.py"
        imports = imported_modules(cli_path)
        allowed_imports = {
            "ai8video",
            "ai8video.application.facade",
            "ai8video.interfaces.web",
        }
        direct_core_imports = sorted(
            module
            for module in imports
            if (module == "ai8video" or module.startswith("ai8video."))
            and module not in allowed_imports
        )
        self.assertEqual(direct_core_imports, [])

    def test_core_viral_breakdown_route_is_registered_once(self) -> None:
        source = (PACKAGE_ROOT / "interfaces" / "web" / "app.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('@app.route("/api/viral-breakdown/guess-script"'), 1)

    def test_legacy_python_entry_packages_are_removed(self) -> None:
        for relative in ("ai8video_cli", "frontends", "tools/ai8video"):
            self.assertFalse((PROJECT_ROOT / relative).exists(), relative)

    def test_web_static_source_files_stay_reviewable(self) -> None:
        static_root = PACKAGE_ROOT / "interfaces" / "web" / "static"
        violations = []
        for pattern in ("*.html", "*.css", "*.js"):
            for path in static_root.rglob(pattern):
                line_count = len(path.read_text(encoding="utf-8").splitlines())
                if line_count > 500:
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}: {line_count}")
        self.assertEqual(violations, [])

    def test_old_product_name_only_exists_in_compatibility_boundaries(self) -> None:
        violations: list[str] = []
        for root in SOURCE_ROOTS:
            for path in root.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                relative = path.relative_to(PROJECT_ROOT)
                if OLD_NAME_PATTERN.search(path.name):
                    violations.append(f"旧路径：{relative}")
                    continue
                if relative in COMPATIBILITY_FILES:
                    continue
                try:
                    source = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if OLD_NAME_PATTERN.search(source):
                    violations.append(f"旧内容：{relative}")
        self.assertEqual(violations, [])

    def test_series_domain_only_exists_in_read_compatibility_boundaries(self) -> None:
        violations: list[str] = []
        for root in SOURCE_ROOTS:
            for path in root.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                relative = path.relative_to(PROJECT_ROOT)
                if relative in SERIES_COMPATIBILITY_FILES:
                    continue
                if SERIES_DOMAIN_PATTERN.search(path.name):
                    violations.append(f"旧领域路径：{relative}")
                    continue
                try:
                    source = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if SERIES_DOMAIN_PATTERN.search(source):
                    violations.append(f"旧领域内容：{relative}")
        self.assertEqual(violations, [])

    def test_legacy_series_payload_is_read_as_video_tasks(self) -> None:
        legacy = {
            "mode": "multi_episode_script",
            "episodeCount": 2,
            "episodes": [{"episodeIndex": 1, "episodeTitle": "历史结果"}],
            "meta": {"rewrittenEpisodeIndex": 1},
        }

        normalized = normalize_legacy_video_payload(legacy)

        self.assertEqual(normalized["mode"], "batch_videos")
        self.assertEqual(normalized["videoCount"], 2)
        self.assertEqual(normalized["videos"][0]["videoIndex"], 1)
        self.assertEqual(normalized["videos"][0]["videoTitle"], "历史结果")
        self.assertEqual(normalized["meta"]["rewrittenVideoIndex"], 1)
        self.assertNotIn("episodeCount", normalized)


if __name__ == "__main__":
    unittest.main()
