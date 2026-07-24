@echo off
setlocal EnableExtensions
chcp 65001 >nul

cd /d "%~dp0"

rem 兼容旧版真实生成保护变量；Python 侧会统一迁移其余 AI8VIDEO_ 配置。
if not defined AI8VIDEO_REAL_JOB_MAX_COUNT if defined AI8MINIVIDEO_REAL_JOB_MAX_COUNT set "AI8VIDEO_REAL_JOB_MAX_COUNT=%AI8MINIVIDEO_REAL_JOB_MAX_COUNT%"
if not defined AI8VIDEO_REAL_JOB_WINDOW_SECONDS if defined AI8MINIVIDEO_REAL_JOB_WINDOW_SECONDS set "AI8VIDEO_REAL_JOB_WINDOW_SECONDS=%AI8MINIVIDEO_REAL_JOB_WINDOW_SECONDS%"
if not defined AI8VIDEO_REAL_JOB_FORCE_DURATION_SECONDS if defined AI8MINIVIDEO_REAL_JOB_FORCE_DURATION_SECONDS set "AI8VIDEO_REAL_JOB_FORCE_DURATION_SECONDS=%AI8MINIVIDEO_REAL_JOB_FORCE_DURATION_SECONDS%"

echo 正在启动 AI8video 工作台...
echo 提示：这个终端窗口就是本地服务窗口，关闭后浏览器页面会断开。

set "PYTHON_BIN="
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if exist "%VENV_PY%" (
  set "PYTHON_BIN=%VENV_PY%"
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_BIN=%%P"
  )
  if not defined PYTHON_BIN (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_BIN=python"
  )
)

if not defined PYTHON_BIN (
  echo 没有找到可用的 Python。请先安装 Python 3.10-3.13。
  pause
  exit /b 1
)

if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,14) else 1)" >nul 2>nul
  if errorlevel 1 (
    echo 检测到旧版 Python 虚拟环境，正在重建为 Python 3.10-3.13 兼容环境...
    rmdir /s /q "%~dp0.venv"
    set "PYTHON_BIN="
    where py >nul 2>nul
    if not errorlevel 1 (
      for /f "delims=" %%P in ('py -3 -c "import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,14) else 1); print(sys.executable)" 2^>nul') do set "PYTHON_BIN=%%P"
    )
    if not defined PYTHON_BIN (
      where python >nul 2>nul
      if not errorlevel 1 set "PYTHON_BIN=python"
    )
  )
)

if not exist "%VENV_PY%" (
  "%PYTHON_BIN%" -c "import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,14) else 1)" >nul 2>nul
  if errorlevel 1 (
    echo 当前 Python 版本不兼容。请安装 Python 3.10-3.13。
    "%PYTHON_BIN%" -V
    pause
    exit /b 1
  )
)

if not exist "%VENV_PY%" (
  echo 首次启动：正在创建项目本地 Python 环境...
  "%PYTHON_BIN%" -m venv "%~dp0.venv"
  if errorlevel 1 (
    echo 创建本地 Python 环境失败。请确认 Python 安装时启用了 venv。
    pause
    exit /b 1
  )
  set "PYTHON_BIN=%VENV_PY%"
)

for /f "delims=" %%U in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$found=''; foreach($p in 18720..18820){ try { $u='http://127.0.0.1:'+$p+'/api/health'; $r=Invoke-WebRequest -UseBasicParsing -Uri $u -TimeoutSec 1; if($r.StatusCode -eq 200){ Start-Sleep -Milliseconds 500; $r2=Invoke-WebRequest -UseBasicParsing -Uri $u -TimeoutSec 1; if($r2.StatusCode -eq 200){ $found='http://127.0.0.1:'+$p; break } } } catch {} }; Write-Output $found" 2^>nul') do set "EXISTING_URL=%%U"

if defined EXISTING_URL (
  echo AI8video 工作台已经在运行: %EXISTING_URL%
  start "" "%EXISTING_URL%"
  exit /b 0
)

