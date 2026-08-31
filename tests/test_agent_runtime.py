from unittest import TestCase

from pydantic import ValidationError

from app.agent_runtime import DeterministicTestAgentRuntime
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

    def test_supervisor_dispatches_from_document_evidence(self):
        decision = DeterministicTestAgentRuntime().supervise(
            {
                "deterministic_kind": "ar_remittance",
                "classification_reason": "structured-remittance-fields",
            }
        )
        self.assertEqual("DISPATCH_AR", decision.action)
        self.assertEqual("ar_remittance", decision.workflow)
