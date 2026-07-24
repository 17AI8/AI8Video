from __future__ import annotations

import json
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
)
from ai8video.knowledge.knowledge_base_agent import (
    KnowledgeBaseAgent,
    KnowledgeBaseAgentRequest,
    build_source_units,
)
from ai8video.knowledge.knowledge_ingestion_supervisor import KnowledgeIngestionSupervisor
from ai8video.knowledge.reviewer_agent import (
    ReviewerAgent,
    build_knowledge_review_prompt,
    parse_knowledge_review,
)
from ai8video.knowledge.script_knowledge_ingestion import (
    KnowledgeIngestionJob,
    flatten_tree_leaves,
    parse_tree_result,
)


class ScriptKnowledgeTextTest(unittest.TestCase):
    def test_tree_result_keeps_hierarchical_leaf_paths(self) -> None:
        source_units = build_source_units("## 功能\n\n支付完成后沉淀客户。")
        result = parse_tree_result(
            '{"title":"产品资料","summary":"测试","tags":["产品"],"tree":['
            '{"title":"功能","children":[{"title":"支付沉淀","sourceUnitIds":["U0002"]}]}]}'
        )

        leaves = flatten_tree_leaves(result["tree"], source_units)

        self.assertEqual(leaves, [{
            "heading": "功能 / 支付沉淀",
            "content": "支付完成后沉淀客户。",
            "path": ["功能", "支付沉淀"],
            "sourceUnitIds": [2],
        }])

    def test_tree_result_rejects_non_json_output(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "有效 JSON"):
            parse_tree_result("这是普通回答")

    def test_knowledge_base_agent_preserves_lists_and_reports_quality(self) -> None:
        source = "## 画面规则\n\n- 禁止黑屏。\n- 禁止可读字幕。\n\n## 音效规则\n\n只使用环境音和动作音，不写背景音乐。"
        response = (
            '{"title":"短视频规则","summary":"规则摘要","tags":["规则"],"tree":['
            '{"title":"画面规则","sourceUnitIds":[2,3]},'
            '{"title":"音效规则","sourceUnitIds":[5]}]}'
        )
        prompts: list[str] = []
        agent = KnowledgeBaseAgent(lambda prompt: prompts.append(prompt) or response)

        result = agent.run(KnowledgeBaseAgentRequest(1, "规则.md", source))

        self.assertEqual(result.quality.leaf_count, 2)
        self.assertEqual(result.quality.used_unit_count, 3)
        self.assertIn("\n", result.leaves[0]["content"])
        self.assertIn("sourceUnitIds", prompts[0])
        self.assertLessEqual(result.quality.max_chars, 2400)

    def test_knowledge_base_agent_accepts_coherent_leaf_under_2400_chars(self) -> None:
        source = "\n".join(["跨境私域规则" + "客户资料必须留存在企业域内。" * 18 for _ in range(3)])
        response = '{"title":"规则","summary":"","tags":[],"tree":[{"title":"私域规则","sourceUnitIds":[1,2,3]}]}'

        result = KnowledgeBaseAgent(lambda prompt: response).run(
            KnowledgeBaseAgentRequest(1, "规则.md", source)
        )

        self.assertGreater(result.quality.max_chars, 650)
        self.assertLessEqual(result.quality.max_chars, 2400)

    def test_knowledge_agent_assigns_each_source_unit_to_only_one_leaf(self) -> None:
        document = {
            "id": 1,
            "name": "重复归属测试",
            "content": "第一条知识\n第二条知识\n第三条知识",
        }
        response = json.dumps({
            "title": "测试树",
            "summary": "",
            "tags": [],
            "tree": [
                {"title": "节点一", "sourceUnitIds": [2, 1, 2]},
                {"title": "节点二", "sourceUnitIds": [2, 3]},
                {"title": "空节点", "sourceUnitIds": [1, 2]},
            ],
        }, ensure_ascii=False)

        result = KnowledgeBaseAgent(lambda _prompt: response).run(
            KnowledgeBaseAgentRequest.from_document(document),
        )

        self.assertEqual([leaf["sourceUnitIds"] for leaf in result.leaves], [[1, 2], [3]])
        self.assertEqual(len(result.tree["tree"]), 2)

    def test_knowledge_agent_splits_oversized_leaf_on_source_unit_boundary(self) -> None:
        source = "\n".join([f"规则{i}：" + "必须保留原文信息。" * 20 for i in range(1, 17)])
        response = json.dumps({
            "title": "规则",
            "summary": "",
            "tags": [],
            "tree": [{"title": "完整规则", "sourceUnitIds": list(range(1, 17))}],
        }, ensure_ascii=False)

        result = KnowledgeBaseAgent(lambda _prompt: response).run(
            KnowledgeBaseAgentRequest(1, "规则.md", source),
        )

        self.assertGreater(len(result.leaves), 1)
        self.assertTrue(all(len(leaf["content"]) <= 2400 for leaf in result.leaves))
        self.assertEqual(result.quality.used_unit_count, 16)

    def test_ingestion_progress_does_not_expose_raw_model_json(self) -> None:
        job = KnowledgeIngestionJob(document_id=1)

        job.emit_delta("knowledge_agent", '{"title":"内')
        job.emit_delta("knowledge_agent", '部候选"}')
        event = job.payload()["events"][0]

        self.assertEqual(event["kind"], "progress")
        self.assertEqual(event["text"], "正在生成节点：内部候选")
        self.assertNotIn('{"title"', event["text"])

    def test_reviewer_progress_streams_human_readable_feedback(self) -> None:
        job = KnowledgeIngestionJob(document_id=1)

        job.emit_delta("reviewer", '{"decision":"revise","summary":"覆盖度需要补')
        event = job.payload()["events"][0]

        self.assertEqual(event["text"], "Reviewer 正在总结：覆盖度需要补")

    def test_knowledge_base_agent_rejects_unknown_source_unit(self) -> None:
        source = "## 音效规则\n只使用环境音和动作音，不写背景音乐。"
        response = (
            '{"title":"规则","summary":"","tags":[],"tree":['
            '{"title":"音效规则","sourceUnitIds":[99]}]}'
        )
        agent = KnowledgeBaseAgent(lambda prompt: response)

        with self.assertRaisesRegex(RuntimeError, "不存在的原文单元"):
            agent.run(KnowledgeBaseAgentRequest(1, "规则.md", source))

    def test_knowledge_base_agent_removes_overlapping_source_units(self) -> None:
        source = "## 规则\n- 禁止黑屏。\n- 禁止可读字幕。"
        response = (
            '{"title":"规则","summary":"","tags":[],"tree":['
            '{"title":"画面规则","sourceUnitIds":[2]},'
            '{"title":"重复规则","sourceUnitIds":[2,3]}]}'
        )
        agent = KnowledgeBaseAgent(lambda prompt: response)

        result = agent.run(KnowledgeBaseAgentRequest(1, "规则.md", source))

        self.assertEqual([leaf["sourceUnitIds"] for leaf in result.leaves], [[2], [3]])

    def test_source_units_split_oversized_paragraphs(self) -> None:
        source = "画面规则：" + "角色始终保持正面同框，动作连续且背景不得出现可读文字，" * 12

        units = build_source_units(source)

        self.assertGreater(len(units), 1)
        self.assertTrue(all(len(unit.text) <= 320 for unit in units))

    def test_knowledge_base_agent_restores_source_unit_order(self) -> None:
        source = "## 规则\n先建立场景。\n再展示角色动作。"
        response = (
            '{"title":"规则","summary":"","tags":[],"tree":['
            '{"title":"动作顺序","sourceUnitIds":[3,2]}]}'
        )
        agent = KnowledgeBaseAgent(lambda prompt: response)

        result = agent.run(KnowledgeBaseAgentRequest(1, "规则.md", source))

        self.assertEqual(result.leaves[0]["sourceUnitIds"], [2, 3])

    def test_reviewer_requires_evidence_for_revision(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "问题证据"):
            parse_knowledge_review('{"decision":"revise","summary":"需要调整","issues":[]}')

        with self.assertRaisesRegex(RuntimeError, "缺少证据或修改指令"):
            parse_knowledge_review(
                '{"decision":"revise","summary":"需要调整","issues":['
                '{"leafPath":"规则","type":"atomicity","evidence":"","instruction":"拆分"}]}'
            )

    def test_reviewer_prompt_limits_feedback_to_tree_and_source_assignment(self) -> None:
        request = KnowledgeBaseAgentRequest(1, "规则.md", "## 规则\n【金句】保留客户资料。")
        proposal = KnowledgeBaseAgent(
            lambda _prompt: '{"title":"规则","summary":"","tags":[],"tree":['
            '{"title":"客户资料","sourceUnitIds":[2]}]}',
        ).run(request)

        prompt = build_knowledge_review_prompt(request, proposal)

        self.assertIn("不要因 Markdown 标签", prompt)
        self.assertIn("只能要求移动、拆分、合并、纳入或排除原文单元", prompt)

    def test_supervisor_allows_one_reviewed_revision(self) -> None:
        source = "## 规则\n- 禁止黑屏。\n- 禁止可读字幕。\n只使用环境音，不写背景音乐。"
        build_responses = iter([
            '{"title":"规则","summary":"","tags":[],"tree":['
            '{"title":"混合规则","sourceUnitIds":[2,3,4]}]}',
            '{"title":"规则","summary":"","tags":[],"tree":['
            '{"title":"画面禁忌","sourceUnitIds":[2,3]},'
            '{"title":"音效边界","sourceUnitIds":[4]}]}',
        ])
        review_responses = iter([
            json.dumps({
                "decision": "revise",
                "summary": "画面和音效属于不同检索问题",
                "issues": [{
                    "leafPath": "混合规则",
                    "type": "atomicity",
                    "evidence": "单个叶子同时包含画面与音效",
                    "instruction": "拆成画面禁忌和音效边界两个叶子",
                }],
            }, ensure_ascii=False),
            '{"decision":"accept","summary":"结构清晰","issues":[]}',
        ])
        build_prompts: list[str] = []
        knowledge_agent = KnowledgeBaseAgent(
            lambda prompt: build_prompts.append(prompt) or next(build_responses)
        )
        reviewer = ReviewerAgent(lambda prompt: next(review_responses))
        supervisor = KnowledgeIngestionSupervisor(knowledge_agent, reviewer)

        outcome = supervisor.run(KnowledgeBaseAgentRequest(1, "规则.md", source))

        self.assertEqual(outcome.revision_count, 1)
        self.assertEqual(outcome.review.decision, "accept")
        self.assertEqual(len(outcome.proposal.leaves), 2)
        self.assertIn("拆成画面禁忌和音效边界两个叶子", build_prompts[1])

    def test_agent_keeps_coherent_candidate_without_model_retry(self) -> None:
        source = "\n".join(["客户沉淀规则：" + "资料必须保留在企业私域。" * 20 for _ in range(5)])
        build_responses = iter([
            '{"title":"规则","summary":"","tags":[],"tree":['
            '{"title":"过大叶子","sourceUnitIds":[1,2,3,4,5]}]}',
            '{"title":"规则","summary":"","tags":[],"tree":['
            '{"title":"客户沉淀前段","sourceUnitIds":[1,2,3]},'
            '{"title":"客户沉淀后段","sourceUnitIds":[4,5]}]}',
        ])
        prompts: list[str] = []
        supervisor = KnowledgeIngestionSupervisor(
            KnowledgeBaseAgent(lambda prompt: prompts.append(prompt) or next(build_responses)),
            ReviewerAgent(lambda prompt: '{"decision":"accept","summary":"结构清晰","issues":[]}'),
        )

        outcome = supervisor.run(KnowledgeBaseAgentRequest(1, "规则.md", source))

        self.assertEqual(outcome.revision_count, 0)
        self.assertEqual(len(outcome.proposal.leaves), 1)
        self.assertEqual(len(prompts), 1)

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

    def test_register_tree_search_metadata_and_remove(self) -> None:
        content = "跨境客户需要沉淀到私域。\n\n六月十八日全球发布，开场使用倒计时钩子。"
        sources = [{
            "name": "全球发布.md",
            "relativePath": "活动/全球发布.md",
            "path": "/tmp/全球发布.md",
            "sizeBytes": len(content.encode("utf-8")),
            "modifiedAt": 100.0,
        }]

        register_result = self.store.register_sources(sources, lambda _: content)
        pending = self.store.list_documents()[0]
        self.store.replace_document_tree(
            pending["id"],
            {
                "title": "跨境私域发布脚本",
                "summary": "面向跨境团队的发布预热",
                "tags": ["跨境", "发布"],
                "tree": [{"title": "全球发布", "content": content}],
            },
            [{"heading": "全球发布", "content": content}],
        )
        results = self.store.search("私域", limit=5)
        sections = self.store.search_sections("全球发布", relative_path="活动/全球发布.md", limit=20)
        document = self.store.get_document(results[0]["id"])
        updated = self.store.update_document(
            document["id"],
            title="跨境私域发布脚本",
            summary="面向跨境团队的发布预热",
            tags=["跨境", "发布"],
        )
        unchanged = self.store.register_sources(sources, lambda _: self.fail("不应重复读取正文"))
        removed = self.store.register_sources([], lambda _: "")

        self.assertEqual(register_result["registered"], 1)
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
