#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest_leads.py — parse opportunities/inbox/*.{md,txt} into pipeline.json.

- Validates the header (source/channel/received/role/company).
- Dedupes against every existing pipeline entry (sent, ready_not_sent,
  prepared-not-sent, inbox-leads) by normalized company+role.
- New leads are appended to pipeline["inbox-leads"] with status NEEDS_TRIAGE.
- Files with missing headers are reported, not guessed. Screenshots are listed
  as pending-agent-transcription. Processed files move to inbox/processed/.

Usage: python scripts/ingest_leads.py
Exit 0 always (report script), 2 only on unreadable inbox dir.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "opportunities" / "inbox"
PROCESSED = INBOX / "processed"
PIPELINE = ROOT / "opportunities" / "pipeline.json"
BACKUPS = ROOT / "backups"

REQUIRED = ("source", "channel", "received", "role", "company")
HEADER_RE = re.compile(r"^(source|channel|received|role|company|deadline|link)\s*:\s*(.+)$", re.I)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_SUFFIXES = {".txt", ".md"}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def token_overlap(a: str, b: str) -> float:
    ta, tb = set(norm(a).split()), set(norm(b).split())
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    return len(inter) / min(len(ta), len(tb))


def existing_keys(pipeline: dict) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for section in ("sent", "ready_not_sent", "prepared-not-sent", "inbox-leads"):
        for e in pipeline.get(section, []):
            c = norm(e.get("company_inbox", "") or e.get("company", "") or "")
            r = norm(e.get("role", "") or "")
            if c or r:
                keys.append((c, r))
    return keys


def is_duplicate(company: str, role: str, keys: list[tuple[str, str]]) -> tuple[bool, str]:
    c, r = norm(company), norm(role)
    for kc, kr in keys:
        if not kc and not kr:
            continue
        same_company = (not kc) or (c and token_overlap(c, kc) >= 0.8)
        same_role = (not kr) or (r and token_overlap(r, kr) >= 0.8)
        if kc and c and token_overlap(c, kc) < 0.8 and not same_role:
            continue
        if same_company and same_role:
            return True, f"{kc or '?'} / {kr or '?'}"
    return False, ""


def parse_lead(path: Path) -> tuple[dict | None, list[str]]:
    problems: list[str] = []
    header: dict[str, str] = {}
    body_start = 0
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, line in enumerate(lines[:40]):
        m = HEADER_RE.match(line.strip())
        if not m:
            if header and i >= len(header):
                break
            if header or "\n".join(lines[i : i + 1]).strip() == "":
                body_start = i + 1 if not m else body_start
            continue
        k = m.group(1).lower()
        v = m.group(2).strip()
        if k in REQUIRED and not v:
            problems.append(f"empty required field: {k}")
        header[k] = v
        body_start = max(body_start, i + 1)
    missing = [k for k in REQUIRED if k not in header]
    if missing:
        problems.append(f"missing header fields: {', '.join(missing)} — copy the header template from README.txt")
    if problems:
        return None, problems
    body = "\n".join(lines[body_start:]).strip()
    if len(body) < 120:
        problems.append("posting text below the header is too short (<120 chars) — paste the complete job ad")
        return None, problems
    lead = {
        "company": header["company"],
        "role": header["role"],
        "source": header["source"],
        "channel": header["channel"],
        "received": header["received"],
        "deadline": header.get("deadline", "open"),
        "link": header.get("link", ""),
        "status": "NEEDS_TRIAGE",
        "inbox_file": path.name,
        "text_chars": len(body),
    }
    return lead, []


