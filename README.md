# Job Application Factory (Kenya)

An autonomous, human-gated job-application pipeline: it scans job boards, triages leads,
builds tailored application packs from master CVs, runs cold-outreach with verified contacts,
and enforces truthfulness gates — so the applicant stays out of the loop for everything that
does not legally or physically require their hands.

Built and hardened over weeks of real daily use (hundreds of prepared applications,
a 1,044-sample soak test with 0 classification failures, self-healing state files).

## The pipeline

```
scan_kenya_boards.py     -> public ATS/board pulls (zero tokens) into opportunities/inbox/
ingest_leads.py          -> parse, validate, dedupe vs sent history; self-heals corrupt state
match_role.py            -> JD -> track classification (SWE / AI-eval / ICT / annotation / marketing)
build_one(spec)          -> tailored CV + cover letter pack from master tracks (private modules)
verify_pack.py (private) -> mechanical checks: facts, dates, links, keywords, pagination
reviewer.py   (private)  -> role-fit + style lens
review_panel.py          -> 4 independent lenses (mechanical, learned guards, send-safety, dossier)
autopilot.py             -> ingest -> re-review -> byte-sync SHIP packs -> READY_QUEUE.txt
followups.py             -> 7/14-day follow-up cadence board (never sends by itself)
outreach.py              -> cold-outreach CRM + draft generator (6 templates, 80-150 words)
send_outreach.py         -> GATED sender: dry-run by default; caps, cooldowns, ledger
factory_daily.sh         -> systemd-timer wrapper: board scan -> cadence -> autopilot -> gates
```

## Design rules

- **Nothing sends without authorization.** `profile/SEND_DISABLED` gates every sender.
  A standing authorization (`profile/outreach_policy.json`) can enable bounded daily
  auto-sends of outreach (daily cap, 30-day per-company cooldown, every send ledgered),
  and the gate is re-armed immediately after each run.
- **Never guess people or channels.** Outreach targets stay `needs_contact` until a real
  person plus a *published* contact exists (e.g. a GPG-verified commit email). Drafts are
  80-150 words, one specific observation per target, no attachments on first contact.
- **Learning loop.** Every correction appends a log event; reusable fixes land in scripts
  and AGENTS; a `learn.py` pass compiles durable rules (e.g. role-classifier gaps found
  in real applications get fixed at the cause).
- **Truthfulness gates.** No fabricated credentials, employers, metrics, or channels.
  Eligibility bars (e.g. "final-year students only") are recorded honestly; stretch
  applications carry explicit stretch flags.
- **Portals are never emailed.** Listings whose only channel is an ATS portal get a
  prepared pack + route, not an email.

## What stays private

The CV builders, verification internals, applicant facts, application packs, leadgers,
and the sender's attachment gates are personal and are **not** part of this repo. The
engine above is the reusable part.

## Status

- Board scanner: Ashby / Greenhouse / Lever / BambooHR JSON + HTML career pages
- Outreach: email channel automated under standing authorization; LinkedIn DMs are a
  two-touch flow (connection note <=300 chars, then full message after acceptance)
- Reliability: corruption self-heal verified; soak-tested ingest at 1,044 samples, 0 failures
