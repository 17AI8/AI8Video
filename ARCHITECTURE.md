# AI8video Agent 架构与边界

AI8video 是面向短视频生产的自研有界垂直 Agent，也是一个本地有界单体：一个 Python 进程承载意图理解、上下文状态、工作流决策、能力调用、观察反馈和媒体处理，Web、CLI 与 Electron 只负责接入。源码统一放在 `src/ai8video/`，避免根目录散落模块和同一能力存在多个入口实现。

AI8video 的 Agent 身份来自完整运行闭环，而不是某个第三方框架：项目在 `application/` 中实现自己的 Agent Runtime，并把自主决策限制在短视频生产能力集合内。

## Agent 运行闭环

```mermaid
flowchart TB
    Goal["用户目标 / 多轮指令"] --> Interface["Web / CLI / Electron\n入口适配器"] --> Intent

    subgraph AgentRuntime["AI8video Agent Runtime · application/"]
        direction TB
        Intent["1. 意图理解\nRequest Interpreter"]
        Context["2. 上下文组装\n会话 / 素材 / 历史任务"]
        Controller["3. Agent Controller\nConversation Controller"]
        Decision{"4. 能力与下一步决策"}
        Guard["5. 执行护栏\n校验 / 额度 / 幂等 / 超时"]

        Intent --> Context --> Controller --> Decision --> Guard
    end

    Decision -. "信息不足" .-> Clarify["追问与约束补全"] --> Await["等待用户补充后进入下一轮"]
    Guard --> Script
    Guard --> Knowledge
    Guard --> Radar
    Guard --> Generate
    Guard --> Media
    Guard --> Asset

    subgraph Capability["6. Short-video Tool Layer · 有界能力集合"]
        direction LR
        Script["脚本生成与拆分\ngeneration/"]
        Knowledge["剧本知识库\nknowledge/"]
        Radar["热点与爆款拆解\nradar/ + breakdown/"]
        Generate["图片 / 视频 / 批量任务\ngeneration/ + batch/"]
        Media["TTS / 字幕 / 合并 / 动效\nmedia/"]
        Asset["素材 / 归档 / 回收站\nassets/"]
    end

    Script --> Boundary["模型 API / FFmpeg / HyperFrames\nPostgreSQL / 文件系统"]
    Knowledge --> Boundary
    Radar --> Boundary
    Generate --> Boundary
    Media --> Boundary
    Asset --> Boundary
    Boundary --> Observation["7. Observation\n结果 / 错误 / 进度 / 产物"]
    Observation --> Evaluate{"Agent Controller\n评估 Observation"}
    Evaluate -->|继续| Continue["↺ 写回状态并进入下一轮决策"]
    Evaluate -->|完成| Delivery["8. 结果交付"] --> Result["短视频 / 脚本 / 任务报告 / 可继续会话"]
    Evaluate -->|失败| Failure["显式错误 / 重试 / 停止"]

    State[("Agent State & Memory\nConversationState / TaskLedger\n剧本知识库 / 结果目录")] -. "读取与写入" .-> Controller

    classDef agent fill:#eef2ff,stroke:#6366f1,color:#111827,stroke-width:1.5px
    classDef decision fill:#f5f3ff,stroke:#7c3aed,color:#111827,stroke-width:2px
    classDef tool fill:#ecfeff,stroke:#0891b2,color:#0f172a
    classDef state fill:#fff7ed,stroke:#ea580c,color:#431407
    classDef external fill:#f8fafc,stroke:#64748b,color:#0f172a
    class Intent,Context,Controller,Guard,Observation,Evaluate,Clarify,Continue,Delivery,Failure agent
    class Decision,Evaluate decision
    class Script,Knowledge,Radar,Generate,Media,Asset tool
    class State state
    class Goal,Interface,Boundary,Result,Await external
    style AgentRuntime fill:#f8fafc,stroke:#6366f1,stroke-width:2px
    style Capability fill:#f0fdfa,stroke:#0891b2,stroke-width:1.5px
```

