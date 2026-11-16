#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kenya board scanner: pulls open roles from public ATS JSON APIs and drops
leads into opportunities/inbox/ using the README header format.

Zero AI tokens. Mechanical only. Never sends anything.

Sources verified live 2026-11-09:
  - Ashby posting-api   (M-KOPA, WeTravel)
  - Greenhouse boards   (GiveDirectly, One Acre Fund, Moniepoint)
  - Lever postings      (Busara Center)
  - BambooHR careers    (Pula, Cellulant, PxD)
  - HTML career pages   (Co-op Bank career_tax, Savannah, BURN/applytojob,
                         Living Goods/applytojob, Zeraki)

Each lead file carries `source: web-board` + `channel: <board>` so triage knows
these are mechanical pulls, and a `title-filter` has already run: only roles
matching the factory's fit keywords survive, SENIOR/wrong-domain are dropped.
Dedup: against logs/send_ledger.jsonl, applications/ pack folders, and a
state file (logs/board_scan_state.json) keyed by URL.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "opportunities" / "inbox"
LEDGER = ROOT / "logs" / "send_ledger.jsonl"
STATE = ROOT / "logs" / "board_scan_state.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

# Title must contain one positive keyword (case-insensitive substring)
POSITIVE = [
    "software engineer", "software developer", "full stack", "full-stack",
    "backend", "back-end", "frontend", "front-end", "web developer",
    "graduate trainee", "data scientist", "data engineer", "data analyst",
    "data annotation", "annotation", "ict", "it support", "help desk",
    "helpdesk", "it officer", "systems administrator", "cloud engineer",
    "devops", "qa engineer", "qa automation", "ai engineer", "ai application",
    "machine learning", "llm", "coding teacher", "computer studies",
    "computer teacher", "ict trainer",
]
# Title must contain none of these
NEGATIVE = [
    "principal", "director", "head of", "chief", "vp ", "staff engineer",
    "architect", "lead engineer", "engineering manager", "nurse", "clinical",
    "medical officer", "sales", "account manager", "graphic designer",
    "driver", "lecturer", "professor", "conducteur", "superviseur",
    "recrutement", "field operations manager", "country manager",
    "country coordinator", "government relations", "carbon project",
]
# Companies whose packs already exist or were sent; scanner still lists their
# roles (the portal link is useful) but marks them with a note.
KNOWN = {
    "co-operative bank of kenya": "packs built 2026-11-08 (AI Application Engineer, Data Engineer)",
    "m-kopa solar": "pack exists 2026-09-28 (Senior Backend)",
    "cellulant": "pack exists 2026-11-08 (Graduate Trainee)",
}


def fetch(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def jget(url: str):
    return json.loads(fetch(url).decode("utf-8", "replace"))


def text_ok(title: str) -> bool:
    t = title.lower()
    if not any(k in t for k in POSITIVE):
        return False
    return not any(k in t for k in NEGATIVE)


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"seen": {}}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=1))


def load_applied_urls() -> set[str]:
    urls: set[str] = set()
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for v in (row.get("app"), row.get("to")):
                if v:
                    urls.add(str(v).lower())
    apps = ROOT / "applications"
    if apps.exists():
        for d in apps.iterdir():
            if d.is_dir():
                urls.add(d.name.lower())
    return urls


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:48]


def write_lead(company: str, title: str, url: str, channel: str,
               location: str, deadline: str, note: str = "") -> str | None:
    today = date.today().isoformat()
    name = f"{today}_board-{slug(company)}_{slug(title)}.txt"
    path = INBOX / name
    if path.exists():
        return None
    body = [
        "source: web-board",
        f"channel: {channel}",
        f"received: {today}",
        f"role: {title}",
        f"company: {company}",
        f"deadline: {deadline or 'open'}",
        f"link: {url}",
        f"location: {location}",
    ]
    if note:
        body.append(f"note: {note}")
    body.append("")
    body.append(f"(Mechanical pull from {channel} on {today}. "
                f"Apply instructions not yet read - verify channel on the page before building.)")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return name


