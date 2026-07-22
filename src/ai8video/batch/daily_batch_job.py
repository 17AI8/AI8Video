from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai8video.application.runtime import run_batch_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="AI8video 批量短视频正式执行入口")
    parser.add_argument("--seed-file", type=str, help="逐行候选内容文件路径")
    parser.add_argument(
        "--seed-message",
        action="append",
        dest="seed_messages",
        default=[],
        help="直接追加一条候选内容，可重复传入",
    )
    parser.add_argument("--target-pass-count", type=int, default=30)
    parser.add_argument("--style-hint", type=str, default="")
    parser.add_argument("--session-id", type=str, default="daily-batch-job")
    parser.add_argument("--source", type=str, default="cli")
    parser.add_argument("--trigger", type=str, default="daily_batch_job")
    parser.add_argument("--refresh-runtime", action="store_true")
    args = parser.parse_args()

    seed_messages = _collect_seed_messages(seed_file=args.seed_file, seed_messages=args.seed_messages)
    if not seed_messages:
        raise SystemExit("缺少候选内容。请通过 --seed-file、--seed-message 或 stdin 提供逐行候选。")

    payload = run_batch_payload(
        seed_messages,
        target_pass_count=max(1, int(args.target_pass_count or 30)),
        style_hint=args.style_hint.strip() or None,
        trigger=args.trigger,
        source=args.source,
        session_id=args.session_id.strip() or None,
        refresh=args.refresh_runtime,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _collect_seed_messages(seed_file: str | None, seed_messages: list[str]) -> list[str]:
    items = [item.strip() for item in seed_messages if item and item.strip()]
    if seed_file:
        path = Path(seed_file).expanduser()
        items.extend(_split_lines(path.read_text(encoding="utf-8")))
    if not items and not sys.stdin.isatty():
        items.extend(_split_lines(sys.stdin.read()))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line and line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
