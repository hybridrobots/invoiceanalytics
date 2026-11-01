"""PDF extraction and end-to-end pipeline tests with synthetic PDFs."""

from pathlib import Path

from invoice_intelligence.ingestion.pdf_extractor import extract_pdf_text
from invoice_intelligence.ingestion.pdf_scanner import scan_pdf_folder
from invoice_intelligence.ingestion.pipeline import process_pdf
from invoice_intelligence.tests.helpers import make_simple_pdf


def test_scan_and_extract_pdf(tmp_path: Path, sample_text: str) -> None:
    pdf_path = make_simple_pdf(tmp_path / "greek_invoice.pdf", sample_text)
    scanned = scan_pdf_folder(tmp_path, recursive=False)
    assert len(scanned) == 1
    assert scanned[0].filename == "greek_invoice.pdf"

    text_result = extract_pdf_text(pdf_path)
    assert text_result.text
    assert "0015" in text_result.text or "1000" in text_result.text.replace(".", "").replace(",", "")
    assert not text_result.is_encrypted


def test_pipeline_on_synthetic_pdf(tmp_path: Path, sample_text: str) -> None:
    pdf_path = make_simple_pdf(tmp_path / "inv.pdf", sample_text)
    result = process_pdf(pdf_path, settings={"default_currency": "EUR", "ollama_enabled": False})
    assert result.invoice.document_hash
    assert result.invoice.invoice_number in {"ΤΙΜ-2024-0015", "TIM-2024-0015"}
    assert result.invoice.gross_amount is not None
