"""Decimal conversion and model helpers."""

from decimal import Decimal

from datetime import date

from invoice_intelligence.database.models import format_date, parse_date, to_decimal


def test_greek_decimal_formats() -> None:
    assert to_decimal("1.000,00") == Decimal("1000.00")
    assert to_decimal("1,240.50") == Decimal("1240.50")
    assert to_decimal("240,00") == Decimal("240.00")
    assert to_decimal("€ 1.240,00") == Decimal("1240.00")
    assert to_decimal(None) is None
    assert to_decimal("") is None


def test_parse_greek_dates() -> None:
    assert parse_date("15/03/2024").isoformat() == "2024-03-15"
    assert parse_date("15-03-2024").isoformat() == "2024-03-15"
    assert parse_date("15.03.2024").isoformat() == "2024-03-15"
    assert parse_date("2024-03-15").isoformat() == "2024-03-15"
    assert parse_date("01-Sep-2022").isoformat() == "2022-09-01"
    assert parse_date("1/Sep/2022").isoformat() == "2022-09-01"
    assert parse_date("01 September 2022").isoformat() == "2022-09-01"
    assert parse_date("Ημερ/νία - Date 01-Sep-2022").isoformat() == "2022-09-01"


def test_format_date_dd_mm_yyyy() -> None:
    assert format_date(date(2024, 3, 15)) == "15/03/2024"
    assert format_date("2024-03-15") == "15/03/2024"
    assert format_date(None) == ""
    assert format_date("") == ""


def test_autofill_gross_from_net_vat() -> None:
    from invoice_intelligence.database.models import Invoice

    inv = Invoice(net_amount=Decimal("299.00"), vat_amount=Decimal("71.76"))
    assert inv.autofill_gross_from_net_vat() is True
    assert inv.gross_amount == Decimal("370.76")
    assert inv.total_payable == Decimal("370.76")
    assert inv.autofill_gross_from_net_vat() is False

    bad = Invoice(
        net_amount=Decimal("100"),
        vat_amount=Decimal("24"),
        gross_amount=Decimal("999"),
        total_payable=Decimal("999"),
    )
    assert bad.autofill_gross_from_net_vat() is True
    assert bad.gross_amount == Decimal("124")
    assert bad.total_payable == Decimal("999")
