# AI8video

AI8video 是本地运行的短视频生产 Agent，覆盖目标理解、信息补全、脚本拆分、素材管理、图片与视频生成、配音、字幕、合并、HTML 动效和批量任务。

项目采用自研的纯 Python Agent Runtime，无需接入第三方通用 Agent 框架：模型理解用户意图，Agent Controller 维护会话与任务状态、选择短视频能力、执行安全护栏，并根据工具返回的结果、错误和进度继续决策。Web、CLI 和批量任务复用同一套 Agent 能力与本地数据。

## 架构

```mermaid
flowchart TB
    Goal["用户目标 / 多轮指令"] --> Entry["Web / CLI / Electron"] --> Intent

    subgraph Agent["AI8video Agent Runtime"]
        direction TB
        Intent["意图理解与信息补全\nRequest Interpreter"]
        Controller["Agent Controller\n会话控制与工作流编排"]
        Decision{"下一步能力决策"}
        Guard["安全护栏\n参数校验 / 额度 / 幂等"]
        Context[("Context & State\n会话草稿 / 任务账本 / 剧本知识库")]

        Intent --> Controller --> Decision --> Guard
        Context -. "读取与写入" .-> Controller
    end

    Decision -. "信息不足" .-> Clarify["追问用户 / 补全约束"] --> Await["等待用户补充后进入下一轮"]
    Guard --> Content
    Guard --> Generation
    Guard --> Media
    Guard --> Assets

    subgraph Tools["Short-video Tool Layer"]
        direction LR
        Content["脚本 / 知识库 / 热点 / 爆款拆解"]
        Generation["图片 / 视频 / 批量生成"]
        Media["TTS / 字幕 / 合并 / HTML 动效"]
        Assets["素材 / 归档 / 回收站"]
    end

    Content --> Resources["模型 API / FFmpeg / HyperFrames\nPostgreSQL / 文件系统"]
    Generation --> Resources
    Media --> Resources
    Assets --> Resources
    Resources --> Observe["Observation\n结果 / 错误 / 进度 / 产物"]
    Observe --> Evaluate{"Agent Controller\n评估 Observation"}
    Evaluate -->|继续| Continue["↺ 更新状态并进入下一轮决策"]
    Evaluate -->|完成| Deliver["交付结果"] --> Result["短视频 / 脚本 / 任务报告"]
    Evaluate -->|失败| Failure["显式错误 / 重试 / 停止"]

    classDef agent fill:#eef2ff,stroke:#6366f1,color:#111827,stroke-width:1.5px
    classDef decision fill:#f5f3ff,stroke:#7c3aed,color:#111827,stroke-width:2px
    classDef tool fill:#ecfeff,stroke:#0891b2,color:#0f172a
    classDef state fill:#fff7ed,stroke:#ea580c,color:#431407
    classDef external fill:#f8fafc,stroke:#64748b,color:#0f172a
    class Intent,Controller,Guard,Observe,Evaluate,Clarify,Continue,Deliver,Failure agent
    class Decision,Evaluate decision
    class Content,Generation,Media,Assets tool
    class Context state
    class Goal,Entry,Resources,Result,Await external
    style Agent fill:#f8fafc,stroke:#6366f1,stroke-width:2px
    style Tools fill:#f0fdfa,stroke:#0891b2,stroke-width:1.5px
```

- AI8video 是面向短视频生产的有界垂直 Agent，自主能力聚焦于内置短视频工具，不开放 Shell、任意文件权限或通用网络工具。
- 完整 Agent 运行闭环、工程分层、模块职责和强制依赖规则见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。
- Python Agent Runtime 直接负责对话理解、上下文状态、能力决策、安全护栏、媒体处理和持久化。
- 剧本知识库使用 PostgreSQL、`pg_trgm` 和 `tsvector` 完成结构化管理与中文词法检索，不依赖 Embedding 模型。
- CLI 通过应用门面复用 Python 核心，不启动 Node Sidecar，也不提供 Shell、任意文件读写或通用网络工具。
- HTML 动效使用项目自有 WAAPI（Web Animations API）运行时，不依赖 GSAP。
- HyperFrames 仅用于可选的 HTML 动效检查与渲染。
- FFmpeg 是外部运行时，仓库不分发 FFmpeg 二进制。

## 系统要求

- Python `3.10` 至 `3.13`。
- Node.js 与 npm：仅在使用 HyperFrames HTML 动效时需要，核心工作台和 CLI 不依赖 Node。
- FFmpeg 与 FFprobe。请使用系统安装或 LGPL 兼容构建；也可设置：

```bash
export AI8VIDEO_FFMPEG_BIN=/absolute/path/to/ffmpeg
export AI8VIDEO_FFPROBE_BIN=/absolute/path/to/ffprobe
```

macOS 本机当前可将自编译运行时放在 `~/.local/bin`，启动脚本会自动加入该路径。

## 配置模型

推荐通过环境变量配置：

```bash
export AI8VIDEO_LLM_BASE_URL=https://example.com/v1
export AI8VIDEO_LLM_API_KEY=your-key
export AI8VIDEO_LLM_MODEL=your-text-model

export AI8VIDEO_IMAGE_BASE_URL=https://example.com/v1
export AI8VIDEO_IMAGE_API_KEY=your-key
export AI8VIDEO_IMAGE_MODEL=your-image-model
```

