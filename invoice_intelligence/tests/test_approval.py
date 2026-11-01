"""Approval gate tests."""

from datetime import date
from decimal import Decimal

from invoice_intelligence.database.models import DocumentDirection, Invoice
from invoice_intelligence.validation.approval import evaluate_approval


def test_sales_requires_customer() -> None:
    inv = Invoice(
        invoice_number="A-1",
        issue_date=date(2024, 1, 1),
        document_direction=DocumentDirection.SALES.value,
        currency="EUR",
        gross_amount=Decimal("100"),
        customer_name=None,
    )
    ready = evaluate_approval(inv)
    assert not ready.can_approve
    assert any("Customer" in b for b in ready.blockers)


def test_sales_ready_when_complete() -> None:
    inv = Invoice(
        invoice_number="A-1",
        issue_date=date(2024, 1, 1),
        document_direction=DocumentDirection.SALES.value,
        currency="EUR",
        gross_amount=Decimal("100"),
        customer_name="Customer Alpha",
    )
    ready = evaluate_approval(inv)
    assert ready.can_approve


def test_unknown_direction_blocks() -> None:
    inv = Invoice(
        invoice_number="A-1",
        issue_date=date(2024, 1, 1),
        document_direction=DocumentDirection.UNKNOWN.value,
        currency="EUR",
        gross_amount=Decimal("100"),
        customer_name="X",
        supplier_name="Y",
    )
    ready = evaluate_approval(inv)
    assert not ready.can_approve
