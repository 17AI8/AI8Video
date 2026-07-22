from __future__ import annotations

import concurrent.futures
import json
import os
import re
import tempfile
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import requests


FEED_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AI8video/0.3; +local hot-topic reader)",
    "Accept": "application/json,application/atom+xml,application/rss+xml,application/xml,text/html,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}


@dataclass(frozen=True)
class FeedSource:
    id: str
    name: str
    category: str
    url: str
    parser: str = "xml"
    builtin: bool = True

    def public_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "type": "builtin" if self.builtin else "custom",
            "url": self.url,
            "parser": self.parser,
        }


@dataclass(frozen=True)
class FeedEntry:
    title: str
    url: str
    description: str = ""
    published_at: str = ""
    heat: str | int | None = None


BUILTIN_FEED_SOURCES = (
    FeedSource("weibo", "微博热搜", "中文热榜", "https://tophub.today/n/KqndgxeLl9", "rank-html"),
    FeedSource("zhihu", "知乎热榜", "中文热榜", "https://tophub.today/n/mproPpoq6O", "rank-html"),
    FeedSource(
        "bilibili",
        "B站热搜",
        "视频趋势",
        "https://api.bilibili.com/x/web-interface/search/square?limit=30",
        "bilibili-json",
    ),
    FeedSource("v2ex", "V2EX 最新主题", "技术社区", "https://www.v2ex.com/index.xml"),
    FeedSource("hackernews", "Hacker News Best", "国际科技", "https://hnrss.org/best"),
    FeedSource("nodeseek", "NodeSeek", "技术社区", "https://rss.nodeseek.com"),
    FeedSource("sspai", "少数派", "数字生活", "https://sspai.com/feed"),
    FeedSource("solidot", "Solidot", "科技资讯", "https://www.solidot.org/index.rss"),
)


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = str(data or "").strip()
        if text:
            self.parts.append(text)


class _RankTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[FeedEntry] = []
        self._in_row = False
        self._capture_link = False
        self._href = ""
        self._title_parts: list[str] = []
        self._row_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._begin_row()
        elif tag == "a" and self._in_row and not self._href:
            self._href = str(dict(attrs).get("href") or "").strip()
            self._capture_link = bool(self._href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._capture_link = False
        elif tag == "tr" and self._in_row:
            self._finish_row()

    def handle_data(self, data: str) -> None:
        if not self._in_row:
            return
        text = re.sub(r"\s+", " ", str(data or "")).strip()
        if not text:
            return
        self._row_parts.append(text)
        if self._capture_link:
            self._title_parts.append(text)

    def _begin_row(self) -> None:
        self._in_row = True
        self._capture_link = False
        self._href = ""
        self._title_parts = []
        self._row_parts = []

    def _finish_row(self) -> None:
        title = re.sub(r"\s+", " ", " ".join(self._title_parts)).strip()
        if title and self._href:
            heat = _rank_row_heat(self._row_parts, title)
            description = f"榜单热度：{heat}" if heat else ""
            self.entries.append(FeedEntry(title, self._href, description, heat=heat or None))
        self._in_row = False
        self._capture_link = False


def load_source_registry(config_path: Path) -> dict[str, FeedSource]:
    registry = {source.id: source for source in BUILTIN_FEED_SOURCES}
    for source in _load_custom_sources(config_path):
        registry[source.id] = source
    return registry


def save_custom_sources(config_path: Path, raw_items: object) -> list[FeedSource]:
    if not isinstance(raw_items, list):
        raise ValueError("feeds 必须是数组")
    sources: list[FeedSource] = []
    seen_ids = {source.id for source in BUILTIN_FEED_SOURCES}
    for raw in raw_items:
        source = _normalize_custom_source(raw)
        if source is None:
            raise ValueError("自定义数据源必须包含有效的标识、名称和 HTTP(S) 地址")
        if source.id in seen_ids:
            raise ValueError(f"数据源标识重复：{source.id}")
        seen_ids.add(source.id)
        sources.append(source)
    _write_source_config(config_path, sources)
    return sources


def _write_source_config(config_path: Path, sources: list[FeedSource]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"feeds": [{"id": item.id, "name": item.name, "category": item.category,
                           "url": item.url, "parser": item.parser} for item in sources]}
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=config_path.parent, delete=False) as handle:
            temp_path = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, config_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def registry_signature(registry: dict[str, FeedSource], source_ids: list[str]) -> str:
    import hashlib

    serialized = json.dumps(
        [[source_id, registry[source_id].name, registry[source_id].category,
          registry[source_id].url, registry[source_id].parser] for source_id in source_ids],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]


