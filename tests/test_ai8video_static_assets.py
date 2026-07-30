from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PROJECT_ROOT / "src" / "ai8video" / "interfaces" / "web" / "static"
FONTAWESOME_ROOT = STATIC_ROOT / "vendor" / "fontawesome-free-7.3.1-desktop"
FONTAWESOME_REFERENCE = re.compile(
    r"fontawesome-free-7\.3\.1-desktop/(svgs-full/solid/[a-z0-9-]+\.svg)"
)


def referenced_fontawesome_assets() -> set[str]:
    referenced: set[str] = set()
    for pattern in ("*.css", "*.html", "*.js"):
        for source_path in STATIC_ROOT.rglob(pattern):
            if FONTAWESOME_ROOT in source_path.parents:
                continue
            source = source_path.read_text(encoding="utf-8")
            referenced.update(FONTAWESOME_REFERENCE.findall(source))
    return referenced


class StaticAssetFootprintTests(unittest.TestCase):
    def test_fontawesome_bundle_only_contains_referenced_icons(self) -> None:
        self.assertTrue((FONTAWESOME_ROOT / "LICENSE.txt").is_file())
        root_entries = {
            path.name for path in FONTAWESOME_ROOT.iterdir() if not path.name.startswith(".")
        }
        self.assertEqual(root_entries, {"LICENSE.txt", "svgs-full"})

        referenced = referenced_fontawesome_assets()
        bundled = {
            path.relative_to(FONTAWESOME_ROOT).as_posix()
            for path in FONTAWESOME_ROOT.rglob("*.svg")
        }
        self.assertTrue(referenced)
        self.assertEqual(bundled, referenced)


if __name__ == "__main__":
    unittest.main()
