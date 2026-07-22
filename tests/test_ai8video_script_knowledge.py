from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.assets import user_materials
from ai8video.knowledge.script_knowledge import (
    ScriptKnowledgeStore,
    _build_search_terms,
    _build_ts_query,
    _split_sections,
)


class ScriptKnowledgeTextTest(unittest.TestCase):
    def test_build_search_terms_adds_chinese_bigrams(self) -> None:
        terms = _build_search_terms("跨境私域 AI8")

        self.assertIn("跨境", terms.split())
        self.assertIn("境私", terms.split())
        self.assertIn("私域", terms.split())
        self.assertIn("ai8", terms.split())

    def test_build_ts_query_uses_or_for_recall(self) -> None:
        query = _build_ts_query("全球发布")

        self.assertIn("全球", query)
        self.assertIn(" | ", query)

    def test_split_sections_keeps_chunks_bounded(self) -> None:
        content = "\n\n".join([f"第{index}段。" + "内容" * 180 for index in range(8)])

        sections = _split_sections(content)

        self.assertGreater(len(sections), 2)
        self.assertTrue(all(len(section["content"]) <= 1200 for section in sections))

    def test_split_sections_prefers_numbered_script_boundaries(self) -> None:
        content = (
            "项目说明\n适合发布预热。\n"
            "脚本1《第一个钩子》\n【前3秒】先制造冲突。\n【正文】给出答案。\n"
            "脚本2《第二个钩子》\n【前3秒】提出问题。\n【正文】完成收口。"
        )

        sections = _split_sections(content)

        self.assertEqual([item["heading"] for item in sections[1:]], ["脚本1《第一个钩子》", "脚本2《第二个钩子》"])
        self.assertNotIn("脚本1《第一个钩子》", sections[1]["content"])
        self.assertIn("【正文】给出答案。", sections[1]["content"])

    def test_source_scan_does_not_read_document_previews(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            script_dir = Path(tempdir).resolve()
            (script_dir / "示例.md").write_text("剧本正文", encoding="utf-8")
            with patch.object(user_materials, "USER_SCRIPT_MATERIAL_DIR", script_dir), patch.object(
                user_materials,
                "ensure_user_material_dirs",
                return_value=None,
            ), patch.object(user_materials, "_read_script_text", side_effect=AssertionError("不应读取正文")):
                sources = user_materials.list_script_material_sources()

        self.assertEqual(sources[0]["name"], "示例.md")
        self.assertNotIn("preview", sources[0])


@unittest.skipUnless(
    os.getenv("AI8VIDEO_TEST_POSTGRES_URL"),
    "需要 AI8VIDEO_TEST_POSTGRES_URL 才运行 PostgreSQL 集成测试",
)
class ScriptKnowledgePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = str(os.environ["AI8VIDEO_TEST_POSTGRES_URL"])
        cls.store = ScriptKnowledgeStore(cls.database_url)
        cls.store.initialize()

    def setUp(self) -> None:
        import psycopg

        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM ai8_script_documents")

    def test_sync_search_metadata_and_remove(self) -> None:
        content = "跨境客户需要沉淀到私域。\n\n六月十八日全球发布，开场使用倒计时钩子。"
        sources = [{
            "name": "全球发布.md",
            "relativePath": "活动/全球发布.md",
            "path": "/tmp/全球发布.md",
            "sizeBytes": len(content.encode("utf-8")),
            "modifiedAt": 100.0,
        }]

        sync_result = self.store.sync_sources(sources, lambda _: content)
        results = self.store.search("私域", limit=5)
        sections = self.store.search_sections("全球发布", relative_path="活动/全球发布.md", limit=20)
        document = self.store.get_document(results[0]["id"])
        updated = self.store.update_document(
            document["id"],
            title="跨境私域发布脚本",
            summary="面向跨境团队的发布预热",
            tags=["跨境", "发布"],
        )
        unchanged = self.store.sync_sources(sources, lambda _: self.fail("不应重复读取正文"))
        removed = self.store.sync_sources([], lambda _: "")

        self.assertEqual(sync_result["indexed"], 1)
        self.assertEqual(results[0]["name"], "全球发布.md")
        self.assertGreater(len(sections), 0)
        self.assertEqual(sections[0]["relativePath"], "活动/全球发布.md")
        self.assertIn("全球发布", sections[0]["content"])
        self.assertGreater(document["sectionCount"], 0)
        self.assertEqual(updated["tags"], ["跨境", "发布"])
        self.assertEqual(unchanged["unchanged"], 1)
        self.assertEqual(removed["removed"], 1)
        self.assertEqual(self.store.list_documents(), [])


if __name__ == "__main__":
    unittest.main()
