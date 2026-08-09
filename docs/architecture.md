# 最终系统架构

## 审核主链

```text
Raw material
  ├─ Deterministic rules → rule context gates ─┐
  └─ Semantic Parser → semantic safety gates ──┤
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
```

规则候选和语义候选各自经过与来源相符的上下文及安全门禁，不宣称所有候选通过完全相同的
validator。Candidate fusion 只合并通过门禁的候选；最终 Finding、severity、原文 offset、
EvidenceLink 和证据状态由系统拥有。

## 分层

- `app/api` / `app/cli`：输入输出适配。
- `app/services/screening`：规则、Semantic Parser、门禁、融合、Finding、EvidenceLink 和报告。
- `app/services/knowledge`：可信准入、物化、校验与 PostgreSQL 词法检索。
- `app/services/explanation`：受控上下文、Provider、双端 Schema 与验证器。
- `app/models` / `app/repositories`：持久化和状态。
- `app/web`：工作台、审核流程、结果、双端 Citation 和冻结验证展示。
- `migrations`：PostgreSQL schema migration。
- `release_assets` / `knowledge_archives`：可信知识恢复与审计资产。

## 关键所有权边界

1. Semantic Parser 只输出 `rule_id`、原文 `matched_text`、受控 citation key、置信度、
   uncertainty 和 polarity；offset 由系统在原文唯一匹配后确定。
2. 只有通过门禁的候选才能进入 candidate fusion；模型不能直接持久化 Finding。
3. TrustedKnowledgeSearch 只使用已审核、已验证、active 的可信知识；RegulatoryCase 不准入。
4. EvidenceLink 与 Citation 是不同对象：前者连接 Finding 与可信块，后者属于特定 audience
   的已验证 Explanation Artifact。
5. 机构端与消费者端 Artifact/Citation 分别绑定各自 ExplanationRun，不交叉回退。
6. Provider 网络、认证、timeout、HTTP、envelope、Schema 或验证失败均 fail closed。

## 数据与运行

- PostgreSQL 16 是正式运行数据库；SQLite 仅用于部分测试。
- 原件、解析产物、Prompt 快照、Finding、EvidenceLink 和 Artifact 使用哈希或不可变快照。
- 启动器仅在空数据库恢复 15 个来源和 73 个 chunk；部分状态拒绝覆盖。
- `SEMANTIC_SCREENING_ENABLED=false` 固定关闭旧实验路径。
- 未形成 RiskFinding 时不触发下游 Explanation Provider；Semantic Parser 仍属于风险发现阶段。
