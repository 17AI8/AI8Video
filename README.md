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
| 用户素材、项目状态和最终结果默认保存在本机 | 从热点、知识、脚本到生成、精剪和归档是一条完整链路 | Skill 提供策略，类型化 Capability 约束输入输出、取消、生命周期和副作用顺序 | 视频、TTS、HTML 动效与背景音乐采用非破坏性分层预览，保留源边界并在确认后烧录 |

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
- 智能分集以结构化 JSON 同时生成可直接提交的视频提示词与纯台词，计划可展开、编辑、保存并在刷新后恢复；
- 确认智能分集后直接进入视频与音频生成，不再重复调用 Planner 改写已确认内容；
- Planner 已通过 `planner.plan-video-content` 类型化 Capability 接入真实执行层，运行上下文携带会话、批次、Trace、取消检查和生命周期事件；
- Skill 与 Capability 分层：Skill 只提供策略，程序继续控制字段、业务门禁、可重放性和副作用顺序；设置页会明确区分“执行能力已绑定”和“仅策略指令”；
- 支持单段视频与 2 段、4 段连续生成，使用尾帧衔接保持连贯；传尾帧可选择自动连续推进，或逐条预览尾帧并手动确认继续；
- 真实提交、轮询、下载、校验、后处理与归档均展示明确状态；
- 任务状态持久化到本地账本，页面刷新后可继续跟踪远端任务；
- 失败任务支持单条重试、缺失首帧重建、安全停止和最终视频提示词查看。

### 2. 热点雷达与剧本知识库

- 内置微博、知乎、B站、V2EX、Hacker News、NodeSeek、少数派、Solidot 等 8 个公开源；
- 支持自定义 RSS/Atom、并行抓取、过滤去重、缓存降级和事实约束总结；
- 热点可以一键转换为创作提示词并填入主工作台；
- TXT、Markdown、DOCX 原稿保留在本地，PostgreSQL 保存可重建的检索结构；
- 对当前选中文档的知识树叶节点建立持久化 BM25 倒排索引，使用 `pg_trgm` 补充错别字召回，再由模型 Rerank（重排）筛选 Top 5；
- 标准号、型号、版本号、日期、数字和单位经过统一 NFKC 标准化及版本化分词，检索范围在召回与重排前后都严格限定为当前文档；
- 支持临时知识和爆款拆解知识树，不必先污染长期素材库。

### 3. 爆款视频拆解

- 按视频时长自动抽取最多 188 张带序号截图；
- 宫格可切换比例、点击查看原图，并保持时间轴对应关系；
- 使用 Whisper 生成台词时间轴，支持编辑、删除、拖动、定位和重新配音；
- 素材库支持管理已上传视频、单条删除与批量删除，并级联清理截图、宫格、台词、镜头语言、剧本和生成副本；
- 台词音频在识别后预切块，编辑阶段只调整顺序与时间映射，导出时再按当前结构合并；
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
- TTS 时间轴支持试听、拖动、删除、切分、左右边缘非破坏性裁剪或恢复、按停顿智能切块和独立重生成；TTS 与 HTML 动效片段均可框选后批量移动或删除；
- 当前草稿可通过系统“保存为”对话框导出 `192 kbps` MP3；
- 视频、TTS 与 HTML 动效组成三条可见编辑轨，背景音乐作为隐藏的第四层参与预览与合并；各层共享刻度尺、播放时钟、播放头、剪刀参考线和吸附提示；
- 视频轨支持定位、切分、删除和左右裁剪，并保持 Ripple（波纹式）紧密拼接；TTS 与 HTML 动效区块支持整体拖动；
- 播放头、裁剪边缘和可移动区块会吸附到时间轴边界、当前播放头及其他片段边缘，按住 `Shift` 可临时关闭吸附；
- 当前预览窗口支持最多 50 步跨轨撤销与重做，以及 `Cmd/Ctrl+Z`、`Shift+Cmd/Ctrl+Z` 和 `Ctrl+Y`；
- 分层草稿保存 `schemaVersion`、`revision` 和原始源边界，既能恢复被裁掉的内容，也能阻止旧页面覆盖较新的编辑；
- 支持从原视频第一帧重新生成片段，或重新截取参考帧后延长、替换与合并；
- 预览与确认阶段均按真实源区间裁剪音视频，不会只改变时间轴外观而保留未裁剪内容。

