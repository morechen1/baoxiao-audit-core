# Multi-record penalty source identity

A single immutable penalty source document may produce multiple `Penalty` records. Every
record has an explicit positive `source_entry_index`, but the index is ordering metadata and
is not part of the record identity.

The caller still supplies `source_entry_fragments` for offline audit, but the list is not a
free-form identity input. The importer first validates field evidence and deterministically
derives the complete fragment set from these fixed identity fields:

- `punished_entity` (required);
- `penalty_result` (when populated);
- `illegal_facts` (required);
- `document_number` (when populated).

Only validated `verbatim` or controlled `normalized` evidence participates. Each quote must
remain an exact substring of the immutable parsed text. The submitted fragments are
canonicalized and must exactly equal the derived set: one extra, missing or substituted
fragment fails with `penalty_source_entry_fragment_set_mismatch`.

Every `verbatim` or controlled `normalized` evidence item for a fixed identity field must
resolve exactly to that field's value under the shared field-evidence normalization rules.
A quote that only contains the value, or that adds surrounding context, fails with
`penalty_source_identity_evidence_not_exact`.

The canonical audit fragment list removes exact duplicate triples, sorts by `start_offset`,
`end_offset`, then `quote`, and rejects every partial or containing overlap with
`penalty_source_entry_fragment_overlap`. Adjacent ranges and one identical range shared by
multiple identity fields remain valid.

`source_entry_content_sha256` is SHA-256 over canonical JSON containing fixed field names and
their exact evidence quotes. Offsets remain in the audit fragments, but are excluded from the
content hash. Thus the same fixed identity content at another offset cannot manufacture a
second identity.
`source_entry_fingerprint` is SHA-256 over canonical JSON containing only:

```json
{
  "raw_artifact_sha256": "...",
  "source_entry_content_sha256": "..."
}
```

The fingerprint intentionally excludes database IDs, timestamps, local paths, entry indexes
and locators. Reordering or relabelling the same source content therefore cannot create a new
identity.

For parsed artifacts whose `metadata.source_format` is `nfra_public_json`,
`source_entry_locator` is audit provenance only. It must contain the exact non-empty
`metadata.nfra.doc_id`, a positive integer `table_index`, and exactly one positive integer
`logical_row` or `numbered_entry`. Non-NFRA artifacts must not carry `nfra_doc_id`. Boolean
integers, unknown keys, missing metadata and mismatched document IDs fail closed.

Identity fields are immutable during structured-draft revision. Duplicate detection may flag
the newly imported record, but it never mutates an existing record that has a
`ReviewDecision`, is `verified_public`, has any human decision result, or is indexed.

Database uniqueness guards `(document_id, source_entry_index)` and
`(document_id, source_entry_fingerprint)`. The migration deterministically backfills legacy
single-record rows. Downgrade fails closed once any source document contains multiple
penalties.
