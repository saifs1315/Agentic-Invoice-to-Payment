from unittest import TestCase

from app.context import ContextRetriever
from app.repository import MemoryRepository


class ContextTests(TestCase):
    def test_llamaindex_policy_retrieval_returns_labeled_context(self):
        retriever = ContextRetriever(MemoryRepository())
        self.assertIsNotNone(retriever.index)
        results = retriever.retrieve_with_ids("duplicate vendor invoice number", top_k=1)
        self.assertEqual("policy-2", results[0][0])
        self.assertIn("Duplicate", results[0][1])
