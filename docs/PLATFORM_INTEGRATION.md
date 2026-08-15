# V1 Core + Platform Integration

## Safety contract

The platform layer is an adapter around the frozen V1 detector. `scripts/verify_v1_core_integrity.py`
must pass before any candidate checkpoint. The protected file list and V1 Git object identities are in
`docs/V1_CORE_PROTECTION_MANIFEST.md`.

## Input contract

`MaterialInput` contains title, material type, exact extracted text, safe source filename, source format
and non-secret metadata. TXT/MD require UTF-8 (BOM accepted). DOCX reads paragraphs and tables in body
order without executing macros or embedded resources. PDF accepts only extractable text and rejects
scanned/empty pages with `upload_pdf_no_extractable_text`.

Files remain in memory, are limited to 10MB and never use user-provided paths for writes.

## Screening contract

- Inputs up to the V1 `MAX_RAW_TEXT_LENGTH` call the original V1 service once.
- Longer inputs are split at paragraph/sentence boundaries with a bounded overlap.
- Each chunk independently enters the unchanged V1 service.
- Platform reports remap child Finding offsets to the exact full text and assert
  `raw_text[start:end] == matched_text`.
- Deduplication only removes same-rule spans that are equal or at least 80% overlapping.
- The platform never changes taxonomy, severity, quote, Finding or EvidenceLink ownership.

## Batch and runtime state

Batch state is bounded in-process memory, not a new database schema. It supports 1–20 files and three
workers by default. Each worker owns its SQLAlchemy session. A failed item is recorded as failed without
rolling back successful sibling items.

## Reports

HTML and JSON exports are built server-side from completed V1 screening reports, persisted
EvidenceLinks and any existing audience-specific explanation Artifacts. Reports exclude secrets,
Prompt snapshots, hidden reasoning, database credentials and local absolute paths.

## Cache

The cache wraps the existing V1 `SemanticClaimParser.supplement` interface. Its key binds exact text
SHA-256, model, V1 parser version, frozen parser blob and output-schema SHA-256. It is bounded LRU memory
only. Approximate matching and cross-text result sharing are prohibited.

## Operational limits

Runtime batch/document state is process-local and intended for the competition demo. A restart clears
that state, while completed child ScreeningRuns remain in PostgreSQL. OCR, durable batch recovery,
distributed queues, PDF export and production observability are explicit non-goals.
