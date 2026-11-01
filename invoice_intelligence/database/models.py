"""Invoice domain models with Decimal-safe financial fields."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


class InvoiceType(str, Enum):
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    RECEIPT = "receipt"
    OTHER = "other"


class DocumentDirection(str, Enum):
    SALES = "sales"
    PURCHASE = "purchase"
    UNKNOWN = "unknown"


class PaymentStatus(str, Enum):
    UNKNOWN = "Unknown"
    UNPAID = "Unpaid"
    PARTIALLY_PAID = "Partially Paid"
    PAID = "Paid"


class ApprovalStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


def to_decimal(value: Any) -> Decimal | None:
    """Convert a value to Decimal; return None for empty/invalid."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    # Greek/EU number formats: 1.234,56 or 1,234.56
    cleaned = text.replace("€", "").replace("EUR", "").replace(" ", "").replace("\u00a0", "")
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    # Normalize dotted month forms and collapse spaces around separators
    text = re.sub(r"\s+", " ", text)
    text = text.replace(".", "-") if re.search(r"\d{1,2}\.\s*[A-Za-z]{3}", text) else text
    formats = (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%Y/%m/%d",
        "%d-%b-%Y",
        "%d/%b/%Y",
        "%d %b %Y",
        "%d-%B-%Y",
        "%d/%B/%Y",
        "%d %B %Y",
        "%d-%b-%y",
        "%d/%b/%y",
        "%d %b %y",
        "%b-%d-%Y",
        "%b/%d/%Y",
        "%b %d %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Fallback: extract a day-month-year token from a longer label string
    m = re.search(
        r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"
        r"|\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2}"
        r"|\d{1,2}[/.\-]\s*[A-Za-z]{3,9}[/.\-]\s*\d{2,4}"
        r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})",
        text,
    )
    if m and m.group(1) != text:
        return parse_date(m.group(1))
    return None


def format_date(value: Any) -> str:
    """Display dates as dd/mm/yyyy. Empty string when missing/invalid."""
    parsed = parse_date(value)
    if parsed is None:
        return ""
    return parsed.strftime("%d/%m/%Y")


@dataclass
class LineItem:
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    net_amount: Decimal | None = None
    vat_rate: Decimal | None = None
    vat_amount: Decimal | None = None
    gross_amount: Decimal | None = None


@dataclass
class VatBreakdownRow:
    vat_rate: Decimal | None = None
    net_base: Decimal | None = None
    vat_amount: Decimal | None = None


@dataclass
class PaymentRecord:
    id: int | None = None
    invoice_id: int | None = None
    amount: Decimal | None = None
    payment_date: date | None = None
    payment_method: str | None = None
    notes: str | None = None
    created_at: str | None = None


@dataclass
class Invoice:
    id: int | None = None
    invoice_number: str | None = None
    invoice_type: str = InvoiceType.INVOICE.value
    document_direction: str = DocumentDirection.SALES.value
    issue_date: date | None = None
    due_date: date | None = None
    currency: str = "EUR"
    document_filename: str | None = None
    supplier_name: str | None = None
    supplier_vat: str | None = None
    customer_name: str | None = None
    customer_vat: str | None = None
    net_amount: Decimal | None = None
    vat_amount: Decimal | None = None
    gross_amount: Decimal | None = None
    discount_amount: Decimal | None = None
    other_charges: Decimal | None = None
    withholding_tax: Decimal | None = None
    total_payable: Decimal | None = None
    payment_status: str = PaymentStatus.UNKNOWN.value
    amount_paid: Decimal | None = Decimal("0")
    payment_date: date | None = None
    outstanding_balance: Decimal | None = None
    payment_method: str | None = None
    description: str | None = None
    extraction_confidence: Decimal | None = None
    validation_warnings: str | None = None  # JSON list as text
    original_pdf_path: str | None = None
    document_hash: str | None = None
    approval_status: str = ApprovalStatus.DRAFT.value
    confidence_json: str | None = None  # per-field confidence
    extracted_text_preview: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    line_items: list[LineItem] = field(default_factory=list)
    vat_breakdown: list[VatBreakdownRow] = field(default_factory=list)
    payments: list[PaymentRecord] = field(default_factory=list)
    duplicate_of_id: int | None = None
    is_duplicate_warning: bool = False

    def autofill_gross_from_net_vat(self, tolerance: Decimal | None = None) -> bool:
        """Set gross from net + VAT when missing or inconsistent. Returns True if changed."""
        tol = Decimal("0.02") if tolerance is None else tolerance
        if self.net_amount is None or self.vat_amount is None:
            return False
        expected = self.net_amount + self.vat_amount
        changed = False
        if self.gross_amount is None or abs(self.gross_amount - expected) > tol:
            self.gross_amount = expected
            changed = True
        if self.total_payable is None and self.gross_amount is not None:
            self.total_payable = self.gross_amount
            changed = True
        return changed

    def recalculate_payment_fields(self) -> None:
        """Derive payment status / outstanding from amount_paid and gross."""
        gross = self.gross_amount or self.total_payable or Decimal("0")
        paid = self.amount_paid or Decimal("0")
        # Credit notes: outstanding is relative to absolute gross
        outstanding = gross - paid
        self.outstanding_balance = outstanding
        if paid == 0:
            if self.payment_status == PaymentStatus.UNKNOWN.value and gross == 0:
                self.payment_status = PaymentStatus.UNKNOWN.value
            elif paid == 0:
                self.payment_status = PaymentStatus.UNPAID.value
        elif abs(paid) >= abs(gross) and gross != 0:
            self.payment_status = PaymentStatus.PAID.value
            self.outstanding_balance = Decimal("0")
        else:
            self.payment_status = PaymentStatus.PARTIALLY_PAID.value

    def to_db_row(self) -> dict[str, Any]:
        money_fields = {
            "net_amount",
            "vat_amount",
            "gross_amount",
            "discount_amount",
            "other_charges",
            "withholding_tax",
            "total_payable",
            "amount_paid",
            "outstanding_balance",
            "extraction_confidence",
        }
        row: dict[str, Any] = {}
        for f in fields(self):
            if f.name in {"line_items", "vat_breakdown", "payments", "is_duplicate_warning", "duplicate_of_id"}:
                continue
            value = getattr(self, f.name)
            if f.name in money_fields:
                row[f.name] = decimal_to_str(value) if isinstance(value, Decimal) or value is None else decimal_to_str(to_decimal(value))
            elif isinstance(value, date):
                row[f.name] = value.isoformat()
            else:
                row[f.name] = value
        return row

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> Invoice:
        money_fields = {
            "net_amount",
            "vat_amount",
            "gross_amount",
            "discount_amount",
            "other_charges",
            "withholding_tax",
            "total_payable",
            "amount_paid",
            "outstanding_balance",
            "extraction_confidence",
        }
        kwargs: dict[str, Any] = {}
        valid = {f.name for f in fields(cls)}
        for key, value in row.items():
            if key not in valid:
                continue
            if key in money_fields:
                kwargs[key] = to_decimal(value)
            elif key in {"issue_date", "due_date", "payment_date"}:
                kwargs[key] = parse_date(value)
            else:
                kwargs[key] = value
        return cls(**kwargs)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, Decimal):
                data[key] = decimal_to_str(value)
            elif isinstance(value, date):
                data[key] = value.isoformat()
        return data


@dataclass
class ExtractionResult:
    """Structured output from the extraction pipeline."""

    invoice: Invoice
    confidence: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    raw_text: str = ""
    used_ocr: bool = False
    used_llm: bool = False
    error: str | None = None
