# 保销智审项目大纲

## 版本状态

- V1 正式提交基线：`submission-v1` / `16ebd6b2dece09a66f9d130ee9e87c669ca2305e`。
- V2 Semantic Detection 2.0：研究分支，unseen Recall 与 F1 未通过替换门槛，不进入正式 detector。
- 当前分支：`upgrade/v1-core-v2-platform`，状态为 **INTEGRATION CANDIDATE**。

## 项目定位

保销智审是保险营销材料智能合规审查与可信解释平台。核心原则是：AI 负责生成结构化
风险候选，系统负责定案，可信监管知识负责举证。

## 当前候选架构

```text
文本 / TXT / MD / DOCX / 文本型 PDF
                 ↓
        MaterialIngestionService
                 ↓
   短文本直通 V1 / 长文本外围确定性分块
                 ↓
        Frozen V1 双通道检测核心
        规则 + Semantic Parser v1
                 ↓
        确定性校验与候选融合
                 ↓
             RiskFinding
                 ↓
       73 KnowledgeChunks → EvidenceLink
                 ↓
           Controlled RAG
          ↙                 ↘
  Institution Artifact   Consumer Artifact
          ↓                 ↓
      audience-specific Citation / Claim / Uncertainty
                 ↓
      单材料详情 / 批量审核 / HTML / JSON
```

## 正式检测基线

V1 taxonomy、rules、Semantic Parser Prompt/schema/validator、0.72 confidence、candidate fusion、
severity/Finding/quote ownership、可信检索、Controlled RAG、Citation/Claim/Uncertainty validator、
知识资产和数据库 migration 在本候选中保持字节不变。保护范围见
[`V1_CORE_PROTECTION_MANIFEST.md`](V1_CORE_PROTECTION_MANIFEST.md)。

156 条项目内部冻结验证结果：Micro Precision 79.50%、Micro Recall 88.89%、Micro F1
83.93%、Quote integrity 100%、Hallucinated quote accepted 0。该指标只对应 V1 核心，
不代表文件接入、批量审核或长文档编排已用同一数据集重新评测。

## 风险等级语义

### 案例类型

预置的“显式多风险案例”、“语境边界案例”和“合规对照案例”用于演示不同表达类型与识别难度，
不参与审核结果计算。“语境边界”不等于“中风险”。

### Finding Severity

Finding severity 由系统按冻结 taxonomy/rule mapping 固定赋值。Semantic Parser 仅生成候选，不能决定
或修改 severity。

### 综合风险等级

综合等级取当前材料所有有效 Finding 的最高 severity：存在 High 则为高风险；否则存在 Medium 则为
中风险；无有效 Finding 则为低风险。`0 Finding` / 低风险不等于法律意义上的完全合规确认。

## 平台扩展

- 粘贴文本、UTF-8/BOM TXT、MD、DOCX 段落与表格、文本型 PDF。
- 扫描 PDF 明确拒绝；无 OCR、宏执行、嵌入文件执行或外部资源执行。
- 长文档外围分块、全局 offset 映射和同 rule 高重叠 span 的保守去重。
- 单批最多 20 份、最多 3 并发、queued/processing/success/failed 状态和单项失败隔离。
- HTML / JSON 报告包含真实 Finding、EvidenceLink、双 audience Artifact/Citation 和验证状态。
- exact Semantic Parser request cache，不同 text/model/prompt/schema 不共享输出。
- Provider 失败保留可用确定性结果并诚实提示，不变成 500 或 Fixture 冒充成功。

## 可信知识与安全边界

正式运行恢复 15 个可信 SourceDocument 和 73 个 active KnowledgeChunk。EvidenceLink 表示
Finding 与监管证据的绑定；Citation 只表示某个 audience Artifact 实际引用的已验证证据，
二者不得混用。API key、完整 Prompt、hidden reasoning、数据库密码和本机绝对路径不得进入
报告、前端或候选包。

## 明确非目标

V2 Statement Mode、V2 Context Guard、taxonomy arbitration、V2 Prompt/schema/threshold、OCR、
Embedding、向量数据库、生产任务队列、PDF 报告栈、生产密钥平台和新的算法 benchmark。