def fetch_source_payloads(
    registry: dict[str, FeedSource],
    source_ids: list[str],
    timeout_seconds: int,
) -> dict[str, dict[str, Any]]:
    def fetch(source_id: str) -> tuple[str, dict[str, Any]]:
        try:
            entries = fetch_feed_entries(registry[source_id], timeout_seconds)
            return source_id, {"entries": entries}
        except Exception as exc:  # pragma: no cover - depends on public sources
            return source_id, {"error": str(exc)}

    worker_count = min(8, max(1, len(source_ids)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        return dict(executor.map(fetch, source_ids))


def fetch_feed_entries(source: FeedSource, timeout_seconds: int) -> list[FeedEntry]:
    response = requests.get(
        source.url,
        headers=FEED_REQUEST_HEADERS,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    content = str(response.text or "").strip()
    if not content:
        raise RuntimeError("数据源返回空内容")
    entries = parse_feed_entries(source, content)
    if not entries:
        raise RuntimeError("数据源没有可用条目")
    return entries[:30]


def parse_feed_entries(source: FeedSource, content: str) -> list[FeedEntry]:
    if source.parser == "rank-html":
        return _parse_rank_html(content)
    if source.parser == "bilibili-json":
        return _parse_bilibili_json(content)
    return _parse_xml(content)


def _load_custom_sources(config_path: Path) -> list[FeedSource]:
    payload = _read_json(config_path)
    raw_items = payload.get("feeds") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return []
    sources: list[FeedSource] = []
    for item in raw_items:
        source = _normalize_custom_source(item)
        if source is not None:
            sources.append(source)
    return sources


def _normalize_custom_source(raw: object) -> FeedSource | None:
    if not isinstance(raw, dict):
        return None
    source_id = re.sub(r"[^a-z0-9_-]+", "-", str(raw.get("id") or "").strip().lower()).strip("-")
    name = str(raw.get("name") or "").strip()
    category = str(raw.get("category") or "自定义").strip() or "自定义"
    url = str(raw.get("url") or "").strip()
    parser = str(raw.get("parser") or "xml").strip().lower()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if not source_id or not name or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parser not in {"xml", "rank-html", "bilibili-json"}:
        parser = "xml"
    return FeedSource(source_id, name, category, url, parser, builtin=False)


def _parse_rank_html(content: str) -> list[FeedEntry]:
    parser = _RankTableParser()
    parser.feed(content)
    parser.close()
    return parser.entries


def _rank_row_heat(parts: list[str], title: str) -> str:
    row_text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    row_text = re.sub(r"^\d+\.\s*", "", row_text)
    if title in row_text:
        row_text = row_text.split(title, 1)[1]
    return re.sub(r"[\s]+", " ", row_text).strip()


def _parse_bilibili_json(content: str) -> list[FeedEntry]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"B站热搜 JSON 无法解析：{exc}") from exc
    raw_items = payload.get("data", {}).get("trending", {}).get("list", [])
    entries: list[FeedEntry] = []
    for rank, item in enumerate(raw_items, 1):
        title = str(item.get("show_name") or item.get("keyword") or "").strip()
        if not title:
            continue
        url = str(item.get("url") or "").strip() or f"https://search.bilibili.com/all?keyword={quote(title)}"
        entries.append(FeedEntry(title, url, heat=max(1, 101 - rank)))
    return entries


def _parse_xml(content: str) -> list[FeedEntry]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise RuntimeError(f"订阅源 XML 无法解析：{exc}") from exc
    entries: list[FeedEntry] = []
    for entry in (node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}):
        title = _entry_text(entry, ("title",))
        if not title:
            continue
        entries.append(
            FeedEntry(
                title=title,
                url=_entry_link(entry),
                description=_entry_text(entry, ("description", "summary", "content"))[:500],
                published_at=_entry_text(entry, ("pubDate", "published", "updated", "date")),
            )
        )
    return entries


def _entry_text(entry: ElementTree.Element, names: tuple[str, ...]) -> str:
    wanted = set(names)
    for child in entry:
        if _local_name(child.tag) not in wanted:
            continue
        text = " ".join(part.strip() for part in child.itertext() if part and part.strip())
        if text:
            return _clean_markup(text)
    return ""


def _entry_link(entry: ElementTree.Element) -> str:
    for child in entry:
        if _local_name(child.tag) != "link":
            continue
        return str(child.attrib.get("href") or child.text or "").strip()
    return ""


def _clean_markup(value: str) -> str:
    parser = _PlainTextParser()
    try:
        parser.feed(unescape(str(value or "")))
        parser.close()
    except Exception:
        return re.sub(r"\s+", " ", str(value or "")).strip()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _local_name(tag: object) -> str:
    return str(tag or "").rsplit("}", 1)[-1]


def _read_json(path: Path) -> dict[str, Any] | list[Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, (dict, list)) else {}
