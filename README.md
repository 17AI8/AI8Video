<div align="center">
  <img src="desktop/electron/icons/icon.svg" width="112" alt="AI8video Logo">

  <h1>AI8video</h1>

  <p><strong>策划、生成、交付——用一个可控的本地 AI Agent 完成短视频生产</strong></p>

  <p>
    AI8video 是一个开源、本地优先的 AI 短视频生产工作台，帮助内容团队和开发者<br>
    在统一流程中完成需求理解、脚本生成、素材管理、媒体生成、批量任务和结果交付。
  </p>

  <p>
    <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-v0.3.0-0ea5e9?style=flat-square" alt="Python package v0.3.0"></a>
    <img src="https://img.shields.io/badge/status-active-22c55e?style=flat-square" alt="Status Active">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2563eb?style=flat-square" alt="MIT License"></a>
    <img src="https://img.shields.io/badge/Agent-bounded-7c3aed?style=flat-square" alt="Bounded Agent">
    <img src="https://img.shields.io/badge/Web-local-0891b2?style=flat-square" alt="Local Web Workbench">
    <img src="https://img.shields.io/badge/Electron-optional-47848f?style=flat-square" alt="Optional Electron Desktop Shell">
  </p>

  <p>
    <img src="https://img.shields.io/badge/Platforms-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Web-16a34a?style=flat-square" alt="Platforms: Windows, macOS, Linux and Web">
  </p>

  <p>
    <a href="#快速开始从源码运行">快速开始</a> ·
    <a href="ARCHITECTURE.md">架构文档</a> ·
    <a href="CONTRIBUTING.md">参与开发</a> ·
    <a href="https://github.com/17AI8/AI8Video">GitHub</a>
  </p>

  <p><a href="README.md">简体中文</a></p>
</div>

**适合谁用：** 希望用自然语言组织短视频生产、需要批量处理脚本与媒体任务、重视本地素材管理和工作流可控性的内容团队、运营人员与开发者。

`AI8video` **不是** 通用自主 Agent，也不是把任务交给云端托管的 SaaS。模型负责理解意图、补全信息和生成内容；本地 Python Runtime（运行时）负责会话状态、能力选择、安全护栏、任务顺序、媒体处理与结果落盘。Agent 的自主范围被限制在项目内置的短视频能力中，不开放 Shell、任意文件权限或通用网络工具。

---

## 核心功能

| 功能 | 说明 |
|---|---|
| **对话式短视频生产** | 用自然语言描述目标，系统自动理解需求、追问缺失信息，并组织后续脚本与生成步骤。 |
| **脚本生成与拆分** | 将主题、产品资料或长文本整理为可执行的分镜、视频提示词和配音文本。 |
| **剧本知识库** | 管理 TXT、Markdown、DOCX 原稿，使用 PostgreSQL 中文检索、模型查询提炼和 Rerank（重排）为生成提供参考。 |
| **图片与视频生成** | 接入兼容的文本、图像和视频模型，支持参考图、首帧、分段生成与结果审核。 |
| **多 Agent 协作** | Supervisor（监督者）统一编排 Planner（规划者）与 Reviewer（审核者），使用本地任务图、租约和账本管理并发、取消与恢复。 |
| **批量任务** | 统一管理生成批次、任务进度、报告、告警与真实额度保护。 |
| **配音、字幕与合并** | 通过本地 TTS、FFmpeg 完成配音、字幕、背景音乐、分段合并和媒体后处理。 |
| **HTML 动效** | 使用项目自有 WAAPI（Web Animations API）时间线生成动效，按需使用 HyperFrames 检查与渲染。 |
| **热点与爆款拆解** | 聚合公开热点源、生成摘要，并将爆款视频拆解为可复用的创作参考。 |
| **素材与结果管理** | 管理用户素材、生成结果、封面、预览、归档和回收站；最终媒体保留在本地用户目录。 |
| **多入口复用** | Web、CLI 与 Electron 复用同一套 Python Agent Runtime 和本地数据，不复制业务流程。 |

