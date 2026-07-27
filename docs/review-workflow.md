# 人工审核工作流

## 导出

`export-review-batch` 仅选择 `pending_review`，支持 JSONL 和 XLSX。每行包含记录标识、
来源元数据、原文摘要、解析字段、证据引用、自动校验详情和当前状态。

## 导入 JSONL

```json
{"record_id":1,"record_type":"penalty","final_status":"approved","field_reviews":{},"corrections":{},"evidence_quality":"A","review_comment":"已核对","reviewer":"human_reviewer"}
```

`final_status` 必须是允许的人工结论。`approved_with_revision` 必须提供非空
`corrections`。系统校验 ID/类型、证据等级和状态，保存完整决定与状态历史；结构化修订
不会覆盖 `source_documents.raw_text` 或原件。

演示数据即使被批准，也因 `demo_only` 被知识索引闸门拒绝。
