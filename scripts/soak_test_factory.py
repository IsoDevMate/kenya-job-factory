#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
soak_test_factory.py - 1000-sample fault-injection soak test for the lead pipeline.

Scenarios (rotation over all iterations):
  s0 valid lead                      -> must be ADDED and the file moved to processed/
  s1 cosmetic duplicate (case/punct/space variants of an existing lead)
                                     -> must be marked DUPLICATE, not added
  s2 company already in sent history (>=0.9 overlap, different role)
                                     -> must be marked DUPLICATE
  s3 missing required header field   -> must be reported INVALID
  s4 posting body too short (<120)   -> must be reported INVALID
  s5 garbage/noise file              -> must be reported INVALID
  s6 near-miss (token overlap <0.8, plausibly a DIFFERENT company)
                                     -> correctly ADDED (tests dedupe precision)
  c0 corrupted pipeline.json         -> hardened behavior:
       with a backups/ copy: self-heals from backup and proceeds
       without any copy: clean exit 2 with instructions (no crash, no
       partial writes). Daily backups/ remain the restore point of record.

First 50 iterations run as true subprocesses (real exit codes); the rest run
in-process. Results: /tmp/opencode/soak/results.json + stdout. Exit 0 iff the
s0..s6 failure rate is 0%.
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
VENV_PY = PROJECT / ".venv" / "bin" / "python"
INGEST_SRC = PROJECT / "scripts" / "ingest_leads.py"
SOAK_ROOT = Path("/tmp/opencode/soak")
RESULTS = SOAK_ROOT / "results.json"

BODY = (
    "We are hiring a Backend Developer. Requirements: 3 years of experience with "
    "Python or Node.js, PostgreSQL, REST APIs, Docker and CI/CD. Send your CV to "
    "careers@example.com by 30 November 2026 with the subject line Backend Developer."
)
HEADER_TMPL = (
    "source: whatsapp-channel\n"
    "channel: {channel}\n"
    "received: 2026-11-05\n"
    "role: {role}\n"
    "company: {company}\n"
    "deadline: open\n"
    "link: \n\n"
)
SENT_FIXTURE = {"company": "Acme Tech Ltd", "role": "Backend Developer", "status": "sent"}
PIPELINE_BASE = {"sent": [SENT_FIXTURE], "ready_not_sent": [], "prepared-not-sent": [], "inbox-leads": []}
NOISE_LINES = [
    "Good morning everyone, please pray for the family.\n",
    "Forwarded as received, the meeting starts at 4 pm.\n",
    "Reminder: the church service moved to Sunday 10am.\n",
]
ROTATION = ["s0", "s1", "s2", "s3", "s4", "s5", "s6"]
SCEN_NAMES = {"s0": "valid", "s1": "dupcosmetic", "s2": "dupsentco", "s3": "missing", "s4": "short", "s6": "nearmiss"}

rnd = __import__("random").Random(20261105)
scenario_counts = {k: {"pass": 0, "fail": 0} for k in ROTATION}
c0 = {"healed_from_backup": 0, "clean_exit_no_backup": 0, "crash": 0}
failures: list[dict] = []


def record(key: str, ok: bool, detail: str = "") -> None:
    bucket = "pass" if ok else "fail"
    scenario_counts[key][bucket] += 1
    if not ok:
        failures.append({"scenario": key, "detail": detail[:200]})


def fresh_sandbox(sub: str) -> Path:
    d = SOAK_ROOT / sub
    if d.exists():
        shutil.rmtree(d)
    (d / "opportunities" / "inbox" / "processed").mkdir(parents=True)
    (d / "opportunities" / "inbox" / "README.txt").write_text("drop zone\n")
    return d