---

## 架构设计

```text
AI8video
├─ 入口层
│  ├─ Web 工作台
│  ├─ CLI
│  └─ Electron 桌面壳（可选）
│
├─ Agent Runtime · application/
│  ├─ 意图理解与信息补全
│  ├─ 会话上下文与状态
│  ├─ 能力选择与工作流编排
│  ├─ Supervisor / Planner / Reviewer 协作
│  └─ 参数校验、额度、幂等与超时护栏
│
├─ 短视频能力层
│  ├─ generation/        脚本、图片、视频与结果审核
│  ├─ batch/             Agent 调度、批量任务、报告、告警与任务账本
│  ├─ knowledge/         剧本知识库、检索与重排
│  ├─ media/             TTS、字幕、合并与 HTML 动效
│  ├─ assets/            素材、结果、归档与回收站
│  ├─ radar/             热点聚合与摘要
│  └─ breakdown/         爆款视频拆解
│
└─ 外部资源适配器
   ├─ 文本、图像与视频模型 API
   ├─ FFmpeg / FFprobe
   ├─ HyperFrames（可选）
   ├─ PostgreSQL（剧本知识库）
   └─ 本地文件系统 / S3（可选）
```

核心设计原则：**Python 是会话、任务状态、业务规则、媒体处理和持久化的唯一真值来源。** Web、CLI 与 Electron 只负责接入；外部资源失败时必须返回真实错误，不伪造成功。

当前消息与用户可编辑业务提示词都是用户输入。生成时必须合并两者全部能够共存的主题、主体、人物、产品、核心关键词、风格、镜头、排版和禁用要求，不能让任何一方把另一方删掉；只有真正无法共存的直接矛盾，才以当前消息处理冲突项。

完整运行闭环、模块职责和依赖规则见 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 多 Agent 协作

AI8video 采用**本地有界多 Agent**，不是让多个通用 Agent 任意调用工具。当前由 Supervisor 负责统一调度，Planner 与 Reviewer 作为受约束的专业角色执行规划和审核；任务状态持久化在本地 SQLite 账本中，并通过依赖关系、租约、心跳、重试和取消规则保证恢复一致性。模型与媒体能力仍由现有 Python Runtime 调用，不拆成独立微服务。

当前 Planner / Reviewer 已进入共享调度器；真实视频提交和媒体处理仍在既有生成流水线中执行。这一边界可以保持计费副作用显式，并避免任务恢复时重复提交外部请求。

多 Agent 调度不改变普通视频生成的产品参数：生成数量、单条时长、并发方式和 `merge2` / `merge4` 合并模式继续以用户请求及本机配置为准，不额外施加 1～5 条、固定 10 秒或强制串行限制。真实模型提交、轮询、媒体后处理与归档具有外部副作用，仍不属于自动重放安全步骤。

`AI8VIDEO_DRY_RUN=1` 下的占位结果会明确标记为模拟内容，并从真实生成结果列表中排除。

---

## 环境要求

- Python `3.10` 至 `3.13`。
- 完整媒体链路需要 FFmpeg 与 FFprobe。请使用系统安装或 LGPL 兼容构建。
- 使用剧本知识库时需要 PostgreSQL `16+`。
- 爆款拆解的台词识别使用 `faster-whisper`；源码启动器会自动安装运行时，模型在首次分析时按需下载。
- 使用可选 HyperFrames HTML 动效时需要 Node.js 与 npm；核心工作台和 CLI 不依赖 Node。
- Electron 桌面壳需要在 `desktop/electron/` 中单独安装 Node 依赖。

需要指定 FFmpeg 路径时设置：

```bash
export AI8VIDEO_FFMPEG_BIN=/absolute/path/to/ffmpeg
export AI8VIDEO_FFPROBE_BIN=/absolute/path/to/ffprobe
```

