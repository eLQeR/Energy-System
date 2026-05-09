"""Stage 2 — relevance filter (no LLM).

Most of a heat-pump manual is install procedures, safety warnings, and
multilingual cover sheets. We only need pages that talk about technical
specifications, performance, capacities, and operating modes.

Filtering here is the single biggest token-saving step: a 50-page PDF
typically reduces to ~15-25 specification-dense pages.
"""
from __future__ import annotations

import re

# Words/phrases that indicate a "specs / technical data" section.
SPEC_WORDS = re.compile(
    r"\b("
    r"specification|technical\s+data|performance|capacity|"
    r"operating\s+(range|mode|conditions|temperature)|"
    r"power\s+(consumption|input|supply)|"
    r"coefficient|cop\b|refrigerant|tank|cylinder|"
    r"compressor|condenser|evaporator|"
    r"flow\s+(rate|temperature)|return\s+temperature|"
    r"nominal|rated|kw\b|kwh\b|"
    r"weight|dimensions|model|series|"
    r"voltage|frequency|hertz|amp"
    r")\b",
    re.I,
)

# Words/phrases that indicate noise.
NOISE_WORDS = re.compile(
    r"\b("
    r"warning|caution|disposal|warranty|"
    r"wiring\s+diagram|fault\s+code|trouble\s*shooting|"
    r"maintenance\s+procedure|installation\s+(steps|procedure)|"
    r"connecting\s+the|fix\s+the|tighten\s+the"
    r")\b",
    re.I,
)

# Multilingual cover-page detector: many short ALL-CAPS strings in non-English.
_MULTILANG_MARKERS = ("FÜR", "POUR", "PARA", "PER", "VOOR", "FÖR", "INSTALLAT", "FOR INSTALLER")


def is_multilingual_header(text: str) -> bool:
    if len(text) > 1500:
        return False
    upper = text.upper()
    hits = sum(1 for m in _MULTILANG_MARKERS if m in upper)
    return hits >= 3


def score_page(text: str) -> int:
    if not text.strip():
        return -1000
    if is_multilingual_header(text):
        return -1000
    spec_hits = len(SPEC_WORDS.findall(text))
    noise_hits = len(NOISE_WORDS.findall(text))
    # tables of numbers tend to have lots of "kW" and digits — boost those
    digit_density = sum(c.isdigit() for c in text) / max(len(text), 1)
    return spec_hits * 3 - noise_hits * 2 + int(digit_density * 100)


def filter_relevant(
    pages: list[tuple[int, str]],
    min_score: int = 5,
    max_pages: int = 30,
) -> list[tuple[int, str]]:
    """Return the most relevant pages, in original order."""
    scored = [(score_page(t), i, t) for i, t in pages]
    scored.sort(key=lambda x: -x[0])
    keep = [(i, t) for s, i, t in scored if s >= min_score][:max_pages]
    keep.sort()
    return keep
