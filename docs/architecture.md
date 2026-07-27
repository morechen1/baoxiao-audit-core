# 架构

## 分层

- `api` / `cli`：输入校验和输出适配，不承载核心规则。
- `services/collection`：网页、文件与本地目录采集，原件哈希存储。
- `services/parsing`：统一解析结果、页码、切片及 OCR 警告。
- `services/validation`：无模型、可重复的确定性规则。
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
4. SHA-256 唯一约束提供并发下最终去重；采集前查询提供友好快速路径。
5. `indexed` 是可信索引状态标记，不表示已生成向量。
6. 评测样本是独立表，不冒充来源文档；允许进入人工审核包，但不进入正式知识索引。
7. API 的本地路径限定在 `DATA_DIR` 下，下载最大 50 MiB，防止目录穿越与无界写入。

## 数据流

`source → collect → immutable raw → parse/chunk → validate → human review → trusted marker`

任何自动处理只能到 `pending_review`。索引前再次检查审核状态和真实性标识。
