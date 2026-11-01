"""Pytest fixtures — synthetic data only; never touch the live app database."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_intelligence.database.db import Database
from invoice_intelligence.database.models import (
    ApprovalStatus,
    DocumentDirection,
    Invoice,
    InvoiceType,
    PaymentStatus,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test_invoices.db")


@pytest.fixture()
def sample_text() -> str:
    return (FIXTURES / "sample_invoice_el.txt").read_text(encoding="utf-8")


@pytest.fixture()
def credit_text() -> str:
    return (FIXTURES / "sample_credit_note_el.txt").read_text(encoding="utf-8")


@pytest.fixture()
def approved_sales_invoices() -> list[Invoice]:
    return [
        Invoice(
            id=1,
            invoice_number="A-1",
            invoice_type=InvoiceType.INVOICE.value,
            document_direction=DocumentDirection.SALES.value,
            issue_date=date(2024, 1, 15),
            due_date=date(2024, 2, 15),
            currency="EUR",
            customer_name="Customer Alpha",
            net_amount=Decimal("1000"),
            vat_amount=Decimal("240"),
            gross_amount=Decimal("1240"),
            total_payable=Decimal("1240"),
            amount_paid=Decimal("1240"),
            payment_date=date(2024, 2, 10),
            payment_status=PaymentStatus.PAID.value,
            outstanding_balance=Decimal("0"),
            approval_status=ApprovalStatus.APPROVED.value,
        ),
        Invoice(
            id=2,
            invoice_number="A-2",
            invoice_type=InvoiceType.INVOICE.value,
            document_direction=DocumentDirection.SALES.value,
            issue_date=date(2024, 2, 15),
            due_date=date(2024, 3, 15),
            currency="EUR",
            customer_name="Customer Alpha",
            net_amount=Decimal("500"),
            vat_amount=Decimal("120"),
            gross_amount=Decimal("620"),
            total_payable=Decimal("620"),
            amount_paid=Decimal("0"),
            payment_status=PaymentStatus.UNPAID.value,
            outstanding_balance=Decimal("620"),
            approval_status=ApprovalStatus.APPROVED.value,
        ),
        Invoice(
            id=3,
            invoice_number="B-1",
            invoice_type=InvoiceType.INVOICE.value,
            document_direction=DocumentDirection.SALES.value,
            issue_date=date(2024, 2, 20),
            due_date=date(2024, 1, 1),  # overdue
            currency="EUR",
            customer_name="Customer Beta",
            net_amount=Decimal("200"),
            vat_amount=Decimal("48"),
            gross_amount=Decimal("248"),
            total_payable=Decimal("248"),
            amount_paid=Decimal("100"),
            payment_status=PaymentStatus.PARTIALLY_PAID.value,
            outstanding_balance=Decimal("148"),
            approval_status=ApprovalStatus.APPROVED.value,
        ),
        Invoice(
            id=4,
            invoice_number="C-1",
            invoice_type=InvoiceType.CREDIT_NOTE.value,
            document_direction=DocumentDirection.SALES.value,
            issue_date=date(2024, 2, 25),
            currency="EUR",
            customer_name="Customer Alpha",
            net_amount=Decimal("50"),
            vat_amount=Decimal("12"),
            gross_amount=Decimal("62"),
            total_payable=Decimal("62"),
            amount_paid=Decimal("0"),
            payment_status=PaymentStatus.UNPAID.value,
            outstanding_balance=Decimal("62"),
            approval_status=ApprovalStatus.APPROVED.value,
        ),
    ]


# PDF helper lives in tests.helpers.make_simple_pdf
