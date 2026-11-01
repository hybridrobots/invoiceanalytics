"""Financial validation and duplicate detection tests."""

from datetime import date
from decimal import Decimal

from invoice_intelligence.database.models import DocumentDirection, Invoice
from invoice_intelligence.validation.invoice_validator import validate_invoice


def test_net_vat_gross_ok() -> None:
    inv = Invoice(
        invoice_number="X1",
        issue_date=date(2024, 1, 1),
        currency="EUR",
        net_amount=Decimal("100"),
        vat_amount=Decimal("24"),
        gross_amount=Decimal("124"),
        document_direction=DocumentDirection.SALES.value,
    )
    result = validate_invoice(inv, settings={"rounding_tolerance": "0.02"})
    assert not any(i.code == "net_vat_gross_mismatch" for i in result.issues)


def test_net_vat_gross_mismatch_flagged() -> None:
    inv = Invoice(
        invoice_number="X2",
        issue_date=date(2024, 1, 1),
        currency="EUR",
        net_amount=Decimal("100"),
        vat_amount=Decimal("24"),
        gross_amount=Decimal("200"),
        document_direction=DocumentDirection.SALES.value,
    )
    result = validate_invoice(inv)
    assert any(i.code == "net_vat_gross_mismatch" for i in result.issues)


def test_overpayment_warning() -> None:
    inv = Invoice(
        invoice_number="X3",
        issue_date=date(2024, 1, 1),
        currency="EUR",
        gross_amount=Decimal("100"),
        amount_paid=Decimal("150"),
        document_direction=DocumentDirection.SALES.value,
    )
    result = validate_invoice(inv)
    assert any(i.code == "overpayment" for i in result.issues)


def test_duplicate_by_hash(tmp_db) -> None:
    first = Invoice(
        invoice_number="DUP-1",
        issue_date=date(2024, 5, 1),
        currency="EUR",
        document_hash="abc123",
        document_direction=DocumentDirection.SALES.value,
        supplier_name="Supplier",
        gross_amount=Decimal("10"),
    )
    tmp_db.insert_invoice(first)
    second = Invoice(
        invoice_number="DUP-1",
        issue_date=date(2024, 5, 1),
        currency="EUR",
        document_hash="abc123",
        document_direction=DocumentDirection.SALES.value,
        supplier_name="Supplier",
        gross_amount=Decimal("10"),
    )
    result = validate_invoice(second, db=tmp_db)
    assert result.is_duplicate
    assert result.hash_duplicate_id is not None


def test_logical_duplicate(tmp_db) -> None:
    first = Invoice(
        invoice_number="L-99",
        issue_date=date(2024, 6, 1),
        currency="EUR",
        document_hash="hash-one",
        document_direction=DocumentDirection.SALES.value,
        supplier_vat="123456789",
        gross_amount=Decimal("10"),
    )
    tmp_db.insert_invoice(first)
    second = Invoice(
        invoice_number="L-99",
        issue_date=date(2024, 6, 1),
        currency="EUR",
        document_hash="hash-two",
        document_direction=DocumentDirection.SALES.value,
        supplier_vat="123456789",
        gross_amount=Decimal("10"),
    )
    result = validate_invoice(second, db=tmp_db)
    assert result.is_duplicate
    assert any(i.code == "logical_duplicate" for i in result.issues)
