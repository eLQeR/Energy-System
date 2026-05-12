"""Stage 2 — relevance filter (no LLM).

A heat-pump installation manual contains four kinds of pages we care about:
1. Specifications / technical data tables
2. Troubleshooting (symptom → cause → solution) tables
3. Error-code tables
4. Maintenance / parts-replacement schedules

Plus lots of pages we don't care about (install steps, wiring diagrams,
warranty boilerplate, multilingual cover sheets). This module scores each
page and keeps the most relevant ones — the single biggest token-saving step.
"""
from __future__ import annotations

import re

# ── Specifications / performance tables ───────────────────────────────
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

# ── Troubleshooting tables (fault symptoms, causes, fixes) ────────────
TROUBLE_WORDS = re.compile(
    r"\b("
    r"trouble\s*shoot(ing)?|fault\s+(symptom|finding|cause)|"
    r"possible\s+cause|solution|symptom|"
    r"error\s+code|fault\s+code|"
    r"reset\s+button|breaker|tripped|cut\s*out|cutout|"
    r"thermostat|thermal\s+cut|overheat|"
    r"replace\s+if|check\s+the|isolate|drain\s+cock|"
    r"strainer|leakage"
    r")\b",
    re.I,
)

# ── Maintenance / inspection schedules ────────────────────────────────
MAINT_WORDS = re.compile(
    r"\b("
    r"maintenance|service\s+(interval|menu|manual)|"
    r"replace\s+every|check\s+every|inspection|"
    r"annual\s+maintenance|log\s+book|"
    r"parts?\s+(which|requiring|require)|"
    r"typical\s+failure|years?\b|hours?\b"
    r")\b",
    re.I,
)

# ── Genuine noise we still want to drop ───────────────────────────────
NOISE_WORDS = re.compile(
    r"\b("
    r"declaration\s+of\s+conformity|warranty|disposal|"
    r"wiring\s+diagram|terminal\s+block|"
    r"connecting\s+the\s+(cable|wire|pipe)|tighten\s+the|"
    r"installation\s+(steps|procedure|location|space)|"
    r"safety\s+(precaution|warning|instruction)"
    r")\b",
    re.I,
)

# Multilingual cover-page detector: many short ALL-CAPS strings in non-English.
_MULTILANG_MARKERS = (
    "FÜR", "POUR", "PARA", "PER", "VOOR", "FÖR",
    "INSTALLAT", "FOR INSTALLER", "ИНСТРУКЦИЯ", "MANUEL",
)


def is_multilingual_header(text: str) -> bool:
    if len(text) > 1500:
        return False
    upper = text.upper()
    hits = sum(1 for m in _MULTILANG_MARKERS if m in upper)
    return hits >= 3


def score_page(text: str) -> int:
    """Composite score: positive for any of the 4 useful categories."""
    if not text.strip():
        return -1000
    if is_multilingual_header(text):
        return -1000

    spec_hits     = len(SPEC_WORDS.findall(text))
    trouble_hits  = len(TROUBLE_WORDS.findall(text))
    maint_hits    = len(MAINT_WORDS.findall(text))
    noise_hits    = len(NOISE_WORDS.findall(text))

    # Tables of numbers (kW, °C, hours) tend to be high-value spec pages.
    digit_density = sum(c.isdigit() for c in text) / max(len(text), 1)

    return (
        spec_hits * 3
        + trouble_hits * 4   # troubleshooting tables are dense and high-signal
        + maint_hits * 3
        - noise_hits * 2
        + int(digit_density * 80)
    )


def filter_relevant(
    pages: list[tuple[int, str]],
    min_score: int = 5,
    max_pages: int = 40,
) -> list[tuple[int, str]]:
    """Return the most relevant pages, in original order.

    max_pages is intentionally generous — Claude's 200K context easily
    holds 40 spec/trouble-dense pages, and the LLM cost difference is
    small compared to the value of capturing all fault knowledge.
    """
    scored = [(score_page(t), i, t) for i, t in pages]
    scored.sort(key=lambda x: -x[0])
    keep = [(i, t) for s, i, t in scored if s >= min_score][:max_pages]
    keep.sort()
    return keep
