"""Scan folders for PDF invoice files."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ScannedPdf:
    path: Path
    filename: str
    size_bytes: int
    document_hash: str


def file_sha256(path: Path, chunk_size: int = 65536) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def scan_pdf_folder(folder: str | Path, recursive: bool = True) -> list[ScannedPdf]:
    """Return sorted list of PDF files found under folder."""
    root = Path(folder)
    if not root.exists():
        raise FileNotFoundError(f"Folder not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    pattern = "**/*.pdf" if recursive else "*.pdf"
    results: list[ScannedPdf] = []
    for path in sorted(root.glob(pattern)):
        if not path.is_file():
            continue
        # Skip macOS resource forks / hidden junk
        if path.name.startswith("._") or path.name.startswith("."):
            continue
        try:
            results.append(
                ScannedPdf(
                    path=path.resolve(),
                    filename=path.name,
                    size_bytes=path.stat().st_size,
                    document_hash=file_sha256(path),
                )
            )
        except OSError:
            continue
    return results
