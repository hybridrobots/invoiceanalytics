"""Payment allocation tests."""

from datetime import date
from decimal import Decimal

from invoice_intelligence.database.models import (
    DocumentDirection,
    Invoice,
    PaymentRecord,
    PaymentStatus,
)


def test_payment_allocation_updates_status(tmp_db) -> None:
    inv = Invoice(
        invoice_number="PAY-1",
        issue_date=date(2024, 4, 1),
        due_date=date(2024, 5, 1),
        currency="EUR",
        document_direction=DocumentDirection.SALES.value,
        customer_name="Pay Customer",
        gross_amount=Decimal("200"),
        total_payable=Decimal("200"),
        amount_paid=Decimal("0"),
        document_hash="pay-hash-1",
    )
    inv_id = tmp_db.insert_invoice(inv)
    tmp_db.add_payment(
        PaymentRecord(invoice_id=inv_id, amount=Decimal("50"), payment_date=date(2024, 4, 10), payment_method="transfer")
    )
    loaded = tmp_db.get_invoice(inv_id)
    assert loaded is not None
    assert loaded.amount_paid == Decimal("50")
    assert loaded.payment_status == PaymentStatus.PARTIALLY_PAID.value
    assert loaded.outstanding_balance == Decimal("150")

    tmp_db.add_payment(
        PaymentRecord(invoice_id=inv_id, amount=Decimal("150"), payment_date=date(2024, 4, 20), payment_method="cash")
    )
    loaded = tmp_db.get_invoice(inv_id)
    assert loaded is not None
    assert loaded.payment_status == PaymentStatus.PAID.value
    assert loaded.outstanding_balance == Decimal("0")
    assert len(loaded.payments) == 2
