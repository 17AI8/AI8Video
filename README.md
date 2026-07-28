<div align="center">
  <img src="src/ai8video/interfaces/web/static/images/ai8video-brand-logo.png" width="260" alt="AI8video Logo">

  <h1>AI8video</h1>

  <p><strong>从选题、脚本、素材到生成、精剪与交付的本地 AI 短视频工作台</strong></p>

  <p>
    把热点研究、知识召回、脚本规划、图片与视频生成、配音动效、批量监督和本地资产管理<br>
    放进一个可观察、可编辑、可恢复的有界 AI Agent 工作流。
  </p>

  <p>
    <a href="pyproject.toml"><img src="https://img.shields.io/badge/version-0.3.0-4f6dff?style=flat-square" alt="Version 0.3.0"></a>
    <img src="https://img.shields.io/badge/Python-3.10--3.13-0ea5e9?style=flat-square&logo=python&logoColor=white" alt="Python 3.10 to 3.13">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-22c55e?style=flat-square" alt="MIT License"></a>
    <img src="https://img.shields.io/badge/runtime-local--first-0891b2?style=flat-square" alt="Local-first Runtime">
    <img src="https://img.shields.io/badge/Agent-bounded-7c3aed?style=flat-square" alt="Bounded Agent">
    <a href="https://github.com/17AI8/AI8Video/stargazers"><img src="https://img.shields.io/github/stars/17AI8/AI8Video?style=flat-square" alt="GitHub Stars"></a>
  </p>

  <p>
    <img src="https://img.shields.io/badge/Windows-supported-2563eb?style=flat-square&logo=windows" alt="Windows">
    <img src="https://img.shields.io/badge/macOS-supported-111827?style=flat-square&logo=apple" alt="macOS">
    <img src="https://img.shields.io/badge/Linux-supported-f59e0b?style=flat-square&logo=linux&logoColor=white" alt="Linux">
    <img src="https://img.shields.io/badge/Web-127.0.0.1-16a34a?style=flat-square" alt="Local Web">
  </p>

  <p>
    <a href="#快速开始">快速开始</a> ·
    <a href="#核心能力">核心能力</a> ·
    <a href="#典型工作流">工作流</a> ·
    <a href="#模型与运行环境">模型配置</a> ·
    <a href="#架构">架构</a> ·
    <a href="CONTRIBUTING.md">参与开发</a>
  </p>
</div>

---

## AI8video 是什么

AI8video 是一个开源、本地优先的 AI 短视频生产工作台。它面向内容团队、运营人员和开发者，用自然语言串联选题研究、脚本规划、素材管理、媒体生成、后期编辑、批量任务和结果交付。

模型负责理解意图、提炼知识和生成内容；本地 Python Runtime（运行时）负责会话状态、任务顺序、安全护栏、媒体处理和结果落盘。所有自主决策都被限制在项目内置的短视频能力中，不开放 Shell、任意文件访问或通用网络工具。

| 适合 | 不适合 |
|---|---|
| 希望用自然语言组织短视频生产流程 | 需要无限自主、可任意调用系统工具的通用 Agent |
| 需要本地管理素材、脚本、结果和编辑状态 | 希望把全部素材交给云端托管的 SaaS |
| 需要批量生成、进度跟踪、失败重试和结果归档 | 需要开箱即用的公网多人协作服务 |
| 希望按需接入自己的文本、图片、视频与 TTS 服务 | 不准备配置任何模型或媒体运行环境 |

## 为什么选择 AI8video

| 本地可控 | 端到端生产 | 有界 Agent | 可继续编辑 |
|---|---|---|---|
| 用户素材、项目状态和最终结果默认保存在本机 | 从热点、知识、脚本到生成、精剪和归档是一条完整链路 | 外部副作用显式执行，生成、归档等步骤不会被盲目自动重放 | 视频、TTS、背景音乐和 HTML 动效保持独立图层，确认后再烧录 |