### 6. HTML 动效与媒体后处理

- Agent 生成基于 WAAPI（Web Animations API）的透明 HTML 动效方案，并在实时预览中保持可定位、可裁剪、可恢复的源时间相位；
- 每次重新生成都会先使旧候选失效，正式视频在用户确认前保持不变，避免误确认上一轮结果；
- 时间轴编辑先更新 artifact、composition 和实时预览；透明层渲染指纹同时覆盖动效输入、Runtime、Worker、渲染器版本和字体；
- 只有指纹、文件大小和 SHA-256 全部匹配时才复用现有透明层；输入变化或缓存损坏时使用临时文件重新渲染并原子替换；
- HyperFrames Worker 显式报告检查、准备、渲染和完成阶段，渲染并发可通过 `AI8VIDEO_HYPERFRAMES_RENDER_WORKERS` 配置为 `1–8`；
- 确认烧录前统一校验视频、TTS 与 HTML 动效边界，再按“视频裁剪 → TTS → 背景音乐 → HTML 透明层”生成正式视频；
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
- 每条规划结果同时包含 `prompt` 与 `narration_text`，确认后分别供视频模型和 TTS 直接使用；
- 正文中的普通数字不会被误判为数量；
- 手动模式可固定 `1–12` 条；
- 尾帧串联时，后续视频明确等待上一条结果，不会伪装成并行；手动模式会先展示已传入的尾帧，点击“继续”后才提交下一条；
- 中断恢复优先对账当前会话批次和本地成品，已生成视频保持成功状态，首个未完成视频自动进入手动等待；
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

编辑阶段始终保留纯净基础视频，以及视频、TTS、HTML 动效和背景音乐四层独立状态。视频与 TTS 编辑会刷新真实候选预览；HTML 动效先更新 WAAPI 实时预览，只有渲染输入变化时才重建透明层；背景音乐由独立音频层跟随播放，合并时按源片段连续拼接。确认前系统统一检查裁剪后的视频边界，确认后依次完成视频裁剪、TTS、背景音乐和 HTML 透明层烧录，并原子替换正式视频；任一步失败都不会把半成品标记为已应用。

</details>

## 工作台体验

| 能力 | 行为 |
|---|---|
| 可折叠玻璃侧栏 | 展开时展示品牌、资源和工具信息；点击品牌标识即可折叠为统一图标轨，并记忆本地状态 |
| 统一工具入口 | 当前进度、图片素材、剧本知识、回收站、智能修图、热点雷达和爆款拆解使用同一导航语言 |
| 内置图标资源 | 离线内置 Font Awesome Free 7.3.1，侧栏、状态卡与工具弹窗无需从第三方 CDN 加载图标 |
| 五阶段任务链与结果卡 | 依次展示理解需求、规划任务、提交生成、生成视频和归档结果；提交后将紧凑视频卡独立展示在步骤卡下方 |
| 结果卡操作 | 按视频横竖比例展示预览，支持播放、失败重试和查看实际提交给视频模型的最终提示词 |
| 三轨精剪 | 视频、TTS 和 HTML 动效使用同一刻度与播放时钟，可完成裁剪、恢复、吸附、撤销和重做 |
| 后台任务跟踪 | 页面从本地 worker 任务账本恢复真实状态，持续回填等待时间、远端任务事件和生成结果，可明确终止 |
| 三种背景 | 网格、点阵和纯色背景可循环切换并持久化 |
| 对话动效 | 新消息淡入，清空对话时气泡淡出，并遵循系统减少动态效果偏好 |
| 本地设置 | 文本规划、多模态、图片和视频模型按类型管理；每类可保存多套连接配置、切换当前配置，并集中维护该类型的通用参数 |

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