def write_pipeline(sandbox: Path, data: dict) -> None:
    (sandbox / "opportunities" / "pipeline.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def read_pipeline(sandbox: Path) -> dict:
    return json.loads((sandbox / "opportunities" / "pipeline.json").read_text(encoding="utf-8"))


def load_module(sandbox_dir: Path):
    """Load ingest_leads.py with ROOT redirected to the sandbox."""
    src = INGEST_SRC.read_text(encoding="utf-8")
    patched = re.sub(
        r"ROOT = Path\(__file__\)\.resolve\(\)\.parents\[1\]",
        f"ROOT = Path('{sandbox_dir}')",
        src,
        count=1,
    )
    if patched == src:
        raise RuntimeError("ROOT patch failed")
    mod_file = sandbox_dir / "ingest_leads_patched.py"
    mod_file.write_text(patched, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("ingest_patched", mod_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def lead_text(scen: str, i: int) -> tuple[str, str | None]:
    """Return (text, noise|None); text may be noise-lines for s5."""
    if scen == "s0":
        return HEADER_TMPL.format(channel=f"NSE Tech Jobs {i}", role="Backend Engineer", company=f"Kahawa Digital {i}") + BODY.replace("Backend Developer", "Backend Engineer"), None
    if scen == "s1":  # cosmetic variants normalized identical to the existing lead
        variant = i % 4
        company = ["Acme Tech LTD", "acme tech ltd.", "Acme  Tech  Ltd", "ACME tech Ltd."][variant]
        role = ["Backend Developer", "backend developer", "Backend   Developer", "Backend Developer "][variant]
        return HEADER_TMPL.format(channel="jobs", role=role, company=company) + BODY, None
    if scen == "s2":  # same company (>=0.9) as one in sent history, different role
        return HEADER_TMPL.format(channel="c", role=f"Data Analyst {i % 7}", company="Acme Tech") + BODY.replace("Backend Developer", "Data Analyst"), None
    if scen == "s3":  # missing required company line
        return "source: whatsapp-channel\nchannel: c\nreceived: 2026-11-05\nrole: Tester\n\n" + BODY, None
    if scen == "s4":  # body below 120 chars
        return HEADER_TMPL.format(channel="c", role="Junior Dev", company="Inline Shortcase") + "Too short body.", None
    if scen == "s6":  # near-miss overlap <0.8: plausibly another company -> added
        return HEADER_TMPL.format(channel="c", role=f"Backend Engineer {i % 5}", company=f"Kahawa Interiors {i}") + BODY.replace("Backend Developer", "Interior Designer"), None
    return None, rnd.choice(NOISE_LINES)  # s5 noise


def embed_path(sandbox: Path) -> str:
    return re.sub(
        r"ROOT = Path\(__file__\)\.resolve\(\)\.parents\[1\]",
        f"ROOT = Path('{sandbox}')",
        INGEST_SRC.read_text(encoding="utf-8"),
        count=1,
    )


def run_subproc(i: int) -> None:
    sandbox = fresh_sandbox("proc")
    write_pipeline(sandbox, copy.deepcopy(PIPELINE_BASE))
    (sandbox / "ingest_leads_run.py").write_text(embed_path(sandbox), encoding="utf-8")
    scen = ROTATION[i % len(ROTATION)]
    text, noise = lead_text(scen, i)
    if noise:
        (sandbox / "opportunities" / "inbox" / f"2026-11-05_i{i:04d}.txt").write_text(noise, encoding="utf-8")
    else:
        (sandbox / "opportunities" / "inbox" / f"2026-11-05_i{i:04d}.txt").write_text(text, encoding="utf-8")
    proc = subprocess.run(
        [str(VENV_PY), str(sandbox / "ingest_leads_run.py")], capture_output=True, text=True, timeout=60
    )
    pl = read_pipeline(sandbox)
    processed = [p.name for p in (sandbox / "opportunities" / "inbox" / "processed").iterdir()]
    n_added = len(pl["inbox-leads"])
    expect_add = scen in ("s0", "s6")
    ok = proc.returncode == 0 and ((n_added == 1 and processed) if expect_add else (n_added == 0 and not processed))
    record(scen, ok, f"iter {i}: rc={proc.returncode} added={n_added} processed={processed} err={proc.stderr[:120]}")


def run_inproc(i: int) -> None:
    sandbox = fresh_sandbox("loop")
    write_pipeline(sandbox, copy.deepcopy(PIPELINE_BASE))
    mod = load_module(sandbox)
    scen = ROTATION[i % len(ROTATION)]
    text, noise = lead_text(scen, i)
    inbox = sandbox / "opportunities" / "inbox"
    if noise:
        (inbox / f"2026-11-05_i{i:04d}.txt").write_text(noise, encoding="utf-8")
    if scen == "s0" and i % 25 == 0:  # mixed batch: noise rides along with the valid lead
        (inbox / f"2026-11-05_i{i:04d}_ridealong.txt").write_text(rnd.choice(NOISE_LINES), encoding="utf-8")
    if text:
        (inbox / f"2026-11-05_i{i:04d}_{SCEN_NAMES.get(scen, 'case')}.txt").write_text(text, encoding="utf-8")

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = mod.main()
        pl = read_pipeline(sandbox)
        processed = [p.name for p in (inbox / "processed").iterdir()]
        n_added = len(pl["inbox-leads"])
        expect_add = scen in ("s0", "s6")
        ok = rc == 0 and ((n_added == 1 and processed) if expect_add else (n_added == 0 and not processed))
        record(scen, ok, f"iter {i}: rc={rc} added={n_added} processed={processed}")
    except Exception as exc:  # noqa: BLE001
        record(scen, False, f"iter {i}: raised {type(exc).__name__}: {exc}")


def run_corruption() -> None:
    """c0: truncated pipeline.json (mid-write crash), two variants."""
    sandbox = fresh_sandbox("corrupt")
    variant = 0 if (c0["healed_from_backup"] <= c0["clean_exit_no_backup"]) else 1
    if variant == 0:  # factory_daily.sh leaves a backup behind in backups/
        (sandbox / "backups").mkdir()
        (sandbox / "backups" / "pipeline-2026-11-05.json").write_text(
            json.dumps(PIPELINE_BASE, indent=2), encoding="utf-8"
        )
    (sandbox / "opportunities" / "pipeline.json").write_text(json.dumps(PIPELINE_BASE)[:-15], encoding="utf-8")
    mod = load_module(sandbox)
    (sandbox / "opportunities" / "inbox" / "corrupt_case.txt").write_text(
        HEADER_TMPL.format(channel="c", role="Backend Engineer", company="Corrupt Co") + BODY, encoding="utf-8"
    )
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = mod.main()
    except Exception as exc:  # noqa: BLE001
        c0["crash"] += 1
        failures.append({"scenario": "c0", "detail": f"crash, not a clean exit: {type(exc).__name__}: {exc}"[:140]})
        return
    if variant == 0:
        pl = read_pipeline(sandbox)
        ok = rc == 0 and len(pl["inbox-leads"]) == 1
        if ok:
            c0["healed_from_backup"] += 1
        else:
            record("c0", False, f"backup recovery failed: rc={rc} leads={len(pl.get('inbox-leads', []))}")
    else:  # no backup: must exit cleanly (2), never crash, never mutate files
        moved = [p.name for p in (sandbox / "opportunities" / "inbox" / "processed").iterdir()]
        ok = rc == 2 and moved == []
        if ok:
            c0["clean_exit_no_backup"] += 1
        else:
            record("c0", False, f"no-backup variant: rc={rc} moved={moved}")


def main(n: int = 1000) -> int:
    SOAK_ROOT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for i in range(50):
        try:
            run_subproc(i)
        except Exception as exc:  # noqa: BLE001
            record(ROTATION[i % len(ROTATION)], False, f"subproc harness iter {i}: {type(exc).__name__}: {exc}")
    for i in range(n - 6):
        try:
            run_inproc(i)
        except Exception as exc:  # noqa: BLE001
            record(ROTATION[i % len(ROTATION)], False, f"inproc harness iter {i}: {type(exc).__name__}: {exc}")
    for _ in range(6):
        try:
            run_corruption()
        except Exception as exc:  # noqa: BLE001
            c0["crash"] += 1
            failures.append({"scenario": "c0", "detail": f"c0 harness: {type(exc).__name__}: {exc}"[:120]})
    dt = time.time() - t0

    total = sum(v["pass"] + v["fail"] for v in scenario_counts.values())
    fails = sum(v["fail"] for v in scenario_counts.values())
    summary = {
        "iterations_requested": n,
        "samples_run": total,
        "failures": fails,
        "failure_rate_pct": round(100 * fails / total, 2) if total else None,
        "wall_seconds": round(dt, 1),
        "scenarios": scenario_counts,
        "corruption_c0": {**c0, "note": "hardened: self-heals from backups/, else clean exit 2 with instructions (no crash, no partial writes)"},
        "first_failures": [f["detail"] for f in failures][:20],
    }
    RESULTS.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "first_failures"}, indent=2))
    for d in summary["first_failures"][:10]:
        print("  FAIL:", d)
    return 0 if fails == 0 and c0["crash"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 1000))
