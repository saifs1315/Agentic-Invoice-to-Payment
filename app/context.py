from __future__ import annotations

from app.repository import MemoryRepository, POLICIES


class ContextRetriever:
    """LlamaIndex document normalization plus repository/pgvector retrieval."""

    def __init__(self, repository: MemoryRepository) -> None:
        self.repository = repository
        self.documents = self._documents()

    @staticmethod
    def _documents() -> list[object]:
        try:
            from llama_index.core import Document

            return [Document(text=policy, metadata={"source": "AP control policy"}) for policy in POLICIES]
        except ImportError:
            return list(POLICIES)

    def retrieve(self, query: str, top_k: int = 2) -> list[str]:
        try:
            return self.repository.search_policies(query, top_k)
        except Exception:
            words = set(query.lower().split())
            ranked = sorted(POLICIES, key=lambda policy: len(words & set(policy.lower().split())), reverse=True)
            return ranked[:top_k]
