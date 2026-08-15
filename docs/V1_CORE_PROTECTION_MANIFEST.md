# V1 Core Protection Manifest

## Authority

- Formal V1 release: `16ebd6b2dece09a66f9d130ee9e87c669ca2305e`
- Integration branch: `upgrade/v1-core-v2-platform`
- Policy: every path below is byte-identical to the V1 release.
- `ALLOW_CHANGE = NO` for every protected path. Platform work must live outside these paths.

Git tree object IDs bind every descendant filename, mode and blob. The integrity checker also compares
the working tree recursively, so an uncommitted edit, deletion or new file under a protected directory
fails the gate.

| Protected path | V1 Git blob/tree SHA | Module | ALLOW_CHANGE |
| --- | --- | --- | --- |
| `app/rules/` | `e048f1c8553d72acb22fe520ef0e081514ba39e3` | 12-class taxonomy and rule definitions | NO |
| `app/services/screening/` | `61faa9ef02d196b5ca0198eba9f3d379b2c49a5b` | deterministic screening, segmentation, Semantic Parser, prompt contract, parser schema, validator, confidence threshold, candidate fusion, severity/Finding/quote ownership, evidence assembly and reports | NO |
| `app/services/explanation/` | `2fd223130ebc88c2e51fd18a923171a3a19adb34` | Controlled RAG, provider boundary, Citation/Claim/Uncertainty validators | NO |
| `app/services/knowledge/` | `e7da196f187baf291fe6d418da1f22a113e82d85` | trusted knowledge materialization and retrieval | NO |
| `app/prompts/` | `813162dce2dd847f892c37478043a2646e342c6d` | controlled explanation prompt authority | NO |
| `app/models/entities.py` | `7a8a5eeb02dad01ee3404ee7301eb6c6c74762a5` | DB entities, Finding/EvidenceLink/Artifact ownership | NO |
| `app/models/enums.py` | `77fb610e403a199337f2e081a3422989a4d5ac37` | persisted state and taxonomy-adjacent enums | NO |
| `migrations/` | `31b6e5b9260868698ffd82e7fbec62c4b70fb709` | complete V1 database schema and constraints | NO |
| `release_assets/trusted_data/` | `50447b8d9758ae65b2a6a1f1bc593f9482777345` | reviewed 15-source trusted corpus and 73-chunk restore inputs | NO |

## Explicitly frozen semantics

- Semantic Parser version, request prompt, strict Pydantic schema and `0.72` confidence threshold.
- Exact-quote resolution, negation/education/role/ambiguity gates and candidate fusion.
- System-owned taxonomy, severity, offsets, `RiskFinding`, `EvidenceLink`, Artifact and Citation.
- Trusted-source admission, retrieval, evidence support validation and snapshot binding.
- Institution/consumer audience separation and Citation/Claim/Uncertainty validation.
- All migrations and trusted-corpus bytes.

## Allowed integration surface

Routes, DTOs, platform services, document ingestion, runtime-only batch state, report export, frontend,
configuration fields and launch/package wiring may change. Every platform screening call must still enter
the unchanged V1 `HybridScreeningService` through the compatibility wrapper.

Run:

```bash
python3 scripts/verify_v1_core_integrity.py
```

The only passing terminal message is `V1 CORE INTEGRITY VERIFIED`.
