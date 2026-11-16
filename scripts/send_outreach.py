#!/usr/bin/env python3
"""Gated cold-email sender. Dry-run by default; nothing sends without the applicant's
explicit approval and an unarmed profile/SEND_DISABLED gate.

Usage:
  .venv/bin/python scripts/send_outreach.py --company "Zeraki"
  .venv/bin/python scripts/send_outreach.py --company "Zeraki" --confirm --i-confirm-send
"""
import argparse
import csv
import json
import re
import smtplib
import ssl
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEND_DISABLED = ROOT / "profile" / "SEND_DISABLED"
OUT_LEDGER = ROOT / "logs" / "outreach_ledger.jsonl"
APP_LEDGER = ROOT / "logs" / "send_ledger.jsonl"
DRAFTS = ROOT / "outreach" / "drafts"
CSV = ROOT / "outreach" / "targets.csv"
POLICY = ROOT / "profile" / "outreach_policy.json"
CAP, COOLDOWN = 10, 30


def env():
    p = ROOT / ".env.gmail"
    if not p.exists():
        raise SystemExit("No .env.gmail (GMAIL_ADDRESS + GMAIL_APP_PASSWORD)")
    d = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def events(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def parse(text):
    meta, body, in_body = {}, [], False
    for line in text.splitlines():
        if line.startswith("BODY:"):
            in_body = True
            continue
        if in_body:
            body.append(line)
        elif line.startswith("TO:"):
            meta["to"] = line.split(":", 1)[1].strip()
        elif line.startswith("SUBJECT:"):
            meta["subject"] = line.split(":", 1)[1].strip()
        elif line.startswith("COMPANY:"):
            meta["company"] = line.split(":", 1)[1].strip()
        elif line.startswith("PERSON:"):
            meta["person"] = line.split(":", 1)[1].strip()
    meta["body"] = "\n".join(body).strip()
    return meta


def latest_draft(company):
    hits = sorted(DRAFTS.glob("*_%s*.txt" % re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")),
                  key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def policy():
    if not POLICY.exists():
        return {}
    try:
        return json.loads(POLICY.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def cmd_auto():
    """Standing-authorization mode: send all ready email-channel drafts within caps.

    Requires the applicant's explicit standing grant recorded in
    profile/outreach_policy.json (standing_authorization: true + authorized_at date).
    LinkedIn/X DMs are never auto-sent here (manual or browser-assisted)."""
    pol = policy()
    if not pol.get("standing_authorization"):
        raise SystemExit(
            "AUTO BLOCKED: standing_authorization is false in profile/outreach_policy.json.\n"
            "The applicant must grant it explicitly in chat; the grant is recorded with a date.")
    cap = int(pol.get("daily_email_cap") or CAP)
    cooldown = int(pol.get("cooldown_days") or COOLDOWN)
    if SEND_DISABLED.exists():
        raise SystemExit("AUTO BLOCKED: profile/SEND_DISABLED is present. "
                         "The runner lifts it for the auto run and re-arms it right after.")
    rows = []
    with CSV.open(newline="", encoding="utf-8") as f:
        rows = [dict(r) for r in csv.DictReader(f)]
    led = events(OUT_LEDGER)
    sent = [e for e in led if e.get("event") == "sent"]
    today = date.today().isoformat()
    count = len([e for e in sent if e.get("ts", "").startswith(today)])
    apps = events(APP_LEDGER)
    results = []
    for r in rows:
        if count >= cap:
            results.append(("(all)", "STOP: daily cap %d reached" % cap))
            break
        if (r.get("status") or "") not in ("drafted", "queued"):
            continue
        if (r.get("channel") or "email") != "email" or "@" not in (r.get("contact") or ""):
            continue  # DM drafts stay manual / browser-assisted
        company = r["company"]
        recent = [e for e in sent if e.get("company", "").lower() == company.lower()
                  and e.get("ts", "")[:10]
                  and (date.today() - date.fromisoformat(e["ts"][:10])).days <= cooldown]
        if recent:
            results.append((company, "skip: %d-day cooldown" % cooldown))
            continue
        applied = [e for e in apps if e.get("event") == "sent"
                   and company.lower().replace(" ", "")[:10] in str(e.get("app", "")).lower().replace("_", "")]
        if applied:
            results.append((company, "skip: application already in ledger (second touch)"))
            continue
        path = latest_draft(company)
        if not path:
            results.append((company, "skip: no draft file"))
            continue
        meta = parse(path.read_text(encoding="utf-8", errors="ignore"))
        to, subject, body = meta.get("to", ""), meta.get("subject", ""), meta.get("body", "")
        if "LINKEDIN" in to.upper() or "@" not in to or not subject or not body:
            results.append((company, "skip: not an email draft"))
            continue
        cfg = env()
        msg = EmailMessage()
        msg["From"] = cfg["GMAIL_ADDRESS"]
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(cfg["GMAIL_ADDRESS"], cfg["GMAIL_APP_PASSWORD"])
            s.send_message(msg)
        with OUT_LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                                "event": "sent", "company": company, "person": meta.get("person", ""),
                                "to": to, "subject": subject, "draft": path.name, "auto": True}) + "\n")
        with CSV.open(newline="", encoding="utf-8") as f:
            all_rows = [dict(x) for x in csv.DictReader(f)]
        fields = list(all_rows[0].keys()) if all_rows else []
        for x in all_rows:
            if x["company"].lower() == company.lower():
                x["status"] = "sent"
                x["first_contact"] = today
        with CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(all_rows)
        count += 1
        results.append((company, "SENT -> %s" % to))
    print("AUTO OUTREACH RUN - sent today: %d" % count)
    for c, s in results:
        print("  %-40s %s" % (c, s))
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--company")
    p.add_argument("--draft")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--i-confirm-send", action="store_true")
    p.add_argument("--force", action="store_true", help="Override the 30-day company cooldown")
    p.add_argument("--auto", action="store_true",
                   help="Standing-authorization mode: send ready email drafts within caps "
                        "(profile/outreach_policy.json must enable standing_authorization)")
    a = p.parse_args()
    if a.auto:
        return cmd_auto()
    if not a.company and not a.draft:
        raise SystemExit("--company or --draft required")
    path = Path(a.draft) if a.draft else latest_draft(a.company)
    if not path or not path.exists():
        raise SystemExit("No draft found. Run scripts/outreach.py draft --company ...")
    meta = parse(path.read_text(encoding="utf-8", errors="ignore"))
    company = meta.get("company") or a.company or "?"
    to, subject, body = meta.get("to", ""), meta.get("subject", ""), meta.get("body", "")
    print("FILE:", path.name)
    print("TO:", to)
    print("SUBJECT:", subject)
    print("BODY (%d words):\n%s" % (len(body.split()), body))
    if to.upper().startswith("LINKEDIN") or "@" not in to:
        raise SystemExit("Not an email draft (manual DM). Send it yourself.")
    if not subject or not body:
        raise SystemExit("Draft is missing SUBJECT or BODY")

    led = events(OUT_LEDGER)
    sent = [e for e in led if e.get("event") == "sent"]
    today = date.today().isoformat()
    today_count = len([e for e in sent if e.get("ts", "").startswith(today)])
    if today_count >= CAP:
        raise SystemExit("BLOCKED: daily cap %d reached" % CAP)
    recent = [e for e in sent if e.get("company", "").lower() == company.lower()
              and e.get("ts", "")[:10] and (date.today() - date.fromisoformat(e["ts"][:10])).days <= COOLDOWN]
    if recent and not a.force:
        raise SystemExit("BLOCKED: %s was contacted within the last %d days" % (company, COOLDOWN))

    apps = events(APP_LEDGER)
    applied = [e for e in apps if e.get("event") == "sent"
               and company.lower().replace(" ", "")[:10] in str(e.get("app", "")).lower().replace("_", "")]
    if applied and not a.force:
        raise SystemExit("BLOCKED: %s already has an application in the ledger; cold email would be a second touch (use --force only if it is deliberate)" % company)

    if not (a.confirm and a.i_confirm_send):
        print("\nDry run only. To send: remove profile/SEND_DISABLED, then re-run with --confirm --i-confirm-send")
        return
    if SEND_DISABLED.exists():
        raise SystemExit("BLOCKED: profile/SEND_DISABLED is present. Nothing was sent.")
    cfg = env()
    msg = EmailMessage()
    msg["From"] = cfg["GMAIL_ADDRESS"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(cfg["GMAIL_ADDRESS"], cfg["GMAIL_APP_PASSWORD"])
        s.send_message(msg)
    with OUT_LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "event": "sent", "company": company, "person": meta.get("person", ""),
                            "to": to, "subject": subject, "draft": path.name}) + "\n")
    if CSV.exists():
        with CSV.open(newline="", encoding="utf-8") as f:
            rows = [dict(r) for r in csv.DictReader(f)]
        fields = list(rows[0].keys()) if rows else []
        for r in rows:
            if r["company"].lower() == company.lower():
                r["status"] = "sent"
                r["first_contact"] = today
        with CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    print("\nSENT. Ledger: logs/outreach_ledger.jsonl | targets.csv updated.")


if __name__ == "__main__":
    main()
