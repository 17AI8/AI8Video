# 剧本知识库与 BM25 检索

AI8video 的剧本知识库是本地文档检索能力，不是独立 RAG 平台。当前目标是让用户上传一份文档、审核其知识树，并在生成时把检索严格限制在所选文档内。

## 数据边界

支持的原始文件：

- TXT；
- Markdown；
- DOCX。

原稿保存在：

```text
用户文件夹/用户素材/剧本素材库/
```

PostgreSQL 保存文档元数据、审核后的知识叶节点、标签、倒排表和可重建索引，不替代或删除用户原稿。原始文件 SHA-256 是内容变化判定依据。

创建数据库并配置连接：

```bash
createdb ai8video
export AI8VIDEO_SCRIPT_DATABASE_URL='postgresql://user:password@127.0.0.1:5432/ai8video'
```

数据库角色需要能够创建和使用表、索引及 `pg_trgm` 扩展。默认连接为 `postgresql:///ai8video`。

## 入库流程

```text
原始 TXT / Markdown / DOCX
        |
        v
KnowledgeBaseAgent 建立单文档知识树
        |
        v
Reviewer 审核原子性、覆盖度与检索价值
        |
        v
审核叶节点写入 PostgreSQL
        |
        v
统一分词 + 当前文档 BM25 倒排索引
```

知识树负责组织和切分原文；BM25 只对审核后的叶节点建立词法索引，不负责建树。

## 检索流程

```text
用户生成要求 + 当前所选文档
        |
        v
文本模型提炼检索问题
程序保留标准号、型号、版本、日期、数字和单位
        |
        v
当前文档 BM25 Top K
        |
        +--> 同文档 pg_trgm 不足补召回
        |
        v
模型 Rerank
        |
        v
再次校验文档范围
        |
        v
带来源知识段注入生成上下文
```

范围约束贯穿召回、模糊补召回、重排和最终注入，不能先跨全库检索再在末尾过滤。

BM25 的 `N`、文档频率、词频、叶节点长度和平均长度都只统计当前选中文档。分词执行 Unicode NFKC、中文二元词和 ASCII 词项，并保护专业标识符。

默认召回 Top 20，重排后注入 Top 5。查询失败或模型重排不可用时必须记录真实降级原因。

## 检索模式

`AI8VIDEO_SCRIPT_RETRIEVAL_MODE` 支持：

| 模式 | 行为 |
|---|---|
| `legacy` | 使用旧 PostgreSQL 全文排序 |
| `shadow` | 返回旧排序，同时记录 BM25 对比数据 |
| `bm25` | 使用 BM25 主排序；当前默认值 |

常用配置：

```bash
export AI8VIDEO_SCRIPT_RETRIEVAL_MODE=bm25
export AI8VIDEO_SCRIPT_RECALL_TOP_K=20
export AI8VIDEO_SCRIPT_INJECT_TOP_K=5
```

范围：

- `AI8VIDEO_SCRIPT_RECALL_TOP_K`：`5–30`，默认 `20`；
- `AI8VIDEO_SCRIPT_INJECT_TOP_K`：`1–10`，默认 `5`。

整篇原文兼容回退：

```bash
export AI8VIDEO_SCRIPT_FULL_FALLBACK_ENABLED=0
```

当前兼容默认允许全文回退。对严格范围、长文档或高敏感场景，建议设为 `0`，让无候选或检索失败显式返回，而不是无界注入整篇原文。

切换为 `legacy` 后重启即可回滚检索排序；增量 Schema 和 BM25 派生表可以保留。

## 生命周期与恢复

- Schema 初始化、原稿同步和无模型 BM25 回填在 Web 启动或知识库管理接口执行。
- 普通生成查询只读取现有索引并实时评分，不扫描源目录、不迁移 Schema，也不重建知识树。
- 旧文档缺少 SHA-256、但文件大小和修改时间一致时，可以只补指纹，不删除 ready 叶节点。
- 原始字节变化但提取正文未变化时，可保留已审核知识树。
- 删除文档时应删除对应派生索引，但不得通过数据库操作删除用户原稿。
- 所选文档未完成建树或索引时应显式阻止知识检索，不能静默改用另一份文档。

当前方案不依赖向量数据库、Redis、Jieba、第三方 BM25 包或本地 Embedding 模型。未来只有出现真实多路召回需求时，才评估 RRF、向量检索或独立检索服务。

## 健康状态与 Trace

知识库健康信息包含：

- 数据库与 Schema 状态；
- ready / pending 文档数量；
- BM25 索引与分词器版本；
- 当前检索模式；
- 最近错误。

检索 Trace 默认追加到：

```text
temp/ai8video/script_knowledge_retrieval_traces.jsonl
```

Trace 用于定位查询提炼、召回、模糊补召回、重排、范围校验和降级原因。它属于运行诊断数据，不应包含 API Key。

## Golden Regression

知识库回归使用独立、可丢弃的 PostgreSQL 测试库：

```bash
AI8VIDEO_TEST_POSTGRES_URL='postgresql:///ai8video_test' PYTHONPATH=src \
  python tests/evaluate_script_knowledge_golden.py
```

评测覆盖 Recall@20、Hit@3、重排后 Hit@5、无答案精度和文档范围泄漏率。BM25 指标退化或未选文档泄漏不为 0 时，脚本返回非零状态。

> [!WARNING]
> PostgreSQL 集成测试会清理专用测试表。`AI8VIDEO_TEST_POSTGRES_URL` 只能指向独立、可丢弃的测试数据库，严禁使用生产库或用户库。

## 当前边界

- 每次生成当前只选择一份已建树文档作为检索范围。
- 默认支持 TXT、Markdown 和 DOCX；复杂 PDF、扫描件和完整 OCR 栈尚未纳入核心依赖。
- BM25 是词法相关性算法，不等于语义向量检索。
- `pg_trgm` 只补充错别字、近似名称和短词候选，不与 BM25 平权。
- 原始文档是事实源，知识树、叶节点索引和 Trace 都是派生数据。

模型与数据库配置见[配置说明](configuration.md)，整体数据边界见[架构说明](../ARCHITECTURE.md)。
