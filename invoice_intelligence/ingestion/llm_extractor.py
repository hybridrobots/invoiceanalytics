"""Optional Layer 3b: Ollama-based structured extraction (never invents values)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

EXTRACTION_SCHEMA_HINT = """
Return ONLY a JSON object with these keys (use null when unknown — never invent):
invoice_number, invoice_type, issue_date, due_date, currency,
supplier_name, supplier_vat, customer_name, customer_vat,
net_amount, vat_amount, gross_amount, discount_amount, other_charges,
withholding_tax, total_payable, description, document_direction
invoice_type: invoice | credit_note | receipt | other
document_direction: sales | purchase | unknown
Dates as YYYY-MM-DD. Amounts as numbers with dot decimal separator.
If unsure, use null.
"""


def ollama_available(endpoint: str, timeout: float = 2.0) -> bool:
    try:
        resp = requests.get(f"{endpoint.rstrip('/')}/api/tags", timeout=timeout)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def extract_with_ollama(
    text: str,
    *,
    endpoint: str = "http://127.0.0.1:11434",
    model: str = "llama3.2",
    timeout: float = 120.0,
) -> dict[str, Any] | None:
    """Ask Ollama to extract invoice fields. Returns dict or None on failure."""
    if not text or not text.strip():
        return None

    # Cap text size to keep prompts manageable
    truncated = text[:8000]
    prompt = (
        "You extract structured invoice data from OCR/PDF text. "
        "NEVER invent missing values. Use null when a field is not clearly present.\n"
        f"{EXTRACTION_SCHEMA_HINT}\n\n"
        f"INVOICE TEXT:\n{truncated}"
    )

    url = f"{endpoint.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        raw = body.get("response", "")
        return _parse_json_response(raw)
    except requests.RequestException as exc:
        logger.warning("Ollama request failed: %s", exc)
        return None
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Ollama JSON parse failed: %s", exc)
        return None


def _parse_json_response(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None