---

## 快速开始（从源码运行）

```bash
git clone https://github.com/17AI8/AI8Video.git
cd AI8Video
cp mykey_template.py mykey.py
./start_ai8video_web.sh
```

首次启动会自动创建 `.venv`、安装 Python 依赖，并从 `18720-18820` 中选择可用端口。已有环境若缺少 `faster-whisper`，启动器也会自动补装。检测到 Node.js 与 npm 时，启动器还会安装可选的 HyperFrames 依赖；安装失败不会阻止核心工作台启动。

爆款拆解默认使用 `base` 模型进行本地 CPU 台词识别。模型权重不提交到仓库，首次分析时从模型源下载，并缓存到 `用户文件夹/爆款拆解/.model-cache/faster-whisper/`；后续分析复用本地缓存。商业发行若把 Python 运行时和模型一并打包，请同时保留 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) 中列出的许可证声明。

### 各平台入口

| 平台 | 启动方式 |
|---|---|
| macOS | 双击 `双击启动.command`，或执行 `./start_ai8video_web.sh` |
| Linux | 执行 `./start_ai8video_web.sh` |
| Windows | 双击 `双击启动.bat` |

默认服务只监听本机回环地址，启动后访问：

```text
http://127.0.0.1:18720
```

实际端口可能因占用情况自动顺延。

---

## 模型配置

推荐使用环境变量配置兼容的模型服务：

```bash
# 文本模型
export AI8VIDEO_LLM_BASE_URL=https://example.com/v1
export AI8VIDEO_LLM_API_KEY=your-key
export AI8VIDEO_LLM_MODEL=your-text-model

# 多模态模型（可选）
export AI8VIDEO_MULTIMODAL_BASE_URL=https://example.com/v1
export AI8VIDEO_MULTIMODAL_API_KEY=your-key
export AI8VIDEO_MULTIMODAL_MODEL=your-multimodal-model

# 图片模型
export AI8VIDEO_IMAGE_BASE_URL=https://example.com/v1
export AI8VIDEO_IMAGE_API_KEY=your-key
export AI8VIDEO_IMAGE_MODEL=your-image-model

# 视频模型
export AI8VIDEO_VIDEO_BASE_URL=https://example.com/v1
export AI8VIDEO_VIDEO_API_KEY=your-key
export AI8VIDEO_VIDEO_MODEL=your-video-model
```

也可以复制 `mykey_template.py` 为 `mykey.py`，只填写模板中的本地配置。`mykey.py` 与 `.env` 已被 Git 忽略，禁止提交真实密钥。

真实图片与视频生成可能产生费用。建议显式启用额度保护：

```bash
export AI8VIDEO_REAL_JOB_MAX_COUNT=5
export AI8VIDEO_REAL_JOB_WINDOW_SECONDS=3600
```

---

## 剧本知识库

剧本知识库将原始 TXT、Markdown、DOCX 文件保存在 `用户文件夹/用户素材/剧本素材库/`，PostgreSQL 只保存文档元数据、知识段、标签和可重建的检索索引，不替代或删除用户原稿。

创建默认数据库：

```bash
createdb ai8video
```

使用远程数据库或自定义账号时设置：

```bash
export AI8VIDEO_SCRIPT_DATABASE_URL='postgresql://user:password@127.0.0.1:5432/ai8video'
```

当前检索链路：

```text
标题 / 标签精确匹配
        ↓
文本模型提炼检索意图
        ↓
pg_trgm 中文模糊匹配 + tsvector 召回
        ↓
SQL 加权排序 + 模型 Rerank
        ↓
向生成模型注入最相关知识段
        ↓
最终输出审核与可执行修正
```

默认方案不运行本地 Embedding（向量嵌入）模型，数据库索引可随时从用户原稿重建。

---

## CLI 命令

