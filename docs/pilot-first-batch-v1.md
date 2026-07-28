# 首批真实公开数据试采集 V1 配置记录

本批次在 `v0.3.0-regulatory-cases` 可信基线上登记 4 个独立来源版本和 20 个逐项审批
URL，只用于受控的采集、解析与后续人工证据核验。

| source_key | 类型 | 发布方 | 上限 | Manifest 数量 | 审批引用 |
|---|---|---|---:|---:|---|
| `nfra_regulations_v1` | regulation | 国家金融监督管理总局 | 3 | 3 | `pilot-first-batch-v1-regulations` |
| `nfra_penalties_v1` | penalty | 国家金融监督管理总局及派出机构 | 10 | 10 | `pilot-first-batch-v1-penalties` |
| `nfra_regulatory_cases_v1` | regulatory_case | 国家金融监督管理总局及派出机构 | 5 | 5 | `pilot-first-batch-v1-regulatory-cases` |
| `cpic_product_documents_v1` | product_document | 中国太平洋人寿保险股份有限公司 | 2 | 2 | `pilot-first-batch-v1-products` |

## NFRA 官方公开文档通道

NFRA 的 18 个已批准 Manifest 仍以人工核准的正式落地页作为 `source_url`。采集器只从
落地页查询参数读取十进制 `docId`，并确定性构造同域官方公开 JSON 地址
`/cn/static/data/DocInfo/SelectByDocId/data_docId={docId}.json`。适配器不会执行页面
JavaScript，不设置 Cookie，也不会携带登录状态。

落地页和取回地址都必须通过既有域名白名单与 `SafeUrlPolicy` 的公网 DNS 校验；实际
连接对端必须属于已校验地址。原始 JSON 响应按原字节保存，`source_url` 保留落地页，
`final_url` 保存取回地址，`metadata_json` 同时记录两者和 `docId`。只有响应为 JSON、
响应 `docId` 一致、正文非空且预期标题可由正式标题或正文支持时才计为采集成功。
错误模板、访问拒绝、标题无关或空正文使用固定错误码失败，不进入解析。
质量门还会识别 Angular 模板壳、验证码、行政许可、任职资格和非保险处罚页；通过后
记录 `consumer_risk_alert`、`penalty_publication` 或 `valid_regulation` 页面分类。
其中 `valid_regulation` 仅表示页面符合本批资料类型和内容质量要求，绝不表示法规
当前效力已被确认，法规效力状态仍保持 `unknown`。

解析阶段只对已保存 JSON 的 `docTitle`、`documentNo` 与 `docClob` 做确定性文本转换，
并进入现有不可变解析产物和 SHA-256 链。该通道不改变真实性：所有新文档仍为
`pending_verification`，也不会自动执行结构化导入、人工审核或索引。

所有来源均采用 3 秒请求间隔、关闭子域自动放行并限制为清单中的明确 URL。监管案例的
初始用途固定为 `external_test_candidate`，不得索引或改为 `retrieval_only`。法规效力
保持 `unknown`；处罚资料未披露销售原话时必须保持原话字段为空。

执行前必须同时通过来源 Schema、审批元数据、20 个全局唯一 `pilot_id`、类型匹配、
域名白名单、URL 凭据和网络安全预检。robots 明确禁止时停止对应来源；DNS、连接对端、
重定向或私网安全检查失败时不得降低限制或改用非审计下载方式。

下载原件、解析产物、数据库、预检/状态/质量报告和人工检查 ZIP 都是被 Git 忽略的运行
制品，不提交到仓库。该配置不授权栏目遍历、自动结构化、人工审核、真实性升级或知识
索引。
