"""Financial and structural validation for extracted invoices."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from invoice_intelligence.config import KNOWN_CURRENCIES
from invoice_intelligence.database.db import Database
from invoice_intelligence.database.models import (
    DocumentDirection,
    ExtractionResult,
    Invoice,
    InvoiceType,
    to_decimal,
)


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: str = "warning"  # info | warning | error
    field: str | None = None


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)
    is_duplicate: bool = False
    duplicate_invoice_ids: list[int] = field(default_factory=list)
    hash_duplicate_id: int | None = None

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def messages(self) -> list[str]:
        return [i.message for i in self.issues]


def _tol(settings: dict[str, Any] | None) -> Decimal:
    raw = (settings or {}).get("rounding_tolerance", "0.02")
    return to_decimal(raw) or Decimal("0.02")


def validate_invoice(
    invoice: Invoice,
    *,
    db: Database | None = None,
    settings: dict[str, Any] | None = None,
    exclude_id: int | None = None,
) -> ValidationResult:
    """Run financial and duplicate checks. Flags inconsistencies; does not auto-correct."""
    result = ValidationResult()
    tolerance = _tol(settings)

    if not invoice.invoice_number:
        result.issues.append(
            ValidationIssue("missing_invoice_number", "Invoice number is missing", "error", "invoice_number")
        )
    if not invoice.issue_date:
        result.issues.append(
            ValidationIssue("missing_issue_date", "Issue date is missing", "error", "issue_date")
        )

    currency = (invoice.currency or "").upper()
    if currency and currency not in KNOWN_CURRENCIES:
        result.issues.append(
            ValidationIssue(
                "unknown_currency",
                f"Currency '{invoice.currency}' is not recognized",
                "warning",
                "currency",
            )
        )
    elif not currency:
        result.issues.append(
            ValidationIssue("missing_currency", "Currency is missing", "warning", "currency")
        )

    net = invoice.net_amount
    vat = invoice.vat_amount
    gross = invoice.gross_amount
    if net is not None and vat is not None and gross is not None:
        expected = net + vat
        if abs(expected - gross) > tolerance:
            result.issues.append(
                ValidationIssue(
                    "net_vat_gross_mismatch",
                    f"Net ({net}) + VAT ({vat}) = {expected} does not equal gross ({gross}) "
                    f"within tolerance {tolerance}",
                    "error",
                    "gross_amount",
                )
            )

    total = invoice.total_payable
    if gross is not None and total is not None:
        adjustments = Decimal("0")
        if invoice.other_charges is not None:
            adjustments += invoice.other_charges
        if invoice.discount_amount is not None:
            adjustments -= invoice.discount_amount
        if invoice.withholding_tax is not None:
            adjustments -= invoice.withholding_tax
        expected_payable = gross + adjustments
        if abs(expected_payable - total) > tolerance:
            result.issues.append(
                ValidationIssue(
                    "payable_mismatch",
                    f"Gross adjusted for charges/discounts/withholding ({expected_payable}) "
                    f"does not equal total payable ({total})",
                    "warning",
                    "total_payable",
                )
            )

    paid = invoice.amount_paid or Decimal("0")
    basis = gross if gross is not None else total
    if basis is not None and abs(paid) > abs(basis) + tolerance:
        result.issues.append(
            ValidationIssue(
                "overpayment",
                f"Amount paid ({paid}) exceeds gross/payable ({basis})",
                "warning",
                "amount_paid",
            )
        )

    if invoice.invoice_type == InvoiceType.CREDIT_NOTE.value:
        result.issues.append(
            ValidationIssue(
                "credit_note",
                "Credit note: revenue KPIs will treat this as a reduction when approved",
                "info",
                "invoice_type",
            )
        )

    if invoice.document_direction == DocumentDirection.UNKNOWN.value:
        result.issues.append(
            ValidationIssue(
                "unknown_direction",
                "Document direction (sales/purchase) is unknown — set it before approving for reports",
                "warning",
                "document_direction",
            )
        )

    # Duplicate checks
    if db is not None:
        if invoice.document_hash:
            existing = db.find_by_hash(invoice.document_hash)
            if existing and existing.id and existing.id != exclude_id:
                result.is_duplicate = True
                result.hash_duplicate_id = existing.id
                result.duplicate_invoice_ids.append(existing.id)
                result.issues.append(
                    ValidationIssue(
                        "hash_duplicate",
                        f"Identical PDF already imported (invoice id {existing.id})",
                        "error",
                        "document_hash",
                    )
                )

        party_key = invoice.supplier_vat or invoice.customer_vat or invoice.supplier_name or invoice.customer_name
        logical = db.find_duplicates(
            invoice.invoice_number,
            party_key,
            invoice.issue_date,
            invoice.document_direction or DocumentDirection.UNKNOWN.value,
            exclude_id=exclude_id,
        )
        for dup in logical:
            if dup.id and dup.id not in result.duplicate_invoice_ids:
                if result.hash_duplicate_id and dup.id == result.hash_duplicate_id:
                    continue
                # Skip if same hash already flagged
                if invoice.document_hash and dup.document_hash == invoice.document_hash:
                    continue
                result.is_duplicate = True
                result.duplicate_invoice_ids.append(dup.id)
                result.issues.append(
                    ValidationIssue(
                        "logical_duplicate",
                        f"Possible duplicate of invoice id {dup.id} "
                        f"(number={dup.invoice_number}, date={dup.issue_date})",
                        "warning",
                        "invoice_number",
                    )
                )

    return result


def validate_extraction(
    extraction: ExtractionResult,
    *,
    db: Database | None = None,
    settings: dict[str, Any] | None = None,
) -> ValidationResult:
    result = validate_invoice(extraction.invoice, db=db, settings=settings)
    for w in extraction.warnings:
        result.issues.append(ValidationIssue("extraction", w, "warning"))
    if extraction.error:
        result.issues.append(ValidationIssue("extraction_error", extraction.error, "error"))
    # Attach summary onto invoice
    extraction.invoice.validation_warnings = _issues_to_json(result)
    extraction.invoice.is_duplicate_warning = result.is_duplicate
    if result.duplicate_invoice_ids:
        extraction.invoice.duplicate_of_id = result.duplicate_invoice_ids[0]
    return result


def _issues_to_json(result: ValidationResult) -> str:
    import json

    return json.dumps(
        [{"code": i.code, "message": i.message, "severity": i.severity, "field": i.field} for i in result.issues],
        ensure_ascii=False,
    )


def apply_validation_to_invoice(invoice: Invoice, validation: ValidationResult) -> Invoice:
    invoice.validation_warnings = _issues_to_json(validation)
    invoice.is_duplicate_warning = validation.is_duplicate
    if validation.duplicate_invoice_ids:
        invoice.duplicate_of_id = validation.duplicate_invoice_ids[0]
    return invoice
