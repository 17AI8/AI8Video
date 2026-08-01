from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ai8video.core.config import AI8VideoConfig
from ai8video.knowledge.script_knowledge import ScriptKnowledgeStore
from ai8video.knowledge.script_knowledge_rerank import (
    build_script_rerank_llm,
    rerank_candidates,
)


DEFAULT_CASES_PATH = Path(__file__).with_name("script_knowledge_golden_cases.json")


def main() -> int:
    arguments = _parse_arguments()
    _assert_safe_database(arguments.database_url, arguments.allow_non_test_database)
    cases = json.loads(arguments.cases.read_text(encoding="utf-8"))
    store = ScriptKnowledgeStore(arguments.database_url)
    store.initialize()
    relative_paths = _seed_documents(store, cases["documents"])
    original_mode = os.environ.get("AI8VIDEO_SCRIPT_RETRIEVAL_MODE")
    try:
        rerank_llm = _build_optional_reranker(arguments.with_reranker)
        report = {
            "casesPath": str(arguments.cases),
            "database": _database_name(arguments.database_url),
            "legacy": _evaluate_mode(store, cases["queries"], "legacy", rerank_llm),
            "bm25": _evaluate_mode(store, cases["queries"], "bm25", rerank_llm),
        }
        report["gate"] = _evaluate_release_gate(report["legacy"], report["bm25"])
    finally:
        _restore_mode(original_mode)
        if not arguments.keep_fixtures:
            for relative_path in relative_paths:
                store.remove_document(relative_path)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output + "\n", encoding="utf-8")
    return 0 if report["gate"]["passed"] else 1


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate legacy and BM25 leaf retrieval")
    parser.add_argument(
        "--database-url",
        default=os.getenv("AI8VIDEO_TEST_POSTGRES_URL", ""),
        help="PostgreSQL test database URL",
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--with-reranker", action="store_true")
    parser.add_argument("--keep-fixtures", action="store_true")
    parser.add_argument("--allow-non-test-database", action="store_true")
    arguments = parser.parse_args()
    if not arguments.database_url:
        parser.error("--database-url or AI8VIDEO_TEST_POSTGRES_URL is required")
    return arguments


def _assert_safe_database(database_url: str, allow_non_test_database: bool) -> None:
    database_name = _database_name(database_url)
    if not allow_non_test_database and not database_name.endswith("_test"):
        raise SystemExit(
            "Golden evaluation only writes to a database ending in '_test'; "
            "pass --allow-non-test-database to override explicitly."
        )


def _database_name(database_url: str) -> str:
    parsed = urlparse(database_url)
    if parsed.scheme:
        return parsed.path.strip("/")
    return str(database_url).rsplit("/", 1)[-1]


def _seed_documents(
    store: ScriptKnowledgeStore,
    documents: list[dict[str, Any]],
) -> list[str]:
    relative_paths: list[str] = []
    for document in documents:
        relative_path = str(document["relativePath"])
        leaves = list(document["leaves"])
        content = "\n".join(str(leaf["content"]) for leaf in leaves)
        store.remove_document(relative_path)
        document_id = store.register_source(
            {
                "name": Path(relative_path).name,
                "relativePath": relative_path,
                "path": f"/tmp/{Path(relative_path).name}",
                "sizeBytes": len(content.encode("utf-8")),
                "modifiedAt": 100.0,
            },
            content,
        )
        store.replace_document_tree(
            document_id,
            {
                "title": str(document.get("title") or Path(relative_path).stem),
                "summary": "Golden retrieval fixture",
                "tags": ["golden"],
                "tree": [],
            },
            leaves,
        )
        relative_paths.append(relative_path)
    return relative_paths


def _evaluate_mode(
    store: ScriptKnowledgeStore,
    queries: list[dict[str, Any]],
    mode: str,
    rerank_llm: Any,
) -> dict[str, Any]:
    os.environ["AI8VIDEO_SCRIPT_RETRIEVAL_MODE"] = mode
    results = []
    answerable_hits_at_20 = 0
    answerable_hits_at_3 = 0
    answerable_count = 0
    no_answer_correct = 0
    no_answer_count = 0
    rerank_hits_at_5 = 0
    candidate_count = 0
    scope_leakage_count = 0
    for case in queries:
        expected_relative_path = str(case["relativePath"])
        candidates = store.search_sections(
            str(case["query"]),
            relative_path=expected_relative_path,
            limit=20,
        )
        candidate_count += len(candidates)
        scope_leakage_count += sum(
            str(candidate.get("relativePath") or "") != expected_relative_path
            for candidate in candidates
        )
        expected_heading = str(case.get("expectedHeadingContains") or "")
        headings = [str(candidate.get("heading") or "") for candidate in candidates]
        if expected_heading:
            answerable_count += 1
            answerable_hits_at_20 += int(_contains_heading(headings[:20], expected_heading))
            answerable_hits_at_3 += int(_contains_heading(headings[:3], expected_heading))
        else:
            no_answer_count += 1
            no_answer_correct += int(not candidates)
        reranked = rerank_candidates(
            str(case["query"]),
            candidates,
            llm=rerank_llm,
            top_k=5,
        )
        reranked_headings = [
            str(candidate.get("heading") or "")
            for candidate in reranked["candidates"]
        ]
        if expected_heading:
            rerank_hits_at_5 += int(_contains_heading(reranked_headings, expected_heading))
        results.append({
            "id": case["id"],
            "expectedHeadingContains": expected_heading,
            "candidateIds": [int(candidate["id"]) for candidate in candidates],
            "headings": headings,
            "hitAt3": _contains_heading(headings[:3], expected_heading) if expected_heading else None,
            "rerankApplied": bool(reranked["rerankApplied"]),
            "rerankHeadings": reranked_headings,
        })
    return {
        "answerableCount": answerable_count,
        "recallAt20": _ratio(answerable_hits_at_20, answerable_count),
        "hitAt3": _ratio(answerable_hits_at_3, answerable_count),
        "postRerankHitAt5": _ratio(rerank_hits_at_5, answerable_count),
        "noAnswerCount": no_answer_count,
        "noAnswerPrecision": _ratio(no_answer_correct, no_answer_count),
        "candidateCount": candidate_count,
        "scopeLeakageCount": scope_leakage_count,
        "scopeLeakageRate": _ratio(scope_leakage_count, candidate_count),
        "results": results,
    }


def _evaluate_release_gate(
    legacy_report: dict[str, Any],
    bm25_report: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    comparable_metrics = (
        "recallAt20",
        "hitAt3",
        "postRerankHitAt5",
        "noAnswerPrecision",
    )
    for metric_name in comparable_metrics:
        legacy_value = float(legacy_report.get(metric_name) or 0)
        bm25_value = float(bm25_report.get(metric_name) or 0)
        if bm25_value < legacy_value:
            failures.append(
                f"{metric_name} regressed: legacy={legacy_value:.4f}, bm25={bm25_value:.4f}"
            )
    if int(bm25_report.get("scopeLeakageCount") or 0) != 0:
        failures.append("scopeLeakageCount must remain zero")
    return {"passed": not failures, "failures": failures}


def _build_optional_reranker(enabled: bool) -> Any:
    if not enabled:
        return None
    return build_script_rerank_llm(AI8VideoConfig.from_env())


def _contains_heading(headings: list[str], expected_heading: str) -> bool:
    return bool(expected_heading) and any(expected_heading in heading for heading in headings)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def _restore_mode(original_mode: str | None) -> None:
    if original_mode is None:
        os.environ.pop("AI8VIDEO_SCRIPT_RETRIEVAL_MODE", None)
    else:
        os.environ["AI8VIDEO_SCRIPT_RETRIEVAL_MODE"] = original_mode


if __name__ == "__main__":
    raise SystemExit(main())
