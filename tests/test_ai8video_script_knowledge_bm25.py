from __future__ import annotations

import math
import os
import statistics
import threading
import time
import unittest
from unittest.mock import patch

from ai8video.knowledge.script_knowledge import ScriptKnowledgeStore
from ai8video.knowledge.script_knowledge_bm25 import (
    BM25_INDEX_VERSION,
    BM25_TOKENIZER_VERSION,
    build_bm25_corpus,
    calculate_bm25_term_score,
    search_bm25_sections_in_memory,
)
from ai8video.knowledge.script_knowledge_query import plan_retrieval_query
from ai8video.knowledge.script_knowledge_text import (
    build_bm25_query_terms,
    extract_protected_terms,
    normalize_retrieval_text,
    tokenize_retrieval_text,
)


class ScriptKnowledgeBM25MathTest(unittest.TestCase):
    def test_positive_idf_matches_reference_formula(self) -> None:
        score = calculate_bm25_term_score(
            document_count=10,
            document_frequency=2,
            term_frequency=1,
            section_length=100,
            average_section_length=100,
        )

        expected_idf = math.log(1 + (10 - 2 + 0.5) / (2 + 0.5))

        self.assertAlmostEqual(score, expected_idf)

    def test_term_frequency_saturates(self) -> None:
        single_occurrence = self._score(term_frequency=1)
        triple_occurrence = self._score(term_frequency=3)
        ten_occurrences = self._score(term_frequency=10)

        self.assertGreater(triple_occurrence, single_occurrence)
        self.assertGreater(ten_occurrences, triple_occurrence)
        self.assertLess(
            ten_occurrences - triple_occurrence,
            triple_occurrence - single_occurrence,
        )

    def test_longer_section_receives_length_normalization(self) -> None:
        short_section = self._score(term_frequency=2, section_length=40)
        long_section = self._score(term_frequency=2, section_length=400)

        self.assertGreater(short_section, long_section)

    def test_empty_corpus_has_stable_metadata(self) -> None:
        corpus = build_bm25_corpus([])

        self.assertEqual(corpus.section_count, 0)
        self.assertEqual(corpus.average_section_length, 0)
        self.assertEqual(len(corpus.corpus_hash), 64)

    def test_in_memory_search_uses_shared_bm25_ranking(self) -> None:
        candidates = search_bm25_sections_in_memory(
            "支付沉淀客户关系",
            [
                {"heading": "开场", "content": "开场展示产品界面。"},
                {"heading": "客户沉淀", "content": "支付完成后自动沉淀客户关系。"},
                {"heading": "结尾", "content": "结尾展示品牌标志。"},
            ],
            limit=5,
            source_id="tutorial.mp4",
            source_title="教程临时知识库",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["heading"], "客户沉淀")
        self.assertEqual(candidates[0]["retrievalBackend"], "memory_bm25")
        self.assertEqual(candidates[0]["retrievalTrace"]["knowledgeSource"], "temporary")

    @staticmethod
    def _score(*, term_frequency: int, section_length: int = 100) -> float:
        return calculate_bm25_term_score(
            document_count=20,
            document_frequency=3,
            term_frequency=term_frequency,
            section_length=section_length,
            average_section_length=100,
        )


class ScriptKnowledgeBM25TokenizerTest(unittest.TestCase):
    def test_normalization_and_protected_identifiers(self) -> None:
        source = (
            "GB/T 19001-2016 ISO-9001 C++ H.264 12.5mg/mL "
            "2026-08-01 v2.4.1 第42条 ＡＰＩ＿Ｖ２"
        )

        protected_terms = extract_protected_terms(source)

        self.assertEqual(normalize_retrieval_text("ＡＰＩ＿Ｖ２"), "api_v2")
        for expected_term in (
            "gb/t 19001-2016",
            "iso-9001",
            "c++",
            "h.264",
            "12.5mg/ml",
            "2026-08-01",
            "v2.4.1",
            "第42条",
            "api_v2",
        ):
            self.assertIn(expected_term, protected_terms)
        self.assertNotIn("12.5mg", protected_terms)

    def test_document_tokens_preserve_frequency_and_query_tokens_dedupe(self) -> None:
        document_tokens = tokenize_retrieval_text("风险风险")
        query_terms = build_bm25_query_terms("风险 风险 风险")

        self.assertEqual(document_tokens.count("风险"), 2)
        self.assertEqual(query_terms.count("风险"), 1)

    def test_query_term_limit_is_bounded(self) -> None:
        query = " ".join(f"term{index}" for index in range(100))

        query_terms = build_bm25_query_terms(query)

        self.assertEqual(len(query_terms), 64)

    def test_query_model_cannot_drop_protected_or_add_excluded_terms(self) -> None:
        query_plan = plan_retrieval_query(
            "查询 ISO-9001 的审核证据，不要 ISO-14001",
            "",
            "",
            llm=lambda _prompt: (
                '{"query":"审核证据","keywords":["审核"],'
                '"excluded_terms":["ISO-14001"]}'
            ),
        )

        self.assertIn("iso-9001", query_plan["query"])
        self.assertIn("iso-9001", query_plan["protectedTerms"])
        self.assertNotIn("iso-14001", query_plan["query"].lower())
        self.assertEqual(query_plan["excludedTerms"], ["ISO-14001"])