| 批量监督 | 多模型接入 | 失败可恢复 | 多入口复用 |
|---|---|---|---|
| 支持日目标、预算门禁、报告、告警和 macOS launchd 守护 | 文本、图片、多模态和多种视频模板可独立配置 | 任务账本、租约、心跳、取消、重试和回收站保留真实状态 | Web、CLI 与可选 Electron 桌面壳共享同一 Python Runtime |

## 快速开始

### 一键启动

```bash
git clone https://github.com/17AI8/AI8Video.git
cd AI8Video
./start_ai8video_web.sh
```

启动器会自动：

- 检查 Python `3.10–3.13`，创建项目内 `.venv`；
- 安装 `.[ai8video]` 依赖；
- 检测 FFmpeg、PostgreSQL 与可选运行时；
- 在检测到 Node.js/npm 且缺少依赖时运行 `npm ci`；
- 从 `18720–18820` 选择可用端口；
- 在 macOS 自动打开浏览器，可通过 `AI8VIDEO_NO_OPEN=1` 禁用。

各平台也可以直接使用：

| 平台 | 启动方式 |
|---|---|
| macOS | 双击 `双击启动.command`，或执行 `./start_ai8video_web.sh` |
| Windows | 双击 `双击启动.bat` |
| Linux | 执行 `./start_ai8video_web.sh` |

默认服务仅监听本机：

```text
http://127.0.0.1:18720
```

实际端口可能因占用情况自动顺延。

> [!IMPORTANT]
> `AI8VIDEO_DRY_RUN` 默认关闭，也就是程序默认允许真实模型任务。首次冒烟建议先执行 `export AI8VIDEO_DRY_RUN=1`，确认配置和流程正确后再切换真实生成。

### 手动安装

```bash
python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[ai8video]'

# 仅 HTML 动效和完整测试需要，要求 Node.js 22+
npm ci

ai8video serve --port 0
```

## 核心能力

### 1. 对话式策划与生成

- 识别新任务、会话跟进、改写、重新分集和信息补全；
- 支持普通生成、智能分集和手动批量；
- 智能计划可展开、编辑、保存并在刷新后恢复；
- 支持单段视频与 2 段、4 段连续生成，使用尾帧衔接保持连贯；
- 真实提交、轮询、下载、校验、后处理与归档均展示明确状态；
- 失败任务支持单条重试、缺失首帧重建和安全停止。

### 2. 热点雷达与剧本知识库

- 内置微博、知乎、B站、V2EX、Hacker News、NodeSeek、少数派、Solidot 等 8 个公开源；
- 支持自定义 RSS/Atom、并行抓取、过滤去重、缓存降级和事实约束总结；
- 热点可以一键转换为创作提示词并填入主工作台；
- TXT、Markdown、DOCX 原稿保留在本地，PostgreSQL 保存可重建的检索结构；
- 使用标题/标签匹配、`pg_trgm`、`tsvector`、查询提炼和模型 Rerank（重排）完成召回；
- 支持临时知识和爆款拆解知识树，不必先污染长期素材库。

### 3. 爆款视频拆解

- 按视频时长自动抽取最多 188 张带序号截图；
- 宫格可切换比例、点击查看原图，并保持时间轴对应关系；
- 使用 Whisper 生成台词时间轴，支持编辑、删除、拖动、定位和重新配音；
- 将全部截图按序号和时间范围提交给多模态模型，生成镜头语言分析；
- 在有台词或无台词场景下都可重建脚本骨架；
- 当前前端会把确认后的脚本填回主创作框，由用户发送后进入生成，不会在拆解结束时偷偷直接提交付费任务。

### 4. 智能修图画布

- 图片素材库与本地导入统一进入无限画布；
- 支持图层、选择、拖拽、局部蒙版、裁剪、比例预览、滤镜、撤销与重做；
- 支持 X1～X8 并行调用图片模型，进度实时回填；
- 同一图层保留原图和多个独立生成结果；
- 画布、图层、结果、提示词和批量数量自动保存；
- 支持保存到素材库和 PNG 导出。

