#!/usr/bin/env python3
"""Cold-outreach CRM + draft generator. Never sends (see outreach/PLAYBOOK.md)."""
import argparse
import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "outreach" / "targets.csv"
DRAFTS = ROOT / "outreach" / "drafts"
DUE = ROOT / "outreach" / "DUE.md"
LEDGER = ROOT / "logs" / "outreach_ledger.jsonl"
FACTS = ROOT / "profile" / "facts.json"
FIELDS = ["id", "company", "person", "role", "channel", "contact", "why_them",
          "template", "subject", "status", "first_contact", "followup1_due",
          "followup2_due", "reply", "outcome", "notes"]
FIRST_BD, FINAL_BD, CAP, COOLDOWN = 4, 10, 10, 30


def links():
    f = json.loads(FACTS.read_text(encoding="utf-8"))
    n = f.get("ai_project_application_notes", {})
    return {"name": f.get("name", ""), "phone": f.get("phone", ""),
            "github": n.get("github_url", ""), "linkedin": n.get("linkedin_url", ""),
            "portfolio": f.get("portfolio_url", "")}


def read_rows():
    if not CSV.exists():
        return []
    with CSV.open(newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


def write_rows(rows):
    with CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def find(rows, company):
    q = company.strip().lower()
    hits = [r for r in rows if q == r["company"].lower() or q in r["company"].lower()]
    if not hits:
        raise SystemExit("No target matching '%s'" % company)
    if len(hits) > 1:
        raise SystemExit("Ambiguous: " + ", ".join(r["company"] for r in hits))
    return hits[0]


def ledger():
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def log_event(e):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    e = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), **e}
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(e) + "\n")


def bdays(d, n):
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def words(t):
    return len(t.replace("—", " ").split())


def clause(why):
    why = (why or "").strip().rstrip(".")
    if why and why[0].isupper() and not why.lower().startswith(("i ", "we ")):
        why = why[0].lower() + why[1:]
    return why


