"""Stage 1 — PDF → text per page (no LLM)."""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader


def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Return list of (page_index, text). page_index is 0-based."""
    reader = PdfReader(str(pdf_path))
    return [(i, p.extract_text() or "") for i, p in enumerate(reader.pages)]
