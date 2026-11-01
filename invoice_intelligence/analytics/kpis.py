"""Sales-first KPI calculations using Decimal arithmetic."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import mean, pstdev
from typing import Any

from invoice_intelligence.database.models import (
    ApprovalStatus,
    DocumentDirection,
    Invoice,
    InvoiceType,
    PaymentStatus,
)

ZERO = Decimal("0")


def _d(value: Decimal | None) -> Decimal:
    return value if value is not None else ZERO


def _signed_revenue(invoice: Invoice) -> tuple[Decimal, Decimal, Decimal]:
    """Return (net, vat, gross) with credit notes as negative."""
    sign = Decimal("-1") if invoice.invoice_type == InvoiceType.CREDIT_NOTE.value else Decimal("1")
    net = _d(invoice.net_amount) * sign
    vat = _d(invoice.vat_amount) * sign
    gross = _d(invoice.gross_amount if invoice.gross_amount is not None else invoice.total_payable) * sign
    return net, vat, gross


def _in_period(d: date | None, start: date | None, end: date | None) -> bool:
    if d is None:
        return False
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


def filter_sales_approved(
    invoices: list[Invoice],
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[Invoice]:
    return [
        inv
        for inv in invoices
        if inv.approval_status == ApprovalStatus.APPROVED.value
        and inv.document_direction == DocumentDirection.SALES.value
        and _in_period(inv.issue_date, start, end)
    ]


@dataclass
class RevenueKpis:
    total_gross: Decimal = ZERO
    total_net: Decimal = ZERO
    total_vat: Decimal = ZERO
    invoice_count: int = 0
    average_invoice_value: Decimal = ZERO
    monthly: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    growth_vs_previous: Decimal | None = None
    previous_period_gross: Decimal = ZERO


@dataclass
class CashflowKpis:
    cash_collected: Decimal = ZERO
    outstanding_receivables: Decimal = ZERO
    partially_paid_count: int = 0
    overdue_receivables: Decimal = ZERO
    overdue_count: int = 0
    average_collection_days: Decimal | None = None
    collection_rate: Decimal | None = None
    monthly_cash: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class CustomerKpis:
    revenue_by_customer: dict[str, Decimal] = field(default_factory=dict)
    share_by_customer: dict[str, Decimal] = field(default_factory=dict)
    avg_invoice_by_customer: dict[str, Decimal] = field(default_factory=dict)
    invoice_count_by_customer: dict[str, int] = field(default_factory=dict)
    top_customer_share: Decimal = ZERO
    top_customer_name: str | None = None
    hhi: Decimal = ZERO  # Herfindahl-Hirschman Index (0-1 scale using shares^2)
    monthly_activity: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class HealthIndicators:
    revenue_growth: Decimal | None = None
    revenue_stability_cv: Decimal | None = None
    concentration_risk: str = "unknown"
    avg_invoice_trend: str = "insufficient_data"
    receivables_ratio: Decimal | None = None
    collection_performance: str = "insufficient_data"
    explanations: dict[str, str] = field(default_factory=dict)


@dataclass
class KpiBundle:
    period_start: date | None
    period_end: date | None
    revenue: RevenueKpis
    cashflow: CashflowKpis
    customers: CustomerKpis
    health: HealthIndicators
    currency: str = "EUR"


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def compute_revenue_kpis(invoices: list[Invoice], start: date | None, end: date | None) -> RevenueKpis:
    sales = filter_sales_approved(invoices, start=start, end=end)
    kpi = RevenueKpis()
    monthly: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"net": ZERO, "vat": ZERO, "gross": ZERO, "count": ZERO})

    for inv in sales:
        net, vat, gross = _signed_revenue(inv)
        kpi.total_net += net
        kpi.total_vat += vat
        kpi.total_gross += gross
        kpi.invoice_count += 1
        if inv.issue_date:
            mk = _month_key(inv.issue_date)
            monthly[mk]["net"] += net
            monthly[mk]["vat"] += vat
            monthly[mk]["gross"] += gross
            monthly[mk]["count"] += Decimal("1")

    kpi.monthly = dict(sorted(monthly.items()))
    if kpi.invoice_count:
        kpi.average_invoice_value = (kpi.total_gross / Decimal(kpi.invoice_count)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    # Previous period of equal length
    if start and end:
        days = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)
        prev_sales = filter_sales_approved(invoices, start=prev_start, end=prev_end)
        prev_gross = ZERO
        for inv in prev_sales:
            _, _, g = _signed_revenue(inv)
            prev_gross += g
        kpi.previous_period_gross = prev_gross
        if prev_gross != 0:
            kpi.growth_vs_previous = ((kpi.total_gross - prev_gross) / abs(prev_gross) * Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        elif kpi.total_gross != 0:
            kpi.growth_vs_previous = None  # cannot compute % from zero base
    return kpi


def compute_cashflow_kpis(
    invoices: list[Invoice],
    payments: list[dict[str, Any]],
    start: date | None,
    end: date | None,
    *,
    as_of: date | None = None,
) -> CashflowKpis:
    """Cash KPIs from confirmed payments only. Outstanding from approved sales invoices."""
    as_of = as_of or date.today()
    kpi = CashflowKpis()
    sales = filter_sales_approved(invoices, start=start, end=end)
    sales_ids = {inv.id for inv in sales if inv.id is not None}

    monthly_cash: dict[str, Decimal] = defaultdict(lambda: ZERO)
    collection_days: list[int] = []

    for pay in payments:
        inv_id = pay.get("invoice_id")
        if inv_id not in sales_ids:
            # Also allow payments tied to sales invoices even if payment date filter differs —
            # for cash collected in period, filter by payment_date
            continue
        amount = Decimal(str(pay.get("amount") or "0"))
        pay_date_raw = pay.get("payment_date")
        from invoice_intelligence.database.models import parse_date

        pay_date = parse_date(pay_date_raw)
        if pay_date and _in_period(pay_date, start, end):
            kpi.cash_collected += amount
            monthly_cash[_month_key(pay_date)] += amount

    # Outstanding / partial / overdue from invoice payment state in period set
    # Use current payment fields on those invoices
    for inv in sales:
        _, _, gross = _signed_revenue(inv)
        outstanding = _d(inv.outstanding_balance)
        if inv.payment_status == PaymentStatus.PARTIALLY_PAID.value:
            kpi.partially_paid_count += 1
        if outstanding > 0 and inv.invoice_type != InvoiceType.CREDIT_NOTE.value:
            kpi.outstanding_receivables += outstanding
            if inv.due_date and inv.due_date < as_of and inv.payment_status != PaymentStatus.PAID.value:
                kpi.overdue_receivables += outstanding
                kpi.overdue_count += 1
        # Collection days for paid invoices
        if (
            inv.payment_status == PaymentStatus.PAID.value
            and inv.issue_date
            and inv.payment_date
        ):
            collection_days.append((inv.payment_date - inv.issue_date).days)

    kpi.monthly_cash = dict(sorted(monthly_cash.items()))
    if collection_days:
        kpi.average_collection_days = Decimal(str(round(mean(collection_days), 1)))

    # Collection rate = cash collected / gross invoiced (period)
    period_gross = ZERO
    for inv in sales:
        _, _, g = _signed_revenue(inv)
        if inv.invoice_type != InvoiceType.CREDIT_NOTE.value:
            period_gross += g
        else:
            period_gross += g  # already negative
    if period_gross != 0:
        kpi.collection_rate = (kpi.cash_collected / abs(period_gross) * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return kpi


def compute_customer_kpis(invoices: list[Invoice], start: date | None, end: date | None) -> CustomerKpis:
    sales = filter_sales_approved(invoices, start=start, end=end)
    kpi = CustomerKpis()
    revenue: dict[str, Decimal] = defaultdict(lambda: ZERO)
    counts: dict[str, int] = defaultdict(int)
    monthly_activity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for inv in sales:
        name = (inv.customer_name or "Unknown customer").strip() or "Unknown customer"
        _, _, gross = _signed_revenue(inv)
        revenue[name] += gross
        counts[name] += 1
        if inv.issue_date:
            monthly_activity[_month_key(inv.issue_date)][name] += 1

    total = sum(revenue.values(), ZERO)
    shares: dict[str, Decimal] = {}
    avgs: dict[str, Decimal] = {}
    for name, rev in revenue.items():
        shares[name] = (
            (rev / total * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if total
            else ZERO
        )
        avgs[name] = (
            (rev / Decimal(counts[name])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if counts[name]
            else ZERO
        )

    kpi.revenue_by_customer = dict(sorted(revenue.items(), key=lambda x: x[1], reverse=True))
    kpi.share_by_customer = {k: shares[k] for k in kpi.revenue_by_customer}
    kpi.avg_invoice_by_customer = avgs
    kpi.invoice_count_by_customer = dict(counts)
    kpi.monthly_activity = {m: dict(v) for m, v in sorted(monthly_activity.items())}

    if kpi.revenue_by_customer:
        top_name, top_rev = next(iter(kpi.revenue_by_customer.items()))
        kpi.top_customer_name = top_name
        kpi.top_customer_share = shares.get(top_name, ZERO)
        # HHI on 0-1 share scale
        hhi = ZERO
        for name, rev in revenue.items():
            share = (rev / total) if total else ZERO
            hhi += share * share
        kpi.hhi = hhi.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return kpi


def compute_health(
    revenue: RevenueKpis,
    cashflow: CashflowKpis,
    customers: CustomerKpis,
) -> HealthIndicators:
    health = HealthIndicators()
    health.revenue_growth = revenue.growth_vs_previous
    health.explanations["revenue_growth"] = (
        f"Gross invoiced revenue changed by {revenue.growth_vs_previous}% versus the previous period of equal length."
        if revenue.growth_vs_previous is not None
        else "Insufficient previous-period data to compute revenue growth percentage."
    )

    monthly_gross = [v["gross"] for v in revenue.monthly.values()]
    if len(monthly_gross) >= 2:
        vals = [float(x) for x in monthly_gross]
        avg = mean(vals)
        if avg != 0:
            cv = pstdev(vals) / abs(avg)
            health.revenue_stability_cv = Decimal(str(round(cv, 4)))
            health.explanations["revenue_stability"] = (
                f"Coefficient of variation of monthly gross revenue is {health.revenue_stability_cv} "
                "(lower means more stable)."
            )
        else:
            health.explanations["revenue_stability"] = "Average monthly revenue is zero; stability not computed."
    else:
        health.explanations["revenue_stability"] = "Need at least two months of data to assess revenue stability."

    share = customers.top_customer_share
    if not customers.revenue_by_customer:
        health.concentration_risk = "unknown"
        health.explanations["concentration"] = "No customer revenue data available."
    elif share >= Decimal("70"):
        health.concentration_risk = "high"
    elif share >= Decimal("40"):
        health.concentration_risk = "moderate"
    else:
        health.concentration_risk = "low"
    if customers.top_customer_name:
        health.explanations["concentration"] = (
            f"Largest customer '{customers.top_customer_name}' represents {share}% of invoiced revenue "
            f"(risk: {health.concentration_risk})."
        )

    # Average invoice trend: compare first half vs second half of monthly averages
    months = list(revenue.monthly.keys())
    if len(months) >= 3:
        mid = len(months) // 2
        first = [revenue.monthly[m]["gross"] / revenue.monthly[m]["count"] for m in months[:mid] if revenue.monthly[m]["count"]]
        second = [revenue.monthly[m]["gross"] / revenue.monthly[m]["count"] for m in months[mid:] if revenue.monthly[m]["count"]]
        if first and second:
            f_avg = sum(first, ZERO) / Decimal(len(first))
            s_avg = sum(second, ZERO) / Decimal(len(second))
            if s_avg > f_avg * Decimal("1.05"):
                health.avg_invoice_trend = "increasing"
            elif s_avg < f_avg * Decimal("0.95"):
                health.avg_invoice_trend = "declining"
            else:
                health.avg_invoice_trend = "stable"
            health.explanations["avg_invoice_trend"] = (
                f"Average invoice value trend across the period: {health.avg_invoice_trend}."
            )
    else:
        health.explanations["avg_invoice_trend"] = "Need at least three months to assess average invoice value trend."

    if revenue.total_gross != 0:
        health.receivables_ratio = (
            cashflow.outstanding_receivables / abs(revenue.total_gross) * Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        health.explanations["receivables_ratio"] = (
            f"Outstanding receivables are {health.receivables_ratio}% of period invoiced gross revenue."
        )
    else:
        health.explanations["receivables_ratio"] = "No invoiced revenue in period; receivables ratio not computed."

    if cashflow.collection_rate is not None:
        if cashflow.collection_rate >= Decimal("90"):
            health.collection_performance = "strong"
        elif cashflow.collection_rate >= Decimal("60"):
            health.collection_performance = "moderate"
        else:
            health.collection_performance = "weak"
        health.explanations["collection_performance"] = (
            f"Collection rate is {cashflow.collection_rate}% of period invoiced gross "
            f"(performance: {health.collection_performance}). Based on confirmed payments only."
        )
    else:
        health.explanations["collection_performance"] = (
            "Insufficient payment data to assess collection performance."
        )

    return health


def compute_kpis(
    invoices: list[Invoice],
    payments: list[dict[str, Any]],
    *,
    start: date | None = None,
    end: date | None = None,
    currency: str = "EUR",
) -> KpiBundle:
    revenue = compute_revenue_kpis(invoices, start, end)
    cashflow = compute_cashflow_kpis(invoices, payments, start, end)
    customers = compute_customer_kpis(invoices, start, end)
    health = compute_health(revenue, cashflow, customers)
    return KpiBundle(
        period_start=start,
        period_end=end,
        revenue=revenue,
        cashflow=cashflow,
        customers=customers,
        health=health,
        currency=currency,
    )
