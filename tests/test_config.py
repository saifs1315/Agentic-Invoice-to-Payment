import os
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from pydantic import ValidationError

from app.config import Settings


class SettingsTests(TestCase):
    def test_monetary_limit_is_environment_configurable(self):
        with patch.dict(os.environ, {"MAX_MONETARY_AMOUNT": "2500000000.00"}):
            configured = Settings()

        self.assertEqual(Decimal("2500000000.00"), configured.max_monetary_amount)

    def test_monetary_limit_must_be_positive(self):
        with patch.dict(os.environ, {"MAX_MONETARY_AMOUNT": "0"}):
            with self.assertRaisesRegex(ValueError, "must be greater than zero"):
                Settings()

    def test_monetary_limit_must_be_a_finite_decimal(self):
        invalid_values = ("abc", "NaN", "Infinity")

        for invalid_value in invalid_values:
            with self.subTest(value=invalid_value):
                with patch.dict(
                    os.environ,
                    {"MAX_MONETARY_AMOUNT": invalid_value},
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "MAX_MONETARY_AMOUNT must be a valid.*decimal number",
                    ):
                        Settings()

    def test_each_instance_reads_current_environment(self):
        with patch.dict(
            os.environ,
            {
                "MATCH_PRICE_TOLERANCE_PCT": "9.5",
                "AUTO_POST_ENABLED": "false",
                "ERP_MODE": "http",
                "MAX_UPLOAD_MB": "7",
            },
        ):
            configured = Settings()

        self.assertEqual(9.5, configured.price_tolerance_pct)
        self.assertFalse(configured.auto_post_enabled)
        self.assertEqual("http", configured.erp_mode)
        self.assertEqual(7, configured.max_upload_mb)

    def test_agent_step_budget_cannot_be_lower_than_the_parent_graph_depth(self):
        with self.assertRaises(ValidationError):
            Settings(agent_max_steps=5)

        self.assertEqual(6, Settings(agent_max_steps=6).agent_max_steps)
