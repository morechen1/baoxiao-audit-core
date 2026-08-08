# Decisions

## D-001: Demo Branch Is Authoritative

`demo/contest-mvp` at `22c1137` is the only current development baseline. The original Codex repository is frozen historical reference.

## D-002: Formal Restore Is Isolated

`feat/controlled-rag-explanation-layer` and its formal restore code are experimental research. They are not automatically mergeable or authoritative for contest delivery.

## D-003: Preserve the Existing Trust Chain

Do not reimplement deterministic screening, trusted-knowledge admission/`pg_trgm` retrieval, institution/consumer reports, controlled-RAG context/citations/Artifacts/Fixture Provider, or Claude's five PR #12 closures.

## D-004: Competition Value Sets Scope

Prefer demonstrability, reliability, and clear evidence boundaries. Avoid production-system expansion and refactoring without concrete Demo benefit.

## D-005: Evidence Status Is Explicit

Claude's final handoff records a historical Demo run of 666 passing tests. M0-B did not rerun the full suite; it directly reconfirmed 144 controlled-explanation tests and offline 12/12 + 43/43 acceptance. Never present historical results as a fresh full-suite run.