@unittest.skipUnless(
    os.getenv("AI8VIDEO_TEST_POSTGRES_URL"),
    "需要 AI8VIDEO_TEST_POSTGRES_URL 才运行 PostgreSQL BM25 集成测试",
)
class ScriptKnowledgeBM25PostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = str(os.environ["AI8VIDEO_TEST_POSTGRES_URL"])
        cls.store = ScriptKnowledgeStore(cls.database_url)
        cls.store.initialize()

    def setUp(self) -> None:
        import psycopg

        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM ai8_script_documents")

    def test_bm25_search_is_strictly_scoped_to_selected_document(self) -> None:
        selected = self._add_document(
            "selected.md",
            [
                {"heading": "质量 / 审核", "content": "ISO-9001 要求保存审核证据。"},
                {"heading": "质量 / 培训", "content": "培训记录保存三年。"},
            ],
        )
        unselected = self._add_document(
            "unselected.md",
            [{"heading": "强命中", "content": "ISO-9001 " * 20}],
        )

        candidates = self.store.search_sections(
            "ISO-9001",
            relative_path=selected["relativePath"],
            limit=20,
        )

        self.assertTrue(candidates)
        self.assertEqual({candidate["documentId"] for candidate in candidates}, {selected["id"]})
        self.assertNotIn(unselected["id"], {candidate["documentId"] for candidate in candidates})
        self.assertEqual(candidates[0]["retrievalBackend"], "bm25")

    def test_unselected_document_changes_do_not_change_selected_scores(self) -> None:
        selected = self._add_document(
            "stable.md",
            [
                {"heading": "风险规则", "content": "风险风险需要分级处理。"},
                {"heading": "普通规则", "content": "普通记录需要保存。"},
            ],
        )
        score_before = self.store.search_sections(
            "风险",
            relative_path=selected["relativePath"],
            limit=20,
        )[0]["bm25Score"]

        self._add_document(
            "unrelated.md",
            [{"heading": "外部文档", "content": "风险 " * 100}],
        )
        score_after = self.store.search_sections(
            "风险",
            relative_path=selected["relativePath"],
            limit=20,
        )[0]["bm25Score"]

        self.assertAlmostEqual(score_before, score_after)

    def test_equal_scores_use_section_order_as_tiebreaker(self) -> None:
        document = self._add_document(
            "ties.md",
            [
                {"heading": "第一规则", "content": "共同术语和相同长度。"},
                {"heading": "第二规则", "content": "共同术语和相同长度。"},
            ],
        )

        candidates = self.store.search_sections(
            "共同术语",
            relative_path=document["relativePath"],
            limit=20,
        )

        self.assertEqual([candidate["sectionOrder"] for candidate in candidates], [0, 1])

    def test_restart_uses_persisted_index_without_rebuild(self) -> None:
        document = self._add_document(
            "restart.md",
            [{"heading": "标准", "content": "GB/T 19001-2016 审核规则。"}],
        )
        restarted_store = ScriptKnowledgeStore(self.database_url)

        candidates = restarted_store.search_sections(
            "GB/T 19001-2016",
            relative_path=document["relativePath"],
            limit=20,
        )

        self.assertEqual(candidates[0]["documentId"], document["id"])
        self.assertEqual(restarted_store.status()["bm25ReadyCount"], 1)

    def test_ready_document_backfills_without_knowledge_agent(self) -> None:
        import psycopg

        document = self._add_document(
            "backfill.md",
            [{"heading": "型号", "content": "设备型号 API_V2。"}],
        )
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM ai8_script_bm25_corpora WHERE document_id = %s", (document["id"],))
            cursor.execute("DELETE FROM ai8_script_bm25_terms WHERE document_id = %s", (document["id"],))
            cursor.execute("UPDATE ai8_script_sections SET token_count = 0 WHERE document_id = %s", (document["id"],))

        restarted_store = ScriptKnowledgeStore(self.database_url)
        backfill_result = restarted_store.backfill_bm25_indexes()
        detail = restarted_store.get_document(document["id"])

        self.assertEqual(backfill_result["rebuilt"], 1)
        self.assertEqual(detail["bm25Status"], "ready")
        self.assertGreater(detail["sections"][0]["token_count"], 0)

    def test_query_does_not_rebuild_missing_bm25_index(self) -> None:
        import psycopg

        document = self._add_document(
            "query-no-rebuild.md",
            [{"heading": "检索规则", "content": "普通查询只读取现有派生索引。"}],
        )
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM ai8_script_bm25_corpora WHERE document_id = %s",
                (document["id"],),
            )
            cursor.execute(
                "DELETE FROM ai8_script_bm25_terms WHERE document_id = %s",
                (document["id"],),
            )

        restarted_store = ScriptKnowledgeStore(self.database_url)
        with patch.object(
            restarted_store,
            "initialize",
            side_effect=AssertionError("普通查询不应初始化 Schema 或回填索引"),
        ):
            candidates = restarted_store.search_sections(
                "普通查询",
                relative_path=document["relativePath"],
                limit=20,
            )
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM ai8_script_bm25_corpora WHERE document_id = %s",
                (document["id"],),
            )
            corpus_count = int(cursor.fetchone()[0])

        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["retrievalBackend"], "legacy")
        self.assertEqual(
            candidates[0]["retrievalTrace"]["retrievalBackendFallbackReason"],
            "bm25_index_unavailable",
        )
        self.assertEqual(corpus_count, 0)

    def test_failed_rebuild_rolls_back_to_previous_sections(self) -> None:
        document = self._add_document(
            "atomic.md",
            [{"heading": "旧规则", "content": "旧索引内容。"}],
        )

        with patch(
            "ai8video.knowledge.script_knowledge_bm25.write_bm25_index",
            side_effect=RuntimeError("forced posting failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced posting failure"):
                self.store.replace_document_tree(
                    document["id"],
                    self._tree("atomic.md"),
                    [{"heading": "新规则", "content": "不应提交的新内容。"}],
                )

        detail = self.store.get_document(document["id"])
        self.assertEqual(detail["sections"][0]["heading"], "旧规则")
        self.assertEqual(detail["bm25Status"], "ready")

    def test_content_hash_fence_rejects_stale_ingestion(self) -> None:
        import psycopg

        document = self._add_document(
            "hash-fence.md",
            [{"heading": "旧规则", "content": "旧内容继续可用。"}],
        )
        expected_content_hash = document["contentHash"]
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE ai8_script_documents SET content_hash = %s WHERE id = %s",
                ("f" * 64, document["id"]),
            )

        with self.assertRaisesRegex(RuntimeError, "原文已在知识入库期间更新"):
            self.store.replace_document_tree(
                document["id"],
                self._tree("hash-fence.md"),
                [{"heading": "过期任务", "content": "不应覆盖。"}],
                expected_content_hash=expected_content_hash,
            )

        detail = self.store.get_document(document["id"])
        self.assertEqual(detail["sections"][0]["heading"], "旧规则")

    def test_document_delete_cascades_bm25_rows(self) -> None:
        import psycopg

        document = self._add_document(
            "delete.md",
            [{"heading": "待删除", "content": "删除时清理 postings。"}],
        )

        self.assertTrue(self.store.remove_document(document["relativePath"]))
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM ai8_script_bm25_corpora WHERE document_id = %s), "
                "(SELECT COUNT(*) FROM ai8_script_bm25_terms WHERE document_id = %s)",
                (document["id"], document["id"]),
            )
            corpus_count, posting_count = cursor.fetchone()

        self.assertEqual((corpus_count, posting_count), (0, 0))

    def test_concurrent_backfill_builds_document_once(self) -> None:
        import psycopg

        document = self._add_document(
            "concurrent.md",
            [{"heading": "并发规则", "content": "并发构建必须原子。"}],
        )
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM ai8_script_bm25_corpora WHERE document_id = %s", (document["id"],))

        rebuilt_counts: list[int] = []
        barrier = threading.Barrier(2)

        def run_backfill() -> None:
            local_store = ScriptKnowledgeStore(self.database_url)
            local_store._schema_ready = True
            barrier.wait()
            rebuilt_counts.append(local_store.backfill_bm25_indexes()["rebuilt"])

        workers = [threading.Thread(target=run_backfill) for _index in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(sum(rebuilt_counts), 1)

    def test_shadow_mode_returns_legacy_order_and_records_bm25_candidates(self) -> None:
        document = self._add_document(
            "shadow.md",
            [
                {"heading": "风险", "content": "风险分级和处置。"},
                {"heading": "培训", "content": "员工培训。"},
            ],
        )
        with patch.dict(os.environ, {"AI8VIDEO_SCRIPT_RETRIEVAL_MODE": "legacy"}):
            legacy_candidates = self.store.search_sections(
                "风险",
                relative_path=document["relativePath"],
                limit=20,
            )
        with patch.dict(os.environ, {"AI8VIDEO_SCRIPT_RETRIEVAL_MODE": "shadow"}):
            shadow_candidates = self.store.search_sections(
                "风险",
                relative_path=document["relativePath"],
                limit=20,
            )

        self.assertEqual(
            [candidate["id"] for candidate in shadow_candidates],
            [candidate["id"] for candidate in legacy_candidates],
        )
        trace = shadow_candidates[0]["retrievalTrace"]
        self.assertEqual(trace["retrievalMode"], "shadow")
        self.assertTrue(trace["bm25CandidateIds"])

    def test_legacy_mode_trace_preserves_selected_document_snapshot(self) -> None:
        document = self._add_document(
            "legacy-snapshot.md",
            [{"heading": "快照规则", "content": "配置回滚仍需验证所选文档快照。"}],
        )

        with patch.dict(os.environ, {"AI8VIDEO_SCRIPT_RETRIEVAL_MODE": "legacy"}):
            candidates = self.store.search_sections(
                "快照规则",
                relative_path=document["relativePath"],
                limit=20,
            )

        trace = candidates[0]["retrievalTrace"]
        self.assertEqual(trace["retrievalBackend"], "legacy")
        self.assertEqual(trace["documentId"], document["id"])
        self.assertEqual(trace["contentHash"], document["contentHash"])
        self.assertEqual(trace["indexVersion"], document["bm25IndexVersion"])
        self.assertEqual(trace["tokenizerVersion"], document["bm25TokenizerVersion"])
        self.assertEqual(trace["corpusHash"], document["bm25CorpusHash"])

    def test_bm25_sql_failure_rolls_back_savepoint_before_legacy_fallback(self) -> None:
        document = self._add_document(
            "savepoint-fallback.md",
            [{"heading": "降级规则", "content": "BM25 技术故障只允许同文档 legacy 降级。"}],
        )

        with patch(
            "ai8video.knowledge.script_knowledge_bm25.bm25_section_search_sql",
            return_value="SELECT * FROM ai8video_missing_bm25_relation",
        ):
            candidates = self.store.search_sections(
                "降级规则",
                relative_path=document["relativePath"],
                limit=20,
            )

        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["documentId"], document["id"])
        self.assertEqual(candidates[0]["retrievalBackend"], "legacy")
        self.assertTrue(
            candidates[0]["retrievalTrace"]["retrievalBackendFallbackReason"].startswith(
                "bm25_failed:"
            )
        )

    def test_120_leaf_query_p95_is_below_initial_budget(self) -> None:
        document = self._add_document(
            "performance.md",
            [
                {
                    "heading": f"质量规则 / 第{index + 1}条",
                    "content": f"ISO-9001 风险分级记录 {index + 1}，单位 12.5mg/mL。",
                }
                for index in range(120)
            ],
        )
        latencies: list[float] = []
        for _iteration in range(20):
            started_at = time.perf_counter()
            self.store.search_sections(
                "ISO-9001 风险 12.5mg/mL",
                relative_path=document["relativePath"],
                limit=20,
            )
            latencies.append((time.perf_counter() - started_at) * 1000)

        p95_latency = statistics.quantiles(latencies, n=20)[18]
        self.assertLess(p95_latency, 150)

    def _add_document(
        self,
        relative_path: str,
        leaves: list[dict[str, str]],
    ) -> dict[str, object]:
        content = "\n".join(leaf["content"] for leaf in leaves)
        document_id = self.store.register_source(
            {
                "name": relative_path,
                "relativePath": relative_path,
                "path": f"/tmp/{relative_path}",
                "sizeBytes": len(content.encode("utf-8")),
                "modifiedAt": 100.0,
            },
            content,
        )
        return self.store.replace_document_tree(
            document_id,
            self._tree(relative_path),
            leaves,
        )

    @staticmethod
    def _tree(relative_path: str) -> dict[str, object]:
        return {
            "title": relative_path,
            "summary": "BM25 integration fixture",
            "tags": ["bm25"],
            "tree": [],
        }


if __name__ == "__main__":
    unittest.main()