为兼容本机已有配置，也可以复制模板：

```bash
cp mykey_template.py mykey.py
```

然后只填写 `native_oai_config`。`mykey.py` 已被 Git 忽略，禁止提交真实密钥。

## 启动

macOS / Linux：

```bash
./start_ai8video_web.sh
```

macOS 也可以双击 `双击启动.command`；Windows 可以双击 `双击启动.bat`。

首次启动会创建 `.venv`、安装 Python 可选依赖，并从 `18720-18820` 选择端口。检测到 Node.js/npm 时会安装可选的 HyperFrames 依赖；安装失败不会阻止核心工作台启动。

也可以使用命令行入口：

```bash
PYTHONPATH=src python -m ai8video serve --port 18720
PYTHONPATH=src python -m ai8video status
PYTHONPATH=src python -m ai8video config
PYTHONPATH=src python -m ai8video chat "生成一条 10 秒的产品介绍短视频" --text
```

`chat` 命令直接调用 Python Runtime，不要求 Web 服务已经运行。默认输出完整 JSON，增加 `--text` 只输出回复文本。

## 剧本知识库

剧本知识库将原始 TXT、Markdown、DOCX 保存在 `用户文件夹/用户素材/剧本素材库/`，PostgreSQL 保存文档元数据、知识段、标签和检索索引。数据库索引可以从原始文件重建，不会替代或删除用户原稿。

本机需要 PostgreSQL 16 或更高版本。默认连接本机数据库 `ai8video`：

```bash
createdb ai8video
```

使用远程地址或自定义账号时设置：

```bash
export AI8VIDEO_SCRIPT_DATABASE_URL='postgresql://user:password@127.0.0.1:5432/ai8video'
```

数据库连接信息属于敏感配置，不要写进仓库。启动后打开侧栏“剧本知识库”，系统会初始化项目专用表并同步现有原稿。

当前检索链路：

```text
精确标题／标签
    ↓
文本模型提炼检索意图（正向关键词 + 排除项）
    ↓
pg_trgm 中文模糊匹配 + tsvector 召回 Top-20 知识段
    ↓
SQL 加权排序
    ↓
文本模型 Rerank（重排，异常时保留 SQL 顺序）
    ↓
向生成模型注入 Top-5 知识段
    ↓
最终输出后审核：返回修正后的视频提示词与配音文本
```

默认剧本参考会根据用户当前要求检索相关知识段，避免把整份长剧本全量塞入生成模型。纯数量请求在不超过 5 条时也会走知识库，并使用用户设置的系统提示词与当前剧本标题、摘要、标签、预览共同构造检索查询；Rerank 会将“禁止／不要／过滤”等否定约束视为降权条件。超过 5 条、明确要求全文、“开始生成”等控制消息以及知识库不可用时仍使用完整原文。显式 `@文件名` 引用也保持全文语义。

可通过环境变量调整召回和注入数量：

```bash
export AI8VIDEO_SCRIPT_RECALL_TOP_K=20
export AI8VIDEO_SCRIPT_INJECT_TOP_K=5
export AI8VIDEO_QUERY_MODEL_ENABLED=1
export AI8VIDEO_QUERY_MODEL_TIMEOUT_SECONDS=8
export AI8VIDEO_RERANK_ENABLED=1
export AI8VIDEO_RERANK_TIMEOUT_SECONDS=8
```

当前方案不运行本地 Embedding 模型，运行基线按普通 8GB 电脑设计，并为浏览器、视频处理与系统保留资源。Rerank 复用已配置的文本模型，只处理少量候选摘要；关闭或调用失败时不会阻断生成。

最终输出后审核不是只给出通过/失败结论，而是返回可直接执行的 `corrected_video_prompt`、`narration_text` 和违规项。配音只读取后审核确认的台词；`source_summary`、选材说明、模型 notes 等内部字段禁止进入 TTS、字幕或最终媒体。

## 运行状态

健康接口会返回模型配置、任务状态、归档与本地运行信息：

```bash
curl http://127.0.0.1:18720/api/health
```

## 数据目录

以下内容只保存在本机，不进入仓库：

- `用户文件夹/`：素材、字体选择、模型设置、生成结果和回收站。
- `用户文件夹/爆款拆解/热点雷达/feeds.json`：可选的自定义公开 RSS/Atom 数据源配置。
- `media_resources/ai8video/`：归档视频、批次报告和告警。
- `temp/ai8video/`：任务账本、进度与运行时状态。
- `mykey.py`、`.env`：密钥和本地配置。

## Electron

Electron 是可选桌面侧车，代码位于 `desktop/electron/`。它只负责启动或连接本地 Web 服务并承载工作台窗口；短视频业务仍由 Python 服务处理。

## 测试

离线测试不会触发真实模型或视频生成：

```bash
AI8VIDEO_DISABLE_MYKEY=1 \
AI8VIDEO_DRY_RUN=1 \
PYTHONPATH=src \
python -m unittest discover -s tests
```

`tests/` 是独立质量门禁，不参与产品运行或打包；它用于防止重构破坏生成、媒体、批量任务和 Web 接口。

## 许可证

项目源码采用 MIT License。第三方依赖及其许可证见 `THIRD_PARTY_NOTICES.md`。仓库不再包含 GPL FFmpeg 二进制或 GSAP 运行时。