def scan_ashby(board: str, company: str) -> list[dict]:
    d = jget(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
    return [
        {"title": j["title"], "url": j["jobUrl"],
         "location": (j.get("location") or ""), "channel": f"ashby/{board}"}
        for j in d.get("jobs", [])
        if not j.get("isClosed") and not j.get("isArchived")
    ]


def scan_greenhouse(board: str, company: str) -> list[dict]:
    d = jget(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs")
    out = []
    for j in d.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        if "kenya" in loc.lower() or "nairobi" in loc.lower() or "remote" in loc.lower():
            out.append({"title": j["title"], "url": j["absolute_url"],
                        "location": loc, "channel": f"greenhouse/{board}"})
    return out


def scan_lever(board: str, company: str) -> list[dict]:
    d = jget(f"https://api.lever.co/v0/postings/{board}?mode=json")
    return [
        {"title": j["text"], "url": j["hostedUrl"],
         "location": ((j.get("categories") or {}).get("location") or ""),
         "channel": f"lever/{board}"}
        for j in d
        if (j.get("createdAt") or 0) > 0
    ]


def scan_bamboo(sub: str, company: str) -> list[dict]:
    d = jget(f"https://{sub}.bamboohr.com/careers/list")
    r = d.get("result", [])
    jobs = r if isinstance(r, list) else r.get("jobs", [])
    return [
        {"title": j.get("jobOpeningName", ""), "url": f"https://{sub}.bamboohr.com/careers/{j.get('id')}",
         "location": (j.get("location") or {}).get("city", "") if isinstance(j.get("location"), dict) else str(j.get("location") or ""),
         "channel": f"bamboohr/{sub}"}
        for j in jobs
        if not j.get("code")  # code set = internal only
    ]


def scan_applytojob(sub: str, company: str) -> list[dict]:
    html = fetch(f"https://{sub}.applytojob.com/").decode("utf-8", "replace")
    rows = []
    for m in re.finditer(r'href="(https?://%s\.applytojob\.com/apply/[A-Za-z0-9]+/[^"]+)">([^<]+)' % re.escape(sub), html):
        url, title = m.group(1), m.group(2).strip()
        rows.append({"title": title, "url": url, "location": "", "channel": f"applytojob/{sub}"})
    return rows


def scan_coopbank(company: str) -> list[dict]:
    html = fetch("https://www.co-opbank.co.ke/career_tax/co-op-bank/").decode("utf-8", "replace")
    rows = []
    seen = set()
    for m in re.finditer(r'href="(https://www\.co-opbank\.co\.ke/careers/co-op-bank/[a-z0-9-]+/)"[^>]*>', html):
        url = m.group(1)
        if url in seen:
            continue
        seen.add(url)
        slugpart = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        rows.append({"title": slugpart.title(), "url": url, "location": "Nairobi",
                     "channel": "coopbank-careers"})
    return rows


def scan_savannah(company: str) -> list[dict]:
    html = fetch("https://www.savannahinformatics.com/careers/openroles/").decode("utf-8", "replace")
    rows = []
    seen = set()
    for m in re.finditer(r'href="([^"]*openroles/[a-z0-9-]+)"', html):
        url = m.group(1)
        if not url.startswith("http"):
            url = "https://www.savannahinformatics.com" + url
        if url in seen:
            continue
        seen.add(url)
        slugpart = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        rows.append({"title": slugpart.title(), "url": url, "location": "Nairobi",
                     "channel": "savannah-careers"})
    return rows


def scan_zeraki(company: str) -> list[dict]:
    html = fetch("https://www.zeraki.app/jobs").decode("utf-8", "replace")
    rows = []
    seen = set()
    for m in re.finditer(r'href="(https://www\.zeraki\.app/jobs/[a-z0-9-]+)"', html):
        url = m.group(1)
        if url.rstrip("/") == "https://www.zeraki.app/jobs" or url in seen:
            continue
        seen.add(url)
        slugpart = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        rows.append({"title": slugpart.title(), "url": url, "location": "Nairobi",
                     "channel": "zeraki-careers"})
    return rows


SOURCES = [
    ("M-KOPA Solar", lambda c: scan_ashby("M-KOPA", c)),
    ("WeTravel", lambda c: scan_ashby("wetravel", c)),
    ("GiveDirectly", lambda c: scan_greenhouse("givedirectly", c)),
    ("One Acre Fund", lambda c: scan_greenhouse("oafkenya", c)),
    ("Moniepoint", lambda c: scan_greenhouse("moniepoint", c)),
    ("Busara Center", lambda c: scan_lever("BusaraCenter", c)),
    ("Pula Advisors", lambda c: scan_bamboo("pula", c)),
    ("Cellulant", lambda c: scan_bamboo("cellulant", c)),
    ("Precision Development (PxD)", lambda c: scan_bamboo("precisiondev", c)),
    ("Living Goods", lambda c: scan_applytojob("livinggoods", c)),
    ("BURN Manufacturing", lambda c: scan_applytojob("burnmanufacturing", c)),
    ("Co-operative Bank of Kenya", scan_coopbank),
    ("Savannah Informatics", scan_savannah),
    ("Zeraki", scan_zeraki),
]


def main() -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    state = load_state()
    applied = load_applied_urls()
    today = date.today().isoformat()
    new_leads, filtered, skipped_dupes, errors = [], 0, 0, []

    for company, fn in SOURCES:
        try:
            roles = fn(company)
        except Exception as e:
            errors.append(f"{company}: {type(e).__name__}: {e}")
            continue
        for r in roles:
            title = (r.get("title") or "").strip()
            url = (r.get("url") or "").strip()
            if not title or not url:
                continue
            if not text_ok(title):
                filtered += 1
                continue
            if url.lower() in state["seen"] or url.lower() in applied:
                skipped_dupes += 1
                continue
            note = KNOWN.get(company.lower(), "")
            name = write_lead(company, title, url, r["channel"],
                              r.get("location", ""), "", note)
            if name:
                new_leads.append(f"{company} | {title} | {url}")
            state["seen"][url.lower()] = today

    save_state(state)
    print(json.dumps({
        "date": today,
        "new_leads": len(new_leads),
        "filtered_out": filtered,
        "dupes_skipped": skipped_dupes,
        "errors": errors,
        "leads": new_leads,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
