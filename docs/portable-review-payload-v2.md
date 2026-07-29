# Portable Review Payload V2

Portable Review Bundle V2 uses one shared canonicalization implementation for export,
current-state recomputation, bundle verification, and decision import.

The canonical payload binds stable review material: official source and retrieval URLs;
raw, parsed-artifact, and plain-text SHA-256 values; document trust and index states;
Pilot identifiers; stable source occurrences; structured business fields; field evidence;
draft provenance; index eligibility; and rejection reasons. Dictionaries use sorted keys,
unordered review collections use explicit deterministic ordering, JSON is UTF-8 with fixed
separators, and non-finite numeric values are rejected.

Database primary keys, batch identifiers, timestamps, local paths, and evidence database
identifiers are excluded. Parsed Artifact Schema 2.0 likewise excludes `document_id` and
`parsed_at` from artifact content so the same parser output over the same immutable raw
artifact has the same SHA-256 in another database. Integrity verification remains backward
compatible with existing Parsed Artifact Schema 1.0 files.

Local `file://` source and occurrence URLs are canonicalized through the same shared function
used by bundle export and payload hashing. Only the filename is retained as
`local-unattributed://{filename}`; an empty filename fails closed. HTTPS and other non-file
URLs are preserved unchanged.

Each structured record has a `portable_record_key` derived from the raw artifact SHA-256,
record type, draft provenance, canonical business fields, and canonical field evidence.
Portable corrections must resolve this key to exactly one current structured record.
Missing or ambiguous keys fail closed.

V1 bundles are not silently upgraded. Decision import requires
`review_payload_schema_version: 2`; unsupported bundles fail with
`review_payload_schema_unsupported`.

The evidence layer additionally supports:

- `collapse_unicode_whitespace_for_chinese_date_v1`, which keeps the exact source quote and
  offsets while deterministically normalizing one Chinese date split only by Unicode
  whitespace;
- `nfra.caption` as a narrowly scoped metadata source for
  `Regulation.document_number`, provided the value was parsed from immutable official NFRA
  JSON and passed the controlled document-number format check. An available
  `nfra.document_number` is always authoritative, so caption evidence is accepted only when
  that primary value is empty.
