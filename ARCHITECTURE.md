# AI8video 架构与运行边界

AI8video 是本地优先的有界短视频单体。Python 应用层是控制面和业务真值；Web、CLI 与 Electron 只是入口，Pi Agent 与 HyperFrames 以受控 Node Sidecar 参与特定能力，不拥有产品状态。

核心约束：

- Python 掌握会话、业务规则、凭据来源、成本、任务状态、媒体处理、持久化和恢复。
- 标准模式与 Agent 模式共享业务服务和资源配置，但保持独立的对话与运行状态。
- 模型只获得显式工具面，不开放 Shell、任意文件访问或通用网络工具。
- 外部模型、FFmpeg、PostgreSQL、对象存储或 Sidecar 失败时显式报错，不返回伪成功。

## 总体拓扑

```text
Web / CLI / Electron
          |
          v
Application Facade + Conversation Store
          |
   +------+-------------------+
   |                          |
workflow                   agent
   |                          |
AI8VideoConversation      AI8VideoMainAgent
Controller                   |
   |                       Pi JSONL Sidecar
   |                          |
   +-------------+------------+
                 v
       Shared Business Services
 generation / batch / media / knowledge
 assets / radar / breakdown / integrations
                 |
                 v
 Models / FFmpeg / HyperFrames / PostgreSQL
 Local Files / Optional S3
```

`workflow` 和 `agent` 是 Conversation Store 中的执行模式标识，不是两套重复的媒体流水线。

## 对话与模式边界

新建对话时选择标准或 Agent 模式；首次消息进入服务端后，Conversation Store 原子完成以下操作：

1. 校验客户端携带的 `revision`，避免过期页面覆盖新状态。
2. 锁定 `execution_mode`，已有对话不能再切换模式。
3. 增加消息计数并保存用户消息。
4. 绑定当前模型 Profile 的非敏感快照、指纹和版本。
5. Agent 模式同时创建独立的 Run；标准模式继续进入原控制器。

Conversation Store 使用 SQLite/WAL，核心实体为：

| 实体 | 作用 |
|---|---|
| Conversation | 标题、模式、锁定状态、revision、epoch、当前 Run 和模型绑定 |
| Message | 按 Conversation 与 epoch 保存用户/助手消息，支持客户端幂等 ID |
| Agent Run | 决策次数、成本、等待状态、终态和错误 |
| Agent Action | 工具、幂等键、副作用、重放策略、批准与尝试次数 |
| Agent Context | 本轮目标、方案、批次、用户确认和最终交付快照 |
| Observation | 审核、运行终态、错误或可交付结果等有业务意义的观察 |

API 更新使用 Revision/CAS；重置对话会进入新的 `epoch`，避免旧页面或旧消息污染新一轮。API Key 等敏感凭据不写入 Conversation Store。

删除对话只改变对话可见性与生命周期，不代表删除用户素材、生成结果、任务账本或审计记录。

## 标准模式

标准模式完整保留原有 `AI8VideoConversationController`：

- 负责意图判断、缺失信息追问、智能分集确认、改写与批量请求。
- 通过确定性分支调用既有规划、生成、媒体和资产服务。
- 保存自己的对话状态和确认卡，不读取 Agent Run/Action/Observation 作为决策依据。
- Agent 功能关闭、Pi Sidecar 不可用或 Agent 模型缺失时，标准模式仍可按原能力独立运行。

标准模式不是 Agent 模式的“简化开关”，也不会因为全局模式选择器变化而改写已有对话。

## Agent 模式

Agent 模式由五个边界清晰的组件组成：

| 组件 | 职责 |
|---|---|
| `AI8VideoMainAgent` | 读取服务端快照，在关键节点选择唯一下一步 |
| Pi Agent Core / Pi AI `0.80.10` | 执行一次模型与工具轮次 |
| JSONL Sidecar | 在 Node 与 Python 间传递 decision、tool call 和 result |
| 6 个复合工具 | 把模型选择映射到项目内置的高层能力 |
| Python Runtime | 执行提交、轮询、下载、校验、后处理、归档和恢复 |

