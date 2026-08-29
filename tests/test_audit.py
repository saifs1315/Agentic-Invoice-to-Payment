from unittest import TestCase

from app.audit import AuditLedger


class AuditTests(TestCase):
    def test_payload_tampering_invalidates_hash_chain(self):
        ledger = AuditLedger()
        event = ledger.append("invoice", "inv_1", "matched", "agent:test", {"matched": True})
        self.assertTrue(ledger.verify())
        event.payload["matched"] = False
        self.assertFalse(ledger.verify())
