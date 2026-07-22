from __future__ import annotations

import argparse
import json
import sys
from urllib.error import URLError
from urllib.request import urlopen

from ai8video import __version__


DEFAULT_URL = "http://127.0.0.1:18720"


def _serve(port: int) -> int:
    from ai8video.interfaces.web import app as ai8video_web

    original_argv = sys.argv
    sys.argv = ["ai8video_web", "--port", str(port)] if port else ["ai8video_web"]
    try:
        return ai8video_web.main()
    finally:
        sys.argv = original_argv


def _read_health(url: str) -> dict:
    health_url = f"{url.rstrip('/')}/api/health"
    with urlopen(health_url, timeout=3) as response:
        return json.load(response)


def _status(url: str) -> int:
    try:
        health = _read_health(url)
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        print(f"未运行：{exc}")
        return 1
    print(json.dumps({
        "url": url.rstrip("/"),
        "chatBackend": health.get("chatBackend"),
        "dryRun": health.get("dryRun"),
        "hasLLM": health.get("hasLLM"),
        "hasVideoModel": health.get("hasVideoModel"),
        "hasImageModel": health.get("hasImageModel"),
    }, ensure_ascii=False, indent=2))
    return 0


def _config_status() -> int:
    from ai8video.application.facade import get_config_status_payload

    print(json.dumps(get_config_status_payload(), ensure_ascii=False, indent=2))
    return 0


def _chat(message: str, session_id: str, timeout_seconds: int, text_only: bool) -> int:
    from ai8video.application.facade import handle_chat

    payload = handle_chat(
        session_id=session_id,
        message=message,
        timeout_seconds=max(10, timeout_seconds),
    )
    if text_only:
        reply = payload.get("reply") if isinstance(payload, dict) else None
        print(str((reply or {}).get("text") or ""))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    reply = payload.get("reply") if isinstance(payload, dict) else None
    return 1 if payload.get("error") or (reply or {}).get("stage") == "error" else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="AI8video",
        description="AI8video 本地运行入口",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="启动本地 Web 工作台")
    serve.add_argument("--port", type=int, default=0, help="监听端口；0 表示自动选择")

    status = subparsers.add_parser("status", help="读取已运行工作台的健康状态")
    status.add_argument("--url", default=DEFAULT_URL, help="工作台地址")

    subparsers.add_parser("config", help="检查本机模型配置，不显示密钥")

    chat = subparsers.add_parser("chat", help="不启动 Web，直接执行一次短视频对话")
    chat.add_argument("message", help="要交给 AI8video 的任务描述")
    chat.add_argument("--session", default="cli", help="会话标识")
    chat.add_argument("--timeout", type=int, default=300, help="等待秒数")
    chat.add_argument("--text", action="store_true", help="只输出回复文本")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {None, "serve"}:
        return _serve(getattr(args, "port", 0))
    if args.command == "status":
        return _status(args.url)
    if args.command == "config":
        return _config_status()
    if args.command == "chat":
        return _chat(args.message, args.session, args.timeout, args.text)
    return 2
