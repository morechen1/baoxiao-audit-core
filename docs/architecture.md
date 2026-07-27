# 架构

## 分层

- `api` / `cli`：输入校验和输出适配，不承载核心规则。
- `services/collection`：网页、文件与本地目录采集，原件哈希存储。
- `services/parsing`：统一解析结果、页码、切片及 OCR 警告。
- `services/validation`：无模型、可重复的确定性规则。
- `services/structured_records`：导入 Pydantic 校验后的人工结构化草稿。
- `services/state_machine`：集中管理审核状态转换和结构化状态同步。
- `services/review`：审核包、决定、修订和状态历史。
- `services/knowledge`：可信数据索引资格闸门。
- `repositories`：持久化访问与状态变更。
- `providers`：未来 LLM、Embedding、OCR 的厂商无关边界。

## 默认决策

1. 使用同步 SQLAlchemy：当前工作量以批处理与 CLI 为主，行为清晰；未来高并发采集可
   增加异步 worker，而不改变数据库模型。
2. PostgreSQL 16 为生产数据库；SQLite 只用于快速本地开发和测试。
3. 原件和 `raw_text` 不被审核修订覆盖。修订进入结构化表和
   `corrected_fields_json`，决定写入 `review_decisions`。
4. SHA-256 唯一约束只去重内容 Blob；`document_occurrences` 保留每个来源 URL。
5. 人工审核状态与知识索引状态完全分离；索引永不改写人工决定。
6. 评测样本是独立表，不冒充来源文档；允许进入人工审核包，但不进入正式知识索引。
7. API 的本地路径限定在 `DATA_DIR` 下；网络采集使用登记来源和 SSRF 安全策略。
8. 审核批次项目绑定完整 payload hash，修订后重新执行确定性校验并以事务提交。

## 数据流

`source → collect → immutable raw → parse/chunk → validate → human review → trusted marker`

任何自动处理只能到 `pending_review`。索引使用严格白名单，同时检查解析、自动校验、
结构化记录、状态一致性、原文、哈希和证据引用。
