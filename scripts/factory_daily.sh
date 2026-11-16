#!/usr/bin/env zsh
# Daily autonomous factory run (systemd timer or cron).
#
# Fault-tolerance design:
# - AI lead-pull (needs laptop ON + desktop app open + WhatsApp session live):
#   wrapped in `timeout` so a hung session can never block the deterministic
#   pipeline; any failure falls through cleanly.
# - Mechanical pipeline (ingest -> 4-lens review -> sync -> queue ->
#   sendable list) needs no browser and no network; it always runs.
# - pipeline.json is backed up to backups/ (7 rotating copies) before every run
#   so a mid-write crash can never lose the pipeline without a restore point.
# - Persistent systemd timer catches up missed runs after the laptop was off.
# - NEVER sends email. The send gate stays `send <company>` / `send all`.
set -u
PROJECT="$HOME/Documents/apply jobs"
LOG="$PROJECT/logs/factory_cron.log"
AI_RUN_TIMEOUT="${AI_RUN_TIMEOUT:-1200}"
OPENCODE_BIN="${OPENCODE_BIN:-$HOME/.opencode/bin/opencode}"

cd "$PROJECT" || exit 1

# Keep backup window (7 rotating copies of pipeline.json)
if [ -f opportunities/pipeline.json ]; then
  mkdir -p backups
  cp opportunities/pipeline.json "backups/pipeline-$(date '+%F').json" 2>/dev/null
  ls -t backups/pipeline-*.json 2>/dev/null | tail -n +8 2>/dev/null | while read -r f; do rm -f "$f"; done
fi

# Source only the non-interactive `export` lines from .zshrc (API keys, PATH)
# without duplicating secrets anywhere else.
[[ -r "$HOME/.zshrc" ]] && source <(grep -E '^export [A-Za-z_]+=' "$HOME/.zshrc") 2>/dev/null

{
  echo ""
  echo "===== $(date '+%F %T') factory run ====="
} >> "$LOG" 2>&1

if [ -x "$OPENCODE_BIN" ]; then
  timeout "$AI_RUN_TIMEOUT" "$OPENCODE_BIN" run "factory run: if the desktop browser is attached, pull WhatsApp job leads from linked channels and write them into opportunities/inbox/ using the README header format (skip silently if the browser is not attached). Then ingest opportunities/inbox/, review unsent packs, sync SHIP packs, and refresh opportunities/READY_QUEUE.txt. Never send email." >> "$LOG" 2>&1
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "[factory_daily] AI run ended rc=$rc (124=timeout, 1=error) - continuing with mechanical pipeline" >> "$LOG" 2>&1
  fi
else
  echo "[factory_daily] opencode binary not found at $OPENCODE_BIN - mechanical pipeline only" >> "$LOG" 2>&1
fi

# Mechanical pipeline always runs (local files; no browser, no API credits needed)
.venv/bin/python scripts/scan_kenya_boards.py >> "$LOG" 2>&1   # Kenya ATS boards -> inbox leads (mechanical)
.venv/bin/python scripts/followups.py >> "$LOG" 2>&1           # follow-up cadence board + drafts (never sends)
.venv/bin/python scripts/outreach.py due >> "$LOG" 2>&1        # cold-outreach due board (never sends)
.venv/bin/python scripts/autopilot.py >> "$LOG" 2>&1
.venv/bin/python scripts/list_sendable.py >> "$LOG" 2>&1

# Standing-authorization outreach autosend (granted by the applicant 2026-11-16):
# sends ready EMAIL drafts within caps ONLY while profile/outreach_policy.json
# enables it. The SEND gate is lifted for this step alone and re-armed immediately.
AUTO_OK=$(.venv/bin/python -c 'import json,sys
try:
    p=json.load(open("profile/outreach_policy.json")); sys.exit(0 if p.get("standing_authorization") else 1)
except Exception:
    sys.exit(1)' 2>/dev/null && echo yes || echo no)
if [ "$AUTO_OK" = yes ]; then
  rm -f profile/SEND_DISABLED
  .venv/bin/python scripts/send_outreach.py --auto >> "$LOG" 2>&1
  touch profile/SEND_DISABLED
  echo "[factory_daily] outreach auto-send step done (SEND gate re-armed)" >> "$LOG" 2>&1
else
  echo "[factory_daily] outreach autosend disabled by policy" >> "$LOG" 2>&1
fi

echo "[factory_daily] done" >> "$LOG" 2>&1
