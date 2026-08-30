import hashlib
import json
from unittest import TestCase

from app.document_processing import UnifiedDocumentProcessor
from app.domain import DocumentKind, SourceEnvelope
from app.extraction import extract_remittance_from_document


def envelope(content: bytes, filename: str, hint: DocumentKind | None = None) -> SourceEnvelope:
    return SourceEnvelope(
        content=content,
        filename=filename,
        media_type="application/json" if filename.endswith(".json") else "text/plain",
        source_ref="test:document",
        content_sha256=hashlib.sha256(content).hexdigest(),
        workflow_hint=hint,
    )


class DocumentProcessingTests(TestCase):
    def test_structured_documents_are_classified_by_domain(self):
        processor = UnifiedDocumentProcessor()
        ap = processor.process(
            envelope(
                json.dumps({"vendor_id": "V1", "invoice_number": "I1"}).encode(),
                "invoice.json",
            )
        )
        ar = processor.process(
            envelope(
                json.dumps({"customer_id": "C1", "open_item_refs": ["AR-1"]}).encode(),
                "remittance.json",
            )
        )
        self.assertEqual(DocumentKind.AP_INVOICE, ap.kind)
        self.assertEqual(DocumentKind.AR_REMITTANCE, ar.kind)

    def test_ambiguous_document_is_not_guessed(self):
        document = UnifiedDocumentProcessor().process(envelope(b"Please see attachment", "note.txt"))
        self.assertEqual(DocumentKind.UNKNOWN, document.kind)
        self.assertEqual("ambiguous-document", document.classification_reason)

    def test_text_remittance_uses_the_shared_canonical_document(self):
        content = b"""Customer ID: CUST-001
Remittance Reference: REM-TEXT-1
Payment Amount: 1000.00
Currency: USD
Open Items: AR-9001, AR-9002"""
        document = UnifiedDocumentProcessor().process(envelope(content, "remittance.txt"))
        remittance = extract_remittance_from_document(document)
        self.assertEqual(DocumentKind.AR_REMITTANCE, document.kind)
        self.assertEqual(["AR-9001", "AR-9002"], remittance.open_item_refs)
        self.assertEqual("text", remittance.extraction_mode)

    def test_explicit_hint_is_authoritative_for_legacy_wrappers(self):
        document = UnifiedDocumentProcessor().process(
            envelope(b"unlabeled", "document.txt", DocumentKind.AP_INVOICE)
        )
        self.assertEqual(DocumentKind.AP_INVOICE, document.kind)
        self.assertEqual("explicit-workflow-hint", document.classification_reason)
