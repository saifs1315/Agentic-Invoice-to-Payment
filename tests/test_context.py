from unittest import TestCase
from unittest.mock import patch

from app.agent_runtime import AgentProtocolError
from app.context import ContextRetriever
from app.repository import MemoryRepository


class ContextTests(TestCase):
    def test_llamaindex_policy_retrieval_returns_labeled_context(self):
        retriever = ContextRetriever(MemoryRepository())
        self.assertIsNotNone(retriever.index)
        results = retriever.retrieve_with_ids("duplicate vendor invoice number", top_k=1)
        self.assertEqual("policy-2", results[0][0])
        self.assertIn("Duplicate", results[0][1])

    def test_agent_query_falls_back_to_governed_seed_when_retrieval_is_empty(self):
        retriever = ContextRetriever(MemoryRepository())
        expected = [("policy-2", "Duplicate invoice policy")]

        with patch.object(retriever, "retrieve_with_ids", side_effect=[[], expected]) as retrieve:
            policies, effective_query, fallback_used = retriever.retrieve_agent_query(
                "weak unrelated wording",
                "invoice duplicate matching policy",
            )

        self.assertEqual(expected, policies)
        self.assertEqual("invoice duplicate matching policy", effective_query)
        self.assertTrue(fallback_used)
        self.assertEqual(2, retrieve.call_count)

    def test_agent_query_contract_and_empty_governed_evidence_fail_closed(self):
        retriever = ContextRetriever(MemoryRepository())

        with self.assertRaisesRegex(AgentProtocolError, "usable policy retrieval query"):
            retriever.retrieve_agent_query(None, "invoice policy")
        with patch.object(retriever, "retrieve_with_ids", return_value=[]):
            with self.assertRaisesRegex(AgentProtocolError, "no governed evidence"):
                retriever.retrieve_agent_query("weak query", "invoice policy")
