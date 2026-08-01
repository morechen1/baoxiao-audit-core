# API

服务默认监听 `:8000`，交互文档位于 `/docs`，OpenAPI JSON 位于 `/openapi.json`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 数据库与可选提供者状态 |
| POST/GET | `/sources` | 注册/列出来源 |
| POST | `/collection/url` | 使用已登记来源采集公开 URL |
| POST | `/collection/local` | 采集 `DATA_DIR` 内文件 |
| POST | `/parsing/run` | 解析待处理文档 |
| POST | `/validation/run` | 执行确定性校验 |
| POST | `/structured-drafts/import` | 导入 `DATA_DIR/parsed` 下 JSONL 草稿 |
| POST | `/review/batches` | 导出审核包 |
| POST | `/review/results/import` | 导入 `review_results` 下 JSONL |
| GET | `/records` | 按状态/类型列出文档 |
| GET | `/records/{type}/{id}` | 查看记录与原文 |
| GET | `/regulatory-cases` | 按类别、用途、审核状态或真实性筛选监管案例 |
| GET | `/regulatory-cases/{id}` | 查看监管案例、字段证据与索引资格 |
| POST | `/knowledge/index-approved` | 标记符合资格的可信记录 |
| GET | `/api/v1/knowledge/search` | 查询经物化的可信法规、产品与处罚知识块 |
| POST | `/api/v1/screenings` | 创建确定性营销风险筛查 |
| GET | `/api/v1/screenings/{run_id}` | 读取筛查结果 |
| GET | `/api/v1/screenings/{run_id}/institution-report` | 读取机构端证据报告 |
| GET | `/api/v1/screenings/{run_id}/consumer-notice` | 读取消费者端固定风险提示 |
| POST | `/api/v1/screenings/{run_id}/explanations` | 为完成的筛查创建受控解释 |
| GET | `/api/v1/explanations/{run_id}` | 读取解释运行状态和审计哈希 |
| GET | `/api/v1/explanations/{run_id}/artifact` | 读取已验证的结构化解释 Artifact |
| GET | `/api/v1/explanations/{run_id}/citations` | 读取已验证的连续证据摘录 |

未捕获异常返回 `{"error":{"code","message","details"}}`。FastAPI 自身的输入错误返回
标准 422 结构；下一阶段可统一转换为同一错误信封。

网络采集必须提供启用的 `source_id`，并在所有重定向上匹配来源类型和允许域名；网络与
本地公开资料真实性始终从 `pending_verification` 开始。审核结果必须提供有效
`batch_id`、`batch_item_id`、`reviewed_payload_hash` 和 `schema_version`。真实性升级
必须作为批准决定中的独立 `authenticity_decision` 提交。

监管记录查询额外返回 `regulation_validity_status=unknown` 和
`regulation_validity_display=效力状态待核验`。当前系统没有独立法规效力确认流程，
因此不得把该值展示或解释为“现行有效”。

监管案例查询返回独立 `RegulatoryCase` 字段、`field_evidence`、人工审核状态、真实性、
`can_index` 和明确的 `index_rejection_reasons`。只有用途为 `retrieval_only` 且通过
全部审核、真实性、解析完整性和字段证据门禁的案例才会显示可索引；外部评测候选和
封存测试集均不可索引。

## 可信知识搜索

`GET /api/v1/knowledge/search` 支持 `query`、可重复的 `record_types`/
`pilot_ids`/`evidence_quality`、`authority`、`date_from`、`date_to`、`limit` 和
`offset`。`query` 最长 500 字符，`limit` 为 1–100。规范化后的空查询必须提供
`record_types`、`pilot_ids`、`authority`、日期或 `evidence_quality` 过滤，否则返回
`knowledge_search_filter_required`。结果只来自活跃的 `verified_public` 已审核、已准入块，不包含
`RegulatoryCase`。

每条结果返回确定性分数/排名、记录与来源身份、原文 snippet、命中 token、
已验证 occurrence URL/locator、字段证据引用和块哈希。排序是
`trusted_lexical_rank_v1`，不是标准 BM25。详细公式与失败关闭边界见
[可信知识检索基础层](trusted-retrieval-foundation.md)。

本 API 当前只适用于受控环境，不包含生产级身份认证、权限系统或自动法律结论。

## 确定性营销风险筛查

`POST /api/v1/screenings` 只接受单份纯文本材料，`title` 最长 300 字符，`raw_text`
最长 100000 字符，`material_type` 必须为受控枚举。HTML 标签或脚本只作为普通字符串，
服务不访问 URL 或执行输入。创建前会验证整个可信知识索引；索引异常时以
`screening_trusted_index_invalid` 失败关闭。

输出只包含风险信号、待核验问题和可信证据快照，不构成违法认定。两个报告 GET 端点
只读取已有 finding，不创建新记录。完整边界见
[确定性营销合规风险筛查](deterministic-compliance-screening.md)。

## 受控解释

解释创建只接受 `audience=institution|consumer` 以及
`provider=deterministic_fixture|external`。当前 external Provider 默认禁用并返回
`explanation_provider_not_configured`。服务只对 `completed` ScreeningRun 工作，并在调用
Provider 前重新验证 finding 数量、可信索引 payload、已选块 identity/content hash、来源
完整来源快照和实质字段证据。`evidence_insufficient` finding 可以生成零引用但带固定
不确定性表述的 Artifact。Artifact 的消费者来源目录及 `/citations` 返回服务端解析的
真实 URL、标题、pilot ID、locator、字段名、context scope 和块哈希；模型不能提交这些
来源元数据。rejected/failed run 的 Artifact 端点返回稳定的
`explanation_artifact_not_available`，不会返回未验证自由文本或内部异常。

完整上下文、引用、无依据陈述、历史快照和 illustrative 产品条款边界见
[受控 RAG 解释编排与引用验证层](controlled-rag-explanation-layer.md)。
