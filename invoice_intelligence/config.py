"""Application configuration — local paths, OCR, Ollama, financial defaults."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

# Project roots
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_SETTINGS_PATH = DEFAULT_DATA_DIR / "settings.json"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "invoices.db"

KNOWN_CURRENCIES = frozenset(
    {
        "EUR",
        "USD",
        "GBP",
        "CHF",
        "JPY",
        "CAD",
        "AUD",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
        "CZK",
        "HUF",
        "RON",
        "BGN",
        "TRY",
        "CNY",
    }
)

DEFAULT_SETTINGS: dict[str, Any] = {
    "database_path": str(DEFAULT_DB_PATH),
    "pdf_import_folder": "",
    "ocr_executable": "tesseract",
    "ocr_languages": "ell+eng",
    "ollama_endpoint": "http://127.0.0.1:11434",
    "ollama_model": "llama3.2",
    "ollama_enabled": False,
    "default_currency": "EUR",
    "rounding_tolerance": "0.02",
    "default_reporting_period_days": 365,
    "ocr_min_text_chars": 40,
    "low_confidence_threshold": 0.55,
    "imports_are_paid": True,
}


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_settings(settings_path: Path | None = None) -> dict[str, Any]:
    """Load settings from JSON, merging with defaults."""
    path = settings_path or DEFAULT_SETTINGS_PATH
    settings = deepcopy(DEFAULT_SETTINGS)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                settings.update(stored)
        except (OSError, json.JSONDecodeError):
            pass
    return settings


def save_settings(settings: dict[str, Any], settings_path: Path | None = None) -> Path:
    """Persist settings to JSON."""
    path = settings_path or DEFAULT_SETTINGS_PATH
    _ensure_parent(path)
    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    merged = deepcopy(DEFAULT_SETTINGS)
    merged.update(settings)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, ensure_ascii=False)
    return path


def get_db_path(settings: dict[str, Any] | None = None) -> Path:
    cfg = settings or load_settings()
    return Path(cfg.get("database_path") or DEFAULT_DB_PATH)


def get_data_dir() -> Path:
    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_DATA_DIR
