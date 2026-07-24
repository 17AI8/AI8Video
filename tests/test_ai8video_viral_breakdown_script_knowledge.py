from __future__ import annotations

import unittest

from ai8video.breakdown.viral_breakdown_script_knowledge import (
    compose_viral_breakdown_knowledge_source,
)


class ViralBreakdownScriptKnowledgeTests(unittest.TestCase):
    def test_compose_source_joins_script_skeleton_and_transcript(self) -> None:
        content = compose_viral_breakdown_knowledge_source(
            script_text="开场冲突，主角决定入局。",
            transcript_text="你看，机会来了。",
        )
        self.assertIn("【剧本骨架】", content)
        self.assertIn("开场冲突，主角决定入局。", content)
        self.assertIn("【台词细节】", content)
        self.assertIn("你看，机会来了。", content)

    def test_compose_source_requires_both_parts(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "剧本骨架"):
            compose_viral_breakdown_knowledge_source(script_text="", transcript_text="台词")
        with self.assertRaisesRegex(RuntimeError, "台词"):
            compose_viral_breakdown_knowledge_source(script_text="骨架", transcript_text="")


if __name__ == "__main__":
    unittest.main()
