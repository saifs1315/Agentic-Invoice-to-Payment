from unittest import TestCase

from pydantic import ValidationError

from app.agent_runtime import (
    DeterministicTestAgentRuntime,
    DomainAgentDecision,
    OllamaAgentRuntime,
)
from app.config import Settings


class AgentRuntimeContractTests(TestCase):
    def test_fake_runtime_is_rejected_outside_test_environment(self):
        with self.assertRaisesRegex(ValidationError, "permitted only"):
            Settings(app_env="production", agent_runtime="fake")

    def test_test_runtime_honors_bounded_action_contract(self):
        runtime = DeterministicTestAgentRuntime()
        decision = runtime.decide(
            domain="ap",
            stage="evaluate_match",
            evidence={"expected_action": "ESCALATE"},
            allowed_actions=["ESCALATE"],
        )
        self.assertEqual("ESCALATE", decision.action)
        self.assertEqual(768, len(runtime.embed("duplicate invoice policy")))

    def test_test_runtime_formulates_policy_query_and_makes_bounded_route_choice(self):
        runtime = DeterministicTestAgentRuntime()
        retrieval = runtime.decide(
            domain="ap",
            stage="retrieve_policy",
            evidence={"query_seed": "invoice duplicate matching policy"},
            allowed_actions=["RETRIEVE_POLICY"],
        )
        route = runtime.decide(
            domain="ap",
            stage="evaluate_match",
            evidence={
                "deterministic_result": {"matched": True},
                "control_eligibility": {"auto_action_permitted": True},
            },
            allowed_actions=["POST_PAYMENT_JOURNAL", "ESCALATE"],
        )
        self.assertEqual("invoice duplicate matching policy", retrieval.retrieval_query)
        self.assertEqual("POST_PAYMENT_JOURNAL", route.action)

    def test_supervisor_dispatches_from_document_evidence(self):
        decision = DeterministicTestAgentRuntime().supervise(
            {
                "deterministic_kind": "ar_remittance",
                "classification_reason": "structured-remittance-fields",
            }
        )
        self.assertEqual("DISPATCH_AR", decision.action)
        self.assertEqual("ar_remittance", decision.workflow)

    def test_live_runtime_accepts_trailing_comma_in_otherwise_valid_json(self):
        decision = OllamaAgentRuntime._validate_json(
            DomainAgentDecision,
            '{"action":"ESCALATE","reason":"literal ,} text",}',
        )

        self.assertEqual("ESCALATE", decision.action)
        self.assertEqual("literal ,} text", decision.reason)
        self.assertIsNone(decision.confidence)
