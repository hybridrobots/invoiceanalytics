"""Lightweight local Flask UI (replaces Streamlit)."""

from invoice_intelligence.web.app_factory import create_app

__all__ = ["create_app"]