# HTML 动效透明层渲染并发，默认 1，可设置为 1–8
export AI8VIDEO_HYPERFRAMES_RENDER_WORKERS=1
```

### 模型接入

| 类别 | 当前接入方式 |
|---|---|
| 文本模型 | OpenAI 兼容 Base URL、API Key 和模型名 |
| 多模态模型 | 可独立配置；缺失时继承文本模型地址和凭据 |
| 图片模型 | OpenAI 兼容图片接口，可独立配置并由用户设置覆盖模型名 |
| 视频模型 | 豆包 Seedance、云雾 Grok、云雾 Omni、云雾 Veo、百炼 Wan，以及通用 OpenAI 兼容视频接口 |
| TTS | MiMo API；接口、密钥、模型、音色和克隆设置由本地设置界面管理 |
| 图床 | 可选内置 MJJ.TODAY 或自定义上传接口，用于只接受公网参考图 URL 的视频模型 |
| 归档 | 本地目录；可选 S3 兼容对象存储 |

工作台的模型设置按文本规划、多模态、图片和视频四类分栏。每类均可新建、复制和保存多套连接配置，明确标记当前使用项，并在独立的类型通用配置区维护共享参数；API Key 留空保存时会保留已有密钥。

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

OpenAI 兼容视频服务之间并不保证字段完全一致。AI8video 默认发送 `seconds` 与具体尺寸；当上游明确返回字段或尺寸不兼容时，会在同一次创建流程中自动降级为 `duration` 或对应宽高比，避免要求用户手动切换模板。参考图仍保持图生视频语义：需要公网 URL 时只上传参考图，不会静默退化成文生视频。

> [!WARNING]
> 内置 MJJ.TODAY 是第三方匿名图床，上传内容会离开本机，存在隐私、可用性与内容保留风险。只有用户在“设置 → 图床”中明确选择后才会使用；敏感素材建议配置自有图床，或选择原生支持本地/base64 参考图的模型服务。

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
KnowledgeBaseAgent 建立单文档知识树 → Reviewer 审核
        ↓
叶节点持久化 → 统一分词 → 构建文档内 BM25 倒排索引
        ↓
文本模型提炼检索意图并确定性保留专业标识符
        ↓
当前文档 BM25 Top 20 + 同文档 pg_trgm 不足补召回
        ↓
模型 Rerank Top 5 → 带来源知识段注入生成模型
```

BM25 的 `N`、`df`、词频和平均叶节点长度均只统计当前选中文档，未选文档不会参与候选或分数计算。普通生成查询只读取已有索引并实时算分；Schema 迁移、源文件同步和无模型 BM25 回填在 Web 启动或知识库管理接口中执行。原始文件 SHA-256 用于判变，旧库首次升级会在文件元数据一致时只补指纹，不删除 ready 叶节点，也不重新调用建树模型。

可通过以下环境变量灰度和回滚：

```bash
# legacy：旧 PostgreSQL 全文排序；shadow：返回旧排序并记录 BM25 对比；bm25：BM25 主排序
export AI8VIDEO_SCRIPT_RETRIEVAL_MODE=bm25

# 候选召回和最终注入数量，默认分别为 20 和 5
export AI8VIDEO_SCRIPT_RECALL_TOP_K=20
export AI8VIDEO_SCRIPT_INJECT_TOP_K=5

# 检索失败时是否允许回退为整篇原文；设为 0 可严格禁止
export AI8VIDEO_SCRIPT_FULL_FALLBACK_ENABLED=0
```

健康接口会返回 Schema、BM25 索引、分词器版本、ready / pending 文档数和当前检索模式。检索 Trace 默认追加到 `temp/ai8video/script_knowledge_retrieval_traces.jsonl`。默认方案不运行本地 Embedding（向量嵌入）模型，不依赖向量数据库、Redis、Jieba 或第三方 BM25 包；数据库派生内容均可从用户原稿和已审核叶节点重建。

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

### Agent Runtime 与 Skills

AI8video 将策略与执行分开：Skill 描述某个 Agent 应遵守的策略，Capability 定义真正可执行的输入、输出、取消、生命周期事件、可重放性和副作用边界。

