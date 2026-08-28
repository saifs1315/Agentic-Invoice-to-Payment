from __future__ import annotations


class ContextRetriever:
    """LlamaIndex-backed policy retrieval with a deterministic offline fallback."""

    POLICIES = [
        "Invoices must reference an active PO unless an authorized non-PO exception is approved.",
        "Duplicate vendor invoice numbers are blocked from posting.",
        "A three-way match requires goods received quantity to cover invoiced quantity.",
        "Only approved and matched invoices may be posted; posting must be idempotent.",
    ]

    def retrieve(self, query: str, top_k: int = 2) -> list[str]:
        try:
            from llama_index.core import Document, VectorStoreIndex

            index = VectorStoreIndex.from_documents([Document(text=p) for p in self.POLICIES])
            nodes = index.as_retriever(similarity_top_k=top_k).retrieve(query)
            return [node.text for node in nodes]
        except Exception:
            words = set(query.lower().split())
            ranked = sorted(self.POLICIES, key=lambda p: len(words & set(p.lower().split())), reverse=True)
            return ranked[:top_k]

