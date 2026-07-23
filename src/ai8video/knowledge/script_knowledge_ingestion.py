from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ai8video.core.config import AI8VideoConfig
from ai8video.integrations.llm_provider import build_openai_compat_llm
from ai8video.knowledge.script_knowledge import get_script_knowledge_store


TreeLLM = Callable[[str], str]
MAX_EVENTS = 320


@dataclass
class KnowledgeIngestionJob:
    document_id: int
    state: str = "queued"
    error: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def emit(self, stage: str, text: str, *, kind: str = "step") -> None:
        event = {"stage": stage, "text": str(text or ""), "kind": kind, "at": time.time()}
        with self._lock:
            self.events.append(event)
            del self.events[:-MAX_EVENTS]

    def payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "documentId": self.document_id,
                "state": self.state,
                "error": self.error,
                "events": list(self.events),
            }


class KnowledgeIngestionManager:
    def __init__(self) -> None:
        self._jobs: dict[int, KnowledgeIngestionJob] = {}
        self._lock = threading.Lock()

    def start(self, document_id: int, config: AI8VideoConfig) -> KnowledgeIngestionJob:
        with self._lock:
            active = self._jobs.get(document_id)
            if active and active.state in {"queued", "running"}:
                return active
            job = KnowledgeIngestionJob(document_id=document_id)
            self._jobs[document_id] = job
        thread = threading.Thread(target=_run_job, args=(job, config), daemon=True)
        thread.start()
        return job

    def status(self, document_id: int) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(document_id)
        return job.payload() if job else {"documentId": document_id, "state": "idle", "error": "", "events": []}


_manager = KnowledgeIngestionManager()


def start_script_knowledge_ingestion(document_id: int, config: AI8VideoConfig) -> dict[str, Any]:
    return _manager.start(int(document_id), config).payload()


def script_knowledge_ingestion_status(document_id: int) -> dict[str, Any]:
    return _manager.status(int(document_id))


def _run_job(job: KnowledgeIngestionJob, config: AI8VideoConfig) -> None:
    job.state = "running"
    job.emit("prepare", "正在读取原始剧本")
    try:
        store = get_script_knowledge_store()
        document = store.get_document(job.document_id)
        llm = _build_tree_llm(config, job)
        if llm is None:
            raise RuntimeError("未配置文本模型，无法执行知识入库")
        job.emit("tree", "正在由模型建立文档目录与知识段")
        result = parse_tree_result(llm(build_tree_prompt(document)))
        leaves = flatten_tree_leaves(result["tree"])
        if not leaves:
            raise RuntimeError("模型没有返回可入库的知识段")
        job.emit("persist", f"正在保存 {len(leaves)} 个结构化知识段")
        store.replace_document_tree(job.document_id, result, leaves)
        job.state = "succeeded"
        job.emit("done", "知识入库完成")
    except Exception as exc:
        job.error = _safe_error(exc)
        job.state = "failed"
        job.emit("failed", job.error)


def _build_tree_llm(config: AI8VideoConfig, job: KnowledgeIngestionJob) -> TreeLLM | None:
    return build_openai_compat_llm(
        config,
        timeout_seconds=max(config.timeout_seconds, 90),
        system_prompt="你是知识库结构化专家。必须只输出合法 JSON，不得使用 Markdown 代码块。",
        on_delta=lambda delta: job.emit("tree", delta, kind="delta"),
    )


def build_tree_prompt(document: dict[str, Any]) -> str:
    content = str(document.get("content") or "").strip()
    return f"""请把以下剧本建立成可检索的文档树。
要求：
1. 只基于原文，不补充事实；叶子 content 必须保留原文语义，不写空内容。
2. tree 最多三层；父节点只放 title、summary、children；叶子放 title、summary、content。
3. 叶子应是可独立回答一个问题的完整语义段，建议 200 到 900 字。
4. 仅输出 JSON：{{\"title\":\"...\",\"summary\":\"...\",\"tags\":[\"...\"],\"tree\":[...]}}。

文档名称：{document.get("name") or "未命名剧本"}
原文：
{content}
"""


def parse_tree_result(value: str) -> dict[str, Any]:
    payload = _parse_json(value)
    if not isinstance(payload, dict):
        raise RuntimeError("模型返回的知识树不是对象")
    tree = _normalize_nodes(payload.get("tree"), depth=0)
    if not tree:
        raise RuntimeError("模型返回的知识树为空")
    return {
        "title": _clean_text(payload.get("title"), 200),
        "summary": _clean_text(payload.get("summary"), 2000),
        "tags": _normalize_tags(payload.get("tags")),
        "tree": tree,
    }


def flatten_tree_leaves(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    for node in tree:
        _collect_leaves(node, [], leaves)
    return leaves


def _collect_leaves(node: dict[str, Any], parents: list[str], leaves: list[dict[str, Any]]) -> None:
    path = [*parents, node["title"]]
    children = node.get("children") or []
    if children:
        for child in children:
            _collect_leaves(child, path, leaves)
        return
    leaves.append({"heading": " / ".join(path), "content": node["content"], "path": path})


def _normalize_nodes(value: Any, *, depth: int) -> list[dict[str, Any]]:
    if depth > 2 or not isinstance(value, list):
        return []
    nodes: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"), 200)
        children = _normalize_nodes(item.get("children"), depth=depth + 1)
        content = _clean_text(item.get("content"), 2400)
        if not title or (not children and not content):
            continue
        node = {"title": title, "summary": _clean_text(item.get("summary"), 1000)}
        if children:
            node["children"] = children
        else:
            node["content"] = content
        nodes.append(node)
    return nodes[:120]


def _parse_json(value: str) -> Any:
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(value or "").strip(), flags=re.IGNORECASE)
    try:
        return json.loads(clean)
    except json.JSONDecodeError as exc:
        raise RuntimeError("模型返回不是有效 JSON，请重试") from exc


def _clean_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _normalize_tags(value: Any) -> list[str]:
    values = value if isinstance(value, list) else []
    return [tag for tag in (_clean_text(item, 40) for item in values) if tag][:12]


def _safe_error(exc: Exception) -> str:
    return str(exc).splitlines()[0].strip() or "知识入库失败"
