# 模型、运行时与安全配置

本文集中说明 AI8video 的系统依赖、模型 Profile、环境变量和真实任务安全门禁。快速启动见项目 [README](../README.md)。

## 系统依赖

| 组件 | 要求 | 是否必需 |
|---|---|---|
| Python | `3.10–3.13` | 必需 |
| FFmpeg / FFprobe | 建议系统安装 LGPL 兼容构建 | 媒体合并、裁剪、TTS、音乐和动效烧录需要 |
| Node.js / npm | `22+` | Agent 模式、HTML 动效和完整 Node 测试需要 |
| PostgreSQL | 建议 `16+` | 仅剧本知识库需要 |
| `zenity` | Linux 可选 | TTS MP3 图形化“保存为”窗口 |
| Electron | 可选 | 桌面壳和安装包 |

标准模式的核心 Web 与 CLI 不强制依赖 Node.js。使用 Agent 模式或 HTML 动效前，在仓库根目录安装锁定依赖：

```bash
npm ci
```

Node 依赖包括 Pi Agent Core/Pi AI、TypeBox 和 HyperFrames，版本由根目录 `package-lock.json` 锁定。

## 配置入口

模型配置可以来自：

- 设置页中的本地模型 Profile；
- `AI8VIDEO_*` 环境变量；
- 兼容用本地 `mykey.py`；
- 工作台保存的模型名与类型通用参数。

文本、多模态、图片和视频模型按类型独立管理，每类可保存多套 Profile。API Key 留空保存时会保留已有密钥，不会用空值覆盖。

项目不会自动加载 `.env`。如需使用，请由 Shell、IDE 或进程管理器显式加载。

## 对话模型绑定

标准模式与 Agent 模式可以读取同一套模型配置来源，但不会共享运行状态。

对话第一次发送消息时，服务端会保存：

- Profile ID；
- Base URL、模型名等非敏感字段；
- 配置指纹与版本；
- 当前执行模式。

API Key 等凭据继续保存在本地模型配置存储中，不写入 Conversation Store。已绑定对话检测到 Profile 漂移时会明确报错，不会在运行中静默切换到新的全局当前模型。

如果需要改变一个已开始对话的执行模式或模型边界，应新建对应模式的对话。

## 核心模型

### 文本模型

```bash
export AI8VIDEO_LLM_BASE_URL=https://example.com/v1
export AI8VIDEO_LLM_API_KEY=your-key
export AI8VIDEO_LLM_MODEL=your-text-model
```

文本模型用于意图、脚本规划、审核、检索问题提炼和 Agent 决策。

### 多模态模型

```bash
export AI8VIDEO_MULTIMODAL_BASE_URL=https://example.com/v1
export AI8VIDEO_MULTIMODAL_API_KEY=your-key
export AI8VIDEO_MULTIMODAL_MODEL=your-multimodal-model
```

多模态模型用于镜头语言和图片理解。缺失独立地址或凭据时，可按当前配置继承文本模型连接。

### 图片模型

```bash
export AI8VIDEO_IMAGE_BASE_URL=https://example.com/v1
export AI8VIDEO_IMAGE_API_KEY=your-key
export AI8VIDEO_IMAGE_MODEL=your-image-model
```

图片模型使用 OpenAI 兼容图片接口。输入/输出格式、尺寸、并发、超时和重试等高级参数保留为 `AI8VIDEO_IMAGE_*` 环境变量，不建议在未遇到兼容问题时修改。

### 视频模型

```bash
export AI8VIDEO_VIDEO_BASE_URL=https://example.com
export AI8VIDEO_VIDEO_API_KEY=your-key
export AI8VIDEO_VIDEO_MODEL=your-video-model
export AI8VIDEO_VIDEO_TEMPLATE=doubao-seedance
```

当前模板覆盖豆包 Seedance、云雾 Grok、云雾 Omni、云雾 Veo、百炼 Wan 和通用 OpenAI 兼容视频接口。不同兼容服务的时长与尺寸字段可能不同；上游明确返回不兼容时，创建流程会在受控范围内尝试 `seconds`/`duration` 或尺寸/比例映射。

参考图仍保持图生视频语义。服务只接受公网 URL 时，必须先配置图床；系统不会静默退化为文生视频。

## TTS 与本地媒体

当前 TTS 引擎为 MiMo API。接口、密钥、模型、音色和克隆设置由本地设置页管理，音频转码、时长适配和混音由 FFmpeg 执行。

指定 FFmpeg 路径：

```bash
export AI8VIDEO_FFMPEG_BIN=/absolute/path/to/ffmpeg
export AI8VIDEO_FFPROBE_BIN=/absolute/path/to/ffprobe
```

HTML 动效透明层渲染并发默认为 `1`，可设置为 `1–8`：

```bash
export AI8VIDEO_HYPERFRAMES_RENDER_WORKERS=1
```

如果 HyperFrames CLI 不在默认位置，可通过 `AI8VIDEO_HYPERFRAMES_CLI` 指定。

## 真实任务安全门禁

首次验证建议启用 dry-run：

```bash
export AI8VIDEO_DRY_RUN=1
```

启用真实图片、视频或 TTS 前，建议配置时间窗口额度保护：

```bash
export AI8VIDEO_REAL_JOB_MAX_COUNT=5
export AI8VIDEO_REAL_JOB_WINDOW_SECONDS=3600
```

`AI8VIDEO_REAL_JOB_MAX_COUNT=0` 表示不设置硬上限。真实调用是否计费以及计费方式由所选服务商决定。

Main Agent 还会记录每次高层决策和工具成本；额外付费重试不能因为轮询、刷新或断连被自动重放，必须经过策略校验，必要时等待用户批准。

## 图床与对象存储

- 本地归档始终可用；S3 兼容归档只有在明确配置后才上传指定产物。
- 内置 MJJ.TODAY 是可选第三方匿名图床，上传后素材会离开本机。
- 敏感素材应使用自有上传接口，或选择支持本地/base64 参考图的模型服务。
- 图床设置可能包含本地鉴权令牌，不应复制到日志、Issue 或提交记录。

## 剧本知识库

PostgreSQL 连接使用：

```bash
export AI8VIDEO_SCRIPT_DATABASE_URL='postgresql://user:password@127.0.0.1:5432/ai8video'
```

Schema、`pg_trgm`、BM25、检索模式和回归评测见[知识库说明](knowledge-base.md)。

## 密钥与仓库安全

- `.env`、`mykey.py`、本地用户设置、数据库密码和对象存储凭据不得提交。
- 不要在 Issue、测试快照、日志或命令输出中粘贴完整 API Key、Authorization 或连接串。
- 模型 Profile 的敏感字段只保存在本地配置存储；Conversation Store 只保存非敏感绑定快照。
- 仓库不分发 FFmpeg、模型权重、浏览器或其他大型外部二进制。

需要 `mykey.py` 兼容模板时：

```bash
cp mykey_template.py mykey.py
```

## 最小诊断

```bash
./AI8video config
./AI8video status --url http://127.0.0.1:18720
```

`config` 只显示配置状态，不显示密钥。`status` 默认检查 `18720`；启动器选择其他端口时必须显式传入实际 URL。

Web 只监听 `127.0.0.1`，当前没有公网认证和 TLS，不应直接反向代理到公网。
