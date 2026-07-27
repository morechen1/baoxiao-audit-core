from app.providers.embedding.base import EmbeddingProvider


class DisabledEmbeddingProvider(EmbeddingProvider):
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("Embedding is disabled; no vectors were generated")
