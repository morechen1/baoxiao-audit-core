# 数据模型

核心表：

- `data_sources`：来源、发布方、类型、抓取策略与请求间隔。
- `source_documents`：来源 URL/最终 URL、原件路径/哈希、当前解析产物/文本哈希、
  不可变文本、解析器版本、真实性、各阶段状态、修订字段和索引时间。
- `document_chunks`：页码、顺序、文本、offset 和未来 embedding 占位；范围必须能
  回到当前不可变解析文本。
- `parsed_artifact_versions`：每次显式解析生成的不可变 JSON 路径、产物/文本/原件
  哈希、解析器版本和时间；重新解析不会覆盖历史版本。
- `document_occurrences`：同一内容在不同来源出现的来源 ID、URL、发布方、HTTP 状态和
  元数据；真实性决定引用具体 occurrence。
- `pilot_source_registrations`：经 robots、使用条款和人工审批的来源登记不可变版本，
  包含稳定哈希、累计请求时间、单版本采集上限及对应 `data_source`。
- `pilot_collection_runs`：一次受控试采集的配置集合哈希、操作者、运行状态和结果计数。
- `pilot_collection_items`：全局唯一 `pilot_id`、Manifest/来源哈希、审批快照、明确的
  document/occurrence 绑定、尝试次数和固定错误码；它是 Pilot 历史报告的事实来源。
- `regulations`、`penalties`、`product_documents`：监管规则、行政处罚和产品资料的
  结构化字段、兼容展示引用和正式校验用的 `field_evidence_json`。
- `regulatory_cases`：监管典型案例、消费者风险提示、“以案说险”、公开消费纠纷及
  法院官方金融消费者案例；保存正式标题、发布机构/日期、案例类别、场景、披露原话、
  事实、官方分析/建议、用途标签、字段证据、人工审核状态和证据质量。每个父文档唯一，
  与 `penalties` 完全隔离。
- `structured_draft_revisions`：预审字段/证据的前后值、动作、原因、操作人和时间。
- `evaluation_samples`：人工构造样本、风险标签、依据、数据集切分与真实性。
- `review_batches`：审核文件和可移植 bundle 的路径/SHA-256、manifest SHA-256、schema
  版本、完成或取消时间及取消原因。
- `review_reservations`：以 `(record_type, record_id)` 的 active 部分唯一索引实现开放
  批次排他预留；完成决定或取消批次后记录释放原因和时间。
- `review_batch_items`：批次成员、导出状态、payload hash 和唯一决定。
- `review_decisions`：人工决定、证据等级、字段复核、修订、审核人、条目 payload hash
  和 schema 版本；`batch_id` 不可为空且数据库只接受人工决定状态。
- `authenticity_decision_logs`：真实性变更前后值、审核人、绑定的来源、已核验
  occurrence、审核决定、原因和时间。
- `status_history`：每次状态变化的旧值、新值、原因和时间。

`data_sources.source_type` 和 `source_documents.data_type` 接受监管规则、处罚、产品
资料和监管案例；评测样本只存在于独立表。`source_documents.sha256` 有唯一索引。
监管案例类别和用途由数据库枚举值 CheckConstraint 约束；用途默认为
`external_test_candidate`。处罚记录中
`original_sales_wording_disclosed=false` 时，`original_sales_wording` 必须为空，
由数据库 CheckConstraint 与 `OriginalWordingValidator` 双重强制执行。处罚和产品主记录
每个父文档唯一；监管规则允许多个条款记录。评测样本类别、split、非空文本和
`constructed_for_evaluation` 真实性由数据库约束。

监管标题及处罚对象属于强制证据字段。法规效力尚未建立独立确认关系，因此
`regulations.validity_status` 由数据库约束为 `NULL/unknown`，应用默认 `unknown`；
普通结构化导入和审核修订不能声明“现行有效”、废止、失效或被替代。

监管案例的 `case_title`、`publisher`、`published_at`、`scenario_text`、
`marketing_wording`、`case_facts`、`regulatory_analysis` 和 `consumer_advice`
只要非空就必须绑定字段证据。`marketing_wording_disclosed=false` 时数据库和应用层
同时要求 `marketing_wording IS NULL`。普通导入不能直接设置非默认用途；调整到
`retrieval_only` 或 `sealed_external_test` 必须经过人工审核。封存用途不能通过草稿
修订或普通审核重新开放。

真实性值为 `verified_public`、`constructed_for_evaluation`、`demo_only`、
`pending_verification`。审核状态集合见 `app/models/enums.py`。

`final_review_status` 只保存人工审核结果。`knowledge_index_status` 独立取值
`not_indexed | indexed | index_failed`，`indexed_at` 记录索引时间。
监管案例只有在用途为 `retrieval_only`、人工决定为批准、真实性为
`verified_public` 且其余完整性/证据门禁全部通过时才可索引；候选和封存外部评测数据
均不能进入知识索引。

Pilot 来源登记发生名称、发布机构、URL、类型或抓取策略变化时创建新版本，不修改旧
`data_sources` 或旧 PilotItem。`max_documents` 和 `last_request_at` 均按来源版本在
数据库事务内锁定与累计；JSONL 只是带 run/item ID 的可选导出，不参与历史指标计算。
