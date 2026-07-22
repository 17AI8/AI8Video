"""热点雷达 HTTP 路由。"""

from bottle import HTTPResponse, request, response

from ai8video.core.config import AI8VideoConfig
from ai8video.interfaces.web.transport import read_query_string_value
from ai8video.radar.hot_topic import (
    build_hot_topic_prompt,
    list_hot_topic_sources,
    list_hot_topics,
    summarize_hot_topic,
    update_hot_topic_sources,
)


def api_hot_topic_sources():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    if request.method == "GET":
        return list_hot_topic_sources()
    payload = request.json or {}
    try:
        feeds = payload.get("feeds") if isinstance(payload, dict) else None
        return update_hot_topic_sources(feeds)
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


def api_hot_topics():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    query = lambda field: read_query_string_value(request, field).strip()
    try:
        return list_hot_topics(
            sources=query("sources") or None,
            category=query("category") or None,
            keyword=query("keyword") or None,
            force_refresh=query("refresh") in {"1", "true", "yes"},
        )
    except RuntimeError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


def api_hot_topic_summary():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return summarize_hot_topic(payload.get("topic"), config=AI8VideoConfig.from_env())
    except (ValueError, RuntimeError) as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


def api_hot_topic_to_prompt():
    if request.method == "OPTIONS":
        return HTTPResponse(status=204)
    payload = request.json or {}
    if not isinstance(payload, dict):
        response.status = 400
        return {"ok": False, "error": "payload must be an object"}
    try:
        return build_hot_topic_prompt(payload.get("topic"))
    except ValueError as exc:
        response.status = 400
        return {"ok": False, "error": str(exc)}


def register_hot_topic_routes(app) -> None:
    app.route("/api/hot-topics/sources", method=["GET", "POST", "OPTIONS"])(api_hot_topic_sources)
    app.route("/api/hot-topics", method=["GET", "OPTIONS"])(api_hot_topics)
    app.route("/api/hot-topics/summary", method=["POST", "OPTIONS"])(api_hot_topic_summary)
    app.route("/api/hot-topics/to-prompt", method=["POST", "OPTIONS"])(api_hot_topic_to_prompt)
