# Task Status

Updated: 2026-08-09

## FINAL RELEASE READY

- Complete insurance-marketing review workspace and result experience are implemented.
- Deterministic rules and Semantic Parser form the frozen dual-channel candidate discovery path.
- Deterministic safety gates, candidate fusion and system-owned RiskFinding are active.
- 15 trusted sources and 73 KnowledgeChunks restore reproducibly into PostgreSQL 16.
- EvidenceLink, institution/consumer Controlled RAG Artifacts and validated Citations are implemented.
- Institution and consumer Citation catalogs are independently bound to their own ExplanationRun.
- EvidenceLink counts, trusted-source counts and Citation counts use distinct backend semantics.
- The 156-case frozen validation is complete, preserved per case and independently verifiable offline.
- Full source packaging excludes secrets, `.venv`, Git history, logs, PID files and local caches.
- One-click and terminal startup bootstrap dependencies and fail closed without a Provider key.

## Frozen Results

- Micro Precision: 79.50%
- Micro Recall: 88.89%
- Micro F1: 83.93%
- Macro F1: 84.73%
- Exact-set: 73.08%
- Negative false-alarm: 16.67%
- Quote integrity: 100%
- Hallucinated quote accepted: 0

These are internal frozen-validation results, not a third-party benchmark or manually reviewed Ground
Truth.

## Release Rule

No further taxonomy, rule, Semantic Parser, validator, confidence, RAG, knowledge-corpus, migration,
frozen-dataset or frozen-prediction changes are permitted in the submission release.
