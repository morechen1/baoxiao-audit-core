# 受控 RAG 解释编排与引用验证层

## 定位

本层只解释已完成的确定性筛查结果。规则引擎决定 finding、命中原文、offset、严重程度和
证据链接；模型无权重新筛查、搜索数据库、增加风险类别、改变严重程度或作出法律定性。
模型解释不可用时，调用方应退回现有机构报告或消费者固定提示，不得降级为无引用自由文本。

## 受控上下文

`ControlledRAGContextBuilder` 输出 `controlled_rag_context_v1`。finding 与 citation 按
PR #11 的稳定业务排序编号为 `F001`、`E001`，不使用数据库主键。上下文只包含材料基本
信息、持久化 finding、已选 `FindingEvidenceLink` 快照和最多 600 字的连续实质字段证据；
每个 finding 最多四条证据，总上下文最多 30000 字。超限时先围绕语义命中位置确定性缩短
quote，再将每个 finding 的证据从四条降为两条、最后降为一条。`supported` 和
`partially_supported` 永远保留至少一条证据；最小上下文仍超限时以
`explanation_context_too_large` 失败关闭。`evidence_insufficient` 可以携带零条证据，
但只能生成固定的不确定性说明和零 Citation。

构建前重新验证可信索引 payload hash、活跃 KnowledgeChunk 的 identity/content hash、
来源标题、pilot ID、record/chunk 类型、审核/真实性状态、URL、locator、字段证据引用和
语义准入结果。`RegulatoryCase`、未入选块、原件全文、本地绝对路径、连接信息和秘密配置
不会进入上下文。

## 提示词和 Provider

`app/prompts/controlled_rag_prompts_v1.yaml` 分别定义机构端和消费者端提示词。Pydantic
使用 `extra=forbid` 验证完整定义；SHA-256 基于规范化完整 JSON，而不是文件格式或字段
顺序。每个 `ExplanationRun` 保存当时的提示词、上下文和安全 Provider 配置快照及哈希，
因此后续提示词变化不影响历史 Artifact。

`ExplanationProvider` 是唯一生成接口。本阶段仅提供：

- `DeterministicFixtureProvider`：离线测试、CI 和验收；
- `DisabledExternalProvider`：稳定返回 `explanation_provider_not_configured`。
- `OpenAICompatibleProvider`：录制视频的可选真实解释模式。仅在 `LLM_ENABLED=true`、
  `LLM_PROVIDER=openai_compatible`、HTTPS endpoint、API key 和 model 均已配置时发起
  chat-completions JSON-object 请求；模型原始 JSON 直接交给下游 validator，不修复 Citation、
  不增加 finding，也不自动回退到 Fixture。

安全 Provider 配置快照只保存 enabled、protocol、model 和 timeout；API key 与 endpoint 不进入
`ExplanationRun`、Artifact、日志或快照，也不保存完整供应商原始响应。网络、timeout、HTTP、
rate-limit 和 response-envelope 错误均记为 `failed`；模型 JSON 未通过 Schema 或 Citation
门禁则记为 `rejected`。没有 Embedding 或向量数据库。

## 输出与引用门禁

机构端输出为 `institution_explanation_v1`，消费者端为
`consumer_explanation_v1`；所有模型可见引用必须是 `E` 加三位 ASCII 数字。验证器依次
检查严格 Schema、finding 归属、citation 归属、KnowledgeChunk 完整来源快照、连续
quote、不确定性、示例产品条款声明和固定 disclaimer。模型事实内容使用
`GroundedClaimV1`，每一项 claim 明确列出 finding keys 和 citation keys；只有持久化的
固定模板可不带引用。引用允许完整 quote 或连续短摘录，不允许改写、跨段拼接、伪造
URL/locator、数据库 ID 或未入选块。

`AllowedEvidenceQuoteBuilderV1` 只开放实质字段：法规依据限 `article_text`；处罚案例限
`illegal_facts`、`original_sales_wording` 和必要的 `legal_basis`；产品上下文限等待期、
犹豫期、责任免除、现金价值、退保风险等受控业务字段。标题、机关、主体、文号、日期、
URL、pilot ID、单独处罚金额和 basic-information 标签只能展示为来源元数据，不能满足
引用门禁。去除 Unicode 空白后少于六字符的引用默认拒绝，只有明确允许的短销售原话例外。

验证完成后服务端根据不可变 binding 生成 `ResolvedCitationV1`，注入真实来源 URL、标题、
locator、pilot ID、上下文范围和哈希。模型不能提供或改写这些来源字段；Artifact 和
`/citations` 只返回持久化的来源快照，不随当前数据库内容变化。

提示词快照中的 `allowed_claim_types`、`forbidden_claim_patterns` 和 `citation_format`
均由验证器执行；新 run 使用新快照，历史 Artifact 不重新验证。`UnsupportedClaimDetectorV1`
作为额外最低安全门，确定性拒绝违法/欺诈定性、必然处罚、保证
赔付或收益、购买/退保建议、绝对退款和“无任何风险”等陈述。它不尝试替代人工语义审核。
产品条款 evidence 的 `context_scope=illustrative_not_material_specific` 时，输出必须明确
“示例产品条款不代表输入材料对应产品”，并提示核对正式合同。

## 状态、失败与历史复现

有效输出创建 `completed` run、一个 Artifact 和经验证 Citation；Artifact SHA 绑定提示词
SHA、上下文 SHA、安全 Provider 配置、结构化输出及稳定引用材料。验证失败创建
`rejected` run 和固定公开错误码，不创建可对外读取 Artifact。Provider 未配置、超时或
异常创建 `failed` run。系统不自动重试；显式重试创建新 run，并通过 `retry_of_id` 保留
关系。Artifact GET 只读历史快照，绝不再次调用 Provider。

构造响应位于 `tests/fixtures/controlled_rag_eval_v1`，全部标记 `constructed=true`，离线验收
会逐条实际调用 Fixture Provider 和验证器，而不是只统计期望值。它们不得进入
`SourceDocument`、`KnowledgeChunk` 或正式证据。最终提交支持可选 OpenAI-compatible
真实 Provider（当前配置为 DeepSeek）和前端双端 Artifact/Citation 展示；密钥只存在于
本机未跟踪配置。该能力仍是比赛提交级受控集成，不宣称生产密钥托管、多供应商路由或
生产可用性保障。
