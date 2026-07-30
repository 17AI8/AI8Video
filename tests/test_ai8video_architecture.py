from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from ai8video.core.legacy_payload import normalize_legacy_video_payload


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "ai8video"
CORE_DIRECTORIES = (
    "agent_runtime",
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
VENDORED_SOURCE_ROOTS = (
    Path("src/ai8video/interfaces/web/static/vendor"),
)
SERIES_DOMAIN_PATTERN = re.compile(
    r"Episode|episode|多集|拆集|第几集|上集|下集|剧集"
)
SERIES_COMPATIBILITY_FILES = {
    Path("src/ai8video/core/legacy_payload.py"),
    Path("src/ai8video/interfaces/web/static/scripts/27b-migrate-legacy-video-schema.js"),
    Path("tests/test_ai8video_architecture.py"),
}
LEGACY_WEB_STATIC_LINE_LIMITS = {
    "index.html": 610,
    "script-knowledge.css": 686,
    "scripts/01-bootstrap.js": 551,
    "scripts/02-init.js": 532,
    "scripts/04-drag.js": 639,
    "scripts/05-refresh-health.js": 624,
    "scripts/06-refresh-generation-mode.js": 539,
    "scripts/07-force-cancel-trigger.js": 529,
    "scripts/08-local-tts-payload-from-input.js": 501,
    "scripts/09-is-default-reference-custom-prompt-focused.js": 505,
    "scripts/11-regenerate-tts-from-video-preview.js": 656,
    "scripts/12-regenerate-html-motion-from-video-preview.js": 536,
    "scripts/14-generate-video-preview-extension.js": 538,
    "scripts/15-group-settings-fields.js": 508,
    "scripts/16-current-resolution-options.js": 509,
    "scripts/18-build-viral-breakdown-prompt-template.js": 579,
    "scripts/19-render-viral-breakdown-workbench.js": 573,
    "scripts/20-build-script-knowledge-card-markup.js": 537,
    "scripts/21-humanize-recycle-bin-reason.js": 545,
    "scripts/22a-conversation-rendering.js": 744,
    "scripts/25-render-assistant-result-cards.js": 648,
    "scripts/26-build-batch-report-card-markup.js": 538,
    "scripts/28-load-sessions.js": 506,
    "scripts/29-pill.js": 506,
    "styles/05-breakdown.css": 1257,
    "styles/06-generation-controls.css": 557,
    "styles/03-results.css": 522,
    "styles/10-settings.css": 970,
    "styles/11-materials.css": 587,
    "styles/12-messages.css": 895,
    "styles/14-video-preview.css": 1158,
    "styles/16-radar-layout.css": 506,
    "styles/18-radar-responsive.css": 507,
    "styles/19-breakdown-theme.css": 566,
    "styles/21-sidebar-nav.css": 557,
}


def is_vendored_source(relative: Path) -> bool:
    return any(relative.is_relative_to(root) for root in VENDORED_SOURCE_ROOTS)


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
        self.assertEqual(source.count('@app.route("/api/viral-breakdown/analyze-shot-language"'), 1)
        self.assertEqual(source.count('@app.route("/api/viral-breakdown/build-script-tree"'), 1)
        self.assertEqual(source.count('@app.route("/api/viral-breakdown/save-script-tree"'), 1)

    def test_legacy_python_entry_packages_are_removed(self) -> None:
        for relative in ("ai8video_cli", "frontends", "tools/ai8video"):
            self.assertFalse((PROJECT_ROOT / relative).exists(), relative)

    def test_web_static_source_files_stay_reviewable(self) -> None:
        static_root = PACKAGE_ROOT / "interfaces" / "web" / "static"
        violations = []
        for pattern in ("*.html", "*.css", "*.js"):
            for path in static_root.rglob(pattern):
                if is_vendored_source(path.relative_to(PROJECT_ROOT)):
                    continue
                line_count = len(path.read_text(encoding="utf-8").splitlines())
                relative = path.relative_to(static_root).as_posix()
                limit = LEGACY_WEB_STATIC_LINE_LIMITS.get(relative, 500)
                if line_count > limit:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}: {line_count} > {limit}"
                    )
        self.assertEqual(violations, [])

    def test_old_product_name_only_exists_in_compatibility_boundaries(self) -> None:
        violations: list[str] = []
        for root in SOURCE_ROOTS:
            for path in root.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                relative = path.relative_to(PROJECT_ROOT)
                if is_vendored_source(relative):
                    continue
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
                if is_vendored_source(relative):
                    continue
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
