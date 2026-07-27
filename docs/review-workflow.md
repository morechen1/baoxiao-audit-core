# 人工审核工作流

## 导出

`export-review-batch` 仅选择 `pending_review`，支持 JSONL 和 XLSX。每行包含 batch/item
标识、文档标识、来源/最终 URL、SHA-256、原件路径、完整原文、实际结构化字段、证据
引用、解析警告、自动校验、真实性和当前状态。文件名使用 UUID；批次保存文件 SHA-256
和 schema 版本。

## 导入 JSONL

```json
{"batch_id":1,"batch_item_id":1,"record_id":1,"record_type":"penalty","final_status":"approved","field_reviews":{},"corrections":{},"evidence_quality":"A","review_comment":"已核对","reviewer":"human_reviewer"}
```

系统强制验证批次归属、`pending_review` 状态、payload hash 和未重复决定。
`approved` 禁止 corrections；`approved_with_revision` 必须提供白名单内修订。未知/受保护
字段被明确拒绝。修订后重新执行全部确定性校验，任何失败都会回滚决定、状态和字段。
成功后父文档和结构化记录状态同步，批次全部完成时写入完成时间。

演示数据即使被批准，也因 `demo_only` 被知识索引闸门拒绝。
