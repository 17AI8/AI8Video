#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "正在启动 AI8video 工作台..."
echo "提示：这个终端窗口就是本地服务窗口，关闭后浏览器页面会断开。"
exec "$SCRIPT_DIR/start_ai8video_web.sh"
