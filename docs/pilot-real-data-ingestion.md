# 首批真实公开数据试采集

## 目的与边界

Pilot 用 3 份核心监管规则、10 条行政处罚、5 条监管典型案例或消费者风险提示、2 份
产品公开资料验证“来源审批 → 定向采集 → 不可变原件 → 解析 → 字段证据 → 自动校验 →
人工审核 → 可信索引”的闭环。它不是通用爬虫，也不授权遍历任何网站。

本阶段不包含 OCR 服务、前端、RAG、Embedding、LLM 或风险识别。系统不自行搜索来源，
不使用搜索摘要、转载站或新闻报道替代官方原文，也不把采集成功解释为内容真实。

## 来源确认

来源登记位于 `pilot/source_registry/*.yaml`。每项包含稳定的 `source_key`、正式机构名称、
发布者、基础 URL、允许域名、资料类型和有限的抓取策略。

来源必须依次完成：

1. 项目规划者确认发布机构、具体官方栏目、使用条款和 robots 政策；
2. 明确基础 URL、允许域名、是否允许子域和最多文件数；
3. 在 `confirmed_by` 记录确认角色或人员标识；
4. 最后才将 `enabled` 改为 `true`。

未经确认的来源必须保持禁用。`allow_subdomains=false` 时，子域即使与基础域同根也会被
拒绝；显式允许的其他域名仍须写入 `allowed_domains`。网络采集继续执行 DNS、私网、
云元数据、连接对端和逐跳重定向检查。

## Manifest 审批

Manifest 位于 `pilot/manifests/*.jsonl`，一行只描述一个明确文件 URL，不允许空 URL、
`file://`、带凭据 URL、私网地址或无限制栏目遍历。状态集合为：

```text
draft
approved_for_collection
collected
parsed
structured
pending_review
reviewed
rejected
```

新条目先使用 `draft`。项目规划者核对明确 URL、来源类型和用途后填写
`confirmed_by`，再改为 `approved_for_collection`。只有该状态会发起网络请求；其他
状态由采集命令跳过。`pilot_id` 在所有 Manifest 中必须唯一。

先运行：

```bash
python -m app.cli.main pilot-validate-manifests \
  --path pilot/manifests
```

校验会检查 JSONL/Pydantic 格式、来源登记和启用状态、类型一致性、域名白名单、
`pilot_id` 唯一性、审批信息和静态网络安全边界。

## 采集与解析

每类资料单独运行：

```bash
python -m app.cli.main pilot-collect \
  --manifest pilot/manifests/regulations.jsonl
python -m app.cli.main pilot-collect \
  --manifest pilot/manifests/penalties.jsonl
python -m app.cli.main pilot-collect \
  --manifest pilot/manifests/product_documents.jsonl
```

采集调用现有安全采集器并保留 `DocumentOccurrence`。每项输出独立 JSON 结果；一个下载
失败不会终止其余条目。新文档和重复文档都不能由采集参数声明
`verified_public`，初始真实性固定为 `pending_verification`。采集不会自动解析、生成
结构化字段、审核或索引。

后续命令必须由操作者显式运行：

```bash
python -m app.cli.main parse-pending
python -m app.cli.main import-structured-drafts \
  --file <人工准备并核对证据的JSONL>
python -m app.cli.main validate-pending
python -m app.cli.main pilot-status
```

扫描 PDF 只进入 `requires_ocr`，等待独立 OCR/人工流程。本阶段不得用猜测文本替代。

## 四类结构化资料

`pilot/templates/` 提供空白模板。监管规则的 `validity_status` 固定从 `unknown` 开始，
不得自动显示“现行有效”。处罚文件未披露营销原话时必须保持：

```json
{
  "original_sales_wording_disclosed": false,
  "original_sales_wording": null
}
```

产品资料未披露的业务字段保持 `null`。系统不得根据常识、相似产品或其他网页补齐。

监管典型案例或消费者风险提示不是行政处罚。此类 Manifest 必须声明：

```json
{
  "source_type": "regulatory_case",
  "case_usage": "external_test_candidate"
}
```

当前模型缺少专用实体，采集命令会明确返回
`regulatory_case_model_not_implemented`，不会写入 `penalties`。下一步应由项目规划者
决定是否新增独立 `RegulatoryCase` 模型、字段和迁移，再开放采集与审核。

## 字段级证据

每个非空业务字段必须由 `field_evidence` 约束到不可变解析文本或允许的文档元数据。
标题、处罚对象、日期、文号、机构、责任、除外、利益和风险描述都不能只依赖
`source_quote`。offset、页码、quote、模式和转换说明继续使用已有证据校验服务。

无法在原文定位的字段保持空值。法规效力保持 `unknown`；处罚原话未披露时保持空值；
采集器、模板或审核人员都不能制造原文没有的事实。

## Portable Review Bundle 与 ChatGPT 协作

真实数据完成解析、结构化和确定性校验后，按资料类型分别使用已有命令：

```bash
python -m app.cli.main export-review-bundle --data-type regulation
python -m app.cli.main export-review-bundle --data-type penalty
python -m app.cli.main export-review-bundle --data-type product_document
```

运行产物可在仓库外按以下名称归档：

```text
pilot-regulations-review-bundle.zip
pilot-penalties-review-bundle.zip
pilot-product-documents-review-bundle.zip
```

专用监管案例模型实现前不得生成伪装成 penalty 的案例 Bundle。每个现有 Bundle 包含
`manifest.json`、`review.jsonl`、`review-results-template.jsonl`、`sources/` 和
`parsed/`，并由哈希绑定原件、解析产物、审核 payload 和字段证据摘要。

向 ChatGPT 提供 Bundle 后，只允许其协助逐项检查来源、字段、quote、offset、缺失值和
建议决定。ChatGPT 不能直接把真实性设置为 `verified_public`；最终结果必须由人类审核
者填写 reviewer、证据质量、真实性决定及理由，再通过既有导入命令验证完整性和状态。
Bundle 和报告不得包含 API Key、Cookie、认证头、登录信息、本地用户名或本地绝对路径。

## 质量报告

```bash
python -m app.cli.main pilot-quality-report \
  --output pilot/reports/pilot-quality-report.json
```

命令同时生成 Markdown，汇总来源/文件数、下载与解析成功率、重复率、扫描 PDF 比例、
字段完整率、字段证据覆盖率、自动校验通过率、待审数量和按来源错误类型。报告目录已被
Git 忽略，只保留 `.gitkeep`。

## 失败处理

- Manifest 或来源错误：保持未采集，修正配置并重新审批；
- 网络、重定向或内容类型错误：保留安全错误码，不降低域名或私网闸门；
- 重复原件：复用内容哈希并保留新的合格 Occurrence；
- 解析失败：不结构化；扫描 PDF 进入 `requires_ocr`；
- 字段证据不足：字段留空或退回，不用摘要或人工猜测填补；
- 来源真实性未核验：保持 `pending_verification`；
- 审核拒绝：保留状态和审计记录，不进入可信索引。

单项失败不应阻塞其他明确审批条目，但也不会被静默忽略。

## 为什么不能直接进入 RAG

采集成功只证明某些字节被下载，不证明网址由正确机构发布、文档未过期、结构化字段正确
或引用完整。只有原件和解析产物完整性通过、字段级证据可定位、确定性校验通过、官方
Occurrence 经人类核验、审核决定被哈希绑定的数据，才可能进入可信索引。RAG、
Embedding 或 LLM 接入不能替代这些前置条件。
