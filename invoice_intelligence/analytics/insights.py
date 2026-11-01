"""Deterministic business insights derived from KPI calculations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from invoice_intelligence.analytics.kpis import KpiBundle
from invoice_intelligence.database.models import format_date


@dataclass
class Insight:
    title: str
    explanation: str
    metric: str
    period: str
    severity: str  # info | positive | warning | critical

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _period_label(bundle: KpiBundle) -> str:
    if bundle.period_start and bundle.period_end:
        return f"{format_date(bundle.period_start)} → {format_date(bundle.period_end)}"
    if bundle.period_start:
        return f"from {format_date(bundle.period_start)}"
    if bundle.period_end:
        return f"until {format_date(bundle.period_end)}"
    return "all available data"


def generate_insights(bundle: KpiBundle) -> list[Insight]:
    """Rule-based insights from actual metrics. No motivational filler."""
    insights: list[Insight] = []
    period = _period_label(bundle)
    rev = bundle.revenue
    cash = bundle.cashflow
    cust = bundle.customers
    health = bundle.health

    if rev.invoice_count == 0:
        insights.append(
            Insight(
                title="Insufficient invoice data",
                explanation=(
                    "No approved sales invoices were found for the selected period. "
                    "Import invoices, set direction to sales, and approve them to generate analytics."
                ),
                metric="invoice_count=0",
                period=period,
                severity="info",
            )
        )
        return insights

    if rev.growth_vs_previous is not None:
        growth = rev.growth_vs_previous
        direction = "increased" if growth >= 0 else "decreased"
        severity = "positive" if growth >= 0 else "warning"
        insights.append(
            Insight(
                title=f"Revenue {direction} versus previous period",
                explanation=(
                    f"Invoiced gross revenue {direction} by {abs(growth)}% compared with the previous "
                    f"period of equal length (previous gross: {rev.previous_period_gross} {bundle.currency})."
                ),
                metric=f"growth_vs_previous={growth}%",
                period=period,
                severity=severity,
            )
        )
    elif rev.previous_period_gross == 0 and rev.total_gross != 0:
        insights.append(
            Insight(
                title="No comparable previous-period revenue",
                explanation=(
                    "There is revenue in the current period but none in the immediately preceding "
                    "window of equal length, so a percentage growth rate is not computed."
                ),
                metric=f"current_gross={rev.total_gross}",
                period=period,
                severity="info",
            )
        )

    if cust.top_customer_name and cust.top_customer_share >= Decimal("40"):
        severity = "critical" if cust.top_customer_share >= Decimal("70") else "warning"
        insights.append(
            Insight(
                title="Customer concentration",
                explanation=(
                    f"Customer '{cust.top_customer_name}' represents {cust.top_customer_share}% of "
                    f"total invoiced revenue. Dependence on a single customer increases revenue risk."
                ),
                metric=f"top_customer_share={cust.top_customer_share}%",
                period=period,
                severity=severity,
            )
        )

    if health.avg_invoice_trend == "declining":
        insights.append(
            Insight(
                title="Average invoice value declining",
                explanation=(
                    "Average invoice value is lower in the later part of the period than in the earlier part "
                    f"(overall average: {rev.average_invoice_value} {bundle.currency})."
                ),
                metric=f"avg_invoice={rev.average_invoice_value}; trend=declining",
                period=period,
                severity="warning",
            )
        )
    elif health.avg_invoice_trend == "increasing":
        insights.append(
            Insight(
                title="Average invoice value increasing",
                explanation=(
                    f"Average invoice value has risen across the period "
                    f"(overall average: {rev.average_invoice_value} {bundle.currency})."
                ),
                metric=f"avg_invoice={rev.average_invoice_value}; trend=increasing",
                period=period,
                severity="positive",
            )
        )

    if health.receivables_ratio is not None and health.receivables_ratio >= Decimal("25"):
        severity = "critical" if health.receivables_ratio >= Decimal("50") else "warning"
        insights.append(
            Insight(
                title="Significant outstanding receivables",
                explanation=(
                    f"Outstanding receivables of {cash.outstanding_receivables} {bundle.currency} "
                    f"represent {health.receivables_ratio}% of period invoiced gross revenue. "
                    "This is invoiced revenue, not cash collected."
                ),
                metric=f"receivables_ratio={health.receivables_ratio}%",
                period=period,
                severity=severity,
            )
        )

    if cash.overdue_receivables > 0:
        insights.append(
            Insight(
                title="Overdue receivables present",
                explanation=(
                    f"{cash.overdue_count} invoice(s) are past due with "
                    f"{cash.overdue_receivables} {bundle.currency} still outstanding. "
                    "Overdue status uses due date and unpaid/partial payment records only."
                ),
                metric=f"overdue={cash.overdue_receivables}",
                period=period,
                severity="warning",
            )
        )

    # Volume vs average value
    months = list(rev.monthly.keys())
    if len(months) >= 3:
        first_half = months[: len(months) // 2]
        second_half = months[len(months) // 2 :]
        vol1 = sum((rev.monthly[m]["count"] for m in first_half), Decimal("0"))
        vol2 = sum((rev.monthly[m]["count"] for m in second_half), Decimal("0"))
        if vol1 > 0 and vol2 > vol1 * Decimal("1.1") and health.avg_invoice_trend == "declining":
            insights.append(
                Insight(
                    title="Volume up, average value down",
                    explanation=(
                        "Invoice volume increased in the later months while average invoice value declined. "
                        "Review pricing mix and customer composition."
                    ),
                    metric=f"volume_first={vol1}; volume_second={vol2}",
                    period=period,
                    severity="warning",
                )
            )

    if cash.collection_rate is not None:
        insights.append(
            Insight(
                title="Collection rate from confirmed payments",
                explanation=(
                    f"Confirmed cash collected is {cash.cash_collected} {bundle.currency}, "
                    f"{cash.collection_rate}% of period invoiced gross. "
                    "Invoiced revenue is not treated as incoming cash."
                ),
                metric=f"collection_rate={cash.collection_rate}%",
                period=period,
                severity="info" if cash.collection_rate >= Decimal("60") else "warning",
            )
        )
    else:
        insights.append(
            Insight(
                title="Payment data incomplete",
                explanation=(
                    "Cash collection metrics require payment records. "
                    "Add payments manually or import a payment CSV to enable cashflow analytics."
                ),
                metric="collection_rate=null",
                period=period,
                severity="info",
            )
        )

    if health.revenue_stability_cv is not None and health.revenue_stability_cv >= Decimal("0.5"):
        insights.append(
            Insight(
                title="Revenue volatility",
                explanation=(
                    f"Monthly revenue coefficient of variation is {health.revenue_stability_cv}, "
                    "indicating relatively unstable monthly invoicing."
                ),
                metric=f"cv={health.revenue_stability_cv}",
                period=period,
                severity="warning",
            )
        )

    return insights


def optionally_rephrase_insights(
    insights: list[Insight],
    *,
    enabled: bool,
    endpoint: str,
    model: str,
) -> list[Insight]:
    """Optionally ask Ollama to rephrase explanations without changing metrics."""
    if not enabled or not insights:
        return insights
    try:
        import requests
    except ImportError:
        return insights

    rephrased: list[Insight] = []
    for insight in insights:
        prompt = (
            "Rephrase the following business insight explanation in clear professional English. "
            "Do NOT add new numbers, claims, or predictions. Keep the same meaning.\n\n"
            f"Title: {insight.title}\nExplanation: {insight.explanation}\nMetric: {insight.metric}"
        )
        try:
            resp = requests.post(
                f"{endpoint.rstrip('/')}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}},
                timeout=60,
            )
            resp.raise_for_status()
            text = (resp.json().get("response") or "").strip()
            if text:
                rephrased.append(
                    Insight(
                        title=insight.title,
                        explanation=text,
                        metric=insight.metric,
                        period=insight.period,
                        severity=insight.severity,
                    )
                )
            else:
                rephrased.append(insight)
        except Exception:  # noqa: BLE001
            rephrased.append(insight)
    return rephrased
