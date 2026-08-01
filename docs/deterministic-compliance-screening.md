# 确定性营销合规风险筛查

## 系统定位

本模块对用户提交的纯文本营销材料生成风险信号、待核验问题和可信证据辅助。
它不是法律判断，不自动认定违法，不替代合规人员审核，也不给出投保、退保或投资建议。
输入中的 HTML、脚本和 URL 均只作为普通文本处理；系统不抓取外部地址、不执行代码。

## 版本化处理链

`marketing_text_normalization_v1` 对每个字符执行 Unicode NFKC，将 Unicode 空白序列折叠
为一个空格，并只为匹配而小写化 ASCII 拉丁字母。原始文本不变；每个规范化字符都保存
到原始 Python 字符串索引区间的映射，因此 finding 的 `matched_text` 永远等于
`raw_text[raw_start_offset:raw_end_offset]`。

`marketing_segmenter_v1` 优先使用空行和中文句号、问号、感叹号、分号分段，将短标题与
后续正文合并，并以固定上限切分超长段落。`segment_sha256` 只使用输入哈希、ordinal、原始
区间、文本和分段器版本，不使用数据库主键或随机值。

## 规则注册表

规则固定在 `app/rules/insurance_marketing_rules_v1.json`，不能通过 API 上传或修改。
启动时由 Pydantic `extra=forbid` 严格验证，规则必须按 `rule_id` 排序且不能重复。
`ruleset_sha256` 是完整规则模型经键排序、无多余空白的 UTF-8 JSON 的 SHA-256。

首版包含 12 类信号：保证收益或本金、零风险或无损失、误导性利率、监管背书、促销奖品、
合同外利益、产品性质混淆、绝对化宣传、不当比较排名、退保/现金价值误述、等待期/犹豫期
误述、责任免除弱化。命中只代表风险信号。

规则按 `rule_id` 固定顺序执行；同一规则的重叠命中确定性合并，局部 exception pattern
只在命中附近生效。`finding_sha256` 由材料、规则集、segment 身份、规则和原始区间构成，
不包含数据库 ID、时间戳或检索结果。

## 可信证据组合

每次 screening run 在任何持久化操作前调用现有 `KnowledgeIndexService.verify_chunks()`。
canonical mismatch、伪造/孤儿 chunk、资格漂移、Penalty 身份错误、locator 缺失等任一问题
都会以 `screening_trusted_index_invalid` 失败关闭。通过验证的 active chunk identities 按字典序
散列为 `trusted_index_payload_hash`，一个 run 只验证一次。

`FindingEvidenceAssembler` 只调用现有 `TrustedKnowledgeSearchService`：

- Regulation 作为 `normative_basis` 优先；
- Penalty 可作为 `enforcement_example`；
- 退保、现金价值、等待期、犹豫期、责任免除等风险可引用 ProductDocument 的
  `product_term_context`；
- RegulatoryCase 永不作为正式证据；
- 每个 finding 最多 5 条，同一 SourceDocument 最多 2 条，同一 chunk 不重复；
- locator 或 evidence references 为空的结果不会进入证据包；
- 快照保存 chunk 两类哈希、来源 URL、信任状态、locator 和 evidence references，不复制原件全文。

证据不足不会删除 finding，也不会被解释为“没有风险”，只会标为
`partially_supported` 或 `evidence_insufficient`。

## 报告

机构端 `institution_compliance_report_v1` 提供计数、精确原文、上下文、固定解释、人工复核
问题、固定整改模板和证据快照。消费者端 `consumer_protection_notice_v1` 仅使用固定通俗提示，
保留来源链接，不推荐购买或退保。两类报告均为纯读取，不创建记录或改变 finding。

API：

- `POST /api/v1/screenings`
- `GET /api/v1/screenings/{run_id}`
- `GET /api/v1/screenings/{run_id}/institution-report`
- `GET /api/v1/screenings/{run_id}/consumer-notice`

CLI：

```bash
python -m app.cli.main screening run \
  --title "测试营销话术" \
  --material-type sales_script \
  --text-file material.txt
python -m app.cli.main screening show 1
python -m app.cli.main screening institution-report 1
python -m app.cli.main screening consumer-notice 1
python -m app.cli.main screening rules
```

API 和 CLI 共用 `DeterministicScreeningService`。

## 事务、幂等与样本隔离

`input_sha256` 给材料明确身份，相同输入复用材料和 segment，但允许绑定不同规则集/可信索引
快照再次筛查。单次 run 在一个数据库事务内写入 finding 和全部证据；任一门禁或证据链接失败
会整体 rollback。报告读取不写数据库。

`tests/fixtures/constructed_screening_eval_v1` 的 30 条样本全部标记 `constructed=true`，只进入
测试进程和 `MarketingMaterial`（正式离线验收时）；它们永不进入 SourceDocument、
KnowledgeChunk 或可信检索结果。

## 已知局限与后续方向

首版只处理文本；不做 OCR、图片、音视频或网页理解。声明式正则无法理解所有隐含语义，可能
存在误报或漏报；证据检索是确定性中文词法检索，不代表证据与具体事实已经形成法律上的充分
对应关系。

本阶段不接入 LLM，是为了先固定输入身份、offset、规则版本、证据准入和可重复结果。后续可在
该底座上加入受控 RAG：检索范围仍受可信索引门禁，模型输出必须引用既有 chunk，不能修改
deterministic finding，并由结构化 schema、引用校验和人工审核约束。LLM 只能提供辅助解释，
不得升级为自动法律结论。
