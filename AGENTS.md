# Project Agent Rules

## Scope

- This is a financial-technology competition Demo: prioritize complete, stable, demonstrable behavior over production-system expansion.
- The authoritative repository is this directory on `demo/contest-mvp` at `22c1137dc02444767f5d19fbdd026048adb06d1f` (`demo-core-baseline-20260802`).
- `/Users/morechen/项目/金科创 2/一队/自命题/baoxiao-audit-core` is frozen history. Never write to it.
- `feat/controlled-rag-explanation-layer` and formal restore are experimental only; never merge or treat them as a newer Demo baseline automatically.

## Preserve

Do not reimplement or roll back deterministic screening, trusted-knowledge admission and `pg_trgm` retrieval, dual reports, or controlled-RAG context/citation/Artifact/Fixture Provider behavior. Claude's PR #12 safety closures in `d48b9a5..22c1137` are baseline behavior.

## Working Rules

- Stable modules are frozen by default. Make the smallest change that advances a stated Demo goal; do not refactor for elegance alone.
- Run focused tests for small changes; reserve full tests for milestones.
- Keep constructed evaluation data separate from formal evidence. Do not use `RegulatoryCase` as trusted retrieval or explanation evidence.
- Do not begin OCR, embeddings, vector retrieval, formal restore, or production hardening unless the task explicitly targets their stated priority.
- Read `docs/PROJECT_CONTEXT.md`, `docs/TASK_STATUS.md`, `docs/QUALITY_GATE.md`, and `docs/AI_WORKFLOW.md` before planning substantial work.
