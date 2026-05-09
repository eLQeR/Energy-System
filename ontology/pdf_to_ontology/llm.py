"""Stage 4 — single LLM call with structured output.

Uses Anthropic forced tool use with strict=True for guaranteed schema-valid
JSON. This works on any anthropic SDK ≥ 0.34 (we ship 0.45). Switch to
client.messages.parse() once we bump the SDK version.

One call per PDF — no chunking needed because we already filtered to
≤30 spec-dense pages (~25K tokens, well under the model's context).
"""
from __future__ import annotations

import logging

import anthropic

from .schema import HeatPumpProfile

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You extract structured technical specifications from
heat-pump manufacturer manuals.

Read the provided manual excerpts (already filtered to specification-rich
pages) and call the record_specs tool exactly once with the extracted
HeatPumpProfile.

Rules:
1. Cite ONLY values stated explicitly in the text. Use null when a value
   is not stated. Do not infer or estimate from related values.
2. Prefer English text. If a value appears only in a non-English section,
   extract it but use the English unit names.
3. If multiple variants are described in one table, pick the most
   representative numbers (typically the middle or default variant) and
   list ALL variant SKUs in model_variants.
4. Convert units to the schema's units: °C, kW, kg, L, V.
5. operating_modes: include a mode only if the manual states the unit
   supports it (heating / cooling / dhw / standby).
"""


def extract_profile(text: str, model: str = "claude-opus-4-7") -> HeatPumpProfile:
    """Send filtered manual text to Claude, return validated profile."""
    client = anthropic.Anthropic()

    schema = HeatPumpProfile.model_json_schema()
    # Anthropic strict mode requires `additionalProperties: false` on every
    # object — Pydantic emits this only when extra="forbid" is set on the
    # model (which we do in schema.py).

    tool = {
        "name": "record_specs",
        "description": "Record extracted heat-pump specifications.",
        "input_schema": schema,
    }

    log.info("Calling %s on %d chars (~%d tokens) of input",
             model, len(text), len(text) // 4)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": "record_specs"},
        messages=[{"role": "user", "content": text}],
    )

    log.info(
        "Tokens — input=%d output=%d (cache_read=%d)",
        response.usage.input_tokens,
        response.usage.output_tokens,
        getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    )

    tool_use = next(b for b in response.content if b.type == "tool_use")
    return HeatPumpProfile.model_validate(tool_use.input)