这个闭环表达的是当前真实行为：模型负责理解意图和结构化信息，`AI8VideoConversationController` 依据上下文选择内置能力，本地 Python 执行工具顺序与安全护栏，再把执行结果作为 Observation（观察）送回控制器。它不是无限自主循环；信息不足时追问用户，任务完成、失败或触发护栏时停止。

## 工程分层

```mermaid
flowchart LR
    Interfaces["interfaces/\nWeb / CLI"] --> Application["application/\nAgent Runtime"]
    Desktop["desktop/electron/\n桌面壳"] --> Interfaces
    Application --> Domains["generation / batch / assets\nknowledge / media / radar / breakdown"]
    Domains --> Integrations["integrations/\n模型与 HTTP 适配"]
    Domains --> Runtime["FFmpeg / HyperFrames\nPostgreSQL / 文件系统"]
    Domains --> Results["用户生成结果\n最终结果事实源"]
```

## 源码布局

```text
src/ai8video/
├── core/          产品身份、配置、路径和基础数据模型
├── application/   Agent Runtime：意图、上下文、决策、会话与应用门面
├── generation/    脚本拆分、生成流水线、任务与结果审核
├── batch/         批量任务、报告、告警、账本和守护进程
├── assets/        用户素材、生成结果、归档和回收站
├── knowledge/     剧本知识库、查询、重排和文本处理
├── media/         FFmpeg、配音、字幕、合并与 HTML 动效
├── integrations/  文本、图片、视频模型及 HTTP 适配器
├── radar/         热点聚合与摘要
├── breakdown/     爆款视频拆解
└── interfaces/    Web、CLI 和演示入口

desktop/electron/  可选桌面壳
tests/             离线质量门禁，不进入运行包
```

## 强制依赖规则

1. `interfaces/` 可以依赖 `application/` 和具体功能模块；核心模块不得反向导入 `interfaces/`。
2. 跨功能的会话、配置、资产和批量用例优先通过 `application/facade.py` 暴露，CLI 不复制业务流程。
3. `core/` 只放稳定基础概念，不依赖业务领域、入口或外部系统。
4. 模型 API、FFmpeg、HyperFrames、PostgreSQL 和文件系统属于边界资源；失败必须显式返回真实错误，不伪造成功。
5. `用户文件夹/用户生成结果/` 是最终结果事实源；`temp/ai8video/` 只保存可丢弃、可重建的过程状态。
6. 产品显示名统一为 `AI8video`，Python 包和命令统一为 `ai8video`，环境变量统一为 `AI8VIDEO_`。旧名称只允许存在于迁移兼容代码中，读取后只写新名称。
7. `ai8video_cli/`、`frontends/` 和 `tools/ai8video/` 已移除，不得重新建立第二套入口或核心包。

上述依赖方向、旧入口残留和重复核心路由由 `tests/test_ai8video_architecture.py` 持续检查。

## 模块职责

| 区域 | 负责 | 不负责 |
|---|---|---|
| `interfaces/` | HTTP、命令参数、输入校验、序列化、进程启动 | 核心状态与业务规则 |
| `application/` | Agent Runtime、会话、请求解释、能力决策和跨领域编排 | 页面样式、模型协议细节 |
| `generation/`、`batch/` | 生成与批量任务生命周期 | 浏览器或桌面窗口 |
| `assets/`、`knowledge/`、`media/`、`radar/`、`breakdown/` | 各自领域能力 | 跨领域总流程 |
| `integrations/` | 外部模型和 HTTP 协议适配 | 产品交互决策 |
| `desktop/electron/` | 桌面窗口和 Python 服务拉起 | 短视频业务实现 |

当前保持单进程、自研 Agent Runtime 和轻量 CLI；除非出现独立部署、独立扩缩容或明确的多实现需求，不引入微服务、第三方通用 Agent 框架或额外抽象层。
