# Multi-record penalty source identity

A single immutable penalty source document may produce multiple `Penalty` records. Every
record has an explicit positive `source_entry_index`, but the index is ordering metadata and
is not part of the record identity.

The immutable identity is represented by `source_entry_fragments`, an ordered list of exact
substrings from the verified parsed text:

```json
[
  {"quote": "被处罚主体", "start_offset": 10, "end_offset": 15},
  {"quote": "罚款10万元", "start_offset": 30, "end_offset": 36}
]
```

Fragments must be sorted, non-overlapping, have exact offsets, include the punished-entity
evidence when that field is populated, and include the penalty-result evidence when that
field is populated. A populated punished entity cannot be the only fragment.

`source_entry_content_sha256` is SHA-256 over canonical JSON for that ordered fragment list.
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

For NFRA documents, `source_entry_locator` is audit provenance only. It contains the exact
parsed NFRA `doc_id`, a positive integer `table_index`, and exactly one positive integer
`logical_row` or `numbered_entry`. Boolean integers, unknown keys and mismatched document IDs
fail closed.

Identity fields are immutable during structured-draft revision. Duplicate detection may flag
the newly imported unreviewed record, but it never mutates an existing approved,
approved-with-revision or indexed record.

Database uniqueness guards `(document_id, source_entry_index)` and
`(document_id, source_entry_fingerprint)`. The migration deterministically backfills legacy
single-record rows. Downgrade fails closed once any source document contains multiple
penalties.
