# Project Context

## Authoritative Baseline

- Repo: `/Users/morechen/项目/金科创 2/一队/自命题/自命题 2/baoxiao-audit-core`
- Branch: `demo/contest-mvp`
- HEAD: `22c1137dc02444767f5d19fbdd026048adb06d1f`
- Tag: `demo-core-baseline-20260802`
- Role: stable contest-Demo backend baseline.

The original Codex repo at `/Users/morechen/项目/金科创 2/一队/自命题/baoxiao-audit-core` is permanent read-only history at `d48b9a5`.

## Product Position

Insurance marketing compliance-review and consumer-protection Demo. The target is a complete, stable, explainable presentation—not a production commercial platform.

## Existing Stable Chain

Immutable collection and parsing → deterministic validation → human review/authenticity → trusted knowledge admission and deterministic `pg_trgm` retrieval → deterministic screening and institution/consumer reports → controlled-RAG explanation of existing findings only.

受控的 OpenAI-compatible LLM 解释可作为初赛录制视频的正式模式；它只能解释既有 finding，
输出仍须通过现有 Schema、Citation 和安全门禁。Fixture 保持默认测试/CI/开发/备用演示模式。
OCR、embedding/vector retrieval、自动法律结论和生产认证仍不属于当前基线能力。

## Claude Baseline Delta

`d48b9a5..22c1137` is part of the official Demo baseline. It closes five PR #12 safety gaps: visible evidence segments, consumer Artifact schema integrity, truncation audit metadata, per-claim uncertainty, and per-finding citation coverage. Do not reimplement or revert these closures.

`feat/controlled-rag-explanation-layer` adds experimental formal restore work after `22c1137`; it is not a release/Demo authority.
