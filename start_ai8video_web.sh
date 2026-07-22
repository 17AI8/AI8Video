#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
export PATH="$HOME/.local/bin:$PATH"

# 兼容旧环境变量；新变量优先，后续进程只读取 AI8VIDEO_ 前缀。
while IFS='=' read -r legacy_name legacy_value; do
  if [[ "$legacy_name" == AI8MINIVIDEO_* ]]; then
    current_name="AI8VIDEO_${legacy_name#AI8MINIVIDEO_}"
    if [[ -z "${!current_name-}" ]]; then
      export "$current_name=$legacy_value"
    fi
  fi
done < <(env)

LOCAL_PYROOT="$PROJECT_DIR/.python/Python.framework/Versions/3.12"
if [[ -x "$LOCAL_PYROOT/bin/python3.12" ]]; then
  export DYLD_LIBRARY_PATH="$LOCAL_PYROOT/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
  export PATH="$LOCAL_PYROOT/bin:$PATH"
  export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-pypi.org files.pythonhosted.org}"
fi

choose_python() {
  if [[ -x "$LOCAL_PYROOT/bin/python3.12" ]]; then
    printf '%s\n' "$LOCAL_PYROOT/bin/python3.12"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    return 1
  fi
}

python_is_supported() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if (3, 10) <= sys.version_info < (3, 14) else 1)
PY
}

PYTHON_BIN="$(choose_python || true)"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "没有找到可用的 Python。请先安装 Python 3.10-3.13。"
  read -r -p "按回车退出..."
  exit 1
fi

if [[ -x "$PROJECT_DIR/.venv/bin/python" ]] && ! python_is_supported "$PROJECT_DIR/.venv/bin/python"; then
  echo "检测到旧版 Python 虚拟环境，正在重建为 Python 3.10-3.13 兼容环境..."
  if [[ "$(uname -s)" == "Darwin" ]]; then
    xattr -dr com.apple.quarantine "$PROJECT_DIR/.venv" >/dev/null 2>&1 || true
    xattr -dr com.apple.provenance "$PROJECT_DIR/.venv" >/dev/null 2>&1 || true
  fi
  LEGACY_VENV="$PROJECT_DIR/.venv.py$(date +%Y%m%d%H%M%S).legacy"
  if ! mv "$PROJECT_DIR/.venv" "$LEGACY_VENV" 2>/dev/null; then
    rm -rf "$PROJECT_DIR/.venv" 2>/dev/null || true
  fi
  if [[ -e "$PROJECT_DIR/.venv" ]]; then
    echo "旧虚拟环境无法自动移走，请关闭占用它的终端或 Python 进程后重试。"
    exit 1
  fi
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  if ! python_is_supported "$PYTHON_BIN"; then
    echo "当前 Python 版本不兼容。请安装 Python 3.10-3.13。"
    "$PYTHON_BIN" -V || true
    read -r -p "按回车退出..."
    exit 1
  fi
  echo "首次启动：正在创建项目本地 Python 环境..."
  "$PYTHON_BIN" -m venv "$PROJECT_DIR/.venv"
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
else
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
  xattr -dr com.apple.quarantine "$PROJECT_DIR/.venv" >/dev/null 2>&1 || true
  xattr -dr com.apple.provenance "$PROJECT_DIR/.venv" >/dev/null 2>&1 || true
fi

CERTIFI_BUNDLE="$("$PYTHON_BIN" - <<'PY' 2>/dev/null || true
try:
    import certifi
except Exception:
    raise SystemExit(0)
print(certifi.where())
PY
)"
if [[ -n "$CERTIFI_BUNDLE" && -f "$CERTIFI_BUNDLE" ]]; then
  export SSL_CERT_FILE="$CERTIFI_BUNDLE"
  export REQUESTS_CA_BUNDLE="$CERTIFI_BUNDLE"
fi

EXISTING_URL=""
if [[ "${AI8VIDEO_SKIP_EXISTING_CHECK:-0}" != "1" ]]; then
EXISTING_URL="$("$PYTHON_BIN" - <<'PY'
from urllib.request import urlopen
import time

def healthy(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.25) as resp:
            return resp.status == 200
    except Exception:
        return False

for port in range(18720, 18821):
    if healthy(port):
        time.sleep(0.5)
        if healthy(port):
            print(f"http://127.0.0.1:{port}")
            break