macOS 与 Linux 可使用根目录的 `AI8video` 启动器；Windows 使用 `AI8video.cmd`。

| 命令 | 说明 |
|---|---|
| `./AI8video --version` | 查看当前版本 |
| `./AI8video serve --port 18720` | 启动本地 Web 工作台；端口设为 `0` 时自动选择 |
| `./AI8video status` | 读取已运行工作台的健康状态 |
| `./AI8video config` | 检查本机模型配置，不显示密钥 |
| `./AI8video chat "生成一条 10 秒的产品介绍短视频" --text` | 不启动 Web，直接执行一次短视频对话 |

`chat` 默认输出完整 JSON；增加 `--text` 后只输出回复文本。

---

## 项目结构

```text
src/ai8video/
├─ core/          产品身份、配置、路径和基础数据模型
├─ application/   Agent Runtime、会话、意图理解与应用门面
├─ generation/    脚本拆分、生成流水线、任务与结果审核
├─ batch/         Agent 调度、批量任务、报告、告警、账本和守护进程
├─ assets/        用户素材、生成结果、归档和回收站
├─ knowledge/     剧本知识库、查询、重排和文本处理
├─ media/         FFmpeg、配音、字幕、合并与 HTML 动效
├─ integrations/  文本、图片、视频模型及 HTTP 适配器
├─ radar/         热点聚合与摘要
├─ breakdown/     爆款视频拆解
└─ interfaces/    Web、CLI 和演示入口

desktop/electron/  可选 Electron 桌面壳
tests/             离线质量门禁，不进入运行包
```

---

## 本地数据

以下内容默认只保存在本机，不进入仓库：

| 路径 | 内容 |
|---|---|
| `用户文件夹/` | 用户素材、模型设置、字体选择、生成结果和回收站 |
| `用户文件夹/用户生成结果/` | 最终可见的本地媒体事实源 |
| `media_resources/ai8video/` | 归档视频、批次报告和告警 |
| `temp/ai8video/` | 可丢弃的任务账本、进度和运行时状态 |
| `mykey.py`、`.env` | 本地密钥与配置 |

可通过健康接口查看模型配置、任务状态和本地运行信息：

```bash
curl http://127.0.0.1:18720/api/health
```

---

## Electron

Electron 是可选桌面壳，代码位于 `desktop/electron/`。它负责发现或启动本地 Python Web 服务并承载工作台窗口，短视频业务仍由 Python Runtime 处理。

```bash
cd desktop/electron
npm install
npm run dev
```

可用打包命令：

| 命令 | 说明 |
|---|---|
| `npm run dist:mac` | 构建 macOS DMG |
| `npm run dist:win` | 构建 Windows NSIS 安装包 |

---

## 测试

离线测试不会调用真实模型或触发视频生成：

```bash
AI8VIDEO_DISABLE_MYKEY=1 \
AI8VIDEO_DRY_RUN=1 \
PYTHONPATH=src \
python -m unittest discover -s tests
```

`tests/` 用于防止重构破坏 Agent Runtime、生成、媒体、批量任务、资产管理和 Web 接口。

---

## 参与开发

提交改动前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。项目坚持本地有界单体和纯 Python 核心，不引入无关的通用 Agent 框架、重复入口或仓库内大型二进制。

---

## 安全说明

- Web 服务默认只监听 `127.0.0.1`，不会主动暴露到局域网或公网。
- 用户素材和生成结果默认保存在本地；启用 S3 归档时，只有配置的归档内容会发送到对应存储服务。
- 调用文本、图片、视频或 TTS 模型时，请求会发送到你配置的服务提供商，请自行确认其隐私与计费政策。
- 不要把真实 API Key、数据库密码、用户素材或生成结果提交到仓库。
- FFmpeg 是外部运行时，仓库不分发 FFmpeg 二进制。

---

## License

项目源码采用 [MIT License](LICENSE)。第三方依赖及其许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
