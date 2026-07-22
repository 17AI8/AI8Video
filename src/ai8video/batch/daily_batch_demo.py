from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai8video.batch.daily_batch_runner import DailyBatchRunner


DEFAULT_MESSAGES = [
    "直接用这个提示词生成一条视频，讲素材散落导致交付反复返工，风格真实克制，不用参考图。",
    "直接用这个提示词生成一条视频，讲团队如何把脚本、素材和成片集中管理，风格像负责人向团队复盘，不用参考图。",
    "直接用这个提示词生成一条视频，讲 AI8video 如何完成批量生成和结果交付，风格简洁专业，不用参考图。",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="AI8video 批量任务离线演示")
    parser.add_argument("--messages-file", help="文本文件，每行一条候选消息")
    parser.add_argument("--target-pass-count", type=int, default=3)
    parser.add_argument("--initial-budget", type=int, default=5)
    parser.add_argument("--max-budget", type=int, default=8)
    args = parser.parse_args()

    messages = DEFAULT_MESSAGES
    if args.messages_file:
        path = Path(args.messages_file)
        messages = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    runner = DailyBatchRunner(
        target_pass_count=args.target_pass_count,
        initial_candidate_budget=args.initial_budget,
        max_candidate_budget=args.max_budget,
    )
    report = runner.run(messages)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
