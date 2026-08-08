# Quality Gate

| Change type | Minimum evidence |
|---|---|
| Small, bounded change | Relevant focused tests |
| Normal feature | Module tests + smoke check |
| Core audit/trust-chain change | Relevant complete tests + necessary Sol review |
| Milestone | Full test suite + Demo smoke + checkpoint |

Use the smallest meaningful verification first. Do not rerun full tests merely to restate historical evidence.

Known baseline evidence: Claude's final handoff records 666 passing tests; M0-B freshly ran 144 controlled-explanation tests plus offline 12/12 valid and 43/43 invalid acceptance. PostgreSQL formal acceptance and Docker rehearsal remain separate work, not implied by the offline result.