echo 使用 Python: %PYTHON_BIN%
echo 检查 AI8video 工作台依赖...
"%PYTHON_BIN%" -c "import importlib; [importlib.import_module(name) for name in ('bottle','requests','charset_normalizer','PIL','faster_whisper')]"
if errorlevel 1 (
  echo 依赖缺失或原生扩展异常，正在修复最小短视频工作台依赖...
  "%PYTHON_BIN%" -m pip install --upgrade pip setuptools wheel
  if errorlevel 1 (
    echo pip 基础工具更新失败，请检查网络或 Python 环境。
    pause
    exit /b 1
  )
  "%PYTHON_BIN%" -m pip install --force-reinstall --no-cache-dir charset-normalizer chardet
  if errorlevel 1 (
    echo 字符编码依赖修复失败，请检查网络或 Python 环境。
    pause
    exit /b 1
  )
  "%PYTHON_BIN%" -m pip install -e ".[ai8video]"
  if errorlevel 1 (
    echo 依赖安装失败，请检查网络或 Python 环境。
    pause
    exit /b 1
  )
)

"%PYTHON_BIN%" -c "import boto3, psycopg" >nul 2>nul
if errorlevel 1 echo 提示：S3 归档或 PostgreSQL 知识库的可选运行时不可用，核心工作台仍会继续启动。

set "HAS_NODE_RUNTIME=1"
where node >nul 2>nul
if errorlevel 1 set "HAS_NODE_RUNTIME=0"
where npm >nul 2>nul
if errorlevel 1 set "HAS_NODE_RUNTIME=0"
if "%HAS_NODE_RUNTIME%"=="1" (
  if not exist "%~dp0node_modules\hyperframes\package.json" (
    echo 正在安装可选的 HTML 动效依赖...
    call npm ci
    if errorlevel 1 echo 提示：HTML 动效依赖安装失败，核心工作台仍会继续启动。
  )
) else (
  echo 提示：未检测到 Node.js/npm；核心工作台可正常使用，HTML 动效功能暂不可用。
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo 提示：未检测到系统 FFmpeg；视频合并、字幕、配音和动效烧录会在调用时失败。
  echo 请安装 LGPL 兼容构建，或设置 AI8VIDEO_FFMPEG_BIN / AI8VIDEO_FFPROBE_BIN。
)

for /f "delims=" %%P in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach($p in 18720..18820){ $listener=$null; try { $listener=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Parse('127.0.0.1'), $p); $listener.Start(); $listener.Stop(); Write-Output $p; break } catch { if($listener){ try { $listener.Stop() } catch {} } } }" 2^>nul') do set "PORT=%%P"

if not defined PORT (
  echo 18720-18820 端口都被占用，无法启动工作台。
  pause
  exit /b 1
)

set "URL=http://127.0.0.1:%PORT%"
echo 浏览器地址: %URL%
if defined AI8VIDEO_REAL_JOB_MAX_COUNT (
  if defined AI8VIDEO_REAL_JOB_WINDOW_SECONDS (
    echo 真实生成保护: 每窗口最多 %AI8VIDEO_REAL_JOB_MAX_COUNT% 条，窗口 %AI8VIDEO_REAL_JOB_WINDOW_SECONDS% 秒
  ) else (
    echo 真实生成保护: 未启用额度硬限制（如需限制，请显式设置 AI8VIDEO_REAL_JOB_MAX_COUNT / AI8VIDEO_REAL_JOB_WINDOW_SECONDS）
  )
) else (
  echo 真实生成保护: 未启用额度硬限制（如需限制，请显式设置 AI8VIDEO_REAL_JOB_MAX_COUNT / AI8VIDEO_REAL_JOB_WINDOW_SECONDS）
)
start "" "%URL%"

set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
"%PYTHON_BIN%" -m ai8video.interfaces.web.app --port %PORT%

echo.
echo AI8video 工作台已退出。
pause
