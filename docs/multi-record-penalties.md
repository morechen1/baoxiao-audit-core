# Multi-record penalty source documents

A single official penalty source document may describe several independently reviewable
administrative-penalty entries. `Penalty.document_id` is therefore no longer unique.

Each entry has two stable identities:

- `source_entry_index`: a positive, source-order ordinal within the immutable document.
- `source_entry_fingerprint`: SHA-256 of canonical JSON containing the raw artifact SHA-256,
  source entry index, a stable source locator, and the SHA-256 of the exact entry text.

Canonical JSON is UTF-8, uses sorted keys and compact separators, preserves Unicode
(`ensure_ascii=False`), and rejects NaN. Database identifiers, timestamps, local paths, and
review state are never fingerprint inputs.

Penalty draft envelopes must provide the stable locator, the exact continuous entry text, and
its SHA-256. Import verifies that text against the immutable parsed text, recomputes the
fingerprint, and rejects mismatches. A JSONL import is one transaction: a duplicate
`(document_id, source_entry_index)`, duplicate `(document_id, source_entry_fingerprint)`, or any
other invalid row rolls back the whole file.

Portable review records are ordered by `source_entry_index` and then
`portable_record_key`. Human corrections continue to address one
`structured_record_id`/portable key and do not implicitly mutate sibling entries.

Exact duplicate candidates across different source documents are retained and marked
`duplicate_candidate`; they are never automatically deleted or merged. A reviewer must decide
how to resolve them.

The migration backfills legacy single-record penalties with index `1` and a deterministic
fingerprint. Downgrade fails with `cannot_downgrade_multi_record_penalties` if any document has
more than one penalty record, preventing silent data loss.
