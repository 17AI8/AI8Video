"""Web 传输层的查询解析、同源保护与本地线程服务器。"""

import json
from urllib.parse import parse_qs, urlsplit

from bottle import HTTPResponse, ServerAdapter, request, response


LOOPBACK_CORS_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
UNSAFE_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def read_query_string_value(current_request, field_name: str) -> str:
    environ = getattr(current_request, "environ", {}) or {}
    raw_query_string = str(environ.get("QUERY_STRING") or "")
    if raw_query_string:
        parsed = parse_qs(raw_query_string, keep_blank_values=True, encoding="utf-8", errors="strict")
        values = parsed.get(field_name)
        if values:
            return str(values[-1])
    query = getattr(current_request, "query", {}) or {}
    return str(query.get(field_name) or "")


class ThreadingWSGIRefServer(ServerAdapter):
    def run(self, app):  # pragma: no cover - exercised by local launcher
        from socketserver import ThreadingMixIn
        from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

        class QuietHandler(WSGIRequestHandler):
            def address_string(self):
                return self.client_address[0]

            def log_request(*args, **kwargs):
                if not self.quiet:
                    return WSGIRequestHandler.log_request(*args, **kwargs)

        class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
            daemon_threads = True
            allow_reuse_address = True

        self.srv = make_server(
            self.host,
            self.port,
            app,
            server_class=ThreadingWSGIServer,
            handler_class=QuietHandler,
        )
        self.port = self.srv.server_port
        try:
            self.srv.serve_forever()
        finally:
            self.srv.server_close()


def allowed_cors_origin(origin: str | None, host: str | None, path: str) -> str | None:
    normalized_origin = str(origin or "").strip()
    if not normalized_origin:
        return None
    try:
        origin_parts = urlsplit(normalized_origin)
        request_parts = urlsplit(f"{origin_parts.scheme}://{str(host or '').strip()}")
    except ValueError:
        return None
    is_valid = (
        origin_parts.scheme in {"http", "https"}
        and not origin_parts.username
        and not origin_parts.password
        and origin_parts.path in {"", "/"}
        and not origin_parts.query
        and not origin_parts.fragment
        and origin_parts.hostname in LOOPBACK_CORS_HOSTS
    )
    if not is_valid:
        return None
    if origin_parts.hostname != request_parts.hostname or origin_parts.port != request_parts.port:
        return None
    return normalized_origin.rstrip("/")


def should_reject_untrusted_browser_write(
    method: str | None,
    origin: str | None,
    host: str | None,
    path: str,
) -> bool:
    normalized_origin = str(origin or "").strip()
    if str(method or "").upper() not in UNSAFE_HTTP_METHODS or not normalized_origin:
        return False
    return allowed_cors_origin(normalized_origin, host, path) is None


def install_transport_hooks(app):
    @app.hook("before_request")
    def reject_untrusted_browser_writes():
        if not should_reject_untrusted_browser_write(
            request.method,
            request.headers.get("Origin"),
            request.headers.get("Host"),
            request.path,
        ):
            return
        raise HTTPResponse(
            status=403,
            body=json.dumps({"error": "不允许来自当前本地工作台之外的写操作请求。"}),
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )

    @app.hook("after_request")
    def add_cors_headers():
        if not response.headers.get("Cache-Control"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Vary"] = "Origin"
        allowed_origin = allowed_cors_origin(
            request.headers.get("Origin"),
            request.headers.get("Host"),
            request.path,
        )
        if allowed_origin:
            response.headers["Access-Control-Allow-Origin"] = allowed_origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"

    return reject_untrusted_browser_writes, add_cors_headers