`CapabilityRegistry` 会校验输入输出类型；有副作用的能力只能串行执行，并在执行前后检查取消状态。目前 `planner.plan-video-content` 是首个真正绑定执行层的 Capability，其他 Skill 会明确标记为策略能力或预留槽位，不把提示词文件冒充成已经接入的 Runtime。

Skill 元数据支持版本、许可证、类型、能力绑定和来源；正文按需加载，并拒绝可破坏宿主提示词边界的保留标记。

### 工程分层

```text
AI8Video/
├── src/ai8video/
│   ├── core/           # 配置、路径和基础模型
│   ├── agent_runtime/  # 类型化 Capability、执行上下文、事件与副作用边界
│   ├── application/    # 会话、应用门面和跨领域编排
│   ├── agent_skills/   # 带版本和能力绑定的策略型 / 工作流型 Skills
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
├── desktop/electron/   # Electron 客户端与安装包配置
├── desktop/runtime/    # 冻结后端、运行时暂存与发布校验脚本
├── 用户字体/           # 内置字体与授权材料
├── start_ai8video_web.sh
├── 双击启动.command
└── 双击启动.bat
```

完整运行闭环、模块职责和依赖规则见 [ARCHITECTURE.md](ARCHITECTURE.md)。

`media/local_tts.py` 只保留稳定调用入口与兼容补丁点；设置和音色库、口播文本提取、MiMo 请求、FFmpeg 时长适配与混音分别由 `local_tts_settings.py`、`local_tts_text.py`、`local_tts_mimo.py`、`local_tts_audio.py` 承担，避免桌面端、Web 与测试各自形成一套 TTS 行为。

## 本地数据与隐私

以下内容默认只保存在本机，不进入仓库：

| 路径 | 内容 |
|---|---|
| `用户文件夹/用户素材/` | 图片、脚本、花字、水印和背景音乐素材 |
| `用户文件夹/用户生成结果/` | 最终视频、封面、预览、manifest 和恢复元数据 |
| `用户文件夹/智能修图画布.json` | 智能修图画布、图层、同层多图结果和提示词 |
| `用户文件夹/爆款拆解/` | 上传视频、宫格、台词、镜头语言、脚本树、热点缓存和模型缓存 |
| `用户文件夹/视频裁剪/reviews/` | 视频轨裁剪草稿、版本号、候选视频、胶片缓存和可恢复源边界 |
| `用户文件夹/TTS/` | TTS 设置、音色克隆、波形缓存、带版本时间轴、候选输出和导出偏好 |
| `用户文件夹/HTML动效/reviews/` | 基础视频、候选视频、动效 artifact、实时 composition、透明层、渲染指纹和确认状态 |
| `用户文件夹/图床/settings.json` | 当前图床选择与自定义上传接口；可能包含本地保存的鉴权令牌 |
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

BM25 与旧检索的 Golden Regression（黄金题集回归）会检查 Recall@20、Hit@3、重排后 Hit@5、无答案精度和文档范围泄漏率，并在 BM25 指标退化或泄漏不为 0 时返回非零状态：

```bash
AI8VIDEO_TEST_POSTGRES_URL='postgresql:///ai8video_test' \
PYTHONPATH=src \
python tests/evaluate_script_knowledge_golden.py
```

## Electron 与部署边界

项目同时保留两条互不干扰的运行通道：

| 通道 | 面向对象 | 运行方式 | 更新方式 |
|---|---|---|---|
| Dev 网页 | 开发者 | 从当前源码和 `.venv` 启动本地 Web 服务，可边改边调试 | 正常 `git pull` 后重新启动 Dev |
| Electron 安装包 | 普通用户 | 使用安装包内置的冻结 Python 后端与 HyperFrames 运行时，不依赖源码目录 | 下载新版本 DMG / EXE 覆盖安装 |

两条通道可以在同一台电脑上共存。Electron 会为安装态生成独立实例标识和用户数据目录，不会误连正在开发的 Dev 服务；开发源码也不会被“冻结”，仍可随时拉取、修改和调试。

开发运行：

```bash
cd desktop/electron
npm ci
npm run dev
```

本地构建安装包前，需要先冻结 Python 后端并暂存发行运行时：

