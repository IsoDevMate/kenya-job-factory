#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review_panel.py — multi-lens independent review for a pack (factory Phase 3).

Importable lenses (each independent, evidence-based PASS/FAIL):
  lens_mechanical(job_title, resume, letter, pack_dir, jd)
      -> runs verify_pack on the real PDFs (ATS/mechanical only, says so)
  lens_guards(job_title, company, resume, letter, jd)
      -> runs reviewer.review_pack with all 65+ learned guards
  lens_send_safety(pack_dir)
      -> filename gate, VERIFIED_OPEN line, no SENT marker, portal/never-email
  lens_quality_dossier(pack_dir, resume, letter)
      -> mechanical dossier: pages, pdf extractability, forbidden names
  ship_decision(...)
      -> SHIP / HOLD quorum across lenses

CLI:
  python scripts/review_panel.py <pack_dir> [--builder-approved]
Writes FACTORY_REPORT.md into the pack dir. Exit 0 = SHIP.

Limits (do not hide): these lenses are mechanical + rules. The human-quality
hiring-manager pass and source-provenance audit remain an agent step per pass —
SHIP here means "green for send preflight", not content certified by a machine.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from list_sendable import (  # noqa: E402
    attach_ok,
    draft_attach,
    is_portal,
    is_sent,
)
from verify_pack import verify_pack  # noqa: E402

RESUME_RE_SUFFIX = "Resume.pdf"
LETTER_RE_SUFFIX = "Cover_Letter.pdf"
FORBIDDEN = {"cv.pdf", "cv.docx", "cover-letter.pdf", "cover_letter.pdf"}
PDFTOTEXT = "pdftotext"


def _pdf_pages(path: Path) -> int:
    try:
        out = subprocess.run(
            ["pdfinfo", str(path)], capture_output=True, text=True, check=True
        ).stdout
        for line in out.splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":", 1)[1])
    except Exception:
        pass
    return -1


import subprocess  # noqa: E402


def _pdf_text_len(path: Path) -> int:
    try:
        r = subprocess.run([PDFTOTEXT, str(path), "-"], capture_output=True, text=True)
        return len((r.stdout or "").strip())
    except Exception:
        return 0


def lens_mechanical(pack_dir: Path, resume: Path, letter: Path, job_title: str, jd: str) -> dict:
    r = verify_pack(pack_dir=pack_dir, resume=resume, letter=letter, job_title=job_title, jd=jd)
    ok = bool(r.get("ok"))
    fails = r.get("fails", [])
    return {
        "lens": "MECHANICAL (verify_pack — ATS/mechanical only)",
        "verdict": "PASS" if ok else "FAIL",
        "detail": ", ".join(fails) if fails else "all mechanical checks pass",
    }


def lens_guards(job_title: str, company: str, resume: Path, letter: Path, jd: str) -> dict:
    import reviewer  # noqa: E402  (guard registry loads with the module)

    r = reviewer.review_pack(
        job_title=job_title, company=company, jd=jd, resume=resume, letter=letter, go_approved=False
    )
    fails = [c["name"] for c in r["checks"] if c["result"] == "FAIL"]
    detail = ", ".join(fails) if fails else "learned guards all pass (P1 send-await gate is expected)"
    return {
        "lens": "GUARDS (reviewer.py learned rules)",
        "verdict": "PASS" if not fails else "FAIL",
        "detail": detail,
    }


def lens_send_safety(pack_dir: Path) -> dict:
    problems: list[str] = []
    resume = pack_dir / f"{pack_dir.name}"  # find by suffix below
    pdfs = sorted(p for p in pack_dir.glob("*.pdf"))
    names = [p.name for p in pdfs]
    if not names:
        problems.append("no PDFs in pack")
    if not attach_ok(names):
        problems.append(f"attachment names fail gate: {names}")
    vd = None
    for cand in ("APPLY.txt", "email-draft.txt"):
        p = pack_dir / cand
        if p.exists():
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.upper().startswith("VERIFICATION:"):
                    vd = line.split(":", 1)[1].strip().upper()
    if vd != "VERIFIED_OPEN":
        problems.append(f"listing not explicitly VERIFIED_OPEN (got {vd!r})")
    if is_sent(pack_dir):
        problems.append("pack carries a SENT marker")
    if is_portal(pack_dir):
        problems.append("portal upload job — never email, needs user upload")
    return {
        "lens": "SEND-SAFETY (names, VERIFIED_OPEN, no-SENT, portal)",
        "verdict": "FAIL" if problems else "PASS",
        "detail": "; ".join(problems) if problems else "send-safety lens clear",
    }


