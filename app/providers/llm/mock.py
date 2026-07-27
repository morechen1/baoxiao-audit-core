from app.providers.llm.base import LLMProvider


class MockLLMProvider(LLMProvider):
    def complete(self, prompt: str) -> str:
        return f"[mock-disabled-external-call] {prompt[:80]}"
