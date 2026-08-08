# Task Status

Updated: 2026-08-08 (M2)

## Completed / Baseline

- M0-B reconciliation completed; Demo baseline and experimental restore line are explicitly separated.
- Claude's controlled-RAG safety closures are accepted baseline behavior.
- M0-B reran 144 controlled-explanation tests and offline acceptance: valid 12/12, invalid blocked 43/43, 41 rejected, 2 provider failures, 0 rejected Artifacts.
- M1 added the judge-facing `保销智审` workspace at `/`: dashboard, constructed showcase cases, staged review flow, result/evidence detail, institution/consumer views, and direct reuse of the screening/report/explanation APIs for online text review.
- M2 adds `make demo`: an isolated local PostgreSQL `baoxiao_demo` runtime, guarded constructed fixture, migration/seed/preflight flow, and three-case API smoke. It never restores formal data or targets a formal database.

## Current Priorities

### P0

- Final demonstration stability and现场 rehearsal.

### P1

- Docker/PostgreSQL full rehearsal.
- Assess real LLM Provider value for the contest before implementing it.
- Improve human-review and presentation experience.

### P2

- OCR.
- Embedding/vector retrieval.
- Formal restore.
- Production-grade engineering enhancements.

## Not Current Work

Formal restore remains quarantined on `feat/controlled-rag-explanation-layer`; do not merge it into Demo work.
