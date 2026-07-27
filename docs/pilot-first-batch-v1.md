# 首批真实公开数据试采集 V1 配置记录

本批次在 `v0.3.0-regulatory-cases` 可信基线上登记 4 个独立来源版本和 20 个逐项审批
URL，只用于受控的采集、解析与后续人工证据核验。

| source_key | 类型 | 发布方 | 上限 | Manifest 数量 | 审批引用 |
|---|---|---|---:|---:|---|
| `nfra_regulations_v1` | regulation | 国家金融监督管理总局 | 3 | 3 | `pilot-first-batch-v1-regulations` |
| `nfra_penalties_v1` | penalty | 国家金融监督管理总局及派出机构 | 10 | 10 | `pilot-first-batch-v1-penalties` |
| `nfra_regulatory_cases_v1` | regulatory_case | 国家金融监督管理总局及派出机构 | 5 | 5 | `pilot-first-batch-v1-regulatory-cases` |
| `cpic_product_documents_v1` | product_document | 中国太平洋人寿保险股份有限公司 | 2 | 2 | `pilot-first-batch-v1-products` |

所有来源均采用 3 秒请求间隔、关闭子域自动放行并限制为清单中的明确 URL。监管案例的
初始用途固定为 `external_test_candidate`，不得索引或改为 `retrieval_only`。法规效力
保持 `unknown`；处罚资料未披露销售原话时必须保持原话字段为空。

执行前必须同时通过来源 Schema、审批元数据、20 个全局唯一 `pilot_id`、类型匹配、
域名白名单、URL 凭据和网络安全预检。robots 明确禁止时停止对应来源；DNS、连接对端、
重定向或私网安全检查失败时不得降低限制或改用非审计下载方式。

下载原件、解析产物、数据库、预检/状态/质量报告和人工检查 ZIP 都是被 Git 忽略的运行
制品，不提交到仓库。该配置不授权栏目遍历、自动结构化、人工审核、真实性升级或知识
索引。
