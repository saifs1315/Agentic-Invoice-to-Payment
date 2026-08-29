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
