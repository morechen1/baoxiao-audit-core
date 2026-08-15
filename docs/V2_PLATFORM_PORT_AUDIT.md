# V2 Platform Port Audit

## Compared revisions

- V1 platform base: `release/v1-final-submission` / `16ebd6b`
- V2 research source: `upgrade/v2-intelligent-compliance-platform` / `1ee82c4`

No V2 commit is safe to merge or cherry-pick whole: `387ed4b` mixes platform code with Semantic
Parser, screening-service, tests, configuration and research-dataset changes. Platform code is therefore
ported manually and reconnected to the frozen V1 detector.

| V2 path/change | Classification | Decision |
| --- | --- | --- |
| `app/services/document_ingestion.py` | PLATFORM_SAFE | Port the TXT/MD/DOCX/text-PDF extraction and file guards; omit V2-only CSV semantics from the single-material contract. |
| `app/services/audit_export.py` | PLATFORM_SAFE | Port as a V1-named report service; preserve EvidenceLink/Citation separation and remove V2 release naming. |
| `app/api/routes/v2.py` | MIXED | Rebuild as `/api/platform`; replace direct V2 wiring with `V1CompatibilityScreeningService`, add document result and batch state endpoints. |
| `app/core/config.py`, `.env.example` | PLATFORM_SAFE | Add upload, batch, long-document and exact-cache limits only. No detector threshold or Provider prompt setting changes. |
| `app/core/exceptions.py` | PLATFORM_SAFE | Add platform ingestion/result errors only. |
| `app/main.py`, `app/api/routes/__init__.py` | PLATFORM_SAFE | Register the isolated platform router. |
| `app/web/index.html`, `app/web/app.js`, `app/web/styles.css` | PLATFORM_SAFE | Port upload/batch/export UX and runtime details while retaining the accepted V1 visual system and V1 metrics. |
| `app/services/screening/semantic_parser.py` | ALGORITHM_SENSITIVE | Do not port. Contains V2 prompt, schema, chunking/cache and context-gate behavior. |
| `app/services/screening/semantic_v2.py` | ALGORITHM_SENSITIVE | Do not port. V2 Statement Mode/context/taxonomy arbitration remains research-only. |
| `app/services/screening/service.py` | ALGORITHM_SENSITIVE | Do not port. Frozen V1 service stays byte-identical. |
| V2 DEV and unseen datasets/results | EVALUATION_ONLY | Do not copy into integration branch or run again. Preserve on research branch only. |
| `scripts/rematerialize_m7_trusted_knowledge.py` | MIXED | Do not port; trusted restore inputs and behavior remain V1. |
| `scripts/start_final_demo.sh`, launcher changes | MIXED | Keep V1 launcher and add only platform metadata/package checks outside frozen core. |
| `docs/PROJECT_OUTLINE.md`, `docs/VERSION_HISTORY.md` | DOCUMENTATION | Reconcile V1 formal baseline, V2 research history and this integration candidate honestly. |

## Integration architecture

1. `MaterialIngestionService` returns exact extracted text and source metadata without rewriting prose.
2. `V1CompatibilityScreeningService` sends short inputs directly into unchanged V1 screening.
3. `LongDocumentOrchestrator` performs deterministic boundary splitting only when the V1 input limit
   would otherwise be exceeded; every chunk independently uses the same V1 compatibility path.
4. Runtime document/batch registries aggregate child run reports, remap offsets to the full text and
   perform only same-rule overlapping-span duplicate suppression.
5. `ExactSemanticResultCache` wraps the existing V1 Semantic Parser interface. Its key binds exact text,
   model, V1 parser version, prompt-contract fingerprint and schema version; cached outcomes are copied
   byte-for-byte and never shared across different text.
6. Report export reads completed database reports and runtime aggregation records; the browser does not
   synthesize Findings or evidence.

## Explicit exclusions

No V2 Statement Mode, context guard, role/beneficiary changes, taxonomy arbitration, parser Prompt,
schema behavior, confidence/acceptance changes, migrations, OCR, approximate cache, LLM deduplication,
Celery, Redis, Kafka or PDF-generation stack is included.
