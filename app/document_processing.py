from __future__ import annotations

import json

from app.domain import CanonicalDocument, DocumentKind, SourceEnvelope
from app.extraction import _text_from_payload


class UnifiedDocumentProcessor:
    """One conversion and classification layer shared by AP and AR."""

    def process(self, envelope: SourceEnvelope, processor: str = "auto") -> CanonicalDocument:
        text, mode, attempts = _text_from_payload(
            envelope.content,
            envelope.filename,
            processor,
        )
        document = CanonicalDocument(
            source_ref=envelope.source_ref,
            filename=envelope.filename,
            media_type=envelope.media_type,
            text=text,
            processing_mode=mode,
            processing_attempts=attempts,
        )
        document.kind, document.classification_reason = self.classify(
            document,
            envelope.workflow_hint,
        )
        return document

    @staticmethod
    def classify(
        document: CanonicalDocument,
        hint: DocumentKind | None = None,
    ) -> tuple[DocumentKind, str]:
        if hint and hint != DocumentKind.UNKNOWN:
            return hint, "explicit-workflow-hint"

        keys: set[str] = set()
        if document.processing_mode == "json":
            try:
                payload = json.loads(document.text)
                if isinstance(payload, dict):
                    keys = {str(key).lower() for key in payload}
            except json.JSONDecodeError:
                pass

        if {"customer_id", "open_item_refs"} <= keys:
            return DocumentKind.AR_REMITTANCE, "structured-remittance-fields"
        if {"vendor_id", "invoice_number"} <= keys:
            return DocumentKind.AP_INVOICE, "structured-invoice-fields"

        lowered = document.text.lower()
        ap_score = sum(
            marker in lowered
            for marker in ("vendor id", "supplier", "purchase order", "po number", "invoice number")
        )
        ar_score = sum(
            marker in lowered
            for marker in ("customer id", "remittance", "open item", "payment reference")
        )
        if ap_score >= 2 and ap_score > ar_score:
            return DocumentKind.AP_INVOICE, "deterministic-ap-markers"
        if ar_score >= 2 and ar_score > ap_score:
            return DocumentKind.AR_REMITTANCE, "deterministic-ar-markers"
        return DocumentKind.UNKNOWN, "ambiguous-document"
