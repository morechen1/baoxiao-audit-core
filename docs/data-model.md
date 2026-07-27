# 数据模型

核心表：

- `data_sources`：来源、发布方、类型、抓取策略与请求间隔。
- `source_documents`：来源 URL/最终 URL、原件路径、不可变文本、SHA-256、真实性、
  各阶段状态、修订字段和索引时间。
- `document_chunks`：页码、顺序、文本、offset 和未来 embedding 占位。
- `regulations`、`penalties`、`product_documents`：三类公开文档的结构化字段与引用。
- `evaluation_samples`：人工构造样本、风险标签、依据、数据集切分与真实性。
- `review_batches`、`review_decisions`：审核导出和完整决定。
- `status_history`：每次状态变化的旧值、新值、原因和时间。

`source_documents.sha256` 有唯一索引。处罚记录中
`original_sales_wording_disclosed=false` 时，`original_sales_wording` 必须为空，
由 `OriginalWordingValidator` 强制执行。

真实性值为 `verified_public`、`constructed_for_evaluation`、`demo_only`、
`pending_verification`。审核状态集合见 `app/models/enums.py`。
