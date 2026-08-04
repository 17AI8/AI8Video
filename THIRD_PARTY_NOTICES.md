# 第三方软件声明

本项目源码采用 MIT License。以下组件通过包管理器安装或作为外部运行时调用，不复制其项目源码到本仓库；使用和再分发时应同时遵守各自许可证。

本仓库额外分发的字体文件不适用项目 MIT License，而是分别按照其原始许可证分发。完整字体许可证见 [`licenses/SourceHanSerif-OFL-1.1.txt`](licenses/SourceHanSerif-OFL-1.1.txt)。

| 组件 | 锁定或支持版本 | 许可证 | 用途 |
|---|---:|---|---|
| HyperFrames | `0.7.84` | Apache-2.0 | HTML 动效检查与渲染 |
| adm-zip | `0.6.0` | MIT | HyperFrames 间接依赖的安全覆盖版本 |
| sharp / libvips | HyperFrames 间接依赖 | Apache-2.0 / LGPL-3.0-or-later | 图像处理与跨平台二进制运行时 |
| Bottle | `>=0.12` | MIT | 本地 HTTP 服务 |
| Requests | `>=2.28` | Apache-2.0 | HTTP 客户端 |
| Psycopg | `>=3.3,<4` | LGPL-3.0-or-later | PostgreSQL 剧本知识库驱动 |
| Pillow | `>=9.0` | HPND | 图片和文字渲染 |
| boto3 | `>=1.34` | Apache-2.0 | 可选 S3 归档 |
| faster-whisper | `>=1.2,<2` | MIT | 爆款拆解的本地台词识别 |
| CTranslate2 | `>=4,<5`（faster-whisper 间接依赖） | MIT | Whisper 模型推理运行时 |
| PyAV / FFmpeg libraries | `>=11`（faster-whisper 间接依赖） | BSD-3-Clause / LGPL-3.0-or-later | 音视频解码；PyPI wheel 会携带 FFmpeg 动态库 |
| Systran faster-whisper-base | 运行时按需下载 | MIT | 默认台词识别模型权重 |
| Electron | `43.2.0` | MIT | 桌面客户端运行时 |
| Font Awesome Free Desktop | `7.3.1` | CC BY 4.0（SVG 图标）/ MIT（许可证文本） | 仅分发界面实际引用的 37 个 Solid SVG 图标与许可证 |
| Source Han Serif SC Bold / Heavy | 当前仓内版本 | SIL OFL-1.1 | 可选本地字幕与花字渲染字体 |
| PyInstaller | `6.21.0` | GPL-2.0-or-later，带 Bootloader Exception | 将 Python 后端冻结为桌面发行运行时 |
| electron-builder | `26.15.3` | MIT | DMG 与 NSIS 安装包构建 |

项目调用 FFmpeg 与 FFprobe 处理媒体，但仓库不分发其二进制。本机应安装符合部署要求的 FFmpeg 构建；当前 macOS 本地开发运行时使用关闭 `GPL` 和 `nonfree` 的 LGPL 2.1-or-later 构建。

仓库只声明 `faster-whisper` 依赖，不复制其源码、PyAV wheel、FFmpeg 动态库或模型权重。源码运行时由包管理器安装依赖，并在首次台词识别时下载模型。若发行方将这些二进制或模型打入商业安装包，应按实际发行平台复核其许可证，保留对应声明，并满足 LGPL 动态库可替换与再分发要求。

热点雷达只在运行时读取公开 RSS/Atom、公开榜单页面和公开 API，不打包第三方服务的源码或文章内容。数据内容及商标归各发布方所有；自定义数据源由使用者自行确认授权与使用条款。

上游项目：

- HyperFrames：[heygen-com/hyperframes](https://github.com/heygen-com/hyperframes)
- Psycopg：[psycopg.org](https://www.psycopg.org/psycopg3/)
- faster-whisper：[SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- PyAV：[PyAV-Org/PyAV](https://github.com/PyAV-Org/PyAV)
- faster-whisper-base：[Systran/faster-whisper-base](https://huggingface.co/Systran/faster-whisper-base)
- FFmpeg：[ffmpeg.org](https://ffmpeg.org/)
- Font Awesome Free：[FortAwesome/Font-Awesome](https://github.com/FortAwesome/Font-Awesome/tree/7.3.1)
- Source Han Serif：[adobe-fonts/source-han-serif](https://github.com/adobe-fonts/source-han-serif)
