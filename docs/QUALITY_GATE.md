# Final Release Quality Gate

The submission is releasable only when all of the following pass:

- algorithm-integrity diff against `1176d10e1fd62b7778324157e585b1f22b3cb035`;
- frozen dataset and prediction byte identity;
- Ruff, mypy and complete pytest suite;
- offline 156-case metric verification;
- release-tree and extracted-ZIP secret scans;
- clean Python 3.12+ virtualenv dependency installation and imports;
- no-key fail-closed application startup;
- configured-provider High/Boundary/Low smoke within the explicitly allowed cases;
- PostgreSQL migration, 15-source restore, 73-chunk verification and partial-state refusal;
- frontend audience Citation binding, EvidenceLink/Citation semantics, browser console, assets and layout;
- `RELEASE_MANIFEST.json` and `SHA256SUMS` verification.

Skipped environment-specific tests are reported honestly and are not altered merely to reach zero skips.
