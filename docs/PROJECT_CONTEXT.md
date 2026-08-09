# Project Context

## Final Product

“保销智审”是保险营销材料智能合规审核系统。最终提交包含完整后端、前端、数据库迁移、
可信知识恢复资产、Semantic Parser、受控 RAG、测试、冻结验证资产和可复现启动器。

## Frozen Authorities

- Algorithm system freeze: `1176d10e1fd62b7778324157e585b1f22b3cb035`
- 156-case evaluation checkpoint: `b6bdd4c15d6795106341104400d13b7b8b2e3184`
- Presentation before release: `e1be3eb895afc20eade576177f70fc915f4792cd`
- Final release checkpoint: recorded in `RELEASE_MANIFEST.json`.

## Final Chain

Immutable material → deterministic rules + Semantic Parser → channel-specific deterministic safety
gates → candidate fusion → system-owned RiskFinding → trusted regulatory retrieval → EvidenceLink →
Controlled RAG → independent institution/consumer Artifact → Citation/Claim/Uncertainty validation.

`SEMANTIC_SCREENING_ENABLED=false` permanently disables the legacy semantic-screening path. The
current Semantic Parser only proposes candidates and cannot own Finding, severity, offsets, sources,
EvidenceLinks, or Citations.

## Trusted Runtime

- PostgreSQL 16 / `baoxiao_contest_final`
- 15 trusted SourceDocuments
- 73 active KnowledgeChunks: 33 regulation, 6 product, 34 penalty
- 34 penalty records
- RegulatoryCase chunks: 0

## External Provider Boundary

The OpenAI-compatible provider is optional and fail-closed. Secrets are local-only. Model output must
pass existing Schema, Finding, Claim, Citation, exact-quote, uncertainty and disclaimer validation.
Fixture output remains explicitly labeled and never impersonates real AI output.

## Explicit Non-goals

Production authentication, OCR service, embeddings/vector search, automatic legal conclusions,
multi-provider routing and production key management are outside this submission.
