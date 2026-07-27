# 人工审核工作流

## 导出

`export-review-batch` 仅选择 `pending_review`，支持 JSONL 和 XLSX。每行包含 batch/item
标识、文档标识、来源/最终 URL、SHA-256、原件路径、完整原文、实际结构化字段、证据
引用、解析警告、自动校验、真实性和当前状态。文件名使用 UUID；批次保存文件 SHA-256
和 schema 版本。

## 导入 JSONL

先由系统生成带正确哈希的结果模板，审核人员只填写决定字段：

```bash
python -m app.cli.main create-review-result-template --batch-id 1
```

```json
{"batch_id":1,"batch_item_id":1,"reviewed_payload_hash":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","schema_version":"2.0","record_id":1,"record_type":"penalty","final_status":"approved","field_reviews":{},"corrections":{},"authenticity_decision":"verified_public","evidence_quality":"A","review_comment":"已核对登记来源和原文引用","reviewer":"human_reviewer"}
```

系统在应用任何决定前重新计算审核包文件 SHA-256；文件缺失或被修改时以
`review_package_tampered` 拒绝整个导入。每项决定还必须精确匹配批次 schema 和条目
payload hash。

只有 `approved_with_revision` 可以包含非空 corrections。文档结构化修订统一使用记录 ID：

```json
{
  "records": [
    {
      "structured_record_id": 123,
      "fields": {"article_text": "修订后的第一条"}
    },
    {
      "structured_record_id": 124,
      "fields": {"article_number": "第二条（修订）"}
    }
  ]
}
```

每个 ID 必须唯一且属于当前文档，字段必须在对应类型白名单内。系统在同一事务中修订并
重新校验该文档全部结构化记录；任一修订失败则全部回滚，并把成功结果单独保存为
`post_review_validation`。评测样本修订另经 Pydantic 和数据库域约束校验，且
`sealed_test` 不可通过普通审核改回其他 split。

## 真实性人工确认

公开网络和本地资料均以 `pending_verification` 进入系统。审核人员只能在
`approved`/`approved_with_revision` 决定中独立填写：

```json
{"authenticity_decision": "verified_public"}
```

系统要求启用且类型匹配的登记来源、来源 URL/最终 URL 均匹配允许域名、每条
`source_quote` 可在不可变原文定位、审核人非空且决定绑定审核包哈希。成功后文档升级为
`verified_public`，并在 `authenticity_decision_logs` 记录旧值、新值、审核人、决定 ID、
原因和时间。拒绝、待核实、专家复核或未绑定决定均不能升级真实性。

演示数据即使被批准，也因 `demo_only` 被知识索引闸门拒绝。