def body_for(tpl, r, f, about=None):
    first = (r.get("person") or "").split()[0] if r.get("person") else ""
    c, role = r["company"], r.get("role") or "the role"
    why = clause(r.get("why_them"))
    sig = "Thanks,\n%s\n%s" % (f["name"], f["phone"])
    if tpl == "founder":
        return ("Interested in building with %s" % c, """Hi %s,

I came across %s — %s.

I build LLM assistants and agent workflows — structured prompts, JSON-schema validation, multi-step tool use and Python scoring on Anthropic/OpenAI APIs. I've also evaluated AI-generated outputs against benchmark criteria at Groreels, and done data annotation and AI training tasks at Sama, Remotasks and Shamiri, including classification work in my Sama role.

I'd love to explore whether I could contribute to what you're building. Are you currently open to bringing on developers or AI evaluators?

Portfolio: %s
GitHub: %s

%s""" % (first, c, why, f["portfolio"], f["github"], sig))
    if tpl == "role":
        about_par = about or ("I'm a software/AI practitioner with hands-on experience in Python, "
                              "LLM integration and data evaluation. At Shwaah I built LLM assistant "
                              "flows with structured prompts, JSON-schema validation and multi-step "
                              "agent workflows; at Groreels I scored AI outputs against benchmark "
                              "criteria in Python.")
        subj = ("%s at %s" % (role, c)) if about else ("%s at %s | Python + LLM evaluation" % (role, c))
        return (subj, """Hi %s,

I came across the %s role at %s and wanted to reach out directly.

%s

Here's my work: %s · %s

Are you still considering candidates for this position? I'd love to be considered.

Thanks,
%s
LinkedIn: %s""" % (first, role, c, about_par, f["portfolio"], f["github"], f["name"], f["linkedin"]))
    if tpl == "referral":
        bg = about or ("My background is Python, LLM evaluation and data annotation/classification, "
                       "with hands-on experience building LLM assistant and agent workflows at Shwaah "
                       "and scoring AI outputs at Groreels.")
        return ("Quick question about %s at %s" % (role, c), """Hi %s,

I came across the %s opening at %s and saw that you work there.

%s

Portfolio: %s

If you think my profile could be a fit, would you be comfortable referring me for the role?

Thanks for your time!
%s""" % (first, role, c, bg, f["portfolio"], f["name"]))
    if tpl == "freelance":
        return ("An idea for %s" % c, """Hi %s,

I came across %s — %s.

I work on LLM-powered tools and data pipelines — structured prompts, schema-validated outputs, and Python evaluation of model quality. Here's an example of what I can do: %s.

Would you be open to me sharing a couple of ideas for %s?

Best,
%s""" % (first, c, why, f["portfolio"], c, f["name"]))
    if tpl == "dm":
        role_line = ""
        if role and role.lower() not in ("the role", "open engineering roles") and "role" not in why.lower():
            role_line = " I saw the %s opening and wanted to reach out directly." % role
        about_par = about or ("I build LLM assistants and agent workflows on Anthropic/OpenAI APIs — "
                              "structured prompts, JSON-schema validation, multi-step tool use and Python "
                              "scoring. I've also evaluated AI-generated outputs against benchmark criteria "
                              "at Groreels, and done data annotation and AI training tasks at Sama, "
                              "Remotasks and Shamiri, including classification work in my Sama role.")
        return ("DM draft — %s" % c, """Hey %s, I came across %s — %s.%s

%s

I'd love to connect and explore whether I could contribute to your team. Portfolio: %s · GitHub: %s""" % (first, c, why, role_line, about_par, f["portfolio"], f["github"]))
    if tpl.startswith("followup"):
        subj = r.get("subject") or ("your note about %s" % c)
        if not subj.lower().startswith("re:"):
            subj = "Re: " + subj
        if tpl == "followup2":
            return (subj, """Hi %s,

One last note — I don't want to clutter your inbox. If %s isn't hiring or exploring this right now, no problem at all; I'll keep following your work.

If a fit opens later, I'd be glad to talk.

Thanks,
%s""" % (first, c, f["name"]))
        return (subj, """Hi %s,

Just following up on my note about %s.

I'm still interested because %s, and I thought my experience with Python, LLM integration and AI-output evaluation might be relevant.

Would this be worth a short conversation?

Thanks,
%s""" % (first, c, why, f["name"]))
    raise SystemExit("Unknown template '%s'" % tpl)


def cmd_list(a):
    rows = read_rows()
    if a.status:
        rows = [r for r in rows if r["status"] == a.status]
    if not rows:
        print("no targets")
        return 0
    for r in rows:
        print("%-30s %-20s %-10s %-14s %s" % (r["company"][:30], (r["person"] or "-")[:20],
              (r["template"] or "-")[:10], (r["status"] or "-")[:14], r["contact"] or "-"))
    return 0


def cmd_add(a):
    rows = read_rows()
    if any(r["company"].lower() == a.company.strip().lower() for r in rows):
        raise SystemExit("already in targets.csv")
    why = (a.why or "").strip()
    if a.template in ("founder", "freelance", "dm") and not why:
        raise SystemExit("--why required for founder/freelance/dm")
    row = {k: "" for k in FIELDS}
    row.update({"id": "".join(ch for ch in a.company.lower() if ch.isalnum())[:18] or "target",
                "company": a.company.strip(), "person": (a.person or "").strip(),
                "role": (a.role or "").strip(), "channel": a.channel or "email",
                "contact": (a.contact or "").strip(), "why_them": why,
                "template": a.template or "role",
                "status": "queued" if (a.person and a.contact) else "needs_contact"})
    rows.append(row)
    write_rows(rows)
    print("added:", a.company)
    return 0


