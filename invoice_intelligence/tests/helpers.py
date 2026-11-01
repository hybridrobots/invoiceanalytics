"""Test helpers for synthetic documents."""

from __future__ import annotations

from pathlib import Path


def make_simple_pdf(path: Path, text: str) -> Path:
    """Create a digital PDF. Prefers Unicode via PyMuPDF textpage; falls back to English labels."""
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    # Try writing with a font file that supports Greek if present
    fontfile = _find_unicode_font()
    y = 50
    wrote = False
    if fontfile:
        try:
            page.insert_font(fontname="uni", fontfile=fontfile)
            for line in text.splitlines():
                page.insert_text((40, y), line[:120], fontsize=11, fontname="uni")
                y += 14
                if y > 800:
                    page = doc.new_page(width=595, height=842)
                    page.insert_font(fontname="uni", fontfile=fontfile)
                    y = 50
            wrote = True
        except Exception:
            wrote = False

    if not wrote:
        # English-labeled content the Greek-first parser still understands via English patterns
        fallback = (
            "INVOICE\n"
            "Invoice Number: TIM-2024-0015\n"
            "Issue Date: 15/03/2024\n"
            "Due Date: 15/04/2024\n"
            "Supplier: NEFELI SERVICES SA\n"
            "VAT ID: 998877665\n"
            "Customer: AKROPOLI CONSULTING\n"
            "VAT ID: 112233445\n"
            "Net Amount: 1.000,00\n"
            "VAT 24%: 240,00\n"
            "Total Amount: 1.240,00\n"
            "Amount Payable: 1.240,00\n"
            "Currency: EUR\n"
        )
        y = 50
        for line in fallback.splitlines():
            page.insert_text((40, y), line, fontsize=11, fontname="helv")
            y += 14

    doc.save(str(path))
    doc.close()
    return path


def _find_unicode_font() -> str | None:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None
