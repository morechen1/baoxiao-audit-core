# 保销智审 V2 系统架构

## 审核主链

```text
Input Layer: pasted text / TXT / MD / DOCX / text PDF / CSV batch
                                                ↓
                          Normalization / natural-boundary segmentation
                                                ↓
  ├─ Deterministic rules → rule context gates ─┐
  └─ Bounded semantic chunks → Parser 2.0 ─────┤
                         → context / role / taxonomy gates
                                                ↓
                                      Candidate fusion
                                                ↓
                                    System-owned RiskFinding
                                                ↓
                               Trusted regulatory knowledge
                                                ↓
                                           EvidenceLink
                                                ↓
                                         Controlled RAG
                              ┌─────────────────┴─────────────────┐
                     Institution Artifact                 Consumer Artifact
                              └─────────────────┬─────────────────┘
                              Citation / Claim / Uncertainty validation
                                                ↓
                                HTML / JSON report and batch output
```

规则候选和语义候选各自经过与来源相符的上下文及安全门禁，不宣称所有候选通过完全相同的
validator。Candidate fusion 只合并通过门禁的候选；最终 Finding、severity、原文 offset、
EvidenceLink 和证据状态由系统拥有。

## 分层

- `app/api` / `app/cli`：输入输出适配。
- `app/services/document_ingestion.py`：内存文件解析、类型/大小门禁和 CSV 展开。
- `app/services/screening`：规则、Semantic Parser、门禁、融合、Finding、EvidenceLink 和报告。
- `app/services/knowledge`：可信准入、物化、校验与 PostgreSQL 词法检索。
- `app/services/explanation`：受控上下文、Provider、双端 Schema 与验证器。
- `app/models` / `app/repositories`：持久化和状态。
- `app/services/audit_export.py`：不含 Prompt、密钥或隐藏 reasoning 的审计报告。
- `app/web`：工作台、单材料/批量审核、结果、双端 Citation、报告导出和冻结验证展示。
- `migrations`：PostgreSQL schema migration。
- `release_assets` / `knowledge_archives`：可信知识恢复与审计资产。

## 关键所有权边界

1. Semantic Parser 2.0 只输出结构化候选语义和逐字 `source_quote`，不接收监管知识、
   Evidence 或 Citation；document offset 由系统在对应原文 chunk 中唯一匹配后回映。
2. 只有通过门禁的候选才能进入 candidate fusion；模型不能直接持久化 Finding。
3. TrustedKnowledgeSearch 只使用已审核、已验证、active 的可信知识；RegulatoryCase 不准入。
4. EvidenceLink 与 Citation 是不同对象：前者连接 Finding 与可信块，后者属于特定 audience
   的已验证 Explanation Artifact。
5. 机构端与消费者端 Artifact/Citation 分别绑定各自 ExplanationRun，不交叉回退。
6. Provider 网络、认证、timeout、HTTP、envelope、Schema 或验证失败均 fail closed。
7. 长文分块、调用次数和文本长度均有上限；超限显示 `partial semantic coverage`，不静默宣称
   全文语义覆盖。缓存键绑定规范化文本、模型、Parser 版本和 Schema 版本。

## 数据与运行

- PostgreSQL 16 是正式运行数据库；SQLite 仅用于部分测试。
- 原件、解析产物、Prompt 快照、Finding、EvidenceLink 和 Artifact 使用哈希或不可变快照。
- 启动器仅在空数据库恢复 15 个来源和 73 个 chunk；部分状态拒绝覆盖。
- `SEMANTIC_SCREENING_ENABLED=false` 固定关闭旧实验路径。
- 未形成 RiskFinding 时不触发下游 Explanation Provider；Semantic Parser 仍属于风险发现阶段。
- V2 上传内容仅在内存中解析；扫描 PDF 明确拒绝，当前不包含 OCR。
- 批量审核使用轻量顺序处理（并发上限实质为 1），单项失败不回滚其他已完成项。
