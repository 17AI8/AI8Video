<div align="center">
  <img src="src/ai8video/interfaces/web/static/images/ai8video-brand-logo.png" width="240" alt="AI8video Logo">

  <h1>AI8video</h1>

  <p><strong>本地优先、有界可控的 AI 短视频生产工作台</strong></p>

  <p>用自然语言串联选题、知识检索、脚本规划、图片与视频生成、TTS、精剪、批量监督和本地交付。</p>

  <p>
    <a href="pyproject.toml"><img src="https://img.shields.io/badge/version-0.3.0-4f6dff?style=flat-square" alt="Version 0.3.0"></a>
    <img src="https://img.shields.io/badge/Python-3.10--3.13-0ea5e9?style=flat-square&logo=python&logoColor=white" alt="Python 3.10 to 3.13">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-22c55e?style=flat-square" alt="MIT License"></a>
    <img src="https://img.shields.io/badge/runtime-local--first-0891b2?style=flat-square" alt="Local-first Runtime">
    <img src="https://img.shields.io/badge/Agent-bounded-7c3aed?style=flat-square" alt="Bounded Agent">
  </p>
</div>

---

## AI8video 是什么

AI8video 是一个开源、本地优先的短视频生产工作台。模型负责理解目标、规划内容和生成媒体；Python Runtime 负责会话、任务顺序、安全护栏、成本、媒体处理、恢复与结果落盘。

它不是拥有 Shell、任意文件访问或通用网络权限的无限自治 Agent。自主决策被限制在项目提供的短视频能力内，外部服务失败时返回真实错误，不伪造成功。

| 设计原则 | 当前实现 |
|---|---|
| 本地优先 | 素材、会话运行态、编辑草稿和生成结果默认保存在本机 |
| 有界 Agent | Main Agent 只能调用 6 个复合工具，通用系统工具不对模型开放 |
| 两层循环 | Main Agent 只在业务关键节点决策；Runtime 负责轮询、下载、后处理和归档 |
| 单一真值 | Python 掌握业务规则、凭据、成本、持久化与恢复状态 |
| 可继续编辑 | 视频、TTS、HTML 动效和背景音乐采用非破坏性分层编辑 |

## 快速开始

### 一键启动

```bash
git clone https://github.com/17AI8/AI8Video.git
cd AI8Video
./start_ai8video_web.sh
```

启动器会创建项目内 `.venv`、安装 Python 依赖、检查可选运行时，并从 `18720–18820` 选择可用端口。

| 平台 | 启动方式 |
|---|---|
| macOS | 双击 `双击启动.command`，或执行 `./start_ai8video_web.sh` |
| Windows | 双击 `双击启动.bat` |
| Linux | 执行 `./start_ai8video_web.sh` |

默认只监听本机：

```text
http://127.0.0.1:18720
```

实际端口可能因占用自动顺延。

> [!IMPORTANT]
> `AI8VIDEO_DRY_RUN` 默认关闭，程序允许真实模型任务。首次验证建议先设置 `export AI8VIDEO_DRY_RUN=1`，避免误用付费额度。

### 手动安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[ai8video]'
ai8video serve --port 0
```

HTML 动效和完整 Node 侧运行时需要 Node.js `22+`，安装方式见[配置说明](docs/configuration.md)。

## 两种对话模式

| 模式 | 执行核心 | 适用场景 |
|---|---|---|
| 标准模式 | 原有 `AI8VideoConversationController` 确定性流程 | 明确、可预测的规划、确认和生成流程 |
| Agent 模式 | `AI8VideoMainAgent` + Pi Agent Core/Pi AI + 6 个复合工具 | 需要在审核、用户确认、失败或部分成功等关键节点动态决策 |

- 模式只在新建对话时选择，默认标准模式。
- 首次发送消息后模式锁定；现有对话不会被模式选择器改写。
- 两种模式共享媒体资源、模型配置来源和业务服务，但不共享对话消息、运行状态或 Agent 决策记录。
- 标准模式是独立且完整的原有链路；Agent 功能不可用时不会把已有标准对话迁移成 Agent 对话。

```text
新建对话
   |
   +-- 标准模式 --> Conversation Controller --> 确定性工作流
   |
   +-- Agent 模式 --> Main Agent --> 复合工具 --> Python Runtime
                                         ^              |
                                         +-- 关键 Observation
