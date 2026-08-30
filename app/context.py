from __future__ import annotations

from typing import Any

from app.repository import MemoryRepository, POLICIES, deterministic_embedding


class ContextRetriever:
    """LlamaIndex document normalization plus repository/pgvector retrieval."""

    def __init__(self, repository: MemoryRepository) -> None:
        self.repository = repository
        self.documents = self._documents()
        self.index = self._index()

    @staticmethod
    def _documents() -> list[object]:
        try:
            from llama_index.core import Document

            return [
                Document(
                    text=policy,
                    id_=f"policy-{index}",
                    metadata={"source": "finance control policy", "policy_id": f"policy-{index}"},
                )
                for index, policy in enumerate(POLICIES, start=1)
            ]
        except ImportError:
            return list(POLICIES)

    def _index(self) -> Any | None:
        try:
            from llama_index.core import VectorStoreIndex
            from llama_index.core.embeddings import BaseEmbedding

            class LocalHashEmbedding(BaseEmbedding):
                def _get_query_embedding(self, query: str) -> list[float]:
                    return deterministic_embedding(query)

                async def _aget_query_embedding(self, query: str) -> list[float]:
                    return self._get_query_embedding(query)

                def _get_text_embedding(self, text: str) -> list[float]:
                    return deterministic_embedding(text)

            return VectorStoreIndex.from_documents(
                self.documents,
                embed_model=LocalHashEmbedding(model_name="ledgerpilot-hash-768"),
            )
        except ImportError:
            return None

    @staticmethod
    def _policy_id(policy: str) -> str:
        return f"policy-{POLICIES.index(policy) + 1}"

    def retrieve_with_ids(self, query: str, top_k: int = 2) -> list[tuple[str, str]]:
        rankings: list[list[str]] = []
        if self.index is not None:
            nodes = self.index.as_retriever(similarity_top_k=len(POLICIES)).retrieve(query)
            rankings.append([node.node.get_content() for node in nodes])
        try:
            rankings.append(self.repository.search_policies(query, len(POLICIES)))
        except Exception:
            words = set(query.lower().split())
            rankings.append(
                sorted(
                    POLICIES,
                    key=lambda policy: len(words & set(policy.lower().split())),
                    reverse=True,
                )
            )

        scores = {policy: 0.0 for policy in POLICIES}
        for ranking in rankings:
            for position, policy in enumerate(ranking, start=1):
                if policy in scores:
                    scores[policy] += 1.0 / (60 + position)
        ordered = sorted(POLICIES, key=lambda policy: scores[policy], reverse=True)[:top_k]
        return [(self._policy_id(policy), policy) for policy in ordered]

    def retrieve(self, query: str, top_k: int = 2) -> list[str]:
        return [policy for _, policy in self.retrieve_with_ids(query, top_k)]