PY
)"
fi
if [[ -n "$EXISTING_URL" ]]; then
  echo "AI8video 工作台已经在运行: ${EXISTING_URL}"
  if [[ "$(uname -s)" == "Darwin" && "${AI8VIDEO_NO_OPEN:-0}" != "1" ]]; then
    open "$EXISTING_URL" >/dev/null 2>&1 || true
  fi
  exit 0
fi

echo "使用 Python: $PYTHON_BIN"
echo "检查 AI8video 工作台依赖..."
if ! "$PYTHON_BIN" - <<'PY'
import importlib
modules = ("bottle", "requests", "charset_normalizer", "PIL")
missing = []
for name in modules:
    try:
        importlib.import_module(name)
    except Exception:
        missing.append(name)
raise SystemExit(1 if missing else 0)
PY
then
  echo "依赖缺失或原生扩展被系统拦截，正在修复短视频工作台依赖..."
  "$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel
  "$PYTHON_BIN" -m pip install --force-reinstall --no-cache-dir charset-normalizer chardet
  "$PYTHON_BIN" -m pip install -e '.[ai8video]'
  if [[ "$(uname -s)" == "Darwin" ]]; then
    xattr -dr com.apple.quarantine "$PROJECT_DIR/.venv" >/dev/null 2>&1 || true
    xattr -dr com.apple.provenance "$PROJECT_DIR/.venv" >/dev/null 2>&1 || true
  fi
fi

OPTIONAL_IMPORT_FAILURES="$("$PYTHON_BIN" - <<'PY'
import importlib

modules = ("boto3", "psycopg")
failures = []
for name in modules:
    try:
        importlib.import_module(name)
    except Exception as exc:
        failures.append(f"{name}: {exc.__class__.__name__}")
print(", ".join(failures))
PY
)"
if [[ -n "$OPTIONAL_IMPORT_FAILURES" ]]; then
  echo "提示：可选运行时不可用（${OPTIONAL_IMPORT_FAILURES}）。核心工作台仍会启动；S3 归档或 PostgreSQL 知识库可能不可用。"
  echo "macOS 若提示系统策略拦截，请换用 Python 3.12/3.13 后重建 .venv。"
fi

if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  if [[ ! -f "$PROJECT_DIR/node_modules/hyperframes/package.json" ]]; then
    echo "正在安装可选的 HTML 动效依赖..."
    if ! npm ci; then
      echo "提示：HTML 动效依赖安装失败，工作台仍会启动；使用动效功能前请修复 npm 环境。"
    fi
  fi
else
  echo "提示：未检测到 Node.js/npm；核心工作台可正常使用，HTML 动效功能暂不可用。"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "提示：未检测到系统 FFmpeg；视频合并、字幕、配音和动效烧录会在调用时失败。"
  echo "请安装 LGPL 兼容构建，或设置 AI8VIDEO_FFMPEG_BIN / AI8VIDEO_FFPROBE_BIN。"
fi

if [[ -n "${AI8VIDEO_SHORTVIDEO_WEB_PORT:-}" ]]; then
  PORT="$AI8VIDEO_SHORTVIDEO_WEB_PORT"
else
PORT="$("$PYTHON_BIN" - <<'PY'
import socket
for port in range(18720, 18821):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port)
        break
else:
    raise SystemExit("18720-18820 端口都被占用，无法启动工作台。")
PY
)"
fi

URL="http://127.0.0.1:${PORT}"
echo "正在启动 AI8video 工作台..."
echo "浏览器地址: ${URL}"

if [[ -n "${AI8VIDEO_REAL_JOB_MAX_COUNT:-}" && -n "${AI8VIDEO_REAL_JOB_WINDOW_SECONDS:-}" ]]; then
  echo "真实生成保护: 每窗口最多 ${AI8VIDEO_REAL_JOB_MAX_COUNT} 条，窗口 ${AI8VIDEO_REAL_JOB_WINDOW_SECONDS} 秒${AI8VIDEO_REAL_JOB_FORCE_DURATION_SECONDS:+，强制 ${AI8VIDEO_REAL_JOB_FORCE_DURATION_SECONDS} 秒}"
else
  echo "真实生成保护: 未启用额度硬限制（如需限制，请显式设置 AI8VIDEO_REAL_JOB_MAX_COUNT / AI8VIDEO_REAL_JOB_WINDOW_SECONDS）"
fi

if [[ "$(uname -s)" == "Darwin" && "${AI8VIDEO_NO_OPEN:-0}" != "1" ]]; then
  (sleep 2; open "$URL" >/dev/null 2>&1 || true) &
fi

export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" -m ai8video.interfaces.web.app --port "$PORT"