```

Agent 模式中的提交、轮询、下载、后处理和归档不会每隔几秒重新调用模型。只有审核结论、用户输入、付费重试批准、生成终态、尾帧检查点或可交付结果，才会形成新的 Observation 并唤醒 Main Agent。

完整边界见[架构说明](ARCHITECTURE.md)。

## 核心能力

| 能力 | 说明 |
|---|---|
| 对话式策划与生成 | 单条、智能分集、手动批量、连续尾帧生成、失败重试与跨刷新批次恢复 |
| 热点雷达 | 聚合 8 个公开源及自定义 RSS/Atom，支持去重、摘要和创作转化 |
| 剧本知识库 | TXT、Markdown、DOCX 建树审核；当前文档 BM25 + `pg_trgm` + 模型重排 |
| 爆款拆解 | 截图宫格、Whisper 台词时间轴、镜头语言分析和脚本骨架重建 |
| 智能修图 | 任务化单图工作台、批量候选、前后对比、非破坏调整和多格式导出 |
| TTS 与精剪 | MiMo TTS、声音克隆、视频/TTS/HTML 三轨编辑、背景音乐合成 |
| HTML 动效 | WAAPI 方案、HyperFrames 透明层、指纹缓存和独立烧录结果 |
| 批量监督 | 日目标、预算门禁、任务账本、租约、心跳、取消、报告和告警 |
| 素材与交付 | 图片、脚本、花字、水印、音乐、结果、归档和回收站统一管理 |

生成批次、智能分集确认、尾帧续传与合并编辑共享同一任务状态；刷新或恢复后会优先沿用当前批次和最新媒体结果，避免回落到过期任务或旧视频基底。

## 典型工作流

```text
用户目标
  -> 热点 / 知识 / 直接创作
  -> 脚本规划与审核
  -> 图片 / 视频生成
  -> Runtime 跟踪、下载、校验和归档
  -> 视频 / TTS / HTML 动效 / 背景音乐编辑
  -> 本地交付、续写、合并、重试或回收
```

## 系统要求

| 组件 | 要求 | 用途 |
|---|---|---|
| Python | `3.10–3.13` | Web、CLI、会话、Agent Runtime 与媒体编排 |
| FFmpeg / FFprobe | 建议安装 LGPL 兼容构建 | 合并、裁剪、配音、音乐和动效烧录 |
| Node.js / npm | Agent 模式、HTML 动效和完整测试需要 `22+` | Pi Sidecar、HyperFrames 与 Node 测试 |
| PostgreSQL | 可选，建议 `16+` | 剧本知识库与 `pg_trgm` |
| Electron | 可选 | 桌面窗口与独立安装包 |

核心 Web 与 CLI 不要求 GPU。爆款拆解默认使用 `faster-whisper` 的 `base` 模型进行本地 CPU 转录，首次使用时按需下载模型。

## 最小模型配置

至少配置文本模型和视频模型：

```bash
# 文本模型
export AI8VIDEO_LLM_BASE_URL=https://example.com/v1
export AI8VIDEO_LLM_API_KEY=your-key
export AI8VIDEO_LLM_MODEL=your-text-model