### 5. 配音、声音克隆与精剪

- 当前 TTS 引擎为 MiMo API，本地使用 FFmpeg 完成转码、时长适配和音量处理；
- 支持上传 MP3、WAV 或视频创建克隆音色；
- TTS 时间轴支持试听、拖拽、删除、按停顿智能切块和独立重生成；
- 当前草稿可通过系统“保存为”对话框导出 `192 kbps` MP3；
- 视频胶片支持定位、剪切、删除与拖动；
- 视频、TTS、背景音乐和 HTML 动效共享播放时钟，但各自保持独立编辑状态。

### 6. HTML 动效与媒体后处理

- Agent 生成基于 WAAPI（Web Animations API）的透明 HTML 动效层；
- 动效方案会经过渲染、修复和 `0–100` 分审核；
- 时间轴 Chunk 可删除、移动、切分并立即预览；
- 确认烧录时按“视频裁剪 → TTS → 背景音乐 → HTML 透明层”生成正式视频；
- 归档阶段不会自动生成 HTML 动效，必须由用户在结果预览中明确触发和确认。

### 7. 批量监督与任务系统

- 按日目标扩展候选，达到目标、预算或停止条件后收敛；
- 运行前探测模型、存储和真实归档链路；
- 输出批次报告、通过率、失败原因和告警；
- SQLite 任务账本保存状态、依赖、CAS（比较并交换）版本、租约、心跳和取消事件；
- 只有明确标记为可重放的纯观察任务可以自动恢复；
- macOS 支持安装、检查和卸载 launchd 批量监督器。

### 8. 素材、结果与回收站

- 统一管理图片、脚本、花字、水印和背景音乐素材；
- `@素材名` 可将本地素材展开到当前任务；
- 生成结果包含视频、封面、预览、manifest 和编辑元数据；
- 支持本地归档，或按需配置 S3 兼容存储；
- 失败任务的视频和相关文件也会被保留；
- 回收站支持原子恢复，并延续可用的编辑元数据。

## 典型工作流

```mermaid
flowchart LR
    Goal["用户目标"] --> Research{"需要选题或参考？"}
    Research -->|热点| Radar["热点雷达"]
    Research -->|原稿| Knowledge["剧本知识库"]
    Research -->|直接创作| Plan["脚本规划 / 分集"]
    Radar --> Plan
    Knowledge --> Plan
    Plan --> Review["编辑并确认计划"]
    Review --> Generate["图片 / 视频模型任务"]
    Generate --> Track["五阶段进度跟踪"]
    Track --> Archive["下载、校验、封面、归档"]
    Archive --> Edit["视频 / TTS / 音乐 / HTML 动效"]
    Edit --> Deliver["确认烧录与本地交付"]
    Deliver --> Extend["续写、合并、重试或回收"]
```

<details>
<summary><strong>智能分集与手动批量</strong></summary>

- Planner 根据全文容量、单条时长和内容完整性规划独立视频数量；
- 正文中的普通数字不会被误判为数量；
- 手动模式可固定 `1–12` 条；
- 尾帧串联时，后续视频明确等待上一条结果，不会伪装成并行；
- 确认计划后才进入真实模型提交。

</details>

<details>
<summary><strong>爆款拆解到主创作</strong></summary>

1. 上传视频并生成宫格截图；
2. 转录台词并编辑时间轴；
3. 完成全量镜头语言分析；
4. 生成并编辑脚本骨架；
5. 将临时知识和脚本填入主创作框；
6. 用户发送后进入标准规划与生成流程。

</details>

<details>
<summary><strong>后期编辑与正式落盘</strong></summary>

编辑阶段始终保留纯净基础视频和独立图层。裁剪视频不会自动改写 TTS，移动 HTML Chunk 也不会触发视频或配音重渲染。只有点击确认烧录后，系统才按确定顺序合成正式媒体。

</details>

## 工作台体验

