# 受控真实公开数据试采集

本目录只保存审批配置、Schema 和空白结构化模板。当前未登记任何真实来源，也未填入
任何真实 URL。原件、解析产物、采集结果、质量报告和审核 Bundle 都是运行产物，不得
提交到 Git。

目录目标数量为 3 份监管规则、10 条行政处罚、5 条监管典型案例或消费者风险提示、
2 份产品公开资料。数量是小规模流程目标，不是批量爬取授权。

## 启用顺序

1. 项目规划者提供正式机构、栏目、允许域名和明确文件 URL。
2. 操作者把来源写入对应 `source_registry/*.yaml`；未经确认保持 `enabled: false`。
3. 确认来源时填写 `confirmed_by`、robots 审核、使用条款审核和独立
   `approval`，再显式设置 `enabled: true`。
4. 将逐文件条目写入 `manifests/*.jsonl`，先保持 `draft`。
5. 项目规划者确认具体条目后填写 `confirmed_by`，再改为
   `approved_for_collection`。
6. 先执行校验，只有校验通过且已审批的条目才能进入采集。

来源登记结构示意（占位符不能直接启用）：

```yaml
sources:
  - source_key: planner_confirmed_key
    name: "<正式发布机构名称>"
    publisher: "<正式发布机构名称>"
    base_url: "https://<项目规划者确认的官方域名>"
    source_type: regulation
    allowed_domains:
      - "<项目规划者确认的官方域名>"
    enabled: false
    confirmed_by: null
    robots_review: null
    terms_review: null
    approval: null
    crawl_policy:
      rate_limit_seconds: 2
      max_documents: 10
      allow_subdomains: false
    notes: "等待项目规划者确认具体栏目、使用条款和 robots 政策"
```

Manifest 结构示意（占位符不能写入正式 JSONL）：

```json
{
  "pilot_id": "REG-001",
  "source_key": "planner_confirmed_key",
  "source_type": "regulation",
  "source_url": "https://<项目规划者确认的官方域名>/<明确文件路径>",
  "expected_title": "待人工核验",
  "collection_method": "url",
  "evaluation_usage": ["knowledge_base"],
  "confirmed_by": null,
  "approved_at": null,
  "approval_reference": null,
  "status": "draft"
}
```

## 命令

```bash
python -m app.cli.main pilot-validate-manifests --path pilot/manifests
python -m app.cli.main pilot-collect \
  --manifest pilot/manifests/regulations.jsonl
python -m app.cli.main pilot-status
python -m app.cli.main pilot-status --run-id 1
python -m app.cli.main pilot-quality-report \
  --output pilot/reports/pilot-quality-report.json
python -m app.cli.main pilot-quality-report \
  --run-id 1 --output pilot/reports/pilot-run-1-quality-report.json
```

`pilot-collect` 先校验全部 `source_registry/*.yaml` 和全部
`manifests/*.jsonl`，任一配置错误都会以 `pilot_configuration_invalid` 整批阻断，
不会请求网络或写数据库。配置通过后，每次执行创建不可变
`PilotCollectionRun`，每个已审批项目创建全局唯一 `PilotCollectionItem`，并绑定采集时
的 `PilotSourceRegistration` 版本、审批快照、Manifest/来源哈希、文档和 Occurrence。

来源内容变化会创建新版本和新的底层 `DataSource`，不会覆盖旧来源。累计
`max_documents` 和 `last_request_at` 均由数据库来源版本行加锁执行。状态和质量报告只
读取账本，不读取可变 Manifest 或历史 JSONL；清空 Manifest 后历史仍然可核验。

监管典型案例或风险提示必须使用 `source_type=regulatory_case` 和
`case_usage=external_test_candidate`。当前数据模型不能准确表达该类型，所以采集命令
会返回 `regulatory_case_model_not_implemented`，绝不会将其写入行政处罚表。

结构化草稿空白模板位于 `templates/`。其中 `source_quote` 和所有 `field_evidence`
必须来自不可变解析文本；模板中的 `null` 只表示“不得猜测”，不是可直接导入的数据。
