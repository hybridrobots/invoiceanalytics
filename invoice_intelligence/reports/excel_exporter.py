"""Professionally formatted Excel workbook export."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from invoice_intelligence.analytics.kpis import KpiBundle, filter_sales_approved
from invoice_intelligence.database.models import Invoice, decimal_to_str, format_date


HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)
THIN = Border(
    left=Side(style="thin", color="D0D0D0"),
    right=Side(style="thin", color="D0D0D0"),
    top=Side(style="thin", color="D0D0D0"),
    bottom=Side(style="thin", color="D0D0D0"),
)
MONEY_FORMAT = '#,##0.00'
DATE_FORMAT = "DD/MM/YYYY"
WARN_FILL = PatternFill("solid", fgColor="FCE4D6")
BAD_FILL = PatternFill("solid", fgColor="FFC7CE")


def _style_header(ws, cols: int) -> None:
    for col in range(1, cols + 1):
        cell = ws.cell(1, col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border = THIN
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(cols)}1"


def _autosize(ws, min_width: int = 10, max_width: int = 40) -> None:
    for col_cells in ws.columns:
        letter = get_column_letter(col_cells[0].column)
        length = 0
        for cell in col_cells:
            if cell.value is not None:
                length = max(length, len(str(cell.value)))
        ws.column_dimensions[letter].width = max(min_width, min(max_width, length + 2))


def _money(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def export_excel(
    path: str | Path,
    *,
    invoices: list[Invoice],
    payments: list[dict[str, Any]],
    kpis: KpiBundle,
    only_approved_sales_in_summaries: bool = True,
) -> Path:
    """Generate a 5-sheet workbook useful without the application."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # --- Sheet 1: Invoice Register ---
    ws1 = wb.active
    ws1.title = "Invoice Register"
    headers1 = [
        "ID",
        "Invoice Number",
        "Type",
        "Direction",
        "Issue Date",
        "Due Date",
        "Currency",
        "Supplier",
        "Supplier VAT",
        "Customer",
        "Customer VAT",
        "Net",
        "VAT",
        "Gross",
        "Discount",
        "Other Charges",
        "Withholding",
        "Total Payable",
        "Payment Status",
        "Amount Paid",
        "Outstanding",
        "Payment Date",
        "Approval",
        "Filename",
        "Confidence",
        "Warnings",
    ]
    ws1.append(headers1)
    source = (
        filter_sales_approved(invoices, start=kpis.period_start, end=kpis.period_end)
        if only_approved_sales_in_summaries
        else invoices
    )
    # Register includes all invoices passed in (caller decides filter); summaries use KPIs
    for inv in invoices:
        ws1.append(
            [
                inv.id,
                inv.invoice_number,
                inv.invoice_type,
                inv.document_direction,
                inv.issue_date,
                inv.due_date,
                inv.currency,
                inv.supplier_name,
                inv.supplier_vat,
                inv.customer_name,
                inv.customer_vat,
                _money(inv.net_amount),
                _money(inv.vat_amount),
                _money(inv.gross_amount),
                _money(inv.discount_amount),
                _money(inv.other_charges),
                _money(inv.withholding_tax),
                _money(inv.total_payable),
                inv.payment_status,
                _money(inv.amount_paid),
                _money(inv.outstanding_balance),
                inv.payment_date,
                inv.approval_status,
                inv.document_filename,
                _money(inv.extraction_confidence),
                inv.validation_warnings,
            ]
        )
    _style_header(ws1, len(headers1))
    for row in ws1.iter_rows(min_row=2, max_row=ws1.max_row, min_col=12, max_col=21):
        for cell in row:
            if cell.column in {12, 13, 14, 15, 16, 17, 18, 20, 21}:
                cell.number_format = MONEY_FORMAT
    for row in ws1.iter_rows(min_row=2, max_row=ws1.max_row, min_col=5, max_col=6):
        for cell in row:
            cell.number_format = DATE_FORMAT
    for row in ws1.iter_rows(min_row=2, max_row=ws1.max_row, min_col=22, max_col=22):
        for cell in row:
            cell.number_format = DATE_FORMAT
    if ws1.max_row >= 2:
        outstanding_col = get_column_letter(21)
        ws1.conditional_formatting.add(
            f"{outstanding_col}2:{outstanding_col}{ws1.max_row}",
            CellIsRule(operator="greaterThan", formula=["0"], fill=WARN_FILL),
        )
        status_col = get_column_letter(19)
        ws1.conditional_formatting.add(
            f"{status_col}2:{status_col}{ws1.max_row}",
            FormulaRule(formula=[f'ISNUMBER(SEARCH("Unpaid",{status_col}2))'], fill=BAD_FILL),
        )
    _autosize(ws1)

    # --- Sheet 2: Monthly Summary ---
    ws2 = wb.create_sheet("Monthly Summary")
    headers2 = [
        "Month",
        "Net Revenue",
        "VAT",
        "Gross Invoiced",
        "Invoice Count",
        "Cash Collected",
        "Outstanding (period end estimate)",
    ]
    ws2.append(headers2)
    months = sorted(set(list(kpis.revenue.monthly.keys()) + list(kpis.cashflow.monthly_cash.keys())))
    for month in months:
        m = kpis.revenue.monthly.get(month, {})
        ws2.append(
            [
                month,
                _money(m.get("net")),
                _money(m.get("vat")),
                _money(m.get("gross")),
                int(m.get("count") or 0),
                _money(kpis.cashflow.monthly_cash.get(month)),
                None,
            ]
        )
    # Totals row
    if months:
        last = ws2.max_row
        ws2.append(
            [
                "TOTAL",
                f"=SUM(B2:B{last})",
                f"=SUM(C2:C{last})",
                f"=SUM(D2:D{last})",
                f"=SUM(E2:E{last})",
                f"=SUM(F2:F{last})",
                _money(kpis.cashflow.outstanding_receivables),
            ]
        )
        for col in range(2, 7):
            ws2.cell(ws2.max_row, col).font = Font(bold=True)
            if col != 5:
                ws2.cell(ws2.max_row, col).number_format = MONEY_FORMAT
    _style_header(ws2, len(headers2))
    for row in ws2.iter_rows(min_row=2, max_row=max(2, ws2.max_row), min_col=2, max_col=7):
        for cell in row:
            if cell.column != 5:
                cell.number_format = MONEY_FORMAT
    _autosize(ws2)

    # --- Sheet 3: Customer Analysis ---
    ws3 = wb.create_sheet("Customer Analysis")
    headers3 = ["Customer", "Revenue", "Share %", "Invoice Count", "Avg Invoice Value"]
    ws3.append(headers3)
    for name, rev in kpis.customers.revenue_by_customer.items():
        ws3.append(
            [
                name,
                _money(rev),
                _money(kpis.customers.share_by_customer.get(name)),
                kpis.customers.invoice_count_by_customer.get(name, 0),
                _money(kpis.customers.avg_invoice_by_customer.get(name)),
            ]
        )
    _style_header(ws3, len(headers3))
    for row in ws3.iter_rows(min_row=2, max_row=max(2, ws3.max_row), min_col=2, max_col=5):
        for cell in row:
            if cell.column in {2, 3, 5}:
                cell.number_format = MONEY_FORMAT
    if ws3.max_row >= 2:
        ws3.conditional_formatting.add(
            f"C2:C{ws3.max_row}",
            CellIsRule(operator="greaterThanOrEqual", formula=["70"], fill=BAD_FILL),
        )
        ws3.conditional_formatting.add(
            f"C2:C{ws3.max_row}",
            CellIsRule(operator="between", formula=["40", "69.99"], fill=WARN_FILL),
        )
    _autosize(ws3)

    # --- Sheet 4: Payment Register ---
    ws4 = wb.create_sheet("Payment Register")
    headers4 = [
        "Payment ID",
        "Invoice ID",
        "Invoice Number",
        "Customer",
        "Supplier",
        "Amount",
        "Currency",
        "Payment Date",
        "Method",
        "Notes",
    ]
    ws4.append(headers4)
    for p in payments:
        ws4.append(
            [
                p.get("id"),
                p.get("invoice_id"),
                p.get("invoice_number"),
                p.get("customer_name"),
                p.get("supplier_name"),
                float(p.get("amount") or 0),
                p.get("currency"),
                p.get("payment_date"),
                p.get("payment_method"),
                p.get("notes"),
            ]
        )
    _style_header(ws4, len(headers4))
    for row in ws4.iter_rows(min_row=2, max_row=max(2, ws4.max_row), min_col=6, max_col=6):
        for cell in row:
            cell.number_format = MONEY_FORMAT
    for row in ws4.iter_rows(min_row=2, max_row=max(2, ws4.max_row), min_col=8, max_col=8):
        for cell in row:
            cell.number_format = DATE_FORMAT
    _autosize(ws4)

    # --- Sheet 5: KPI Summary ---
    ws5 = wb.create_sheet("KPI Summary")
    ws5.append(["Indicator", "Value", "Unit / Note", "Explanation"])
    rows = [
        ("Report generated", datetime.now().isoformat(timespec="seconds"), "", ""),
        (
            "Period start",
            format_date(kpis.period_start),
            "",
            "",
        ),
        ("Period end", format_date(kpis.period_end), "", ""),
        ("Total invoiced revenue (gross)", _money(kpis.revenue.total_gross), kpis.currency, "Approved sales only"),
        ("Revenue excluding VAT (net)", _money(kpis.revenue.total_net), kpis.currency, "Approved sales only"),
        ("Total VAT charged", _money(kpis.revenue.total_vat), kpis.currency, ""),
        ("Number of invoices", kpis.revenue.invoice_count, "count", ""),
        ("Average invoice value", _money(kpis.revenue.average_invoice_value), kpis.currency, ""),
        (
            "Revenue growth vs previous period",
            _money(kpis.revenue.growth_vs_previous),
            "%",
            kpis.health.explanations.get("revenue_growth", ""),
        ),
        ("Total cash collected", _money(kpis.cashflow.cash_collected), kpis.currency, "From payment records only"),
        ("Outstanding receivables", _money(kpis.cashflow.outstanding_receivables), kpis.currency, ""),
        ("Partially paid invoices", kpis.cashflow.partially_paid_count, "count", ""),
        ("Overdue receivables", _money(kpis.cashflow.overdue_receivables), kpis.currency, ""),
        (
            "Average collection days",
            _money(kpis.cashflow.average_collection_days),
            "days",
            "Where payment dates exist",
        ),
        (
            "Collection rate",
            _money(kpis.cashflow.collection_rate),
            "%",
            kpis.health.explanations.get("collection_performance", ""),
        ),
        (
            "Top customer share",
            _money(kpis.customers.top_customer_share),
            f"% ({kpis.customers.top_customer_name or 'n/a'})",
            kpis.health.explanations.get("concentration", ""),
        ),
        (
            "Customer concentration risk",
            kpis.health.concentration_risk,
            "",
            kpis.health.explanations.get("concentration", ""),
        ),
        (
            "Receivables ratio",
            _money(kpis.health.receivables_ratio),
            "% of gross",
            kpis.health.explanations.get("receivables_ratio", ""),
        ),
        (
            "Revenue stability (CV)",
            _money(kpis.health.revenue_stability_cv),
            "ratio",
            kpis.health.explanations.get("revenue_stability", ""),
        ),
        (
            "Average invoice trend",
            kpis.health.avg_invoice_trend,
            "",
            kpis.health.explanations.get("avg_invoice_trend", ""),
        ),
    ]
    for r in rows:
        ws5.append(list(r))
    _style_header(ws5, 4)
    _autosize(ws5, max_width=60)

    wb.save(out)
    return out