def cmd_draft(a):
    rows = read_rows()
    r = find(rows, a.company)
    tpl = ("followup%d" % a.followup) if a.followup else (r.get("template") or "role")
    if not r.get("person"):
        raise SystemExit("add the person's name first (research; never guess)")
    if tpl != "dm" and not r.get("contact"):
        raise SystemExit("no verified email; add contact or use channel=linkedin + dm")
    if tpl in ("founder", "freelance", "dm") and not r.get("why_them"):
        raise SystemExit("why_them is empty")
    subject, body = body_for(tpl, r, links(), about=getattr(a, "about", None))
    n = words(body)
    if n < 50 or n > 200:
        print("WARNING: %d words (target 80-150)" % n)
    DRAFTS.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "-" for ch in r["company"].lower()).strip("-")[:40]
    suffix = ("-followup%d" % a.followup) if a.followup else ""
    path = DRAFTS / ("%s_%s%s.txt" % (date.today().isoformat(), safe, suffix))
    to = r.get("contact") if tpl != "dm" else "LINKEDIN/X-DM (manual)"
    path.write_text("""DRAFT — cold outreach. Nothing sends until the applicant approves.
TO: %s
SUBJECT: %s
COMPANY: %s
PERSON: %s
TEMPLATE: %s
WORDS: %d
BODY:
%s
""" % (to, subject, r["company"], r["person"], tpl, n, body), encoding="utf-8")
    r["subject"] = subject
    r["status"] = ("followup%d_drafted" % a.followup) if a.followup else "drafted"
    write_rows(rows)
    log_event({"event": "drafted", "company": r["company"], "person": r["person"],
               "template": tpl, "words": n})
    print("draft: %s (%d words)\n%s" % (path.relative_to(ROOT), n, "-" * 60))
    print(body)
    return 0


def cmd_due(_a):
    rows, led = read_rows(), ledger()
    sends = {}
    for e in led:
        if e.get("event") == "sent" and e.get("company"):
            try:
                sends.setdefault(e["company"], []).append(date.fromisoformat(e["ts"][:10]))
            except ValueError:
                pass
    today = date.today()
    needs, ready, follow, replied = [], [], [], []
    for r in rows:
        st = r.get("status") or "needs_contact"
        if st == "needs_contact":
            needs.append(r); continue
        if st in ("drafted", "queued"):
            ready.append(r); continue
        if st == "replied":
            replied.append(r); continue
        if st in ("closed", "dead"):
            continue
        s = sorted(sends.get(r["company"], []))
        if not s:
            ready.append(r); continue
        f1, f2 = bdays(s[0], FIRST_BD), bdays(s[0], FINAL_BD)
        if len(s) == 1:
            follow.append((r, f1, (today - f1).days, "first"))
        elif len(s) >= 2 and today >= f2:
            follow.append((r, f2, (today - f2).days, "final"))
    lines = ["# COLD OUTREACH — DUE BOARD", "",
             "Generated %s · cap %d/day · cooldown %dd · first follow-up %d business days · final %d."
             % (today.isoformat(), CAP, COOLDOWN, FIRST_BD, FINAL_BD), ""]
    if replied:
        lines += ["## Replies — answer", ""] + ["- **%s** (%s): %s" % (r["company"], r["person"], r["reply"] or "log it") for r in replied] + [""]
    if ready:
        lines += ["## Drafted / ready", ""]
        for r in ready:
            if r.get("channel") in ("linkedin", "x") or "linkedin" in (r.get("contact") or "").lower():
                lines.append("- %s (%s) — send the DM manually on LinkedIn" % (r["company"], r["person"]))
            else:
                lines.append("- %s (%s) — `send outreach %s`" % (r["company"], r["person"], r["company"]))
        lines.append("")
    if follow:
        lines += ["## Follow-up cadence", ""]
        for r, due, delta, which in follow:
            state = "OVERDUE" if delta > 0 else "in %dd" % -delta
            num = 1 if which == "first" else 2
            lines.append("- %s (%s) — %s follow-up %s — **%s** — `python3 scripts/outreach.py draft --company \"%s\" --followup %d`" % (r["company"], r["person"], which, due, state, r["company"], num))
        lines.append("")
    if needs:
        lines += ["## Needs a verified contact (research first)", ""] + ["- %s — %s — %s" % (r["company"], r["role"], r["notes"]) for r in needs] + [""]
    DUE.write_text("\n".join(lines), encoding="utf-8")
    print("due board: %s | ready %d | follow-ups %d | needs contact %d | replies %d" % (DUE.relative_to(ROOT), len(ready), len(follow), len(needs), len(replied)))
    return 0