Pi 是 AI8video Agent 的内部执行组件，可以理解为受约束的“决策员工”；它不是完整产品、不是业务真值，也不接管标准模式。

### 固定工具面

Main Agent 每次只能选择一个工具：

| 工具 | 作用 | 主要副作用 |
|---|---|---|
| `prepare_video_plan` | 把用户目标转换为结构化视频方案 | 模型规划 |
| `review_video_plan` | 审核并修正最新方案 | 模型审核 |
| `generate_video_batch` | 提交已审核方案给 Runtime | 可能产生付费生成 |
| `inspect_generation_result` | 汇总真实终态、成功资产和失败原因 | 只读 |
| `archive_and_deliver` | 整理可交付结果，不向外部平台发布 | 本地交付快照 |
| `task_user` | 遇到歧义、额外成本或高风险选择时暂停 | 等待用户 |

工具由 `ActionPolicyGuard` 校验数量、成本、副作用、重放安全性和批准边界。相同动作使用稳定幂等键；额外付费重试需要明确批准，单个动作有尝试上限。

Pi Sidecar 不提供 Shell、文件系统、浏览器、代码执行或通用 HTTP 工具。它一次只处理一个 decision，请求中的工具按顺序执行，完成一个工具后立即把控制权交还 Python。

### 两层循环

```text
主 Agent 决策循环
用户目标 / 关键 Observation
          |
          v
     Main Agent 决策
          |
          v
       一个复合工具
          |
          +-------------------------+
                                    |
Runtime 执行循环                    v
提交 -> 轮询 -> 下载 -> 校验 -> 后处理 -> 归档
  ^                                      |
  +----------- 机械进度事件 -------------+
                                         |
                              生成终态 / 用户检查点
                                         |
                                         +--> 新 Observation
```

Runtime 的等待时间、百分比、轮询次数和普通下载进度不会触发新的模型调用。以下事件才会唤醒 Main Agent：

- 方案审核结论；
- 用户补充、确认或拒绝；
- 额外付费重试批准；
- 生成成功、失败或部分成功终态；
- 尾帧连续生成等需要用户选择的检查点；
- 已形成可交付结果。

生成进入 `pending` 后，Main Agent 立即停止本轮决策。Runtime 完成真实工作并记录终态 Observation，再异步恢复对应 Run。

### 恢复与失败语义

- Run、Action、Context 与 Observation 持久化到 Conversation Store，不依赖浏览器标签页存活。
- 视频任务的提交与终态以本地生成批次和任务账本为准，页面刷新只读取真实状态。
- 已成功动作可按幂等记录读取结果；不确定的付费副作用不得在断连后盲目重放。
- 相同高层动作失败后最多进入受控重试；已经计费或会新增成本的重试转为等待批准。
- 无进展、成本上限、决策上限、业务歧义和部分成功交付都会显式暂停或终止。
- 最终交付只整理已由 Runtime 证明存在的资产，不自动发布到外部平台。

### Skills 与专项能力

`agent_skills/` 中的 Skills 继续承载 Planner、知识建树、知识审核、镜头语言和剧本重建等专项策略。它们不是主对话中的并列自治 Agent，也不组成 Supervisor → Planner → Reviewer 的持续接力。

复合工具可以在内部复用这些策略和既有服务，但字段校验、幂等、成本、取消、副作用顺序和持久化始终由程序负责。

## 共享业务层

标准与 Agent 模式都复用同一套领域实现：

| 区域 | 负责 |
|---|---|
| `generation/` | 脚本拆分、图片/视频生成、连续尾帧、审核与结果组装 |
| `batch/` | 任务账本、调度、租约、取消、监督器、报告和告警 |
| `media/` | FFmpeg、MiMo TTS、时间轴、合并和 HTML 动效 |
| `knowledge/` | 文档建树、审核、BM25、模糊召回和重排 |
| `assets/` | 用户素材、结果、归档和回收站 |
| `radar/`、`breakdown/` | 热点聚合、视频拆解和脚本重建 |
| `integrations/` | 文本、图片、视频模型与 HTTP 协议适配 |