| 能力 | 行为 |
|---|---|
| 可折叠玻璃侧栏 | 展开时展示品牌、资源和工具信息；折叠后变为统一图标轨，并记忆本地状态 |
| 统一工具入口 | 当前进度、图片素材、剧本知识、回收站、智能修图、热点雷达和爆款拆解使用同一导航语言 |
| 五阶段任务链 | 依次展示理解需求、规划任务、提交生成、生成视频和归档结果；只有当前阶段显示状态环 |
| 后台任务跟踪 | 页面持续回填等待时间、任务事件和生成结果，可明确终止 |
| 三种背景 | 网格、点阵和纯色背景可循环切换并持久化 |
| 对话动效 | 新消息淡入，清空对话时气泡淡出，并遵循系统减少动态效果偏好 |
| 本地设置 | 模型、接口、TTS、视频参数和系统偏好统一在工作台内管理 |

## 模型与运行环境

### 系统要求

| 组件 | 要求 | 用途 |
|---|---|---|
| Python | `3.10–3.13` | 核心 Web、CLI、Agent Runtime 和媒体编排 |
| FFmpeg / FFprobe | 建议系统安装 LGPL 兼容构建 | 视频合并、裁剪、花字、配音、音乐和动效烧录 |
| Node.js / npm | HTML 动效和完整测试要求 Node.js `22+` | HyperFrames Worker 与 Node 子进程测试 |
| PostgreSQL | 可选，建议 `16+` | 剧本知识库；角色需能创建并使用 `pg_trgm`、表和索引 |
| `zenity` | Linux 可选 | TTS MP3 图形化“保存为”窗口 |
| Electron | 可选 | 桌面窗口和本地 Python 服务发现/启动 |

核心 Web 与 CLI 不强制安装 Node.js，也不要求 GPU。爆款拆解默认使用 `faster-whisper` 的 `base` 模型进行本地 CPU 转录，首次分析时按需下载模型。

需要指定 FFmpeg 路径时：

```bash
export AI8VIDEO_FFMPEG_BIN=/absolute/path/to/ffmpeg
export AI8VIDEO_FFPROBE_BIN=/absolute/path/to/ffprobe
```

### 模型接入

| 类别 | 当前接入方式 |
|---|---|
| 文本模型 | OpenAI 兼容 Base URL、API Key 和模型名 |
| 多模态模型 | 可独立配置；缺失时继承文本模型地址和凭据 |
| 图片模型 | OpenAI 兼容图片接口，可独立配置并由用户设置覆盖模型名 |
| 视频模型 | 豆包 Seedance、云雾 Grok、云雾 Omni、云雾 Veo、百炼 Wan 等直连模板 |
| TTS | MiMo API；接口、密钥、模型、音色和克隆设置由本地设置界面管理 |
| 归档 | 本地目录；可选 S3 兼容对象存储 |

推荐通过环境变量配置核心模型：

```bash
# 文本模型
export AI8VIDEO_LLM_BASE_URL=https://example.com/v1
export AI8VIDEO_LLM_API_KEY=your-key
export AI8VIDEO_LLM_MODEL=your-text-model

# 多模态模型（可选）
export AI8VIDEO_MULTIMODAL_BASE_URL=https://example.com/v1
export AI8VIDEO_MULTIMODAL_API_KEY=your-key
export AI8VIDEO_MULTIMODAL_MODEL=your-multimodal-model

# 图片模型（可选）
export AI8VIDEO_IMAGE_BASE_URL=https://example.com/v1
export AI8VIDEO_IMAGE_API_KEY=your-key
export AI8VIDEO_IMAGE_MODEL=your-image-model

# 视频模型
export AI8VIDEO_VIDEO_BASE_URL=https://example.com
export AI8VIDEO_VIDEO_API_KEY=your-key
export AI8VIDEO_VIDEO_MODEL=your-video-model
export AI8VIDEO_VIDEO_TEMPLATE=doubao-seedance
```

