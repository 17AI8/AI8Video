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

## 多 Agent 演进边界

当前已进入多 Agent 改造的影子运行阶段：

1. `generation_batches` 中的现有生成批次会同步为 `supervisor` 根任务，保持旧接口和运行行为兼容。
2. `agent_tasks`、`agent_task_edges` 和 `agent_task_events` 分别保存任务事实、DAG（有向无环图）依赖和不可变审计事件。
3. 新任务通过幂等键创建，执行器使用 CAS（比较并交换）版本抢占和续租，避免两个 worker 同时接管同一任务。
4. 租约过期只进入 `recovery_required`，不会盲目重放模型提交、视频生成或归档等有外部副作用的步骤。
5. Planner 和 Reviewer 已作为根任务的一层 Specialist Agent 接入；Reviewer 依赖 Planner，但两者都直接以 `supervisor` 为父任务，任务图深度固定为 1。
6. 当前 Specialist 只复用既有规划输出、后审核结果和确定性检查证据，不增加模型请求、不改变提示词或视频结果；任务账本故障采用 fail-open（失败开放），不能阻断真实生成。
7. 子任务快照只保存数量、状态、字段存在性和摘要哈希，不保存完整提示词、模型原始响应、URL 或绝对路径。
8. 每条独立视频后续仍可演进为并行 `VideoTask` Worker；FFmpeg、TTS、轮询和归档继续作为确定性工具，不包装成自治 Agent。
9. 单进程 `AgentTaskScheduler` 先取得本地并发容量再原子认领 SQLite 任务；取消中的 handler 在真实退出前继续占用槽位，避免突破并发上限。
10. 每次认领使用唯一 worker ID 作为 fencing（栅栏）令牌；续租和终态提交必须同时满足所有权、当前状态及租约未过期，迟到 worker 不能覆盖恢复后的结果。
11. 启动恢复默认拒绝自动重放；只有 handler 注册表明确标记为 `replay_safe` 的纯观察任务可以从 `recovery_required` 重新排队。模型调用、视频创建、媒体后处理和归档仍停留在人工对账边界。
12. 失败或取消的依赖会把尚未执行的下游任务收敛为 `cancelled`，不再永久停留在 queued。
13. 取消请求是 sticky（粘性）的：一旦进入 `cancel_requested`，迟到的成功或失败结果只能收敛为 `cancelled`，不能重新解锁依赖或进入重试。
14. 根任务和 Agent 子任务都采用 terminal first-wins（终态首次写入获胜）；任何迟到终态、结果、worker 或错误信息都不能覆盖首个终态事实。
15. 调度器关闭是一次性的完整状态迁移：关闭与入队按同一周期锁排序，账本持久化异常会显式记录但不能阻止 dispatcher、线程池和本机运行态停止；轮询周期必须短于心跳周期，心跳周期必须短于租约。
16. 普通聊天批量生成单轮限制为 1～5 条独立成片，每条固定 10 秒；该策略不改变 `DailyBatchRunner` 的每日跑量语义。
17. 普通批量采用严格串行的自适应链路：生成第 N 条 → 归档 → 审查真实成片 → 把可迁移反馈写入第 N+1 条提示词，再提交下一条；不会预先并发提交全部视频。
18. 成片 Reviewer 从本地成片抽取四个时间点组成联系表（contact sheet，时间线宫格），结合时长技术检查和多模态模型输出问题、优化空间及下一条约束；每条审查都映射为独立作用域的 Reviewer 影子任务。
19. 审查反馈只影响下一条独立视频，不自动重做当前条，避免无限循环和重复计费；最后一条仍会审查并把问题展示给用户。
20. 前一条生成、归档或成片审查不可用时，剩余未提交视频统一转为 skipped（已跳过），停止继续消耗外部生成额度；有外部副作用的提交仍不属于自动重放安全步骤。

Planner/Reviewer 影子观察已由单进程调度器执行，最大并发为 2，并在 Web 启动时完成安全恢复扫描。真实生成根任务尚未迁移到该调度器；只有出现真实的进程隔离、故障域或独立扩缩容需求，才替换执行 transport，不先引入微服务或第三方通用 Agent 框架。

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
