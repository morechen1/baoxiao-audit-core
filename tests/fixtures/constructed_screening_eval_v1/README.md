# Constructed screening evaluation V1

本目录样本全部为人工构造，只用于确定性营销风险筛查的软件测试。

当前 44 条样本包含 12 类基础规则和局部上下文、转折、否定、超长重叠分段、
组合字符 NFKC、证据状态、禁止来源及产品条款上下文范围等对抗验收字段。

它们不是监管事实、处罚案例、法规证据或真实营销材料，不得写入
`SourceDocument`、`KnowledgeChunk`，不得标记为 `verified_public`，也不得由
`TrustedKnowledgeSearchService` 返回。