def _latest_draft(company):
    slug = "".join(ch if ch.isalnum() else "-" for ch in company.lower()).strip("-")[:40]
    hits = sorted(DRAFTS.glob("*_%s*.txt" % slug), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def cmd_pack(_a):
    rows = read_rows()
    today = date.today().isoformat()
    lines = ["# COLD OUTREACH BATCH — %s" % today, "",
             "Nothing here has been sent. Send each message yourself on LinkedIn.",
             "If LinkedIn limits the connection note to 300 characters, connect without a note first, then paste the message.",
             "After sending, record it: `.venv/bin/python scripts/outreach.py mark --company \"<Company>\" --status sent`",
             "",]
    n = 0
    for r in rows:
        if (r.get("status") or "") not in ("drafted", "queued"):
            continue
        path = _latest_draft(r["company"])
        if not path:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        body = text.split("BODY:", 1)[-1].strip()
        n += 1
        lines += ["## %d. %s — %s" % (n, r["company"], r["person"]),
                  "LinkedIn: %s" % (r.get("contact") or "(no URL)"),
                  "Role: %s" % (r.get("role") or "-"), ""]
        lines += ["```text", body, "```", ""]
    out = ROOT / "outreach" / ("BATCH_%s.md" % today)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("batch: %s | %d messages" % (out.relative_to(ROOT), n))
    return 0


def cmd_mark(a):
    rows = read_rows()
    r = find(rows, a.company)
    r["status"] = a.status
    if a.reply:
        r["reply"] = a.reply
    if a.outcome:
        r["outcome"] = a.outcome
    write_rows(rows)
    log_event({"event": a.status, "company": r["company"], "reply": a.reply or "", "outcome": a.outcome or ""})
    print(r["company"], "->", a.status)
    return 0


def cmd_report(_a):
    led, rows = ledger(), read_rows()
    delivered = len([e for e in led if e.get("event") == "sent"])
    replies = [r for r in rows if (r.get("reply") or "").strip()]
    positive = [r for r in replies if (r.get("reply") or "").lower() in ("yes", "positive", "interested", "constructive", "maybe")]
    outcomes = [r for r in rows if (r.get("outcome") or "").strip()]
    pct = lambda x: ("%.0f%%" % (100.0 * x / delivered)) if delivered else "n/a"
    print("delivered: %d | replies: %d (%s) | positive: %d (%s) | outcomes: %d (%s)" %
          (delivered, len(replies), pct(len(replies)), len(positive), pct(len(positive)),
           len(outcomes), pct(len(outcomes))))
    return 0


def main():
    p = argparse.ArgumentParser(description="Cold-outreach CRM (never sends)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("--status"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("add")
    s.add_argument("--company", required=True); s.add_argument("--person", required=True)
    s.add_argument("--role"); s.add_argument("--channel", choices=["email", "linkedin", "x"], default="email")
    s.add_argument("--contact"); s.add_argument("--why"); s.add_argument("--template",
        choices=["founder", "role", "referral", "freelance", "dm"], default="role")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("draft")
    s.add_argument("--company", required=True); s.add_argument("--followup", type=int, choices=[1, 2])
    s.add_argument("--about")
    s.set_defaults(fn=cmd_draft)
    s = sub.add_parser("due"); s.set_defaults(fn=cmd_due)
    s = sub.add_parser("mark")
    s.add_argument("--company", required=True)
    s.add_argument("--status", required=True, choices=["sent", "replied", "closed", "dead", "queued"])
    s.add_argument("--reply"); s.add_argument("--outcome")
    s.set_defaults(fn=cmd_mark)
    s = sub.add_parser("pack"); s.set_defaults(fn=cmd_pack)
    s = sub.add_parser("report"); s.set_defaults(fn=cmd_report)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
