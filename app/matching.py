from __future__ import annotations

from decimal import Decimal

from app.domain import Invoice, MatchResult, PurchaseOrder, Variance


MONEY = Decimal("0.01")


def _pct(actual: Decimal, expected: Decimal) -> float:
    if expected == 0:
        return 0.0 if actual == 0 else 100.0
    return float(abs(actual - expected) / abs(expected) * 100)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY)


def match_invoice(invoice: Invoice, po: PurchaseOrder | None, price_tolerance_pct: float = 2.0, quantity_tolerance_pct: float = 0.0, total_tolerance_pct: float = 2.0, require_goods_receipt: bool = True, duplicate: bool = False) -> MatchResult:
    variances: list[Variance] = []
    match_type = "3-way" if require_goods_receipt else "2-way"
    line_total = Decimal("0")
    for index, line in enumerate(invoice.lines, 1):
        calculated = _money(line.quantity * line.unit_price)
        line_total += line.amount
        if _money(line.amount) != calculated:
            variances.append(
                Variance(
                    "LINE_AMOUNT_MISMATCH",
                    f"lines[{index}].amount",
                    str(calculated),
                    str(_money(line.amount)),
                    _pct(line.amount, calculated),
                    "Line amount does not equal quantity multiplied by unit price",
                )
            )

    declared_subtotal = invoice.subtotal if invoice.subtotal is not None else line_total
    if invoice.subtotal is not None and _money(line_total) != _money(invoice.subtotal):
        variances.append(
            Variance(
                "SUBTOTAL_MISMATCH",
                "subtotal",
                str(_money(line_total)),
                str(_money(invoice.subtotal)),
                _pct(invoice.subtotal, line_total),
                "Invoice subtotal does not equal the sum of line amounts",
            )
        )
    calculated_total = _money(
        declared_subtotal
        + invoice.tax_amount
        + invoice.freight_amount
        - invoice.discount_amount
    )
    if _money(invoice.total) != calculated_total:
        variances.append(
            Variance(
                "INVOICE_TOTAL_MISMATCH",
                "total",
                str(calculated_total),
                str(_money(invoice.total)),
                _pct(invoice.total, calculated_total),
                "Invoice total does not reconcile to subtotal, tax, freight, and discount",
            )
        )
    if duplicate:
        variances.append(Variance("DUPLICATE_INVOICE", "invoice_number", None, invoice.invoice_number, None, "Vendor and invoice number already exist"))
    if not invoice.po_number or po is None:
        variances.append(Variance("MISSING_PO", "po_number", None, invoice.po_number, None, "Referenced purchase order was not found"))
        return MatchResult(invoice.id, match_type, False, variances, invoice.po_number)
    if invoice.vendor_id != po.vendor_id:
        variances.append(Variance("VENDOR_MISMATCH", "vendor_id", po.vendor_id, invoice.vendor_id, None, "Invoice vendor differs from PO vendor"))
    if invoice.currency != po.currency:
        variances.append(Variance("CURRENCY_MISMATCH", "currency", po.currency, invoice.currency, None, "Invoice currency differs from PO currency"))

    po_lines = {line.line_number: line for line in po.lines}
    expected_goods_total = Decimal("0")
    for index, line in enumerate(invoice.lines, 1):
        po_line = po_lines.get(line.po_line or index)
        if po_line is None:
            variances.append(Variance("MISSING_PO_LINE", f"lines[{index}]", None, str(line.po_line or index), None, "Invoice line does not map to a PO line"))
            continue
        expected_goods_total += line.quantity * po_line.unit_price
        price_pct = _pct(line.unit_price, po_line.unit_price)
        if price_pct > price_tolerance_pct:
            variances.append(Variance("PRICE_VARIANCE", f"lines[{index}].unit_price", str(po_line.unit_price), str(line.unit_price), round(price_pct, 4), "Unit price exceeds configured tolerance"))
        quantity_pct = _pct(line.quantity, po_line.quantity)
        if line.quantity > po_line.quantity and quantity_pct > quantity_tolerance_pct:
            variances.append(Variance("QUANTITY_VARIANCE", f"lines[{index}].quantity", str(po_line.quantity), str(line.quantity), round(quantity_pct, 4), "Invoice quantity exceeds configured tolerance"))
        if require_goods_receipt and line.quantity > po_line.received_quantity:
            variances.append(Variance("RECEIPT_SHORTFALL", f"lines[{index}].received_quantity", str(po_line.received_quantity), str(line.quantity), _pct(line.quantity, po_line.received_quantity), "Invoiced quantity exceeds goods received"))

    total_pct = _pct(declared_subtotal, expected_goods_total)
    if total_pct > total_tolerance_pct:
        variances.append(Variance("TOTAL_VARIANCE", "subtotal", str(_money(expected_goods_total)), str(_money(declared_subtotal)), round(total_pct, 4), "Invoice goods subtotal exceeds the PO value for the invoiced quantities"))
    return MatchResult(invoice.id, match_type, not any(v.blocking for v in variances), variances, po.number)
