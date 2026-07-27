# 数据模型

核心表：

- `data_sources`：来源、发布方、类型、抓取策略与请求间隔。
- `source_documents`：来源 URL/最终 URL、原件路径、不可变文本、SHA-256、真实性、
  各阶段状态、修订字段和索引时间。
- `document_chunks`：页码、顺序、文本、offset 和未来 embedding 占位。
- `document_occurrences`：同一内容在不同来源出现的来源 ID、URL、发布方、HTTP 状态和
  元数据；真实性决定引用具体 occurrence。
- `regulations`、`penalties`、`product_documents`：三类公开文档的结构化字段与引用。
- `evaluation_samples`：人工构造样本、风险标签、依据、数据集切分与真实性。
- `review_batches`：审核文件和可移植 bundle 的路径/SHA-256、manifest SHA-256、schema
  版本与完成时间。
- `review_batch_items`：批次成员、导出状态、payload hash 和唯一决定。
- `review_decisions`：人工决定、证据等级、字段复核、修订、审核人、条目 payload hash
  和 schema 版本；`batch_id` 不可为空且数据库只接受人工决定状态。
- `authenticity_decision_logs`：真实性变更前后值、审核人、绑定的来源、已核验
  occurrence、审核决定、原因和时间。
- `status_history`：每次状态变化的旧值、新值、原因和时间。

`data_sources.source_type` 和 `source_documents.data_type` 只接受监管、处罚和产品资料；
评测样本只存在于独立表。`source_documents.sha256` 有唯一索引。处罚记录中
`original_sales_wording_disclosed=false` 时，`original_sales_wording` 必须为空，
由数据库 CheckConstraint 与 `OriginalWordingValidator` 双重强制执行。处罚和产品主记录
每个父文档唯一；监管规则允许多个条款记录。评测样本类别、split、非空文本和
`constructed_for_evaluation` 真实性由数据库约束。

真实性值为 `verified_public`、`constructed_for_evaluation`、`demo_only`、
`pending_verification`。审核状态集合见 `app/models/enums.py`。

`final_review_status` 只保存人工审核结果。`knowledge_index_status` 独立取值
`not_indexed | indexed | index_failed`，`indexed_at` 记录索引时间。
