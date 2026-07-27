# 人工审核工作流

## 导出

`export-review-batch` 仅选择 `pending_review`，支持 JSONL 和 XLSX。每行包含 batch/item
标识、文档标识、来源/最终 URL、SHA-256、原件路径、完整原文、实际结构化字段、证据
引用、全部来源 occurrence、解析警告、自动校验、真实性和当前状态。文件名使用 UUID；
批次保存文件 SHA-256 和 schema 版本。真实资料优先使用
`export-review-bundle`：ZIP 内的 `manifest.json`、`review.jsonl`、决定模板和
`sources/<raw_sha256>.<ext>`、`parsed/<parsed_artifact_sha256>.json` 均由服务器固定
哈希，且只使用相对路径。manifest 同时保存原件哈希、解析产物/文本哈希、解析器版本及
字段证据摘要。结构化记录展示证据 quote、页码、offset、mode 和 transformation note。

同一记录在开放批次中只有一个 `active` 预留。并发导出依赖数据库唯一约束抢占，冲突
记录被跳过；无可用记录时返回 `no_records_available_for_review`，不会留下空批次。
需要重新分配时执行：

```bash
python -m app.cli.main cancel-review-batch \
  --batch-id 1 --reason "审核任务重新分配"
```

仅 `exported` 批次可取消。取消会记录原因/时间并释放未决预留；`completed` 批次不可
取消，也不会删除已产生的决定。

## 导入 JSONL

先由系统生成带正确哈希的结果模板，审核人员只填写决定字段：

```bash
python -m app.cli.main create-review-result-template --batch-id 1
```

```json
{"batch_id":1,"batch_item_id":1,"reviewed_payload_hash":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","schema_version":"2.0","record_id":1,"record_type":"penalty","final_status":"approved","field_reviews":{},"corrections":{},"authenticity_decision":{"new_type":"verified_public","verified_occurrence_id":10,"reason":"已核对官方网站、文件原文及发布机构"},"evidence_quality":"A","review_comment":"已核对登记来源和原文引用","reviewer":"human_reviewer"}
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
      "fields": {"article_text": "修订后的第一条"},
      "field_evidence": {
        "article_text": [{
          "quote": "修订后的第一条",
          "page_number": 2,
          "start_offset": 100,
          "end_offset": 108,
          "mode": "verbatim"
        }]
      }
    },
    {
      "structured_record_id": 124,
      "fields": {"article_number": "第二条（修订）"},
      "field_evidence": {
        "article_number": [{
          "quote": "第二条（修订）",
          "page_number": 2,
          "start_offset": 109,
          "end_offset": 116,
          "mode": "verbatim"
        }]
      }
    }
  ]
}
```

每个 ID 必须唯一且属于当前文档，字段必须在对应类型白名单内。证据型字段必须同步提交
同名 `field_evidence`；未修改字段不得替换证据。系统先把修订合并到完整候选记录，再用
对应草稿模型校验类型，并从不可变解析产物复核 quote、页码、offset 和声明的确定性转换。
ISO 日期会转换为日期对象；任一字段或证据非法时返回领域错误并回滚整个决定。成功结果
连同字段证据摘要写入 `post_review_validation`。评测样本修订另经 Pydantic 和数据库域
约束校验，且 `sealed_test` 不可通过普通审核改回其他 split。

## 不可变解析与字段证据

解析产物保存为 `data/parsed_artifacts/<raw_sha256>-<artifact_sha256>.json`，包含原件
哈希、解析器名称/版本、完整文本、页文本和全局 offset。任一后续信任闸门都会验证：

- 原件哈希与受管路径；
- 解析 JSON 哈希及其 `raw_sha256`；
- `SHA256(plain_text)`、数据库 `parsed_text_sha256` 和数据库 `raw_text`；
- document chunks 对相同文本范围的引用。

字段证据支持 `verbatim`、`normalized`、`summary` 和 `document_metadata`。normalized
只执行白名单内的空白、全半角、标点、日期、列表或字段标签转换，并要求可重放的
`transformation_note`；summary 不得自动通过，会进入 `requires_expert_review`。

正式草稿缺少证据返回 `missing_field_evidence`。常见拒绝码还包括
`evidence_offset_mismatch`、`evidence_page_mismatch`、
`field_not_supported_by_evidence` 和 `summary_evidence_requires_expert_review`。
监管记录的 `title` 与处罚记录的非空 `punished_entity` 也必须提供字段证据。标题可以
使用正文 `verbatim`，或绑定 `source_title`/正式 `filename` 的
`document_metadata`；处罚对象只接受正文 `verbatim` 或可重放的 `normalized`。
修改这两个字段时，corrections 必须同步提交同名新证据，旧证据不能支持新值时整体回滚。

法规效力采用严格最小方案：`validity_status` 只允许 `NULL` 或 `unknown`，省略时默认为
`unknown`。普通草稿、预审修订和 `approved_with_revision` 均不能设置 `effective`、
`expired`、`repealed`、`superseded` 或 `pending_effective`。在后续建立独立效力确认
流程前，记录查询统一返回 `regulation_validity_display = "效力状态待核验"`。

`auto_validation_failed` 的草稿通过专用入口修正，字段与证据共同留痕：

```bash
python -m app.cli.main revise-structured-draft \
  --file data/parsed/structured_draft_revision.jsonl \
  --reason "修正错误的原文引用" --actor "operator"
```

修订清除旧自动校验并把文档恢复为 `parsed`，必须再次运行 `validate-pending` 才能进入
`pending_review`。监管条款可按明确 ID 更新，也可在预审阶段新增/删除并写入审计表。
Penalty/ProductDocument 只更新其现有主记录。重新解析只能使用
`reparse-document --document-id ... --reason ...`，会保存旧解析版本并使旧草稿失效。

## 真实性人工确认

公开网络和本地资料均以 `pending_verification` 进入系统。审核人员只能在
`approved`/`approved_with_revision` 决定中独立填写：

```json
{
  "authenticity_decision": {
    "new_type": "verified_public",
    "verified_occurrence_id": 10,
    "reason": "已核对官方网站、文件原文及发布机构"
  }
}
```

系统从指定 occurrence 验证其属于当前文档、绑定启用且类型匹配的登记来源、来源
URL/最终 URL 均为 HTTP(S) 并匹配允许域名，同时重新核对原件 SHA-256，并确认每条
`source_quote` 和字段级证据可在不可变解析文本定位。成功后文档升级为
`verified_public`，并在
`authenticity_decision_logs` 记录来源、occurrence、旧值、新值、审核人、决定、原因和
时间。`pending_verification` 文档若没有合格真实性决定，不得批准；应先进入
`pending_source_verification`，补齐来源后通过 `resubmit-for-review` 重新送审。

结构化草稿仅可在 `parsed` 或 `auto_validation_failed` 状态导入。进入
`pending_review` 或任何人工终态后返回 `structured_record_locked`；一致性修复也只报告
人工终态的不一致，绝不自动把未知记录同步成批准状态。

演示数据即使被批准，也因 `demo_only` 被知识索引闸门拒绝。
