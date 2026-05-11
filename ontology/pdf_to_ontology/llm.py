"""Stage 4 — single LLM call with structured output.

Uses Anthropic forced tool use with strict=True for guaranteed schema-valid
JSON. This works on any anthropic SDK ≥ 0.34 (we ship 0.45). Switch to
client.messages.parse() once we bump the SDK version.

One call per PDF — no chunking needed because we already filtered to
≤30 spec-dense pages (~25K tokens, well under the model's context).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import anthropic

from .schema import HeatPumpProfile

# Common install locations for the `claude` CLI (in addition to PATH).
CLAUDE_FALLBACK_PATHS = [
    Path.home() / ".local/bin/claude",
    Path.home() / ".claude/local/claude",
    Path("/usr/local/bin/claude"),
    Path("/opt/homebrew/bin/claude"),
]

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
    """Send filtered manual text to Claude, return validated profile.

    Uses the Anthropic API when ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN)
    is set; otherwise falls back to the `claude` CLI which uses the user's
    subscription credentials.
    """
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return _extract_via_api(text, model)
    claude_bin = _find_claude_binary()
    if claude_bin:
        log.info("No API key set — falling back to `claude` CLI at %s", claude_bin)
        return _extract_via_cli(text, model, claude_bin)
    raise RuntimeError(
        "No ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN set and `claude` CLI "
        "not found in PATH or common locations. Set an API key in .env, "
        "install Claude Code CLI, or set CLAUDE_BIN to its full path."
    )


def _find_claude_binary() -> str | None:
    """Find the `claude` binary: env override → PATH → common install dirs."""
    override = os.getenv("CLAUDE_BIN")
    if override and Path(override).exists():
        return override
    found = shutil.which("claude")
    if found:
        return found
    for p in CLAUDE_FALLBACK_PATHS:
        if p.exists():
            return str(p)
    return None


def _extract_via_api(text: str, model: str) -> HeatPumpProfile:
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


def _extract_via_cli(text: str, model: str, claude_bin: str) -> HeatPumpProfile:
    """Fallback: call the local `claude` CLI; it authenticates via subscription."""
    schema = HeatPumpProfile.model_json_schema()
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"<json_schema>\n{json.dumps(schema, indent=2)}\n</json_schema>\n\n"
        f"<manual_excerpts>\n{text}\n</manual_excerpts>\n\n"
        "Return ONLY a valid JSON object matching the schema above. "
        "No prose, no markdown fences, no commentary — just the JSON."
    )
    log.info("Calling `claude` CLI (model=%s, %d chars prompt)", model, len(prompt))

    try:
        result = subprocess.run(
            [claude_bin, "-p", "--model", model, "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"`claude` CLI not found at {claude_bin}") from e

    if result.returncode != 0:
        raise RuntimeError(
            f"`claude` CLI exited {result.returncode}: {result.stderr.strip()[:500]}"
        )

    raw = _strip_json_fence(result.stdout.strip())
    if not raw:
        raise RuntimeError("`claude` CLI returned empty output")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"`claude` CLI output is not valid JSON: {e}. First 300 chars: {raw[:300]}"
        ) from e
    return HeatPumpProfile.model_validate(data)


def _strip_json_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s
