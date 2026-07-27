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

本 API 当前只适用于受控环境，不包含生产级身份认证、权限系统或自动法律结论。
