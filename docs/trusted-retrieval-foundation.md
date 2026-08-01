# 可信知识检索基础层

## 准入边界

可信检索只物化同时满足以下条件的来源文档：

- `final_review_status` 是 `approved` 或 `approved_with_revision`；
- `authenticity_type` 是 `verified_public`；
- `knowledge_index_status` 是 `indexed`；
- 原件、解析产物、自动校验、字段证据和结构化状态均通过现有
  `KnowledgeIndexService` 确定性闸门；
- Penalty 额外通过 `PenaltySourceIdentityService`；
- 类型只能是 Regulation、ProductDocument 或 Penalty。

`RegulatoryCase` 本阶段始终排除。它们的 `external_test_candidate` 用途是留出评测，
不是可信检索语料；本层也不接受类型参数绕过该限制。legacy
`reimport_required` Penalty 不具备当前可信条目身份，固定以
`penalty_source_identity_reimport_required` 拒绝。

## KnowledgeChunk 与物化

`KnowledgeChunk` 保存原文 `text`、检索规范文本 `normalized_text`、空格分隔的
`lexical_tokens`、结构化过滤字段、已验证 occurrence 来源、字段证据引用、审核/
真实性快照和版本化哈希。它只能由 `KnowledgeIndexService` 的受控物化方法创建，
没有任意写入 API。`KnowledgeIndexRun` 记录全量运行状态、数量和有效块身份清单哈希。

单文档重建在事务中比对候选块：不再出现的旧块设为 `is_active=false` 并记录
`retired_at`，新身份创建，已有同一身份直接复用。候选构建或事务中任一校验失败时，
不会留下该文档半成品，上一版有效块保持可用。重复输入不新增块。

## 确定性 Chunk 策略

- Regulation：标题/文号/机关/日期基本信息块，按自然段和完整句分割的正文块，
  以及条款编号/机关/效力快照块。
- ProductDocument：产品身份、保障责任、条款与风险三类块，只写数据库中实际非空字段。
- Penalty：每条结构记录一个独立块，保留联合主体结构、条目索引、fingerprint、
  locator 和实际字段；空值不生成推断性占位词。

`trusted_structured_chunker_v1` 的 ordinal 由上述固定顺序产生。正文首选空行段落边界，
块过长时只在句末标点分隔，不任意截断句子。

## 规范化、词元与哈希

`trusted_lexical_normalization_v1` 执行 Unicode NFKC、拉丁字母小写和 Unicode 空白折叠。
它不改写原文或字段证据。`han_bigram_lexical_v1` 按确定性顺序产生：

1. 连续汉字的相邻二元组，单汉字则保留单字；
2. 连续拉丁字母、数字及受控连接符整体；
3. 受控格式的处罚文号、金额和日期整体 token；
4. 重复 token 保留首次出现顺序并去重。

`chunk_content_sha256 = SHA-256(UTF-8(normalized_text))`。

`chunk_identity_sha256` 是以规范 JSON 序列化的以下字段的 SHA-256：原件 SHA-256、
record type、portable record key、chunk kind、ordinal、content SHA-256、normalization/
tokenizer/chunker version。它不包含数据库主键、时间戳、本机路径或审核批次 ID。

## PostgreSQL 召回与确定性排序 v1

PostgreSQL 启用 `pg_trgm`，建立 `to_tsvector('simple', lexical_tokens)` GIN 全文索引和
`normalized_text gin_trgm_ops` GIN 索引。查询使用 token 全文匹配、trigram 和规范文本子串合并召回，
然后计算 `trusted_lexical_rank_v1`：

```text
score = 2.00 * unique_query_token_coverage
      + deterministic_trigram_dice_similarity
      + 0.50 * title_exact_substring
      + 0.50 * exact_document_number
      + 0.35 * punished_entity_substring
      + evidence_quality_bonus

evidence_quality_bonus: A=0.10, B=0.07, C=0.03, D/NULL=0
```

分数保留八位小数。并列时按 `pilot_id`、`record_type`、`chunk_ordinal`、
`chunk_identity_sha256` 升序。这是“确定性词法排序 v1”，可称 BM25 式词法基础，
但不是标准 BM25，也不应被解释为法律结论。

## API 和 CLI

```bash
curl 'http://localhost:8000/api/v1/knowledge/search?query=销售误导&record_types=penalty'
python -m app.cli.main knowledge rebuild
python -m app.cli.main knowledge rebuild --document-id 123
python -m app.cli.main knowledge verify
python -m app.cli.main knowledge search '优惠 中奖' --record-type penalty
python -m app.cli.main knowledge stats
```

API 和 CLI 共用 `TrustedKnowledgeSearchService`。结果 snippet 仅从块原文截取，不由模型生成；
每条结果返回已验证 occurrence URL、locator、evidence references 和内容/身份哈希。

`query` 最长 500 字符，`limit` 范围 1–100，`offset` 最大 10000。空查询仅做有界
结构化过滤和分页，不取消上限全库返回。

## 验证、失败关闭与后续扩展

`knowledge verify` 重算哈希并检查 orphan、不合格文档活跃块、RegulatoryCase 误入、
Penalty 身份、来源 locator 和活跃身份重复。任一异常都返回非零。所有可搜结果还会在
查询时再与当前 `SourceDocument` 的审核、真实性和索引状态联合校验，因此失去资格后
即使尚未执行退役也不可被搜到。

当前没有 Embedding、向量数据库、RAG、重排模型或 LLM。下一阶段可在不改变准入闸门和
来源引用的前提下，对同一版本化活跃块生成经批准的 Embedding，将向量候选与本词法候选
合并，再使用可回归、版本化的混合排序；生成层仍必须引用块与不可变证据。
