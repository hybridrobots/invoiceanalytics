"""Layer 1: extract embedded text from digitally generated PDFs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PdfTextResult:
    text: str
    page_count: int
    method: str
    error: str | None = None
    is_encrypted: bool = False
    is_corrupt: bool = False


def extract_text_pymupdf(path: Path) -> PdfTextResult:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return PdfTextResult(text="", page_count=0, method="pymupdf", error="PyMuPDF not installed")

    try:
        doc = fitz.open(path)
    except Exception as exc:  # noqa: BLE001 — treat all open failures as corrupt/unreadable
        msg = str(exc).lower()
        encrypted = "password" in msg or "encrypted" in msg
        return PdfTextResult(
            text="",
            page_count=0,
            method="pymupdf",
            error=f"Cannot open PDF: {exc}",
            is_encrypted=encrypted,
            is_corrupt=not encrypted,
        )

    try:
        if doc.is_encrypted:
            # Try empty password; if still locked, report encrypted
            auth = doc.authenticate("")
            if not auth:
                page_count = doc.page_count
                doc.close()
                return PdfTextResult(
                    text="",
                    page_count=page_count,
                    method="pymupdf",
                    error="PDF is password-protected",
                    is_encrypted=True,
                )
        parts: list[str] = []
        for page in doc:
            parts.append(page.get_text("text") or "")
        text = "\n".join(parts).strip()
        page_count = doc.page_count
        doc.close()
        return PdfTextResult(text=text, page_count=page_count, method="pymupdf")
    except Exception as exc:  # noqa: BLE001
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
        return PdfTextResult(
            text="",
            page_count=0,
            method="pymupdf",
            error=f"PDF read error: {exc}",
            is_corrupt=True,
        )


def extract_text_pdfplumber(path: Path) -> PdfTextResult:
    try:
        import pdfplumber
    except ImportError:
        return PdfTextResult(text="", page_count=0, method="pdfplumber", error="pdfplumber not installed")

    try:
        parts: list[str] = []
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
        return PdfTextResult(text=text, page_count=page_count, method="pdfplumber")
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        return PdfTextResult(
            text="",
            page_count=0,
            method="pdfplumber",
            error=f"pdfplumber error: {exc}",
            is_encrypted="password" in msg or "encrypted" in msg,
            is_corrupt=True,
        )


def extract_pdf_text(path: str | Path, min_chars: int = 40) -> PdfTextResult:
    """Extract text preferring PyMuPDF, falling back to pdfplumber if sparse."""
    pdf_path = Path(path)
    primary = extract_text_pymupdf(pdf_path)
    if primary.is_encrypted or (primary.error and primary.is_corrupt and not primary.text):
        return primary
    if len(primary.text.strip()) >= min_chars:
        return primary
    fallback = extract_text_pdfplumber(pdf_path)
    if len(fallback.text.strip()) > len(primary.text.strip()):
        return fallback
    if primary.text:
        return primary
    return fallback if fallback.text else primary
