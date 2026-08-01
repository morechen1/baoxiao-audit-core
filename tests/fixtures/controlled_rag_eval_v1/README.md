# Controlled RAG evaluation V1

All 55 responses are synthetic (`constructed=true`). They are validator/provider test inputs only
and must never be imported into `SourceDocument`, `KnowledgeChunk`, or trusted evidence tables.
The corpus contains 12 accepted and 43 rejected/failure scenarios. Formal acceptance executes every
sample and records the run status, validation status, public error code, and artifact count.
