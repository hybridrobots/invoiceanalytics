"""Standalone HTML financial report with embedded Plotly charts."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from invoice_intelligence.analytics.insights import Insight
from invoice_intelligence.analytics.kpis import KpiBundle
from invoice_intelligence.database.models import format_date


def _num(value: Decimal | float | int | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _esc(text: Any) -> str:
    s = "" if text is None else str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate_html_report(
    path: str | Path,
    *,
    kpis: KpiBundle,
    insights: list[Insight],
    title: str = "Invoice Intelligence — Financial Report",
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    months = list(kpis.revenue.monthly.keys())
    gross_series = [_num(kpis.revenue.monthly[m]["gross"]) for m in months]
    vat_series = [_num(kpis.revenue.monthly[m]["vat"]) for m in months]
    count_series = [int(kpis.revenue.monthly[m]["count"]) for m in months]
    cash_months = list(kpis.cashflow.monthly_cash.keys())
    cash_series = [_num(kpis.cashflow.monthly_cash[m]) for m in cash_months]

    customers = list(kpis.customers.revenue_by_customer.keys())[:12]
    customer_vals = [_num(kpis.customers.revenue_by_customer[c]) for c in customers]

    period = "All data"
    if kpis.period_start or kpis.period_end:
        left = format_date(kpis.period_start) or "…"
        right = format_date(kpis.period_end) or "…"
        period = f"{left} → {right}"

    severity_colors = {
        "info": "#3b82f6",
        "positive": "#16a34a",
        "warning": "#d97706",
        "critical": "#dc2626",
    }
    insight_html = []
    for ins in insights:
        color = severity_colors.get(ins.severity, "#64748b")
        insight_html.append(
            f"""
            <article class="insight" style="border-left-color:{color}">
              <h3>{_esc(ins.title)}</h3>
              <p>{_esc(ins.explanation)}</p>
              <div class="meta"><span>{_esc(ins.metric)}</span><span>{_esc(ins.period)}</span>
              <span class="sev" style="color:{color}">{_esc(ins.severity)}</span></div>
            </article>
            """
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{_esc(title)}</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    :root {{
      --bg: #f4f7fb;
      --card: #ffffff;
      --ink: #0f172a;
      --muted: #64748b;
      --accent: #0b3d5c;
      --line: #e2e8f0;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      color: var(--ink);
      background:
        radial-gradient(1200px 600px at 10% -10%, #dbeafe 0%, transparent 50%),
        radial-gradient(900px 500px at 100% 0%, #e2e8f0 0%, transparent 45%),
        var(--bg);
    }}
    header.hero {{
      padding: 2.5rem 1.5rem 1.5rem;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(135deg, #0b3d5c, #1e5f8a 55%, #2f7aa8);
      color: #fff;
    }}
    header.hero h1 {{
      margin: 0 0 0.35rem;
      font-size: clamp(1.6rem, 3vw, 2.2rem);
      letter-spacing: -0.02em;
    }}
    header.hero p {{ margin: 0.2rem 0; opacity: 0.92; }}
    main {{ max-width: 1100px; margin: 0 auto; padding: 1.5rem; }}
    h2 {{ margin: 2rem 0 0.75rem; color: var(--accent); font-size: 1.25rem; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 0.85rem;
    }}
    .kpi {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 1rem;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }}
    .kpi .label {{ color: var(--muted); font-size: 0.8rem; }}
    .kpi .value {{ font-size: 1.35rem; font-weight: 700; margin-top: 0.25rem; }}
    .kpi .hint {{ color: var(--muted); font-size: 0.75rem; margin-top: 0.35rem; }}
    .chart {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 0.5rem;
      margin: 0.75rem 0 1rem;
    }}
    .insight {{
      background: var(--card);
      border: 1px solid var(--line);
      border-left: 4px solid #64748b;
      border-radius: 10px;
      padding: 0.9rem 1rem;
      margin: 0.65rem 0;
    }}
    .insight h3 {{ margin: 0 0 0.35rem; font-size: 1rem; }}
    .insight p {{ margin: 0; color: #334155; line-height: 1.45; }}
    .meta {{
      display: flex; gap: 1rem; flex-wrap: wrap;
      margin-top: 0.55rem; font-size: 0.75rem; color: var(--muted);
    }}
    .note {{
      background: #fff7ed; border: 1px solid #fed7aa; color: #9a3412;
      padding: 0.75rem 1rem; border-radius: 10px; font-size: 0.9rem;
    }}
    footer {{
      max-width: 1100px; margin: 0 auto; padding: 1rem 1.5rem 2rem;
      color: var(--muted); font-size: 0.8rem;
    }}
  </style>
</head>
<body>
  <header class="hero">
    <h1>{_esc(title)}</h1>
    <p>Reporting period: {_esc(period)}</p>
    <p>Generated: {_esc(datetime.now().isoformat(timespec="seconds"))}</p>
  </header>
  <main>
    <p class="note">
      Management analytics only — not statutory accounting. Invoiced revenue is distinct from collected cash.
      Figures use approved sales invoices unless otherwise noted.
    </p>

    <h2>Executive summary</h2>
    <div class="grid">
      <div class="kpi"><div class="label">Invoiced revenue (gross)</div>
        <div class="value">{_num(kpis.revenue.total_gross):,.2f} {_esc(kpis.currency)}</div>
        <div class="hint">Not cash</div></div>
      <div class="kpi"><div class="label">Revenue excl. VAT</div>
        <div class="value">{_num(kpis.revenue.total_net):,.2f} {_esc(kpis.currency)}</div></div>
      <div class="kpi"><div class="label">VAT charged</div>
        <div class="value">{_num(kpis.revenue.total_vat):,.2f} {_esc(kpis.currency)}</div></div>
      <div class="kpi"><div class="label">Invoices issued</div>
        <div class="value">{kpis.revenue.invoice_count}</div></div>
      <div class="kpi"><div class="label">Cash collected</div>
        <div class="value">{_num(kpis.cashflow.cash_collected):,.2f} {_esc(kpis.currency)}</div>
        <div class="hint">From payment records</div></div>
      <div class="kpi"><div class="label">Outstanding receivables</div>
        <div class="value">{_num(kpis.cashflow.outstanding_receivables):,.2f} {_esc(kpis.currency)}</div></div>
      <div class="kpi"><div class="label">Overdue receivables</div>
        <div class="value">{_num(kpis.cashflow.overdue_receivables):,.2f} {_esc(kpis.currency)}</div></div>
      <div class="kpi"><div class="label">Collection rate</div>
        <div class="value">{(_num(kpis.cashflow.collection_rate) if kpis.cashflow.collection_rate is not None else 0):.2f}%</div></div>
    </div>

    <h2>Monthly revenue</h2>
    <div id="chart-revenue" class="chart"></div>

    <h2>Monthly cash collection</h2>
    <div id="chart-cash" class="chart"></div>

    <h2>Customer revenue distribution</h2>
    <div id="chart-customers" class="chart"></div>

    <h2>Business health</h2>
    <div class="grid">
      <div class="kpi"><div class="label">Revenue growth</div>
        <div class="value">{_esc(kpis.revenue.growth_vs_previous if kpis.revenue.growth_vs_previous is not None else 'n/a')}%</div>
        <div class="hint">{_esc(kpis.health.explanations.get('revenue_growth', ''))}</div></div>
      <div class="kpi"><div class="label">Concentration risk</div>
        <div class="value">{_esc(kpis.health.concentration_risk)}</div>
        <div class="hint">{_esc(kpis.health.explanations.get('concentration', ''))}</div></div>
      <div class="kpi"><div class="label">Receivables ratio</div>
        <div class="value">{_esc(kpis.health.receivables_ratio if kpis.health.receivables_ratio is not None else 'n/a')}%</div>
        <div class="hint">{_esc(kpis.health.explanations.get('receivables_ratio', ''))}</div></div>
      <div class="kpi"><div class="label">Collection performance</div>
        <div class="value">{_esc(kpis.health.collection_performance)}</div>
        <div class="hint">{_esc(kpis.health.explanations.get('collection_performance', ''))}</div></div>
    </div>

    <h2>Business insights</h2>
    {''.join(insight_html) if insight_html else '<p>No insights for this period.</p>'}
  </main>
  <footer>Invoice Intelligence — local report. Charts require network access to Plotly CDN on first load.</footer>
  <script>
    const months = {json.dumps(months)};
    const gross = {json.dumps(gross_series)};
    const vat = {json.dumps(vat_series)};
    const counts = {json.dumps(count_series)};
    const cashMonths = {json.dumps(cash_months)};
    const cash = {json.dumps(cash_series)};
    const customers = {json.dumps(customers, ensure_ascii=False)};
    const customerVals = {json.dumps(customer_vals)};

    Plotly.newPlot('chart-revenue', [
      {{x: months, y: gross, name: 'Gross', type: 'bar', marker: {{color: '#0b3d5c'}}}},
      {{x: months, y: vat, name: 'VAT', type: 'scatter', mode: 'lines+markers', yaxis: 'y2', line: {{color: '#d97706'}}}}
    ], {{
      margin: {{t: 30, r: 50, l: 50, b: 40}},
      yaxis: {{title: 'Gross'}},
      yaxis2: {{title: 'VAT', overlaying: 'y', side: 'right'}},
      legend: {{orientation: 'h'}},
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)'
    }}, {{responsive: true, displayModeBar: false}});

    Plotly.newPlot('chart-cash', [{{
      x: cashMonths, y: cash, type: 'scatter', mode: 'lines+markers',
      fill: 'tozeroy', line: {{color: '#16803c'}}, name: 'Cash collected'
    }}], {{
      margin: {{t: 30, r: 20, l: 50, b: 40}},
      yaxis: {{title: 'Cash'}},
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)'
    }}, {{responsive: true, displayModeBar: false}});

    Plotly.newPlot('chart-customers', [{{
      labels: customers, values: customerVals, type: 'pie',
      hole: 0.35, textinfo: 'label+percent',
      marker: {{colors: ['#0b3d5c','#1e5f8a','#2f7aa8','#4b93bd','#7ab0cf','#a9cce2']}}
    }}], {{
      margin: {{t: 20, r: 20, l: 20, b: 20}},
      paper_bgcolor: 'rgba(0,0,0,0)',
      showlegend: false
    }}, {{responsive: true, displayModeBar: false}});
  </script>
</body>
</html>
"""
    out.write_text(html, encoding="utf-8")
    return out
