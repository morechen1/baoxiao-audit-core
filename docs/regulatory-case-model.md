# RegulatoryCase 独立模型与可信链路

## 适用范围

`RegulatoryCase` 用于监管典型案例、消费者风险提示、“以案说险”、金融消费者教育
案例、公开消费纠纷案例，以及法院官方发布的金融消费者案例。它们可能披露消费场景、
营销表达、风险分析或消费者建议，但不一定存在处罚决定、处罚文号、被处罚机构或处罚
结果，因此绝不写入 `Penalty` 或 `penalties` 表。

采集文档类型、结构化记录类型、审核类型和 Portable Review Bundle 类型统一使用
`regulatory_case`。每个结构化案例唯一绑定一个 `SourceDocument`。

## 字段与证据边界

类别只允许：

```text
regulatory_typical_case
consumer_risk_alert
case_based_education
consumer_dispute_case
judicial_case
```

非空的正式标题、发布机构、发布日期、案例场景、营销原话、案例事实、监管分析和
消费者建议必须具有字段级证据。正文证据的 quote、页码和 offset 必须回到当前文档的
不可变解析文本；规范化转换必须是确定性的。标题、发布机构和日期可使用经过白名单
限制的正式文档元数据，不能根据域名或常识猜测。summary 证据不能自动通过，必须进入
人工或专家复核。

`marketing_wording_disclosed` 只表示公开材料是否明确披露销售表达：

```text
false → marketing_wording 必须为 NULL，且不得提交该字段证据
true  → marketing_wording 必须非空，并由原文证据支持
```

系统不得从案例事实反向生成“原话”，也不得把审核人员或模型生成的文字写成公开资料
披露的营销表达。`regulatory_analysis` 和 `consumer_advice` 仅保存原始官方资料明确
给出的内容，不承载系统推断或法律结论。

事实字段修订与对应证据必须在同一事务提交；任一证据失败时整个操作回滚。草稿仅能在
`parsed` 或 `auto_validation_failed` 状态修订，进入待审或人工终态后由草稿入口锁定。

## 用途隔离

用途只允许：

```text
external_test_candidate
retrieval_only
sealed_external_test
```

普通结构化导入固定从 `external_test_candidate` 开始。该用途保留真实外部评测候选，
不进入知识索引。调整用途必须通过哈希绑定的人工审核决定：

- `retrieval_only`：通过人工内容审核和官方来源真实性确认后，可进入案例检索知识库；
- `external_test_candidate`：开发期间可供后续评测策划，但普通索引命令永远拒绝；
- `sealed_external_test`：封存外部测试集，不进入索引，不用于开发调参，且不能通过
  普通草稿修订或普通审核重新开放。

## 可信审核与索引

链路为：

```text
受控来源审批
→ Pilot 定向采集
→ 不可变原件和解析产物
→ 人工准备字段证据
→ 确定性自动校验
→ Portable Review Bundle
→ 人工内容决定及官方 Occurrence 真实性决定
→ 可信索引资格检查
```

Pilot 采集只创建 `data_type=regulatory_case`、真实性为
`pending_verification` 的文档、Occurrence 和不可变 Pilot 账本项；不自动创建结构化
案例、不自动审核、不自动索引。真实性只能由人工决定引用当前文档的合格官方
Occurrence 后升级为 `verified_public`，并写入独立审计日志。

`index-approved` 只接受同时满足以下条件的记录：

```text
case_usage = retrieval_only
final_review_status = approved | approved_with_revision
authenticity_type = verified_public
解析产物、原件、数据库文本和字段证据完整
自动校验与父文档/结构化记录状态一致
```

索引 payload 保存标题、类别、场景、披露原话、事实、官方分析、消费者建议、发布机构、
发布日期、来源 URL、文档 ID 和记录 ID。当前实现只保存可信结构化 payload 和索引
状态，不实现全文检索、向量检索、RAG、LLM 或自动法律判断。

## 操作入口

```bash
python -m app.cli.main import-structured-drafts --file regulatory-cases.jsonl
python -m app.cli.main revise-structured-draft \
  --file regulatory-case-revision.jsonl --reason "修正原文证据" --actor operator
python -m app.cli.main export-review-bundle --data-type regulatory_case
python -m app.cli.main list-regulatory-cases \
  --case-category consumer_risk_alert \
  --case-usage external_test_candidate
python -m app.cli.main show-regulatory-case --case-id 1
python -m app.cli.main index-approved
```

API 使用 `GET /regulatory-cases` 查询列表，可按类别、用途、审核状态和真实性筛选；
`GET /regulatory-cases/{id}` 返回单条字段、证据、可信状态、索引资格及拒绝原因。
