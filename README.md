# Invoice Intelligence

Local-first Python application for processing PDF invoices, reviewing extracted data, storing results in SQLite, and producing business analytics.

Designed for **Greek-first** invoice extraction (ΑΦΜ, τιμολόγιο, ΦΠΑ) with English fallbacks, and **sales-first** KPIs (revenue, receivables, collection). Purchase invoices are stored and filterable but do not mix into revenue KPIs.

## Features

- Local Flask UI (Import → Review → Dashboard → Settings)
- Folder scan and per-PDF extraction (PyMuPDF → optional OCR → Greek heuristic parser → optional Ollama)
- Imports default to paid (confirmed payment for full amount)
- Strict review / approval checklist; customer and supplier must differ
- Duplicate detection by file hash and invoice identity (never silently overwrites)
- SQLite persistence with Decimal-safe money fields
- Dashboard: invoiced revenue vs collected cash vs outstanding receivables
- Deterministic business insights engine
- Optional Tesseract OCR and optional Ollama LLM (not required for basic digital PDFs)

## Requirements

- Python 3.10+
- Optional: [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) with `ell` + `eng` languages
- Optional: [Ollama](https://ollama.com/) for intelligent extraction / insight rephrasing

## Run (recommended on Debian/Ubuntu)

Debian blocks system-wide `pip install` (PEP 668). Use the launcher — it creates `.venv`, installs dependencies, and starts the app:

```bash
cd InvoiceAnalytics
chmod +x run.sh   # once
./run.sh
```

First run may take a minute while packages install. Later runs start immediately.

If `python3-venv` is missing:

```bash
sudo apt install python3 python3-venv python3-full
```

Open the local URL shown in the terminal (usually `http://localhost:8501`). The UI is a local Flask app (no Streamlit).

Optional flags:

```bash
./run.sh --no-browser
./run.sh --port 8080
```

### Manual alternative

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

## Typical workflow

1. **Import** — scan a PDF folder and process. By default invoices are marked paid. Use overwrite to re-extract.
2. **Review** — fix missing/wrong fields (customer ≠ supplier), then **Approve**.
3. **Dashboard** — KPIs for approved sales invoices.
4. **Settings** — paths, ΑΦΜ, OCR/Ollama, “imports are paid” toggle.

## Payments

Payment status is **never** inferred from due dates or invoice age.

- Add payments on the Review page, or
- Import a CSV in Settings with columns: `invoice_number`, `amount`, `date`, `method` (optional `notes`).

## Data locations

| Path | Purpose |
|------|---------|
| `data/settings.json` | App settings |
| `data/invoices.db` | Default SQLite database |
| `exports/` | Generated Excel / HTML reports |

Original PDFs are referenced by path and are not modified. Use **Settings → Backup database** before major changes.

## Tests

Synthetic Greek fixtures only — they never seed the live database.

```bash
pytest
```

## Architecture

```
invoice_intelligence/
  app.py                 # Streamlit entry
  config.py
  database/              # SQLite models & repositories
  ingestion/             # PDF scan, extract, OCR, parse, LLM
  validation/            # Financial & duplicate checks
  analytics/             # KPIs & insights
  reports/               # Excel & HTML exporters
  web/                   # Flask UI (templates + routes)
  validation/approval.py # Approval readiness checklist
  tests/                 # Pytest + synthetic fixtures
```

## Notes

- Management analytics only — not statutory accounting or tax filing.
- Invoiced revenue is not presented as cash.
- Credit notes reduce sales KPIs when approved.
- LLM extraction (when enabled) may only fill missing/low-confidence fields and must not invent values.
