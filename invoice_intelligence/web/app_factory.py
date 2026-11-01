"""Flask application factory and routes."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, url_for

from invoice_intelligence.analytics.insights import generate_insights
from invoice_intelligence.analytics.kpis import compute_kpis
from invoice_intelligence.config import (
    get_data_dir,
    get_db_path,
    load_settings,
    save_settings,
)
from invoice_intelligence.database.db import Database
from invoice_intelligence.database.models import (
    ApprovalStatus,
    DocumentDirection,
    Invoice,
    InvoiceType,
    PaymentRecord,
    PaymentStatus,
    format_date,
    parse_date,
    to_decimal,
)
from invoice_intelligence.ingestion.invoice_parser import parse_invoice_text
from invoice_intelligence.ingestion.pdf_extractor import extract_pdf_text
from invoice_intelligence.ingestion.pdf_scanner import scan_pdf_folder
from invoice_intelligence.ingestion.pipeline import process_scanned
from invoice_intelligence.validation.approval import evaluate_approval
from invoice_intelligence.validation.invoice_validator import apply_validation_to_invoice, validate_invoice


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.secret_key = "invoice-intelligence-local"
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    @app.template_filter("money")
    def money_filter(value: Any, currency: str = "") -> str:
        if value is None:
            return "—"
        try:
            text = f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)
        return f"{text} {currency}".strip()

    @app.template_filter("datefmt")
    def datefmt_filter(value: Any) -> str:
        return format_date(value)

    def settings() -> dict[str, Any]:
        return load_settings()

    def db() -> Database:
        return Database(get_db_path(settings()))

    def period_defaults() -> tuple[date, date]:
        days = int(settings().get("default_reporting_period_days", 365))
        end = date.today()
        start = end - timedelta(days=max(days - 1, 0))
        return start, end

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        d = db()
        pending = len(d.list_invoices(approval_status=ApprovalStatus.DRAFT.value))
        return {"pending_review_count": pending, "today": format_date(date.today())}

    # ---- Pages ----

    @app.get("/")
    def home():
        return redirect(url_for("import_page"))

    @app.get("/dashboard")
    def dashboard():
        cfg = settings()
        start_default, end_default = period_defaults()
        start = parse_date(request.args.get("start")) or start_default
        end = parse_date(request.args.get("end")) or end_default
        d = db()
        invoices = d.list_invoices()
        payments = d.list_payments()
        kpis = compute_kpis(
            invoices,
            payments,
            start=start,
            end=end,
            currency=cfg.get("default_currency", "EUR"),
        )
        insights = generate_insights(kpis)
        monthly_labels = list(kpis.revenue.monthly.keys())
        monthly_gross = [float(v["gross"]) for v in kpis.revenue.monthly.values()]
        monthly_cash_labels = list(kpis.cashflow.monthly_cash.keys())
        monthly_cash = [float(v) for v in kpis.cashflow.monthly_cash.values()]
        customers = [
            {"name": n, "revenue": float(r), "share": float(kpis.customers.share_by_customer.get(n, 0))}
            for n, r in list(kpis.customers.revenue_by_customer.items())[:12]
        ]
        allowed_tabs = {
            "revenue",
            "cashflow",
            "monthly_revenue",
            "monthly_cash",
            "customers",
            "insights",
        }
        active_tab = (request.args.get("tab") or "revenue").strip().lower()
        if active_tab not in allowed_tabs:
            active_tab = "revenue"
        return render_template(
            "dashboard.html",
            kpis=kpis,
            insights=insights,
            start=format_date(start),
            end=format_date(end),
            monthly_labels=json.dumps(monthly_labels),
            monthly_gross=json.dumps(monthly_gross),
            monthly_cash_labels=json.dumps(monthly_cash_labels),
            monthly_cash=json.dumps(monthly_cash),
            customers=customers,
            customer_labels=json.dumps([c["name"] for c in customers[:8]]),
            customer_shares=json.dumps([round(c["share"], 2) for c in customers[:8]]),
            currency=cfg.get("default_currency", "EUR"),
            active_tab=active_tab,
        )

    @app.route("/import", methods=["GET", "POST"])
    def import_page():
        cfg = settings()
        d = db()
        results: list[dict[str, Any]] = []
        scanned_rows: list[dict[str, Any]] = []
        folder = cfg.get("pdf_import_folder") or ""
        cache = get_data_dir() / ".last_scan.json"

        if request.method == "POST":
            folder = (request.form.get("folder") or "").strip()
            action = request.form.get("action")
            recursive = request.form.get("recursive") == "on"
            if action == "scan":
                try:
                    scanned = scan_pdf_folder(folder, recursive=recursive)
                    scanned_rows = [
                        {
                            "filename": s.filename,
                            "size_kb": round(s.size_bytes / 1024, 1),
                            "hash": s.document_hash[:12],
                            "path": str(s.path),
                        }
                        for s in scanned
                    ]
                    cache.write_text(
                        json.dumps({"folder": folder, "paths": [str(s.path) for s in scanned]}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    flash(f"Found {len(scanned)} PDF(s).", "ok")
                except OSError as exc:
                    flash(str(exc), "error")
            elif action == "process":
                overwrite = request.form.get("overwrite_existing") == "on"
                if not cache.exists():
                    flash("Scan a folder first.", "error")
                else:
                    payload = json.loads(cache.read_text(encoding="utf-8"))
                    folder = payload.get("folder") or folder
                    paths = payload.get("paths") or []
                    from invoice_intelligence.ingestion.pdf_scanner import ScannedPdf, file_sha256

                    run_id = d.start_processing_run(folder, len(paths))
                    success = failure = overwritten = 0
                    for p in paths:
                        path = Path(p)
                        try:
                            resolved = str(path.resolve())
                            scanned = ScannedPdf(
                                path=path.resolve(),
                                filename=path.name,
                                size_bytes=path.stat().st_size,
                                document_hash=file_sha256(path),
                            )
                            extraction = process_scanned(scanned, settings=cfg)
                            extraction.invoice.approval_status = ApprovalStatus.DRAFT.value
                            extraction.invoice.confidence_json = json.dumps(
                                extraction.confidence, ensure_ascii=False
                            )
                            _apply_paid_default(extraction.invoice, cfg)

                            existing = d.find_existing_for_reimport(
                                document_hash=extraction.invoice.document_hash,
                                pdf_path=resolved,
                            )
                            overwrite_id = existing.id if (existing and overwrite) else None

                            validation = validate_invoice(
                                extraction.invoice,
                                db=d,
                                settings=cfg,
                                exclude_id=overwrite_id,
                            )
                            apply_validation_to_invoice(extraction.invoice, validation)

                            if existing and not overwrite:
                                failure += 1
                                results.append(
                                    {
                                        "file": path.name,
                                        "status": "skipped_existing",
                                        "detail": (
                                            f"Already imported as invoice #{existing.id}. "
                                            "Enable “Overwrite existing” to re-extract."
                                        ),
                                        "invoice_id": existing.id,
                                    }
                                )
                            elif overwrite_id is not None:
                                inv_id = d.insert_invoice(
                                    extraction.invoice,
                                    overwrite_id=overwrite_id,
                                    preserve_payments=False,
                                )
                                success += 1
                                overwritten += 1
                                results.append(
                                    {
                                        "file": path.name,
                                        "status": "overwritten",
                                        "detail": (
                                            f"Re-extracted over #{inv_id} "
                                            f"(number={extraction.invoice.invoice_number}); "
                                            "draft; marked paid"
                                        ),
                                        "invoice_id": inv_id,
                                    }
                                )
                            else:
                                inv_id = d.insert_invoice(extraction.invoice)
                                success += 1
                                results.append(
                                    {
                                        "file": path.name,
                                        "status": "draft_saved",
                                        "detail": f"id={inv_id} number={extraction.invoice.invoice_number}; paid",
                                        "invoice_id": inv_id,
                                    }
                                )
                        except Exception as exc:  # noqa: BLE001
                            failure += 1
                            results.append({"file": path.name, "status": "failed", "detail": str(exc)})
                    d.finish_processing_run(run_id, success, failure)
                    msg = f"Import done — saved: {success}, skipped/failed: {failure}"
                    if overwritten:
                        msg += f", overwritten: {overwritten}"
                    flash(msg, "ok")

        return render_template(
            "import.html",
            folder=folder,
            scanned_rows=scanned_rows,
            results=results,
            overwrite_existing=request.form.get("overwrite_existing") == "on" if request.method == "POST" else False,
        )

    @app.route("/review", methods=["GET"])
    @app.route("/review/<int:invoice_id>", methods=["GET", "POST"])
    def review(invoice_id: int | None = None):
        cfg = settings()
        d = db()
        # DB returns newest-first; reverse so the queue is oldest-first (FIFO).
        drafts = list(reversed(d.list_invoices(approval_status=ApprovalStatus.DRAFT.value)))
        approved_queue = list(
            reversed(d.list_invoices(approval_status=ApprovalStatus.APPROVED.value))
        )
        all_inv = d.list_invoices()
        if invoice_id is None:
            if drafts:
                return redirect(url_for("review", invoice_id=drafts[0].id))
            if approved_queue:
                return redirect(url_for("review", invoice_id=approved_queue[0].id))
            if all_inv:
                return redirect(url_for("review", invoice_id=list(reversed(all_inv))[0].id))
            flash("No invoices to review. Import PDFs first.", "error")
            return redirect(url_for("import_page"))

        invoice = d.get_invoice(invoice_id)
        if not invoice:
            flash("Invoice not found.", "error")
            return redirect(url_for("import_page"))

        if _repair_missing_issue_date(invoice):
            d.update_invoice(invoice)

        if invoice.autofill_gross_from_net_vat():
            invoice.recalculate_payment_fields()
            d.update_invoice(invoice)

        if request.method == "POST":
            action = request.form.get("action")
            if action in {"quick_approve_all", "approve_without_issues"}:
                approved_n, skipped_n = _bulk_approve(
                    d,
                    cfg,
                    only_without_issues=True,
                )
                flash(
                    f"Approved {approved_n} without issues; skipped {skipped_n}.",
                    "ok" if approved_n else "error",
                )
                return redirect(url_for("review"))
            if action == "approve_selected":
                raw_ids = request.form.getlist("selected_ids")
                selected_ids = [int(x) for x in raw_ids if str(x).isdigit()]
                approved_n, skipped_n = _bulk_approve(
                    d,
                    cfg,
                    invoice_ids=selected_ids,
                    only_without_issues=False,
                )
                flash(
                    f"Approved {approved_n} selected; skipped {skipped_n} (checklist failed or invalid).",
                    "ok" if approved_n else "error",
                )
                return redirect(url_for("review"))
            if action == "delete":
                d.delete_invoice(invoice_id)
                flash("Invoice deleted.", "ok")
                return redirect(url_for("review"))
            if action == "add_payment":
                amt = to_decimal(request.form.get("pay_amount"))
                if amt is None:
                    flash("Invalid payment amount.", "error")
                else:
                    d.add_payment(
                        PaymentRecord(
                            invoice_id=invoice_id,
                            amount=amt,
                            payment_date=parse_date(request.form.get("pay_date")) or date.today(),
                            payment_method=(request.form.get("pay_method") or "").strip() or None,
                        )
                    )
                    flash("Payment recorded.", "ok")
                return redirect(url_for("review", invoice_id=invoice_id))

            _apply_form_to_invoice(invoice, request.form)
            validation = validate_invoice(invoice, db=d, settings=cfg, exclude_id=invoice.id)
            apply_validation_to_invoice(invoice, validation)
            readiness = evaluate_approval(
                invoice,
                low_confidence_threshold=float(cfg.get("low_confidence_threshold", 0.55)),
                hash_duplicate=bool(validation.hash_duplicate_id),
            )

            if action == "approve":
                if not readiness.can_approve:
                    flash("Cannot approve yet: " + "; ".join(readiness.blockers), "error")
                    d.update_invoice(invoice)
                else:
                    invoice.approval_status = ApprovalStatus.APPROVED.value
                    d.update_invoice(invoice)
                    flash("Invoice approved.", "ok")
                    return redirect(url_for("review"))
            elif action == "reject":
                invoice.approval_status = ApprovalStatus.REJECTED.value
                d.update_invoice(invoice)
                flash("Invoice rejected.", "ok")
                return redirect(url_for("review"))
            else:
                invoice.approval_status = ApprovalStatus.DRAFT.value
                d.update_invoice(invoice)
                flash("Draft saved.", "ok")
            return redirect(url_for("review", invoice_id=invoice_id))

        threshold = float(cfg.get("low_confidence_threshold", 0.55))
        review_ctx = _review_detail_context(invoice, d, cfg, threshold)
        pending_flags = [_queue_flags(inv, d, cfg, threshold) for inv in drafts]
        pending_error_count = sum(1 for f in pending_flags if f["has_error"])
        pending_low_count = sum(1 for f in pending_flags if f["has_low"])
        pending_clean_count = sum(1 for f in pending_flags if f["is_clean"])
        return render_template(
            "review.html",
            invoice=invoice,
            pending_queue=drafts,
            pending_flags=pending_flags,
            pending_error_count=pending_error_count,
            pending_low_count=pending_low_count,
            pending_clean_count=pending_clean_count,
            approved_queue=approved_queue,
            drafts=drafts,
            validation=review_ctx["validation_obj"],
            readiness=review_ctx["readiness_obj"],
            confidence=review_ctx["confidence"],
            field_meta=review_ctx["field_meta"],
            invoice_types=[t.value for t in InvoiceType],
            directions=[x.value for x in DocumentDirection],
            payment_statuses=[p.value for p in PaymentStatus],
            threshold=threshold,
        )

    @app.get("/review/<int:invoice_id>/data")
    def review_data(invoice_id: int):
        """JSON payload for SPA-style pending-queue navigation."""
        cfg = settings()
        d = db()
        invoice = d.get_invoice(invoice_id)
        if not invoice:
            return jsonify({"error": "not_found"}), 404
        if _repair_missing_issue_date(invoice):
            d.update_invoice(invoice)
        if invoice.autofill_gross_from_net_vat():
            invoice.recalculate_payment_fields()
            d.update_invoice(invoice)
        threshold = float(cfg.get("low_confidence_threshold", 0.55))
        ctx = _review_detail_context(invoice, d, cfg, threshold)
        return jsonify(ctx["payload"])

    @app.get("/invoices/<int:invoice_id>/pdf")
    def invoice_pdf(invoice_id: int):
        invoice = db().get_invoice(invoice_id, include_children=False)
        if not invoice or not invoice.original_pdf_path:
            flash("PDF not available.", "error")
            return redirect(url_for("review", invoice_id=invoice_id))
        path = Path(invoice.original_pdf_path)
        if not path.exists():
            flash("PDF file missing on disk.", "error")
            return redirect(url_for("review", invoice_id=invoice_id))
        return send_file(path, mimetype="application/pdf", download_name=path.name)

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        cfg = dict(settings())
        if request.method == "POST":
            action = request.form.get("action")
            d = db()
            if action == "backup":
                dest = d.backup()
                flash(f"Backup created: {dest}", "ok")
            elif action == "import_payments":
                upload = request.files.get("payments_csv")
                if not upload:
                    flash("Choose a CSV file.", "error")
                else:
                    imported, skipped = _import_payments_csv(d, upload.read())
                    flash(f"Imported {imported} payment(s); skipped {skipped}.", "ok")
            else:
                for key in [
                    "database_path",
                    "pdf_import_folder",
                    "ocr_executable",
                    "ocr_languages",
                    "own_vat",
                    "ollama_endpoint",
                    "ollama_model",
                    "default_currency",
                    "rounding_tolerance",
                ]:
                    if key in request.form:
                        cfg[key] = request.form.get(key, "")
                cfg["ollama_enabled"] = request.form.get("ollama_enabled") == "on"
                cfg["imports_are_paid"] = request.form.get("imports_are_paid") == "on"
                cfg["default_reporting_period_days"] = int(
                    request.form.get("default_reporting_period_days") or 365
                )
                cfg["low_confidence_threshold"] = float(
                    request.form.get("low_confidence_threshold") or 0.55
                )
                cfg["ocr_min_text_chars"] = int(request.form.get("ocr_min_text_chars") or 40)
                save_settings(cfg)
                flash("Settings saved.", "ok")
            return redirect(url_for("settings_page"))
        return render_template("settings.html", settings=cfg)

    return app


def _money_str(value: Any) -> str:
    if value is None:
        return ""
    return format(value, "f") if isinstance(value, Decimal) else str(value)


def _review_detail_context(
    invoice: Invoice,
    db: Database,
    cfg: dict[str, Any],
    threshold: float,
) -> dict[str, Any]:
    validation = validate_invoice(invoice, db=db, settings=cfg, exclude_id=invoice.id)
    readiness = evaluate_approval(
        invoice,
        low_confidence_threshold=threshold,
        validation_has_blocking_errors=validation.has_errors,
        hash_duplicate=bool(validation.hash_duplicate_id),
    )
    conf: dict[str, float] = {}
    if invoice.confidence_json:
        try:
            conf = {str(k): float(v) for k, v in json.loads(invoice.confidence_json).items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            conf = {}
    field_meta = {
        f.field: {
            "required": f.required,
            "present": f.present,
            "low": f.low_confidence,
            "css": (
                "missing"
                if f.required and not f.present
                else ("low" if f.low_confidence else "")
            ),
        }
        for f in readiness.fields
    }
    payload = {
        "id": invoice.id,
        "url": url_for("review", invoice_id=invoice.id),
        "data_url": url_for("review_data", invoice_id=invoice.id),
        "pdf_url": (
            url_for("invoice_pdf", invoice_id=invoice.id) if invoice.original_pdf_path else None
        ),
        "title": f"Review #{invoice.id} — Invoice Intelligence",
        "approval_status": invoice.approval_status,
        "fields": {
            "invoice_number": invoice.invoice_number or "",
            "invoice_type": invoice.invoice_type or InvoiceType.INVOICE.value,
            "document_direction": invoice.document_direction or DocumentDirection.UNKNOWN.value,
            "issue_date": format_date(invoice.issue_date),
            "due_date": format_date(invoice.due_date),
            "clear_due": not bool(invoice.due_date),
            "currency": invoice.currency or "EUR",
            "customer_name": invoice.customer_name or "",
            "customer_vat": invoice.customer_vat or "",
            "supplier_name": invoice.supplier_name or "",
            "supplier_vat": invoice.supplier_vat or "",
            "net_amount": _money_str(invoice.net_amount),
            "vat_amount": _money_str(invoice.vat_amount),
            "gross_amount": _money_str(invoice.gross_amount),
            "total_payable": _money_str(invoice.total_payable),
            "discount_amount": _money_str(invoice.discount_amount),
            "other_charges": _money_str(invoice.other_charges),
            "withholding_tax": _money_str(invoice.withholding_tax),
            "amount_paid": _money_str(invoice.amount_paid if invoice.amount_paid is not None else Decimal("0")),
            "payment_status": invoice.payment_status or PaymentStatus.UNKNOWN.value,
            "payment_method": invoice.payment_method or "",
            "description": invoice.description or "",
        },
        "field_meta": field_meta,
        "readiness": {
            "can_approve": readiness.can_approve,
            "blockers": readiness.blockers,
            "warnings": readiness.warnings,
            "fields": [
                {
                    "field": f.field,
                    "label": f.label,
                    "required": f.required,
                    "present": f.present,
                    "low_confidence": f.low_confidence,
                    "confidence": f.confidence,
                }
                for f in readiness.fields
            ],
        },
        "validation": {
            "issues": [
                {"severity": i.severity, "message": i.message, "code": i.code, "field": i.field}
                for i in validation.issues
            ]
        },
        "original_pdf_path": invoice.original_pdf_path,
        "extracted_text_preview": invoice.extracted_text_preview or "",
        "payments": [
            {
                "id": p.id,
                "amount": _money_str(p.amount),
                "payment_date": format_date(p.payment_date) or "—",
                "payment_method": p.payment_method or "—",
            }
            for p in (invoice.payments or [])
        ],
    }
    return {
        "payload": payload,
        "validation_obj": validation,
        "readiness_obj": readiness,
        "confidence": conf,
        "field_meta": field_meta,
    }


def _repair_missing_issue_date(invoice: Invoice) -> bool:
    """Re-read issue date from the PDF when extraction previously missed columnar layouts."""
    if invoice.issue_date or not invoice.original_pdf_path:
        return False
    path = Path(invoice.original_pdf_path)
    if not path.exists():
        return False
    try:
        extracted = extract_pdf_text(path)
        text = extracted.text if extracted else ""
    except Exception:
        return False
    if not text.strip():
        return False
    parsed = parse_invoice_text(text, filename=path.name).invoice.issue_date
    if parsed is None:
        return False
    invoice.issue_date = parsed
    return True


def _queue_flags(
    invoice: Invoice,
    db: Database,
    cfg: dict[str, Any],
    threshold: float,
) -> dict[str, Any]:
    """Per-draft error / low-confidence flags for the pending queue list."""
    full = db.get_invoice(invoice.id) if invoice.id else invoice
    target = full or invoice
    validation = validate_invoice(target, db=db, settings=cfg, exclude_id=target.id)
    readiness = evaluate_approval(
        target,
        low_confidence_threshold=threshold,
        validation_has_blocking_errors=validation.has_errors,
        hash_duplicate=bool(validation.hash_duplicate_id),
    )
    error_n = sum(1 for i in validation.issues if i.severity == "error") + len(readiness.blockers)
    low_n = sum(1 for f in readiness.fields if f.low_confidence)
    has_error = error_n > 0
    has_low = low_n > 0
    return {
        "id": target.id,
        "error_count": error_n,
        "low_count": low_n,
        "has_error": has_error,
        "has_low": has_low,
        "can_approve": readiness.can_approve,
        "is_clean": readiness.can_approve and not has_low and not has_error,
    }


def _bulk_approve(
    db: Database,
    cfg: dict[str, Any],
    *,
    invoice_ids: list[int] | None = None,
    only_without_issues: bool = False,
) -> tuple[int, int]:
    """Approve drafts by id list, or all clean drafts when only_without_issues."""
    threshold = float(cfg.get("low_confidence_threshold", 0.55))
    approved = skipped = 0
    if invoice_ids is not None:
        candidates = []
        for iid in invoice_ids:
            inv = db.get_invoice(iid)
            if inv and inv.approval_status == ApprovalStatus.DRAFT.value:
                candidates.append(inv)
            else:
                skipped += 1
    else:
        candidates = list(db.list_invoices(approval_status=ApprovalStatus.DRAFT.value))

    for draft in candidates:
        invoice = db.get_invoice(draft.id) if draft.id else None
        if not invoice or invoice.id is None:
            skipped += 1
            continue
        validation = validate_invoice(invoice, db=db, settings=cfg, exclude_id=invoice.id)
        readiness = evaluate_approval(
            invoice,
            low_confidence_threshold=threshold,
            validation_has_blocking_errors=validation.has_errors,
            hash_duplicate=bool(validation.hash_duplicate_id),
        )
        low_n = sum(1 for f in readiness.fields if f.low_confidence)
        val_errors = sum(1 for i in validation.issues if i.severity == "error")
        if not readiness.can_approve:
            skipped += 1
            continue
        if only_without_issues and (low_n > 0 or val_errors > 0 or readiness.blockers):
            skipped += 1
            continue
        invoice.approval_status = ApprovalStatus.APPROVED.value
        apply_validation_to_invoice(invoice, validation)
        db.update_invoice(invoice)
        approved += 1
    return approved, skipped


def _quick_approve_all(db: Database, cfg: dict[str, Any]) -> tuple[int, int]:
    """Backward-compatible alias: approve drafts with no issues."""
    return _bulk_approve(db, cfg, only_without_issues=True)


def _apply_paid_default(invoice: Invoice, cfg: dict[str, Any]) -> None:
    """Mark imported invoices as fully paid with a confirmed payment record."""
    if not cfg.get("imports_are_paid", True):
        return
    gross = invoice.gross_amount or invoice.total_payable
    if gross is None:
        invoice.payment_status = PaymentStatus.PAID.value
        invoice.amount_paid = Decimal("0")
        invoice.outstanding_balance = Decimal("0")
        return
    pay_date = invoice.issue_date or date.today()
    invoice.payments = [
        PaymentRecord(
            amount=gross,
            payment_date=pay_date,
            payment_method="confirmed",
            notes="Assumed paid on import",
        )
    ]
    invoice.amount_paid = gross
    invoice.payment_date = pay_date
    invoice.payment_method = "confirmed"
    invoice.payment_status = PaymentStatus.PAID.value
    invoice.outstanding_balance = Decimal("0")


def _apply_form_to_invoice(invoice: Invoice, form) -> None:
    invoice.invoice_number = (form.get("invoice_number") or "").strip() or None
    invoice.invoice_type = form.get("invoice_type") or invoice.invoice_type
    invoice.document_direction = form.get("document_direction") or invoice.document_direction
    invoice.issue_date = parse_date(form.get("issue_date"))
    due = form.get("due_date")
    invoice.due_date = None if form.get("clear_due") == "on" else parse_date(due)
    invoice.currency = ((form.get("currency") or "EUR").strip().upper()) or "EUR"
    invoice.supplier_name = (form.get("supplier_name") or "").strip() or None
    invoice.supplier_vat = (form.get("supplier_vat") or "").strip() or None
    invoice.customer_name = (form.get("customer_name") or "").strip() or None
    invoice.customer_vat = (form.get("customer_vat") or "").strip() or None
    invoice.payment_status = form.get("payment_status") or invoice.payment_status
    invoice.payment_method = (form.get("payment_method") or "").strip() or None
    invoice.net_amount = to_decimal(form.get("net_amount"))
    invoice.vat_amount = to_decimal(form.get("vat_amount"))
    invoice.gross_amount = to_decimal(form.get("gross_amount"))
    invoice.total_payable = to_decimal(form.get("total_payable"))
    invoice.discount_amount = to_decimal(form.get("discount_amount"))
    invoice.other_charges = to_decimal(form.get("other_charges"))
    invoice.withholding_tax = to_decimal(form.get("withholding_tax"))
    invoice.amount_paid = to_decimal(form.get("amount_paid")) or Decimal("0")
    invoice.description = (form.get("description") or "").strip() or None
    invoice.autofill_gross_from_net_vat()
    invoice.recalculate_payment_fields()


def _import_payments_csv(db: Database, raw: bytes) -> tuple[int, int]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    imported = skipped = 0
    for row in reader:
        number = (row.get("invoice_number") or row.get("Invoice Number") or "").strip()
        amount = to_decimal(row.get("amount") or row.get("Amount"))
        pay_date = parse_date(row.get("date") or row.get("payment_date") or row.get("Date"))
        method = (row.get("method") or row.get("payment_method") or "").strip() or None
        notes = (row.get("notes") or "").strip() or None
        if not number or amount is None:
            skipped += 1
            continue
        inv = db.find_invoice_by_number(number)
        if not inv or inv.id is None:
            skipped += 1
            continue
        db.add_payment(
            PaymentRecord(
                invoice_id=inv.id,
                amount=amount,
                payment_date=pay_date,
                payment_method=method,
                notes=notes,
            )
        )
        imported += 1
    return imported, skipped
