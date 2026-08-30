from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.domain import Remittance, Variance


def match_remittance(
    remittance: Remittance,
    open_items: list[dict[str, Any]],
    duplicate: bool = False,
) -> dict[str, Any]:
    """Deterministically allocate one remittance across explicitly referenced AR items."""

    variances: list[Variance] = []
    items_by_ref = {str(item["reference"]): item for item in open_items}
    if duplicate:
        variances.append(
            Variance(
                "DUPLICATE_REMITTANCE",
                "reference",
                None,
                remittance.reference,
                None,
                "Customer and remittance reference already exist",
            )
        )
    if not remittance.open_item_refs:
        variances.append(
            Variance(
                "OPEN_ITEM_REFERENCE_MISSING",
                "open_item_refs",
                "At least one open AR item reference",
                None,
                None,
                "No AR items were supplied for allocation",
            )
        )

    selected: list[dict[str, Any]] = []
    for reference in remittance.open_item_refs:
        item = items_by_ref.get(reference)
        if item is None:
            variances.append(
                Variance(
                    "OPEN_ITEM_NOT_FOUND",
                    "open_item_refs",
                    "Open item owned by the customer",
                    reference,
                    None,
                    "Referenced AR item is absent, closed, or owned by another customer",
                )
            )
        else:
            selected.append(item)

    currencies = sorted({str(item["currency"]) for item in selected})
    if currencies and any(currency != remittance.currency for currency in currencies):
        variances.append(
            Variance(
                "CURRENCY_MISMATCH",
                "currency",
                ",".join(currencies),
                remittance.currency,
                None,
                "Remittance currency differs from the referenced open items",
            )
        )

    expected = sum((Decimal(str(item["amount"])) for item in selected), Decimal("0"))
    if selected and expected != remittance.amount:
        code = "PARTIAL_REMITTANCE" if remittance.amount < expected else "OVERPAYMENT"
        variances.append(
            Variance(
                code,
                "amount",
                str(expected),
                str(remittance.amount),
                float(abs(remittance.amount - expected) / expected * 100) if expected else None,
                "Remittance amount does not equal the selected open-item balance",
            )
        )

    matched = bool(selected) and not any(variance.blocking for variance in variances)
    result: dict[str, Any] = {
        "matched": matched,
        "applied": False,
        "match_type": "customer-open-item",
        "open_item_refs": list(remittance.open_item_refs),
        "expected_amount": str(expected),
        "variances": [variance.to_dict() for variance in variances],
    }
    if variances:
        primary = variances[0]
        result.update(
            reason=primary.code.lower(),
            expected=primary.expected,
            actual=primary.actual,
        )
        if primary.code == "CURRENCY_MISMATCH":
            result["reason"] = "currency_mismatch"
        elif primary.code in {"PARTIAL_REMITTANCE", "OVERPAYMENT"}:
            result["reason"] = "amount_mismatch"
        elif primary.code == "OPEN_ITEM_NOT_FOUND":
            result["reason"] = "open_item_not_found"
    return result
