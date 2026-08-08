# Task Status

Updated: 2026-08-08 (M0-C)

## Completed / Baseline

- M0-B reconciliation completed; Demo baseline and experimental restore line are explicitly separated.
- Claude's controlled-RAG safety closures are accepted baseline behavior.
- M0-B reran 144 controlled-explanation tests and offline acceptance: valid 12/12, invalid blocked 43/43, 41 rejected, 2 provider failures, 0 rejected Artifacts.

## Current Priorities

### P0

- Judge-facing frontend.
- Complete Demo operation chain.
- Stable demo data/cases.
- One-command or minimal startup.
- Final demonstration stability.

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
