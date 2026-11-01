"""KPI and insights tests."""

from datetime import date
from decimal import Decimal

from invoice_intelligence.analytics.insights import generate_insights
from invoice_intelligence.analytics.kpis import compute_kpis
from invoice_intelligence.database.models import Invoice


def test_revenue_and_credit_note(approved_sales_invoices: list[Invoice]) -> None:
    payments = [
        {
            "id": 1,
            "invoice_id": 1,
            "amount": "1240",
            "payment_date": "2024-02-10",
            "invoice_number": "A-1",
            "customer_name": "Customer Alpha",
            "currency": "EUR",
        },
        {
            "id": 2,
            "invoice_id": 3,
            "amount": "100",
            "payment_date": "2024-02-22",
            "invoice_number": "B-1",
            "customer_name": "Customer Beta",
            "currency": "EUR",
        },
    ]
    bundle = compute_kpis(
        approved_sales_invoices,
        payments,
        start=date(2024, 1, 1),
        end=date(2024, 3, 31),
        currency="EUR",
    )
    # 1240 + 620 + 248 - 62 = 2046
    assert bundle.revenue.total_gross == Decimal("2046")
    assert bundle.revenue.invoice_count == 4
    assert bundle.cashflow.cash_collected == Decimal("1340")
    assert bundle.cashflow.outstanding_receivables == Decimal("768")  # 620 + 148
    assert bundle.customers.top_customer_name == "Customer Alpha"
    assert bundle.customers.top_customer_share > Decimal("50")

    insights = generate_insights(bundle)
    assert insights
    assert any("concentration" in i.title.lower() or "customer" in i.title.lower() for i in insights)


def test_insufficient_data_insight() -> None:
    bundle = compute_kpis([], [], start=date(2024, 1, 1), end=date(2024, 1, 31))
    insights = generate_insights(bundle)
    assert len(insights) == 1
    assert "insufficient" in insights[0].title.lower()
