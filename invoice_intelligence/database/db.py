"""SQLite persistence layer for Invoice Intelligence."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Generator, Iterable

from invoice_intelligence.database.models import (
    Invoice,
    LineItem,
    PaymentRecord,
    VatBreakdownRow,
    decimal_to_str,
    parse_date,
    to_decimal,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT,
    invoice_type TEXT NOT NULL DEFAULT 'invoice',
    document_direction TEXT NOT NULL DEFAULT 'sales',
    issue_date TEXT,
    due_date TEXT,
    currency TEXT DEFAULT 'EUR',
    document_filename TEXT,
    supplier_name TEXT,
    supplier_vat TEXT,
    customer_name TEXT,
    customer_vat TEXT,
    net_amount TEXT,
    vat_amount TEXT,
    gross_amount TEXT,
    discount_amount TEXT,
    other_charges TEXT,
    withholding_tax TEXT,
    total_payable TEXT,
    payment_status TEXT DEFAULT 'Unknown',
    amount_paid TEXT DEFAULT '0',
    payment_date TEXT,
    outstanding_balance TEXT,
    payment_method TEXT,
    description TEXT,
    extraction_confidence TEXT,
    validation_warnings TEXT,
    original_pdf_path TEXT,
    document_hash TEXT,
    approval_status TEXT NOT NULL DEFAULT 'draft',
    confidence_json TEXT,
    extracted_text_preview TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_document_hash
    ON invoices(document_hash) WHERE document_hash IS NOT NULL AND document_hash != '';

CREATE INDEX IF NOT EXISTS idx_invoices_number_direction
    ON invoices(invoice_number, document_direction, issue_date);

CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_name);
CREATE INDEX IF NOT EXISTS idx_invoices_supplier ON invoices(supplier_name);
CREATE INDEX IF NOT EXISTS idx_invoices_approval ON invoices(approval_status);
CREATE INDEX IF NOT EXISTS idx_invoices_issue_date ON invoices(issue_date);

CREATE TABLE IF NOT EXISTS invoice_line_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    description TEXT,
    quantity TEXT,
    unit_price TEXT,
    net_amount TEXT,
    vat_rate TEXT,
    vat_amount TEXT,
    gross_amount TEXT,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vat_breakdown (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    vat_rate TEXT,
    net_base TEXT,
    vat_amount TEXT,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    amount TEXT NOT NULL,
    payment_date TEXT,
    payment_method TEXT,
    notes TEXT,
    created_at TEXT,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS processing_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    folder_path TEXT,
    total_files INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS extraction_warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    processing_run_id INTEGER,
    invoice_id INTEGER,
    document_filename TEXT,
    warning_level TEXT DEFAULT 'warning',
    message TEXT NOT NULL,
    created_at TEXT,
    FOREIGN KEY (processing_run_id) REFERENCES processing_runs(id) ON DELETE SET NULL,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
);
"""


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class Database:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)

    def backup(self, destination: Path | str | None = None) -> Path:
        """Copy the database file to a timestamped backup."""
        dest = Path(destination) if destination else self.db_path.with_name(
            f"{self.db_path.stem}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}{self.db_path.suffix}"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.db_path, dest)
        return dest

    def export_json(self, destination: Path | str) -> Path:
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        invoices = self.list_invoices()
        payload = {
            "exported_at": _now_iso(),
            "invoice_count": len(invoices),
            "invoices": [inv.as_dict() for inv in invoices],
        }
        with dest.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        return dest

    # --- Duplicate helpers ---

    def find_by_hash(self, document_hash: str) -> Invoice | None:
        if not document_hash:
            return None
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM invoices WHERE document_hash = ?", (document_hash,)
            ).fetchone()
        return Invoice.from_db_row(dict(row)) if row else None

    def find_by_pdf_path(self, pdf_path: str) -> Invoice | None:
        if not pdf_path:
            return None
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM invoices WHERE original_pdf_path = ? ORDER BY id DESC LIMIT 1",
                (pdf_path,),
            ).fetchone()
            if row:
                return Invoice.from_db_row(dict(row))
            # Fallback: match by filename when the folder path changed
            name = Path(pdf_path).name
            row = conn.execute(
                "SELECT * FROM invoices WHERE document_filename = ? ORDER BY id DESC LIMIT 1",
                (name,),
            ).fetchone()
        return Invoice.from_db_row(dict(row)) if row else None

    def find_existing_for_reimport(
        self,
        *,
        document_hash: str | None,
        pdf_path: str | None,
    ) -> Invoice | None:
        """Locate an existing invoice to overwrite during re-import."""
        if document_hash:
            found = self.find_by_hash(document_hash)
            if found:
                return self.get_invoice(found.id) if found.id else found
        if pdf_path:
            found = self.find_by_pdf_path(pdf_path)
            if found:
                return self.get_invoice(found.id) if found.id else found
        return None

    def find_duplicates(
        self,
        invoice_number: str | None,
        party_key: str | None,
        issue_date: date | None,
        document_direction: str,
        exclude_id: int | None = None,
    ) -> list[Invoice]:
        """Find potential duplicates by number + party + date + direction."""
        if not invoice_number:
            return []
        normalized = invoice_number.strip().upper()
        sql = """
            SELECT * FROM invoices
            WHERE UPPER(TRIM(invoice_number)) = ?
              AND document_direction = ?
        """
        params: list[Any] = [normalized, document_direction]
        if issue_date:
            sql += " AND issue_date = ?"
            params.append(issue_date.isoformat())
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        results = [Invoice.from_db_row(dict(r)) for r in rows]
        if party_key:
            party_norm = party_key.strip().upper()
            filtered = []
            for inv in results:
                names = " ".join(
                    filter(
                        None,
                        [
                            inv.supplier_name,
                            inv.supplier_vat,
                            inv.customer_name,
                            inv.customer_vat,
                        ],
                    )
                ).upper()
                if party_norm in names or (inv.supplier_vat or "").upper() == party_norm or (
                    inv.customer_vat or ""
                ).upper() == party_norm:
                    filtered.append(inv)
                elif not inv.supplier_name and not inv.customer_name:
                    filtered.append(inv)
            return filtered
        return results

    # --- Invoice CRUD ---

    def insert_invoice(
        self,
        invoice: Invoice,
        overwrite_id: int | None = None,
        *,
        preserve_payments: bool = False,
    ) -> int:
        """Insert a new invoice, or replace an existing one if overwrite_id is set.

        When overwriting with preserve_payments=True, existing payment rows are
        re-attached after the fresh extraction is saved. Approval is whatever
        the caller set on ``invoice`` (typically draft on re-import).
        """
        preserved_payments = []
        preserved_created = None
        if overwrite_id is not None and preserve_payments:
            existing = self.get_invoice(overwrite_id)
            if existing:
                preserved_payments = list(existing.payments)
                preserved_created = existing.created_at

        if preserve_payments and preserved_payments:
            invoice.payments = preserved_payments
            # Restore payment summary from preserved records after attach
            total_paid = sum((p.amount or Decimal("0")) for p in preserved_payments)
            invoice.amount_paid = total_paid
            paid_dates = [p.payment_date for p in preserved_payments if p.payment_date]
            invoice.payment_date = max(paid_dates) if paid_dates else None

        invoice.recalculate_payment_fields()
        now = _now_iso()
        row = invoice.to_db_row()
        row.pop("id", None)
        row["created_at"] = preserved_created or invoice.created_at or now
        row["updated_at"] = now

        with self.connection() as conn:
            if overwrite_id is not None:
                # Cascade deletes children; payments already copied into memory if preserving
                conn.execute("DELETE FROM invoices WHERE id = ?", (overwrite_id,))
                cols = list(row.keys())
                placeholders = ", ".join("?" for _ in cols)
                col_sql = ", ".join(cols)
                conn.execute(
                    f"INSERT INTO invoices (id, {col_sql}) VALUES (?, {placeholders})",
                    [overwrite_id, *[row[c] for c in cols]],
                )
                invoice_id = overwrite_id
            else:
                cols = list(row.keys())
                placeholders = ", ".join("?" for _ in cols)
                col_sql = ", ".join(cols)
                cur = conn.execute(
                    f"INSERT INTO invoices ({col_sql}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )
                invoice_id = int(cur.lastrowid)

            self._save_children(conn, invoice_id, invoice)
        return invoice_id

    def _save_children(self, conn: sqlite3.Connection, invoice_id: int, invoice: Invoice) -> None:
        for item in invoice.line_items:
            conn.execute(
                """
                INSERT INTO invoice_line_items
                (invoice_id, description, quantity, unit_price, net_amount, vat_rate, vat_amount, gross_amount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    item.description,
                    decimal_to_str(item.quantity),
                    decimal_to_str(item.unit_price),
                    decimal_to_str(item.net_amount),
                    decimal_to_str(item.vat_rate),
                    decimal_to_str(item.vat_amount),
                    decimal_to_str(item.gross_amount),
                ),
            )
        for vat in invoice.vat_breakdown:
            conn.execute(
                """
                INSERT INTO vat_breakdown (invoice_id, vat_rate, net_base, vat_amount)
                VALUES (?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    decimal_to_str(vat.vat_rate),
                    decimal_to_str(vat.net_base),
                    decimal_to_str(vat.vat_amount),
                ),
            )
        for payment in invoice.payments:
            conn.execute(
                """
                INSERT INTO payments (invoice_id, amount, payment_date, payment_method, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    decimal_to_str(payment.amount) or "0",
                    payment.payment_date.isoformat() if payment.payment_date else None,
                    payment.payment_method,
                    payment.notes,
                    payment.created_at or _now_iso(),
                ),
            )

    def update_invoice(self, invoice: Invoice) -> None:
        if invoice.id is None:
            raise ValueError("Cannot update invoice without id")
        invoice.recalculate_payment_fields()
        row = invoice.to_db_row()
        invoice_id = row.pop("id")
        row["updated_at"] = _now_iso()
        # Preserve created_at
        row.pop("created_at", None)
        assignments = ", ".join(f"{k} = ?" for k in row)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE invoices SET {assignments} WHERE id = ?",
                [*row.values(), invoice_id],
            )
            conn.execute("DELETE FROM invoice_line_items WHERE invoice_id = ?", (invoice_id,))
            conn.execute("DELETE FROM vat_breakdown WHERE invoice_id = ?", (invoice_id,))
            # Do not wipe payments on field update — only refresh line/vat
            temp = Invoice(
                line_items=invoice.line_items,
                vat_breakdown=invoice.vat_breakdown,
                payments=[],
            )
            self._save_children(conn, invoice_id, temp)

    def get_invoice(self, invoice_id: int, include_children: bool = True) -> Invoice | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
            if not row:
                return None
            invoice = Invoice.from_db_row(dict(row))
            if include_children:
                invoice.line_items = self._load_line_items(conn, invoice_id)
                invoice.vat_breakdown = self._load_vat(conn, invoice_id)
                invoice.payments = self._load_payments(conn, invoice_id)
        return invoice

    def _load_line_items(self, conn: sqlite3.Connection, invoice_id: int) -> list[LineItem]:
        rows = conn.execute(
            "SELECT * FROM invoice_line_items WHERE invoice_id = ?", (invoice_id,)
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            items.append(
                LineItem(
                    description=d.get("description"),
                    quantity=to_decimal(d.get("quantity")),
                    unit_price=to_decimal(d.get("unit_price")),
                    net_amount=to_decimal(d.get("net_amount")),
                    vat_rate=to_decimal(d.get("vat_rate")),
                    vat_amount=to_decimal(d.get("vat_amount")),
                    gross_amount=to_decimal(d.get("gross_amount")),
                )
            )
        return items

    def _load_vat(self, conn: sqlite3.Connection, invoice_id: int) -> list[VatBreakdownRow]:
        rows = conn.execute(
            "SELECT * FROM vat_breakdown WHERE invoice_id = ?", (invoice_id,)
        ).fetchall()
        return [
            VatBreakdownRow(
                vat_rate=to_decimal(dict(r).get("vat_rate")),
                net_base=to_decimal(dict(r).get("net_base")),
                vat_amount=to_decimal(dict(r).get("vat_amount")),
            )
            for r in rows
        ]

    def _load_payments(self, conn: sqlite3.Connection, invoice_id: int) -> list[PaymentRecord]:
        rows = conn.execute(
            "SELECT * FROM payments WHERE invoice_id = ? ORDER BY payment_date", (invoice_id,)
        ).fetchall()
        payments = []
        for r in rows:
            d = dict(r)
            payments.append(
                PaymentRecord(
                    id=d.get("id"),
                    invoice_id=d.get("invoice_id"),
                    amount=to_decimal(d.get("amount")),
                    payment_date=parse_date(d.get("payment_date")),
                    payment_method=d.get("payment_method"),
                    notes=d.get("notes"),
                    created_at=d.get("created_at"),
                )
            )
        return payments

    def delete_invoice(self, invoice_id: int) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))

    def list_invoices(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        customer: str | None = None,
        supplier: str | None = None,
        invoice_type: str | None = None,
        payment_status: str | None = None,
        currency: str | None = None,
        document_direction: str | None = None,
        approval_status: str | None = None,
        search: str | None = None,
    ) -> list[Invoice]:
        clauses: list[str] = []
        params: list[Any] = []
        if date_from:
            clauses.append("issue_date >= ?")
            params.append(date_from.isoformat())
        if date_to:
            clauses.append("issue_date <= ?")
            params.append(date_to.isoformat())
        if customer:
            clauses.append("LOWER(customer_name) LIKE ?")
            params.append(f"%{customer.lower()}%")
        if supplier:
            clauses.append("LOWER(supplier_name) LIKE ?")
            params.append(f"%{supplier.lower()}%")
        if invoice_type:
            clauses.append("invoice_type = ?")
            params.append(invoice_type)
        if payment_status:
            clauses.append("payment_status = ?")
            params.append(payment_status)
        if currency:
            clauses.append("currency = ?")
            params.append(currency)
        if document_direction:
            clauses.append("document_direction = ?")
            params.append(document_direction)
        if approval_status:
            clauses.append("approval_status = ?")
            params.append(approval_status)
        if search:
            clauses.append(
                "(LOWER(invoice_number) LIKE ? OR LOWER(customer_name) LIKE ? "
                "OR LOWER(supplier_name) LIKE ? OR LOWER(document_filename) LIKE ?)"
            )
            q = f"%{search.lower()}%"
            params.extend([q, q, q, q])

        sql = "SELECT * FROM invoices"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(issue_date, '') DESC, id DESC"

        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Invoice.from_db_row(dict(r)) for r in rows]

    def set_approval_status(self, invoice_id: int, status: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE invoices SET approval_status = ?, updated_at = ? WHERE id = ?",
                (status, _now_iso(), invoice_id),
            )

    # --- Payments ---

    def add_payment(self, payment: PaymentRecord) -> int:
        if payment.invoice_id is None:
            raise ValueError("payment.invoice_id required")
        with self.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO payments (invoice_id, amount, payment_date, payment_method, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payment.invoice_id,
                    decimal_to_str(payment.amount) or "0",
                    payment.payment_date.isoformat() if payment.payment_date else None,
                    payment.payment_method,
                    payment.notes,
                    _now_iso(),
                ),
            )
            payment_id = int(cur.lastrowid)
        self._refresh_invoice_payment_summary(payment.invoice_id)
        return payment_id

    def delete_payment(self, payment_id: int) -> None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT invoice_id FROM payments WHERE id = ?", (payment_id,)
            ).fetchone()
            if not row:
                return
            invoice_id = int(row["invoice_id"])
            conn.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
        self._refresh_invoice_payment_summary(invoice_id)

    def _refresh_invoice_payment_summary(self, invoice_id: int) -> None:
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            return
        total_paid = sum((p.amount or Decimal("0")) for p in invoice.payments)
        invoice.amount_paid = total_paid
        paid_dates = [p.payment_date for p in invoice.payments if p.payment_date]
        invoice.payment_date = max(paid_dates) if paid_dates else None
        methods = [p.payment_method for p in invoice.payments if p.payment_method]
        invoice.payment_method = methods[-1] if methods else invoice.payment_method
        invoice.recalculate_payment_fields()
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE invoices
                SET amount_paid = ?, payment_date = ?, payment_method = ?,
                    payment_status = ?, outstanding_balance = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    decimal_to_str(invoice.amount_paid),
                    invoice.payment_date.isoformat() if invoice.payment_date else None,
                    invoice.payment_method,
                    invoice.payment_status,
                    decimal_to_str(invoice.outstanding_balance),
                    _now_iso(),
                    invoice_id,
                ),
            )

    def list_payments(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        invoice_id: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if date_from:
            clauses.append("p.payment_date >= ?")
            params.append(date_from.isoformat())
        if date_to:
            clauses.append("p.payment_date <= ?")
            params.append(date_to.isoformat())
        if invoice_id is not None:
            clauses.append("p.invoice_id = ?")
            params.append(invoice_id)
        sql = """
            SELECT p.*, i.invoice_number, i.customer_name, i.supplier_name, i.currency
            FROM payments p
            JOIN invoices i ON i.id = p.invoice_id
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(p.payment_date, '') DESC, p.id DESC"
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def find_invoice_by_number(self, invoice_number: str) -> Invoice | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM invoices WHERE UPPER(TRIM(invoice_number)) = ? ORDER BY id DESC LIMIT 1",
                (invoice_number.strip().upper(),),
            ).fetchone()
        return Invoice.from_db_row(dict(row)) if row else None

    # --- Processing runs ---

    def start_processing_run(self, folder_path: str, total_files: int) -> int:
        with self.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO processing_runs (started_at, folder_path, total_files)
                VALUES (?, ?, ?)
                """,
                (_now_iso(), folder_path, total_files),
            )
            return int(cur.lastrowid)

    def finish_processing_run(
        self,
        run_id: int,
        success_count: int,
        failure_count: int,
        notes: str | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE processing_runs
                SET finished_at = ?, success_count = ?, failure_count = ?, notes = ?
                WHERE id = ?
                """,
                (_now_iso(), success_count, failure_count, notes, run_id),
            )

    def add_extraction_warning(
        self,
        message: str,
        *,
        processing_run_id: int | None = None,
        invoice_id: int | None = None,
        document_filename: str | None = None,
        warning_level: str = "warning",
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO extraction_warnings
                (processing_run_id, invoice_id, document_filename, warning_level, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    processing_run_id,
                    invoice_id,
                    document_filename,
                    warning_level,
                    message,
                    _now_iso(),
                ),
            )

    def distinct_values(self, column: str) -> list[str]:
        allowed = {
            "customer_name",
            "supplier_name",
            "currency",
            "invoice_type",
            "payment_status",
            "document_direction",
            "approval_status",
        }
        if column not in allowed:
            raise ValueError(f"Column not allowed: {column}")
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT {column} FROM invoices WHERE {column} IS NOT NULL AND {column} != '' ORDER BY {column}"
            ).fetchall()
        return [r[0] for r in rows]
