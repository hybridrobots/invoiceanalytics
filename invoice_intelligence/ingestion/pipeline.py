"""End-to-end PDF → structured invoice extraction pipeline."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from invoice_intelligence.database.models import ExtractionResult, Invoice
from invoice_intelligence.ingestion.invoice_parser import merge_llm_fields, parse_invoice_text
from invoice_intelligence.ingestion.llm_extractor import extract_with_ollama
from invoice_intelligence.ingestion.ocr import ocr_pdf
from invoice_intelligence.ingestion.pdf_extractor import extract_pdf_text
from invoice_intelligence.ingestion.pdf_scanner import ScannedPdf, file_sha256

logger = logging.getLogger(__name__)


def process_pdf(
    path: str | Path,
    *,
    settings: dict[str, Any] | None = None,
    document_hash: str | None = None,
) -> ExtractionResult:
    """Process a single PDF through text → OCR → parse → optional LLM."""
    settings = settings or {}
    pdf_path = Path(path)
    filename = pdf_path.name
    doc_hash = document_hash or file_sha256(pdf_path)
    min_chars = int(settings.get("ocr_min_text_chars", 40))
    default_currency = settings.get("default_currency", "EUR")
    own_vat = settings.get("own_vat") or None

    text_result = extract_pdf_text(pdf_path, min_chars=min_chars)
    if text_result.is_encrypted:
        inv = Invoice(
            document_filename=filename,
            original_pdf_path=str(pdf_path),
            document_hash=doc_hash,
            currency=default_currency,
        )
        return ExtractionResult(
            invoice=inv,
            warnings=["PDF is password-protected"],
            error=text_result.error or "encrypted",
        )
    if text_result.is_corrupt and not text_result.text:
        inv = Invoice(
            document_filename=filename,
            original_pdf_path=str(pdf_path),
            document_hash=doc_hash,
            currency=default_currency,
        )
        return ExtractionResult(
            invoice=inv,
            warnings=[text_result.error or "Corrupt or unreadable PDF"],
            error=text_result.error or "corrupt",
        )

    used_ocr = False
    text = text_result.text or ""
    if len(text.strip()) < min_chars:
        ocr = ocr_pdf(
            pdf_path,
            executable=settings.get("ocr_executable", "tesseract"),
            languages=settings.get("ocr_languages", "ell+eng"),
        )
        if ocr.text and len(ocr.text.strip()) > len(text.strip()):
            text = ocr.text
            used_ocr = True
        elif ocr.error:
            logger.info("OCR skipped/failed for %s: %s", filename, ocr.error)

    result = parse_invoice_text(
        text,
        filename=filename,
        pdf_path=str(pdf_path.resolve()),
        document_hash=doc_hash,
        default_currency=default_currency,
        own_vat=own_vat,
    )
    result.used_ocr = used_ocr

    if settings.get("ollama_enabled"):
        llm_data = extract_with_ollama(
            text,
            endpoint=settings.get("ollama_endpoint", "http://127.0.0.1:11434"),
            model=settings.get("ollama_model", "llama3.2"),
        )
        if llm_data:
            threshold = float(settings.get("low_confidence_threshold", 0.55))
            result = merge_llm_fields(result, llm_data, low_confidence_threshold=threshold)
        else:
            result.warnings.append("LLM extraction enabled but Ollama returned no usable result")

    result.invoice.confidence_json = json.dumps(result.confidence, ensure_ascii=False)
    result.invoice.validation_warnings = json.dumps(result.warnings, ensure_ascii=False)
    return result


def process_scanned(scanned: ScannedPdf, settings: dict[str, Any] | None = None) -> ExtractionResult:
    return process_pdf(scanned.path, settings=settings, document_hash=scanned.document_hash)