不得为 Agent 模式复制第二套视频生成、TTS、知识库或归档实现。

## 数据与事实源

| 位置 | 事实与恢复边界 |
|---|---|
| `用户文件夹/用户素材/` | 用户原始素材 |
| `用户文件夹/用户生成结果/` | 最终视频、封面、manifest 和恢复元数据 |
| `用户文件夹/*/reviews/` | 视频、TTS、HTML 动效等非破坏编辑草稿 |
| `temp/ai8video/` | Conversation Store、Agent Journal、生成任务账本和运行恢复状态 |
| PostgreSQL | 剧本文档元数据、审核叶节点和可重建检索索引 |
| `media_resources/ai8video/` | 可选本地归档、批次报告和告警 |
| S3 兼容存储 | 只有显式配置后才保存指定归档产物 |

`temp/ai8video/` 不是普通可丢弃缓存。任务运行或恢复期间手动清空会破坏对话、Agent 与任务账本的一致性。

原始用户文档和媒体是主事实源；数据库索引、预览缓存和透明动效层属于可重建派生数据。

## 源码布局

```text
src/ai8video/
├── core/           配置、路径和基础模型
├── application/    会话门面、标准控制器、Main Agent、Store 与恢复
├── agent_runtime/  Pi 适配、复合工具、策略护栏和终态观察
├── agent_skills/   带版本与能力绑定的专项 Skills
├── generation/     规划、图片/视频生成和结果审核
├── batch/          任务账本、调度、监督、报告与告警
├── media/          FFmpeg、TTS、精剪和 HTML 动效
├── knowledge/      剧本知识库、BM25、查询和重排
├── assets/         素材、结果、归档和回收站
├── radar/          热点聚合与摘要
├── breakdown/      爆款视频拆解
├── integrations/   模型、数据库和 HTTP 适配
└── interfaces/     Web 与 CLI

desktop/electron/   Electron 桌面壳
desktop/runtime/    冻结后端与发行运行时工具
tests/              离线质量门禁，不进入运行包
```

## 强制依赖规则

1. `interfaces/` 可以调用 `application/`；核心业务模块不得反向依赖 Web 或 Electron。
2. 跨领域用例通过应用门面和共享服务编排，CLI、Web、标准模式与 Agent 模式不得各复制一套流程。
3. `core/` 只保存稳定基础概念，不依赖入口、业务领域或外部系统。
4. Pi、模型 API、FFmpeg、HyperFrames、PostgreSQL、文件系统和对象存储都是边界资源，适配器不得成为第二业务真值。
5. 第三方 Agent 库只能藏在受控适配层后；不得暴露其通用系统工具、会话存储或产品身份。
6. 产品显示名统一为 `AI8video`，Python 包和命令统一为 `ai8video`，环境变量统一使用 `AI8VIDEO_` 前缀。
7. `media/local_tts.py` 是 TTS 稳定门面；设置、文本、MiMo 请求和 FFmpeg 处理分别保留在对应 `local_tts_*` 模块。
8. 用户数据与恢复账本不得因 UI 删除、缓存清理或模式切换被隐式级联删除。

相关结构约束由 `tests/test_ai8video_architecture.py` 等最小测试持续检查。

## 演进原则

- 只有出现真实独立部署、故障域或扩缩容需求时，才把领域执行拆成远程服务。
- 只有出现第二个真实实现时，才为检索、模型或工具增加接口、工厂和策略层。
- 新增 Agent 工具前必须先定义副作用、幂等键、成本、批准、重放和终态 Observation。
- 新增轮询或进度事件不得默认唤醒 Main Agent；先判断它是否改变下一步业务决策。
- 标准模式的公共契约和行为必须作为独立兼容基线保留。

运行配置见[配置说明](docs/configuration.md)，知识检索见[知识库说明](docs/knowledge-base.md)，桌面构建与发布见[桌面发行说明](docs/desktop-release.md)。