def load_pipeline() -> tuple[dict, str]:
    """Load pipeline.json; if corrupted, recover from the newest backups/ copy.

    Returns (pipeline, recovered_from) — recovered_from is '' when the live
    file parsed fine. With no readable backup, raises ValueError with a clear
    instruction instead of leaving half-written state.
    """
    try:
        return json.loads(PIPELINE.read_text(encoding="utf-8")), ""
    except Exception:
        pass
    if not BACKUPS.is_dir():
        raise ValueError(
            f"pipeline.json is corrupted and no backups/ copies exist. Restore it manually; "
            f"inbox files were not touched."
        )
    for cand in sorted(BACKUPS.glob("pipeline-*.json"), reverse=True):
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data, cand.name
        except Exception:
            continue
    raise ValueError(
        "pipeline.json is corrupted and every backups/pipeline-*.json copy is unreadable. "
        "Restore it manually; inbox files were not touched."
    )


def write_pipeline_atomic(pipeline: dict) -> None:
    """Replace pipeline.json atomically (tmpfile + rename) so a crash mid-write
    can never leave a truncated file behind."""
    tmp = PIPELINE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pipeline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, PIPELINE)


def main() -> int:
    if not INBOX.is_dir():
        print(f"no inbox dir: {INBOX}", file=sys.stderr)
        return 2
    PROCESSED.mkdir(parents=True, exist_ok=True)
    try:
        pipeline, recovered_from = load_pipeline()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if recovered_from:
        print(f"WARN: pipeline.json was corrupted; recovered from backups/{recovered_from}", file=sys.stderr)
    pipeline.setdefault("inbox-leads", [])
    keys = existing_keys(pipeline)

    added, dupes, bad, screenshots = [], [], [], []
    for path in sorted(INBOX.iterdir()):
        if path.name == "README.txt" or path.name.startswith("."):
            continue
        if path.is_dir():
            continue
        if path.suffix.lower() in IMAGE_SUFFIXES:
            screenshots.append(path.name)
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        lead, problems = parse_lead(path)
        if lead is None:
            bad.append((path.name, problems))
            continue
        dupe, why = is_duplicate(lead["company"] + " " + lead["role"].split()[0], lead["role"], keys)
        # company-level duplicate check against sent history
        for kc, _kr in keys:
            if kc and token_overlap(norm(lead["company"]), kc) >= 0.9:
                sent_names = [k for k in pipeline.get("sent", []) if norm(k.get("company", "")) == kc]
                if sent_names:
                    dupe = True
                    why = f"company already in sent history: {sent_names[0]['company']}"
                    break
        if dupe:
            dupes.append((path.name, why))
            continue
        pipeline["inbox-leads"].append(lead)
        keys.append((norm(lead["company"]), norm(lead["role"])))
        added.append(path.name)
        shutil.move(str(path), PROCESSED / path.name)

    # trailing duplicate check: two new leads that match each other
    leads = pipeline["inbox-leads"]
    for i in range(len(leads)):
        for j in range(i + 1, len(leads)):
            li, lj = leads[i], leads[j]
            if li.get("status") == lj.get("status") == "NEEDS_TRIAGE" and token_overlap(
                norm(li["company"]), norm(lj["company"])
            ) >= 0.9 and token_overlap(norm(li["role"]), norm(lj["role"])) >= 0.8:
                if "DUPLICATE_OF_NEXT" not in li.get("status", ""):
                    li["note"] = "DUPLICATE_OF_NEXT"

    write_pipeline_atomic(pipeline)

    print(f"inbox ingest {date.today()}  ->  pipeline['inbox-leads']")
    print(f"added          : {len(added)} {added}")
    print(f"duplicates     : {len(dupes)}")
    for name, why in dupes:
        print(f"  - {name}: matches {why}")
    print(f"invalid        : {len(bad)}")
    for name, problems in bad:
        print(f"  - {name}: {'; '.join(problems)}")
    print(f"screenshots    : {len(screenshots)} (agent transcribes in next factory session)")
    for name in screenshots:
        print(f"  - {name}")
    print(f"inbox-leads total: {len([l for l in leads if l.get('status') == 'NEEDS_TRIAGE'])} pending NEEDS_TRIAGE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