def lens_quality_dossier(pack_dir: Path) -> dict:
    problems: list[str] = []
    resume = next((p for p in pack_dir.glob(f"*{RESUME_RE_SUFFIX}")), None)
    letter = next((p for p in pack_dir.glob(f"*{LETTER_RE_SUFFIX}")), None)
    if not resume or not letter:
        problems.append("resume/letter PDF missing")
    else:
        rp, lp = _pdf_pages(resume), _pdf_pages(letter)
        if rp in (-1, 0):
            problems.append("resume PDF unreadable by pdfinfo")
        elif rp > 2:
            problems.append(f"resume has {rp} pages")
        if lp in (-1, 0):
            problems.append("letter PDF unreadable")
        if _pdf_text_len(resume) < 200:
            problems.append("resume text barely extractable (<200 chars)")
        if _pdf_text_len(letter) < 100:
            problems.append("letter text barely extractable")
    for p in pack_dir.glob("*"):
        if p.name.lower() in FORBIDDEN:
            problems.append(f"forbidden filename present: {p.name}")
    return {
        "lens": "DOSSIER (pages, extractability, forbidden names)",
        "verdict": "FAIL" if problems else "PASS",
        "detail": "; ".join(problems) if problems else "p1-2 resume, extractable, names clean",
    }


def ship_decision(results: list[dict], builder_approved: bool) -> tuple[str, str]:
    fails = [r for r in results if r["verdict"] == "FAIL"]
    if fails:
        return ("FAIL", f"{len(fails)} lens(es) failed: {[f['lens'].split(' ')[0] for f in fails]}")
    if not builder_approved:
        return ("HOLD", "green mechanically — needs agent builder-review + your eyeball in cursor cvs before send")
    return ("SHIP", "green across lenses; builder review done; user does final check then: send <company>")


def review(pack_dir: Path, builder_approved: bool = False) -> dict:
    pack_dir = pack_dir.resolve()
    parts = pack_dir.name.split("_", 2)
    job_title = parts[2].replace("_", " ") if len(parts) > 2 else pack_dir.name
    company = parts[1] if len(parts) > 1 else pack_dir.name
    resume = next(pack_dir.glob(f"*{RESUME_RE_SUFFIX}"), None)
    letter = next(pack_dir.glob(f"*{LETTER_RE_SUFFIX}"), None)
    jd_parts: list[str] = []
    for fd in ("JOB_SPEC.txt", "JD.txt"):
        f = pack_dir / fd
        if f.exists():
            jd_parts.append(f.read_text(encoding="utf-8", errors="ignore"))
            break
    for fd in ("APPLY.txt", "DECISION.md"):
        f = pack_dir / fd
        if f.exists():
            jd_parts.append(f.read_text(encoding="utf-8", errors="ignore"))
    jd = " ".join(jd_parts) or job_title

    results: list[dict] = []
    if resume and letter:
        results.append(lens_mechanical(pack_dir, resume, letter, job_title, jd))
    else:
        results.append({"lens": "MECHANICAL", "verdict": "FAIL", "detail": "resume/letter PDF missing"})
    if resume and letter:
        try:
            results.append(lens_guards(job_title, company, resume, letter, jd))
        except Exception as e:  # reviewer is strict; a crash means HOLD, not pass
            results.append({"lens": "GUARDS", "verdict": "FAIL", "detail": f"reviewer crashed: {e}"})
    results.append(lens_send_safety(pack_dir))
    results.append(lens_quality_dossier(pack_dir))
    verdict, verdict_why = ship_decision(results, builder_approved)

    lines = [
        "# FACTORY REPORT — multi-lens review",
        f"**Pack:** {pack_dir.name}  ",
        f"**Date:** {date.today()}  ",
        f"**Verdict: {verdict}** — {verdict_why}",
        "",
        "| Lens | Verdict | Detail |",
        "|---|---|---|",
    ]
    lines += [f"| {r['lens']} | {r['verdict']} | {r['detail']} |" for r in results]
    lines += [
        "",
        "## What this does NOT certify",
        "- Content truthkeeping (provenance) and hiring-manager quality remain the agent's per-pass duty and your eyeball.",
        f"- Send authorization: type `send {company}`.",
    ]
    (pack_dir / "FACTORY_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"verdict": verdict, "results": results, "report": pack_dir / "FACTORY_REPORT.md"}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        print("Usage: review_panel.py <pack_dir> [--builder-approved]", file=sys.stderr)
        return 2
    pack_dir = ROOT / "applications" / argv[0] if not Path(argv[0]).is_dir() else Path(argv[0])
    builder_approved = "--builder-approved" in argv
    r = review(pack_dir, builder_approved)
    print((pack_dir / "FACTORY_REPORT.md").read_text(encoding="utf-8"))
    return 0 if r["verdict"] == "SHIP" else (3 if r["verdict"] == "HOLD" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
