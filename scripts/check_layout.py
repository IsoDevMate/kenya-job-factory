#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Durable layout gate for generated CVs (born from the 2026-11-16 "clamped
CV" correction on the Konnect pack).

Checks the real rendered PDF, not the source DOCX:

1. page count -- a one-page target that overflows to page 2 fails the build
   loudly instead of shipping a clamped or broken layout;
2. bold action-verb leads -- the generator bolds the first word of every
   bullet; this counts short bold spans that start a line and fails when the
   styling regresses (plain-text bullets);

Spacing itself is set explicitly in scripts/ict_resume.py (2pt after bullets,
6pt before role headings, 8pt before section headings) and verified by the
human visual pass on the rendered preview -- this gate catches the two
failure modes a script can measure reliably.

Usage:
  .venv/bin/python scripts/check_layout.py <pdf> [--pages 1] [--min-bold-leads 16]

Or imported by a builder:
  from check_layout import check_layout
  problems = check_layout(pdf, max_pages=1, min_bold_leads=16)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf

_BOLD_FLAG = 1 << 4  # pymupdf span flags: bit 4 = bold


def check_layout(pdf: Path, *, max_pages: int = 1, min_bold_leads: int = 16) -> list[str]:
    """Return a list of human-readable problems; empty list = pass."""
    pdf = Path(pdf)
    if not pdf.exists():
        return [f"pdf not found: {pdf}"]

    problems: list[str] = []
    doc = pymupdf.open(pdf)
    try:
        if doc.page_count > max_pages:
            problems.append(
                f"page_count={doc.page_count} exceeds max_pages={max_pages}"
            )

        bold_leads = 0
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue
                    first = spans[0]
                    text = first["text"].strip()
                    size = first["size"]
                    is_bold = bool(first["flags"] & _BOLD_FLAG)
                    if (
                        is_bold
                        and 9.5 <= size <= 11.5
                        and 0 < len(text) <= 14
                        and text[0].isupper()
                    ):
                        bold_leads += 1
        if bold_leads < min_bold_leads:
            problems.append(
                f"bold action-verb leads={bold_leads} < required {min_bold_leads} "
                "(bullets must start with a bold verb)"
            )
    finally:
        doc.close()
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Layout gate for generated CV PDFs")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--min-bold-leads", type=int, default=16)
    args = ap.parse_args()
    problems = check_layout(args.pdf, max_pages=args.pages, min_bold_leads=args.min_bold_leads)
    if problems:
        print("LAYOUT GATE FAILED:", args.pdf)
        for p in problems:
            print("  -", p)
        return 1
    print(f"LAYOUT GATE PASS: {args.pdf} (<= {args.pages} page(s), bold leads ok)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