配置优先使用对应环境变量；多模态和图片凭据缺失时可继承文本模型；文本模型缺失时兼容读取本地 `mykey.py`；模型名称还可由工作台用户设置覆盖。需要本地模板时执行：

```bash
cp mykey_template.py mykey.py
```

> [!NOTE]
> 项目不会自动加载 `.env`。如需使用 `.env`，请由 Shell、IDE 或进程管理器显式加载。`.env`、`mykey.py` 和用户设置文件都已被 Git 忽略，禁止提交真实密钥。

### 真实任务安全门禁

首次验证建议：

```bash
export AI8VIDEO_DRY_RUN=1
```

启用真实模型前，建议同时配置额度保护：

```bash
export AI8VIDEO_REAL_JOB_MAX_COUNT=5
export AI8VIDEO_REAL_JOB_WINDOW_SECONDS=3600
```

`AI8VIDEO_REAL_JOB_MAX_COUNT=0` 表示没有硬上限。真实图片、视频和 TTS 请求可能产生费用，请以所配置服务商的计费规则为准。

## 剧本知识库

原始 TXT、Markdown、DOCX 文件保存在：

```text
用户文件夹/用户素材/剧本素材库/
```

PostgreSQL 只保存文档元数据、知识段、标签和可重建索引，不替代或删除用户原稿。

```bash
createdb ai8video
export AI8VIDEO_SCRIPT_DATABASE_URL='postgresql://user:password@127.0.0.1:5432/ai8video'
```

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

默认方案不运行本地 Embedding（向量嵌入）模型，数据库内容可从用户原稿重建。

## CLI

macOS 与 Linux 可使用根目录的 `AI8video` 启动器；Windows 使用 `AI8video.cmd`。

| 命令 | 说明 |
|---|---|
| `./AI8video --version` | 查看版本 |
| `./AI8video serve --port 0` | 自动选择端口并启动本地工作台 |
| `./AI8video status --url http://127.0.0.1:18720` | 读取指定工作台健康状态 |
| `./AI8video config` | 检查本机模型配置，不显示密钥 |
| `./AI8video chat "生成一条产品介绍短视频" --session cli --timeout 300 --text` | 不启动 Web，直接执行一次对话 |

`status` 默认只检查 `18720`。如果启动器选择了其他端口，请通过 `--url` 指定实际地址。

## 架构

```mermaid
flowchart LR
    Interfaces["Web / CLI / Electron"] --> Runtime["Python Agent Runtime"]
    Runtime --> Conversation["Conversation Controller"]
    Runtime --> Scheduler["Agent Task Scheduler"]
    Conversation --> Domains["Generation · Knowledge · Radar · Breakdown"]
    Scheduler --> Domains
    Domains --> Media["FFmpeg · TTS · HTML Motion"]
    Domains --> Models["Text · Image · Video APIs"]
    Domains --> Storage["Local Files · PostgreSQL · Optional S3"]
    Media --> Results["用户生成结果"]
    Storage --> Results
```

核心原则：**Python 是会话、任务状态、业务规则、媒体处理和持久化的唯一真值来源。** Web、CLI 与 Electron 只负责接入；外部资源失败时返回真实错误，不伪造成功。

### 工程分层

```text
AI8Video/
├── src/ai8video/
│   ├── core/           # 配置、路径和基础模型
│   ├── application/    # Agent Runtime、会话和跨领域编排
│   ├── agent_skills/   # 角色级 Skill 注册与注入
│   ├── interfaces/     # Web、CLI
│   ├── integrations/   # 模型、数据库和 HTTP 适配器
│   ├── generation/     # 视频生成与连续合并流程
│   ├── media/          # FFmpeg、TTS、精剪和 HTML 动效
│   ├── batch/          # 批量任务、监督器、报告和账本
│   ├── knowledge/      # 剧本知识库、查询和重排
│   ├── breakdown/      # 爆款视频拆解
│   ├── radar/          # 热点聚合与摘要
│   └── assets/         # 素材、结果、归档和回收站
├── tests/              # 60+ 个 unittest 测试文件
├── desktop/electron/   # 可选 Electron 桌面壳
├── 用户字体/           # 内置字体与授权材料
├── start_ai8video_web.sh
├── 双击启动.command
└── 双击启动.bat
```

