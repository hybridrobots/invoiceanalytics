"""Approval gate — required fields and readiness checklist before official reports."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from invoice_intelligence.database.models import DocumentDirection, Invoice


@dataclass
class FieldStatus:
    field: str
    label: str
    value: Any
    required: bool
    present: bool
    low_confidence: bool = False
    confidence: float | None = None


@dataclass
class ApprovalReadiness:
    can_approve: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fields: list[FieldStatus] = field(default_factory=list)

    @property
    def missing_required(self) -> list[str]:
        return [f.label for f in self.fields if f.required and not f.present]


def _confidence_map(invoice: Invoice) -> dict[str, float]:
    if not invoice.confidence_json:
        return {}
    try:
        data = json.loads(invoice.confidence_json)
        return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def evaluate_approval(
    invoice: Invoice,
    *,
    low_confidence_threshold: float = 0.55,
    validation_has_blocking_errors: bool = False,
    hash_duplicate: bool = False,
) -> ApprovalReadiness:
    """
    Determine whether an invoice may be approved for official reports.

    Sales invoices require a customer name.
    Purchase invoices require a supplier name.
    Direction must be explicitly sales or purchase.
    """
    conf = _confidence_map(invoice)
    direction = invoice.document_direction or DocumentDirection.UNKNOWN.value

    require_customer = direction in {
        DocumentDirection.SALES.value,
        DocumentDirection.UNKNOWN.value,
    }
    require_supplier = direction in {
        DocumentDirection.PURCHASE.value,
        DocumentDirection.UNKNOWN.value,
    }
    # Once direction is known, only the counterparty for that flow is mandatory
    if direction == DocumentDirection.SALES.value:
        require_supplier = False
        require_customer = True
    elif direction == DocumentDirection.PURCHASE.value:
        require_customer = False
        require_supplier = True

    specs: list[tuple[str, str, Any, bool]] = [
        ("invoice_number", "Invoice number", invoice.invoice_number, True),
        ("issue_date", "Issue date", invoice.issue_date, True),
        ("document_direction", "Document direction (sales/purchase)", direction, True),
        ("currency", "Currency", invoice.currency, True),
        ("customer_name", "Customer name", invoice.customer_name, require_customer),
        ("supplier_name", "Supplier name", invoice.supplier_name, require_supplier),
        ("gross_amount", "Gross amount", invoice.gross_amount, True),
        ("net_amount", "Net amount", invoice.net_amount, False),
        ("vat_amount", "VAT amount", invoice.vat_amount, False),
        ("customer_vat", "Customer VAT / ΑΦΜ", invoice.customer_vat, False),
        ("supplier_vat", "Supplier VAT / ΑΦΜ", invoice.supplier_vat, False),
    ]

    fields: list[FieldStatus] = []
    blockers: list[str] = []
    warnings: list[str] = []

    for key, label, value, required in specs:
        present = _present(value)
        if key == "document_direction":
            present = direction in {
                DocumentDirection.SALES.value,
                DocumentDirection.PURCHASE.value,
            }
        c = conf.get(key)
        low = c is not None and c < low_confidence_threshold and present
        fields.append(
            FieldStatus(
                field=key,
                label=label,
                value=value,
                required=required,
                present=present,
                low_confidence=low,
                confidence=c,
            )
        )
        if required and not present:
            blockers.append(f"Missing required field: {label}")
        elif low:
            warnings.append(f"Low confidence: {label} ({c:.0%})")

    if hash_duplicate:
        blockers.append("Identical PDF already exists in the database — cannot approve a hash duplicate")
    if validation_has_blocking_errors:
        # Financial mismatches are warnings for review but net+vat mismatch stays blocking via validator errors
        pass

    # Customer and supplier must be distinct
    if _present(invoice.customer_name) and _present(invoice.supplier_name):
        c = " ".join(str(invoice.customer_name).split()).casefold()
        s = " ".join(str(invoice.supplier_name).split()).casefold()
        if c == s or c in s or s in c:
            blockers.append("Customer and supplier are the same — correct the party names")

    if _present(invoice.customer_vat) and _present(invoice.supplier_vat):
        if str(invoice.customer_vat).replace(" ", "").upper() == str(invoice.supplier_vat).replace(" ", "").upper():
            blockers.append("Customer and supplier VAT / ΑΦΜ are the same — correct the tax IDs")

    # Soft: missing party VAT
    if direction == DocumentDirection.SALES.value and not _present(invoice.customer_vat):
        warnings.append("Customer VAT / ΑΦΜ is missing (recommended)")
    if direction == DocumentDirection.PURCHASE.value and not _present(invoice.supplier_vat):
        warnings.append("Supplier VAT / ΑΦΜ is missing (recommended)")

    can_approve = len(blockers) == 0 and not hash_duplicate
    return ApprovalReadiness(
        can_approve=can_approve,
        blockers=blockers,
        warnings=warnings,
        fields=fields,
    )
