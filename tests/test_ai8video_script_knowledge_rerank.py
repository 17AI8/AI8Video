from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ai8video.knowledge import default_script_reference
from ai8video.application.conversation_controller import AI8VideoConversationController
from ai8video.core.models import ConversationState, VideoPrompt, ParsedRequest, PipelineResult, QuickVideoJob
from ai8video.knowledge.script_knowledge_context import retrieve_reference_context
from ai8video.knowledge.script_knowledge_query import plan_retrieval_query
from ai8video.knowledge.script_knowledge_rerank import rerank_candidates


def _candidates(count: int = 8) -> list[dict]:
    return [
        {
            "id": index,
            "heading": f"脚本{index}",
            "content": f"候选正文 {index}",
            "score": float(count - index),
        }
        for index in range(1, count + 1)
    ]


class ScriptKnowledgeRerankTest(unittest.TestCase):
    def test_query_model_separates_positive_and_excluded_terms(self) -> None:
        result = plan_retrieval_query(
            "5 个",
            "禁止 618，不要 App 界面，开头使用夏季美女，结尾邀请好友返佣",
            "跨境私域 全球沟通",
            llm=lambda _: (
                '{"query":"夏季美女 跨境私域 全球沟通 邀请好友返佣",'
                '"keywords":["夏季美女","跨境私域","全球沟通","邀请好友返佣"],'
                '"excluded_terms":["618","App界面"]}'
            ),
        )

        self.assertTrue(result["queryModelApplied"])
        self.assertNotIn("618", result["query"])
        self.assertEqual(result["excludedTerms"], ["618", "App界面"])
        self.assertIn("应排除：618、App界面", result["rankingQuery"])

    def test_query_model_failure_falls_back_to_reference_metadata(self) -> None:
        result = plan_retrieval_query(
            "5 个",
            "禁止 618",
            "跨境私域 全球沟通",
            llm=lambda _: "invalid",
        )

        self.assertFalse(result["queryModelApplied"])
        self.assertEqual(result["query"], "跨境私域 全球沟通")
        self.assertIn("query_model_failed", result["fallbackReason"])

    def test_rerank_uses_valid_model_order_and_ignores_unknown_ids(self) -> None:
        result = rerank_candidates(
            "私域老板",
            _candidates(),
            llm=lambda _: '{"ranking":[4,2,999,4,1]}',
            top_k=5,
        )

        self.assertTrue(result["rerankApplied"])
        self.assertEqual([item["id"] for item in result["candidates"]], [4, 2, 1, 3, 5])

    def test_rerank_failure_falls_back_to_postgres_order(self) -> None:
        result = rerank_candidates(
            "私域老板",
            _candidates(),
            llm=lambda _: "不是 JSON",
            top_k=3,
        )

        self.assertFalse(result["rerankApplied"])
        self.assertEqual([item["id"] for item in result["candidates"]], [1, 2, 3])
        self.assertIn("rerank_failed", result["fallbackReason"])

    def test_context_retrieval_reranks_top_twenty_to_top_five(self) -> None:
        store = Mock()
        store.status.return_value = {"available": True}
        store.search_sections.return_value = _candidates(20)
        with patch(
            "ai8video.knowledge.script_knowledge_context.register_script_knowledge_sources",
            return_value={"unchanged": 1},
        ), patch(
            "ai8video.knowledge.script_knowledge_context.get_script_knowledge_store",
            return_value=store,
        ):
            result = retrieve_reference_context(
                "使用当前剧本参考，围绕私域老板写一条",
                "老板话术.docx",
                rerank_llm=lambda _: '{"ranking":[7,4,2,1,3]}',
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["rerankApplied"])
        self.assertEqual(result["recallCount"], 20)
        self.assertEqual([item["id"] for item in result["sections"]], [7, 4, 2, 1, 3])
        self.assertIn("[知识段 1｜脚本7]", result["contextText"])


