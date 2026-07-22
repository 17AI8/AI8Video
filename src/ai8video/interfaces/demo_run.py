from __future__ import annotations

import argparse
import json

from ai8video.generation.pipeline import AI8VideoPipeline


DEFAULT_MESSAGE = (
    "我贴一段 3 集剧本，你帮我拆成 3 条短视频。风格简洁专业。"
    "剧本：第一集讲素材散落造成返工；第二集讲统一管理脚本和参考图；"
    "第三集讲 AI8video 如何批量生成并汇总结果。"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI8video 生成流水线离线演示")
    parser.add_argument("message", nargs="?", default=DEFAULT_MESSAGE)
    args = parser.parse_args()

    result = AI8VideoPipeline().run_from_message(args.message)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