完整运行闭环、模块职责和依赖规则见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 本地数据与隐私

以下内容默认只保存在本机，不进入仓库：

| 路径 | 内容 |
|---|---|
| `用户文件夹/用户素材/` | 图片、脚本、花字、水印和背景音乐素材 |
| `用户文件夹/用户生成结果/` | 最终视频、封面、预览、manifest 和恢复元数据 |
| `用户文件夹/智能修图画布.json` | 智能修图画布、图层、同层多图结果和提示词 |
| `用户文件夹/爆款拆解/` | 上传视频、宫格、台词、镜头语言、脚本树、热点缓存和模型缓存 |
| `用户文件夹/TTS/` | TTS 设置、音色克隆、时间轴审核、输出和导出偏好 |
| `用户文件夹/HTML动效/reviews/` | 基础视频、透明图层、可编辑时间轴和待确认状态 |
| `media_resources/ai8video/` | 可选归档、批次报告和告警 |
| `temp/ai8video/` | 可丢弃、可重建的任务账本和运行时状态 |
| `mykey.py`、`.env` | 本地密钥和配置，不会提交到仓库 |

## 测试

```bash
AI8VIDEO_DISABLE_MYKEY=1 \
AI8VIDEO_DRY_RUN=1 \
PYTHONPATH=src \
python -m unittest discover -s tests
```

测试说明：

- 测试套件使用标准库 `unittest`，覆盖 Agent Runtime、生成、媒体、批量、资产和 Web 接口；
- 完整测试包含真实 Node 子进程测试，请先安装 Node.js `22+`；
- FFmpeg 缺失时，部分媒体测试会跳过；
- PostgreSQL 集成测试只在设置 `AI8VIDEO_TEST_POSTGRES_URL` 后运行；
- PostgreSQL 测试会清理专用测试表，**只能使用独立、可丢弃的测试数据库，严禁指向生产库或用户库**。

## Electron 与部署边界

Electron 位于 `desktop/electron/`，负责承载桌面窗口并发现或启动本地 Python Web 服务。短视频业务仍由项目目录中的 Python Runtime 执行。

```bash
cd desktop/electron
npm install
npm run dev
npm run dist:mac
npm run dist:win
```

当前边界：

- Web 服务固定监听 `127.0.0.1`；
- 项目未提供官方 Docker、Compose、Kubernetes 或生产服务器部署方案；
- 当前 Web 层没有面向公网的认证和 TLS，不应直接反向代理到公网；
- Electron DMG/NSIS 是桌面启动壳，不包含完整 Python 服务、项目源码和媒体依赖。

## 安全说明

- 不要提交真实 API Key、数据库密码、S3 凭据、用户素材或生成结果；
- 启用模型前确认服务商的隐私、内容审核和计费政策；
- S3 归档只有在明确配置后才会发送对应产物；
- FFmpeg 是外部运行时，仓库不分发其二进制；
- 本项目是本地生产工具，不是经过加固的公网多租户服务；
- Agent 不拥有 Shell、任意文件或通用网络工具权限。

## 参与开发

提交改动前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。项目坚持本地有界单体和纯 Python 核心，不重复实现入口，不用伪成功掩盖外部错误，也不为尚不存在的需求引入微服务或通用 Agent 框架。

- 架构与边界：[ARCHITECTURE.md](ARCHITECTURE.md)
- 第三方依赖声明：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- 字体授权：[FONT_LICENSES.md](FONT_LICENSES.md)
- 问题反馈：[GitHub Issues](https://github.com/17AI8/AI8Video/issues)

## License

项目源码采用 [MIT License](LICENSE)。第三方依赖、模型和媒体运行时遵循各自许可证与服务条款。
