# 受控 RAG 解释编排与引用验证层

## 定位

本层只解释已完成的确定性筛查结果。规则引擎决定 finding、命中原文、offset、严重程度和
证据链接；模型无权重新筛查、搜索数据库、增加风险类别、改变严重程度或作出法律定性。
模型解释不可用时，调用方应退回现有机构报告或消费者固定提示，不得降级为无引用自由文本。

## 受控上下文

`ControlledRAGContextBuilder` 输出 `controlled_rag_context_v1`。finding 与 citation 按
PR #11 的稳定业务排序编号为 `F001`、`E001`，不使用数据库主键。上下文只包含材料基本
信息、持久化 finding、已选 `FindingEvidenceLink` 快照和最多 600 字的连续证据摘录；
每个 finding 最多四条证据，总上下文最多 30000 字。超限时按稳定逆序删除完整引用并标记
`truncated=true`，不会拆散引用编号与 quote。

构建前重新验证可信索引 payload hash、活跃 KnowledgeChunk 的 identity/content hash、
来源 URL 与 locator。`RegulatoryCase`、未入选块、原件全文、本地绝对路径、连接信息和
秘密配置不会进入上下文。

## 提示词和 Provider

`app/prompts/controlled_rag_prompts_v1.yaml` 分别定义机构端和消费者端提示词。Pydantic
使用 `extra=forbid` 验证完整定义；SHA-256 基于规范化完整 JSON，而不是文件格式或字段
顺序。每个 `ExplanationRun` 保存当时的提示词、上下文和安全 Provider 配置快照及哈希，
因此后续提示词变化不影响历史 Artifact。

`ExplanationProvider` 是唯一生成接口。本阶段仅提供：

- `DeterministicFixtureProvider`：离线测试、CI 和验收；
- `DisabledExternalProvider`：稳定返回 `explanation_provider_not_configured`。

没有绑定商业模型、外部网络、Embedding 或向量数据库，也不保存完整供应商原始响应。

## 输出与引用门禁

机构端输出为 `institution_explanation_v1`，消费者端为
`consumer_explanation_v1`；所有模型可见引用必须是 `E` 加三位 ASCII 数字。验证器依次
检查严格 Schema、finding 归属、citation 归属、KnowledgeChunk 快照、连续 quote、
不确定性、示例产品条款声明和固定 disclaimer。引用允许完整 quote 或连续短摘录，不允许
改写、跨段拼接、伪造 URL/locator、数据库 ID 或未入选块。

`UnsupportedClaimDetectorV1` 是最低安全门，确定性拒绝违法/欺诈定性、必然处罚、保证
赔付或收益、购买/退保建议、绝对退款和“无任何风险”等陈述。它不尝试替代人工语义审核。
产品条款 evidence 的 `context_scope=illustrative_not_material_specific` 时，输出必须明确
“示例产品条款不代表输入材料对应产品”，并提示核对正式合同。

## 状态、失败与历史复现

有效输出创建 `completed` run、一个 Artifact 和经验证 Citation；Artifact SHA 绑定提示词
SHA、上下文 SHA、安全 Provider 配置、结构化输出及稳定引用材料。验证失败创建
`rejected` run 和固定公开错误码，不创建可对外读取 Artifact。Provider 未配置、超时或
异常创建 `failed` run。系统不自动重试；显式重试创建新 run，并通过 `retry_of_id` 保留
关系。Artifact GET 只读历史快照，绝不再次调用 Provider。

构造响应位于 `tests/fixtures/controlled_rag_eval_v1`，全部标记 `constructed=true`，不得
进入 `SourceDocument`、`KnowledgeChunk` 或正式证据。当前尚未绑定生产模型；下一阶段
需单独完成供应商安全审计、密钥托管、超时/限流、生产评测和前端集成。
