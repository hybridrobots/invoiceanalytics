"""Layer 2: OCR fallback for scanned invoice PDFs."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class OcrResult:
    text: str
    page_count: int
    error: str | None = None
    available: bool = True


def tesseract_available(executable: str = "tesseract") -> bool:
    return shutil.which(executable) is not None


def ocr_pdf(
    path: str | Path,
    *,
    executable: str = "tesseract",
    languages: str = "ell+eng",
    dpi: int = 200,
    max_pages: int = 20,
) -> OcrResult:
    """Rasterize PDF pages with PyMuPDF and run Tesseract OCR."""
    if not tesseract_available(executable):
        return OcrResult(
            text="",
            page_count=0,
            error=f"Tesseract not found ({executable}). Install tesseract-ocr or set path in Settings.",
            available=False,
        )

    try:
        import fitz
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        return OcrResult(text="", page_count=0, error=f"OCR dependencies missing: {exc}", available=False)

    pytesseract.pytesseract.tesseract_cmd = executable
    pdf_path = Path(path)

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # noqa: BLE001
        return OcrResult(text="", page_count=0, error=f"Cannot open PDF for OCR: {exc}")

    parts: list[str] = []
    try:
        page_count = min(doc.page_count, max_pages)
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for i in range(page_count):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            try:
                page_text = pytesseract.image_to_string(image, lang=languages)
            except pytesseract.TesseractError:
                # Fallback to English only
                try:
                    page_text = pytesseract.image_to_string(image, lang="eng")
                except Exception as ocr_exc:  # noqa: BLE001
                    logger.warning("OCR failed on page %s: %s", i + 1, ocr_exc)
                    page_text = ""
            parts.append(page_text or "")
        doc.close()
        return OcrResult(text="\n".join(parts).strip(), page_count=page_count)
    except Exception as exc:  # noqa: BLE001
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
        return OcrResult(text="", page_count=0, error=f"OCR failed: {exc}")
