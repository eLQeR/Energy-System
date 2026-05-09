"""CLI: PDF + device_id → Turtle fragment for the ontology.

  python3 -m ontology.pdf_to_ontology.cli \\
      --pdf ontology/BH79D188H02.pdf \\
      --device-id ehst20 \\
      --out ontology/extracted/ehst20.ttl

In Docker:
  docker compose --profile tools run --rm pdf_extractor \\
      --pdf ontology/BH79D188H02.pdf --device-id ehst20 \\
      --out ontology/extracted/ehst20.ttl
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path

from .extract import extract_pages
from .filter import filter_relevant
from .llm import extract_profile
from .schema import HeatPumpProfile
from .turtle import to_turtle

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("pdf_to_ontology")


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract heat-pump specs from a PDF manual.")
    ap.add_argument("--pdf", required=True, type=Path, help="Path to manufacturer PDF")
    ap.add_argument("--device-id", required=True, help="Local name in the ontology (e.g. ehst20)")
    ap.add_argument("--out", type=Path, help="Write Turtle here (else stdout)")
    ap.add_argument("--model", default="claude-opus-4-7",
                    help="Anthropic model. claude-haiku-4-5 is ~5x cheaper.")
    ap.add_argument("--cache-dir", type=Path, default=Path("ontology/.pdf_cache"),
                    help="Cache LLM output here (resumable; safe to delete)")
    ap.add_argument("--max-pages", type=int, default=30,
                    help="Cap of relevant pages to send to the LLM")
    ap.add_argument("--no-cache", action="store_true", help="Force a fresh LLM call")
    args = ap.parse_args()

    if not args.pdf.exists():
        log.error("PDF not found: %s", args.pdf)
        return 1

    pages = extract_pages(args.pdf)
    log.info("Extracted %d pages from %s", len(pages), args.pdf.name)

    relevant = filter_relevant(pages, max_pages=args.max_pages)
    log.info("Kept %d pages after relevance filter (pages: %s)",
             len(relevant), [i + 1 for i, _ in relevant])

    text = "\n\n".join(f"=== Page {i + 1} ===\n{t}" for i, t in relevant)
    log.info("Filtered text → %d chars (~%d tokens)", len(text), len(text) // 4)

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256((args.model + text).encode()).hexdigest()[:16]
    cache_file = args.cache_dir / f"{args.device_id}-{digest}.json"

    if cache_file.exists() and not args.no_cache:
        log.info("Loading cached extraction: %s", cache_file)
        profile = HeatPumpProfile.model_validate_json(cache_file.read_text())
    else:
        profile = extract_profile(text, model=args.model)
        cache_file.write_text(profile.model_dump_json(indent=2))
        log.info("Cached extraction: %s", cache_file)

    print("--- extracted profile ---", file=sys.stderr)
    print(profile.model_dump_json(indent=2), file=sys.stderr)
    print("--- end profile ---\n", file=sys.stderr)

    ttl = to_turtle(args.device_id, profile)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(ttl)
        log.info("Wrote Turtle → %s", args.out)
    else:
        sys.stdout.write(ttl)

    return 0


if __name__ == "__main__":
    sys.exit(main())
