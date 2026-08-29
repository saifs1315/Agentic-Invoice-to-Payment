import os
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

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