# 视频模型
export AI8VIDEO_VIDEO_BASE_URL=https://example.com
export AI8VIDEO_VIDEO_API_KEY=your-key
export AI8VIDEO_VIDEO_MODEL=your-video-model
export AI8VIDEO_VIDEO_TEMPLATE=doubao-seedance
```

文本、多模态、图片和视频模型也可在设置页按类型保存多套 Profile。对话首次发送消息时会绑定当前 Profile 的非敏感快照和版本；API Key 不写入对话记录。

> [!NOTE]
> 项目不会自动加载 `.env`。`.env`、`mykey.py` 和本地用户设置都不应提交到仓库。

多模态、图片、TTS、图床、额度门禁、FFmpeg 和 Profile 绑定细节见[配置说明](docs/configuration.md)。

## CLI

macOS 与 Linux 使用根目录的 `AI8video` 启动器；Windows 使用 `AI8video.cmd`。

| 命令 | 说明 |
|---|---|
| `./AI8video --version` | 查看版本 |
| `./AI8video serve --port 0` | 自动选择端口并启动本地工作台 |
| `./AI8video status --url http://127.0.0.1:18720` | 读取指定工作台健康状态 |
| `./AI8video config` | 检查本机模型配置，不显示密钥 |
| `./AI8video chat "生成一条产品介绍短视频" --session cli --timeout 300 --text` | 不启动 Web，执行一次对话 |

## 架构概览

```text
Web / CLI / Electron
          |
          v
Conversation Store + Model Binding
          |
   +------+------+
   |             |
Standard       Agent
Controller     Main Agent -- Pi JSONL Sidecar
   |             |
   +------v------+
     Shared Business Services
 generation / batch / media / knowledge / assets / radar / breakdown
          |
          v
Models / FFmpeg / HyperFrames / PostgreSQL / Local Files / Optional S3
```

Python 是会话、任务状态、业务规则、成本、媒体处理和持久化的唯一真值来源。Pi Agent Core/Pi AI 只负责 Agent 模式中的模型与工具轮次，是 AI8video Agent 的内部执行组件，不是产品本身，也不接管标准模式。

## 本地数据与隐私

| 位置 | 内容与边界 |
|---|---|
| `用户文件夹/用户素材/` | 图片、脚本、花字、水印和背景音乐 |
| `用户文件夹/用户生成结果/` | 视频、封面、预览、manifest 和恢复元数据 |
| `用户文件夹/爆款拆解/` | 上传视频、宫格、台词、镜头语言和脚本树 |
| `用户文件夹/TTS/`、`HTML动效/reviews/` | 分层编辑草稿、候选、缓存和确认状态 |
| `temp/ai8video/` | Conversation Store、Agent Run/Action/Observation、任务账本和恢复状态 |
| `mykey.py`、`.env` | 本地密钥与配置，已被 Git 忽略 |

任务运行时不要手动清空 `temp/ai8video/`。删除对话不会删除用户媒体、生成结果、任务账本或审计记录。

内置 MJJ.TODAY 是可选第三方匿名图床；使用后素材会离开本机。敏感内容应使用自有图床，或选择支持本地/base64 参考图的模型服务。

## 测试

只运行与改动直接相关的最小测试，例如：

```bash
AI8VIDEO_DISABLE_MYKEY=1 AI8VIDEO_DRY_RUN=1 PYTHONPATH=src \
  python -m unittest tests.test_ai8video_architecture
```

测试数据库、Node 运行时和提交前检查见[参与开发](CONTRIBUTING.md)。

## 文档

- [架构与运行边界](ARCHITECTURE.md)
- [模型、运行时与安全配置](docs/configuration.md)
- [剧本知识库与 BM25 检索](docs/knowledge-base.md)
- [Electron 构建与桌面发行](docs/desktop-release.md)
- [参与开发](CONTRIBUTING.md)
- [第三方依赖声明](THIRD_PARTY_NOTICES.md)
- [字体授权](FONT_LICENSES.md)

## 安全与部署边界

- 不要提交 API Key、数据库密码、S3 凭据、用户素材或生成结果。
- Web 服务固定监听 `127.0.0.1`，当前没有面向公网的认证和 TLS。
- 项目未提供官方 Docker、Compose、Kubernetes 或生产服务器部署方案。
- 真实图片、视频和 TTS 请求可能产生费用，应先使用 dry-run 和额度门禁。
- Agent 不拥有 Shell、任意文件访问或通用网络工具权限。

安全问题请通过 [GitHub Security Advisories](https://github.com/17AI8/AI8Video/security/advisories/new) 私下报告。

## 参与开发与 License

提交改动前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。项目源码采用 [MIT License](LICENSE)；第三方依赖、模型和媒体运行时遵循各自许可证与服务条款。
