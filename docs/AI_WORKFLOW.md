# AI Workflow and Safety Ownership

## Runtime roles

- Deterministic rules identify explicit and bounded marketing-risk patterns.
- Semantic Parser proposes structured candidates for natural-language coverage.
- Deterministic gates verify taxonomy, original quote, context, role, confidence and ambiguity.
- Candidate fusion deduplicates accepted candidates.
- The system—not the model—owns RiskFinding, severity, offsets and evidence status.
- Controlled RAG explains only persisted Findings and selected EvidenceLinks.
- Institution and consumer outputs are independently validated and persisted.

## Provider behavior

- OpenAI-compatible Provider is optional, timeout-bounded and configured only through local env.
- API key and endpoint are not persisted in artifacts, runs, logs or release files.
- Provider failure never silently becomes Fixture success.
- Fixture mode is explicitly labeled deterministic demo output.
- Invalid model output is rejected; it is not repaired into an apparently valid answer.

## Release controls

- Keep taxonomy, rules, Semantic Parser contract, 0.72 confidence threshold, validators, RAG retrieval,
  trusted corpus, migrations and frozen validation artifacts unchanged.
- Verify metrics offline; never rerun the 156 external-Provider evaluation for packaging.
- Exclude local secrets, virtualenvs, logs, caches, PID files and Git history.
- Full milestone evidence is Ruff, mypy, pytest, validation verification, clean-environment startup,
  database restore, browser regression and extracted-ZIP verification.