```bash
python -m pip install ".[ai8video]" "pyinstaller==6.21.0"
npm ci
python -m PyInstaller --noconfirm --clean \
  --distpath build/frozen \
  --workpath build/pyinstaller \
  desktop/runtime/ai8video_backend.spec
python desktop/runtime/stage_release.py \
  --backend-dir build/frozen/ai8video-backend \
  --target desktop/electron/runtime
node desktop/runtime/stage_node_runtime.mjs \
  node_modules desktop/electron/runtime/node_modules
cd desktop/electron
npm ci
npm run dist:mac
```

`.github/workflows/desktop-release.yml` 支持手动运行；手动触发时可填写现有 `v*` 标签，通过当前 `main` 重新生成安装包、覆盖对应 Release 附件，并把触发提交的完整模块化说明同步到 Release 正文。推送 `v*` 注解标签时也会自动生成 macOS ARM64 DMG 与 Windows x64 EXE，并附加到对应 GitHub Release；首次发布的正文直接采用标签说明。工作流会在 `desktop/electron` 工作目录内安装依赖和执行打包，确保不同系统及 npm 版本都能稳定读取已纳入版本控制的 `package-lock.json`。发行暂存会移除 Node 依赖中的 source map、TypeScript 声明文件和 npm `.bin` 命令垫片，避免 GitHub Actions 临时目录的绝对符号链接进入应用并破坏 macOS 签名；Electron 仅保留简体中文和英文语言包。当前 TTS 已统一为 MiMo API，桌面运行时不再携带不可达的旧 sherpa-onnx 引擎、模型音色表及重复 ONNX 动态库。普通代码推送不会触发大型打包任务，避免无意义消耗构建时长和制品存储。

GitHub Actions 支持按平台自动启用代码签名和 Apple 公证。仓库 Secrets 必须成组配置，缺少任意一项会立即中止，避免误以为已签名；整组未配置时仍会构建，并在 Actions 制品名和任务摘要中明确标记 `unsigned`，但 macOS 应用会走独立的完整 ad-hoc 签名通道并通过严格结构校验，不能发布系统会判定为“已损坏”的无效包。配置证书后则切换到 Developer ID 签名与公证通道，不会被 ad-hoc 参数覆盖。检查脚本只输出变量名和状态，不输出证书、密码或账号值。

| 平台能力 | GitHub Secrets | 结果 |
|---|---|---|
| macOS 代码签名 | `MAC_CSC_LINK`、`MAC_CSC_KEY_PASSWORD` | 映射为 electron-builder 的 `CSC_LINK`、`CSC_KEY_PASSWORD` |
| macOS Apple 公证 | `APPLE_ID`、`APPLE_APP_SPECIFIC_PASSWORD`、`APPLE_TEAM_ID` | 签名完成后自动提交公证并验证装订票据 |
| Windows 代码签名 | `WIN_CSC_LINK`、`WIN_CSC_KEY_PASSWORD` | 同时验证安装器、Electron 主程序和冻结后端 |

正式桌面版本请从 [GitHub Releases](https://github.com/17AI8/AI8Video/releases) 下载；未填写 `release_tag` 的 Actions 手动运行只产生 14 天临时制品，填写标签后则会更新对应正式 Release，交付仍以版本标签下的 DMG / EXE 为准。

当前边界：

- Web 服务固定监听 `127.0.0.1`；
- 安装包内置 Python 后端、Web 静态资源、HyperFrames 和两款授权中文字体，不包含项目源码与本地密钥；
- FFmpeg / FFprobe 仍使用用户系统中可用的外部运行时；
- macOS 无证书构建现使用完整 ad-hoc 签名并通过严格结构校验，避免把无效包误发为“应用已损坏”；但 ad-hoc 不等于 Developer ID 信任，未完成 Apple 公证的制品仍会标记为 `unsigned`，正式公开交付须配置证书与公证 Secrets；
- 项目未提供官方 Docker、Compose、Kubernetes 或生产服务器部署方案；
- 当前 Web 层没有面向公网的认证和 TLS，不应直接反向代理到公网。

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