class DefaultScriptReferenceTopKTest(unittest.TestCase):
    def test_default_reference_injects_retrieved_sections_without_full_text(self) -> None:
        item = {
            "name": "老板话术.docx",
            "relativePath": "老板话术.docx",
            "path": "/tmp/老板话术.docx",
            "kind": "script",
        }
        retrieval = {
            "ok": True,
            "query": "私域老板",
            "recallCount": 20,
            "topK": 2,
            "rerankApplied": True,
            "fallbackReason": "",
            "sections": [
                {"id": 4, "heading": "私域噩梦", "content": "客户资产不能丢。", "score": 5.0},
                {"id": 9, "heading": "老板要装", "content": "效率决定利润。", "score": 4.0},
            ],
            "contextText": "[知识段 1｜私域噩梦]\n客户资产不能丢。",
        }
        with patch.object(default_script_reference, "load_default_script_reference", return_value=item), patch.object(
            default_script_reference,
            "retrieve_reference_context",
            return_value=retrieval,
        ), patch.object(default_script_reference, "read_script_material_text") as read_full:
            text, context = default_script_reference.apply_default_script_reference(
                "使用当前剧本参考写私域老板",
                None,
                prefer_full=False,
                rerank_llm=lambda _: "{}",
            )

        read_full.assert_not_called()
        self.assertIn("相关知识段（Top 2）", text)
        self.assertEqual(context["scripts"][0]["retrievalMode"], "topK")
        self.assertTrue(context["scripts"][0]["rerankApplied"])

    def test_controller_keeps_full_mode_for_control_and_large_batch(self) -> None:
        controller = AI8VideoConversationController(Mock())
        state = ConversationState(session_id="top-k-gating")

        self.assertFalse(controller._prefer_full_script_reference(state, "使用当前剧本参考写私域老板"))
        self.assertFalse(controller._prefer_full_script_reference(state, "5 个"))
        self.assertTrue(controller._prefer_full_script_reference(state, "开始生成"))
        self.assertTrue(controller._prefer_full_script_reference(state, "使用当前剧本参考生成10条视频"))
        self.assertTrue(controller._prefer_full_script_reference(state, "使用当前剧本参考，按完整原文生成"))

    def test_controller_injects_top_k_sections_for_semantic_request(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            script_rerank_llm = staticmethod(lambda _: '{"ranking":[4,9]}')

            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[VideoPrompt(index=1, title="第 1 条", prompt=request.raw_text)],
                    first_frame=None,
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        item = {
            "name": "老板话术.docx",
            "relativePath": "老板话术.docx",
            "path": "/tmp/老板话术.docx",
            "kind": "script",
        }
        retrieval = {
            "ok": True,
            "query": "私域噩梦",
            "recallCount": 20,
            "topK": 2,
            "rerankApplied": True,
            "fallbackReason": "",
            "sections": _candidates(2),
            "contextText": "[知识段 1｜私域噩梦]\n客户资料不能丢。",
        }
        controller = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "none")
        with patch.object(default_script_reference, "load_default_script_reference", return_value=item), patch.object(
            default_script_reference,
            "retrieve_reference_context",
            return_value=retrieval,
        ), patch.object(default_script_reference, "read_script_material_text") as read_full:
            reply = controller.handle_message(
                "top-k-controller",
                "使用当前剧本参考，围绕私域噩梦生成一条短视频，不用参考图",
            )

        self.assertEqual(reply.stage, "completed")
        read_full.assert_not_called()
        self.assertIn("相关知识段（Top 2）", captured["request"].raw_text)
        self.assertIn("客户资料不能丢", captured["request"].raw_text)
        self.assertNotIn("完整原文", captured["request"].raw_text)

    def test_count_only_query_uses_reference_metadata_hint(self) -> None:
        from ai8video.knowledge.script_knowledge_context import build_retrieval_query

        query = build_retrieval_query("5 个", query_hint="全球发布 私域客户资产")

        self.assertEqual(query, "全球发布 私域客户资产")


if __name__ == "__main__":
    unittest.main()
