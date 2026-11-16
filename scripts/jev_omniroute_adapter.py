#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JEV -> OmniRoute adapter.

Why this exists
---------------
jev's rating model (`typesafe/jev-1.13`) is served by OpenRouter's
`/api/alpha/decisions` endpoint, which takes `{model, state, questions}` and
returns typed probability answers. OmniRoute is an OpenAI-compatible gateway:
it has `/v1/chat/completions` but no decisions endpoint. This adapter listens
on localhost and translates between the two protocols, so jev can run with the
OmniRoute key the applicant already has instead of an OpenRouter account.

It is a faithful emulation, not the original model: answers are produced by a
general model under the JEV contract and then validated/clamped here. Review
low-confidence decisions as you would any model output.

Protocol (input):
    POST /decisions  {"model": "...", "state": <str|obj|list>, "questions": {...}}
    Question types: noul (probability 0..1), choice (one criterion + probs),
    score (probability-weighted index over ordered criteria).
Protocol (output):
    {"id","model","provider","answers":{qid: answer},"usage":{...}}

Env (loaded from tools/jev-job-search/.env, then the process environment):
    OMNIROUTE_API_KEY   required
    OMNIROUTE_BASE      default http://localhost:20128/v1
    OMNIROUTE_MODEL     default auto/best-fast
    JEV_ADAPTER_PORT    default 20129
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "tools" / "jev-job-search" / ".env"


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if k and os.environ.get(k.strip()) is None:
            os.environ[k.strip()] = v


load_env()

API_KEY = os.environ.get("OMNIROUTE_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
BASE = os.environ.get("OMNIROUTE_BASE", "http://localhost:20128/v1").rstrip("/")
MODEL = os.environ.get("OMNIROUTE_MODEL", "auto/best-fast")
PORT = int(os.environ.get("JEV_ADAPTER_PORT", "20129"))

SYSTEM = """You are the decision engine behind the JEV protocol (typesafe/jev-1.13).
You receive a STATE (context) and QUESTIONS. Answer every question by reasoning
only from the STATE. Never invent facts that are not in the state.

Output STRICT JSON, no prose, exactly this shape:
{"answers": {"<question_key>": <answer>, ...}}

Answer formats:
- noul:   {"type":"noul","noul": <number 0..1>}   // probability the statement holds
- choice: {"type":"choice","choice": "<one criterion key>",
           "probabilities": {"<criterion>": <0..1>, ...}, "confidence": <0..1>}
- score:  {"type":"score","score": <number 0..N-1>,
           "legend": {"0":"<label>", "1":"<label>", ...},
           "probabilities": {"<label>": <0..1>, ...}, "confidence": <0..1>}
           // score = probability-weighted index over the ORDERED criteria list

Rules:
- choice.probabilities must contain exactly the criteria keys given.
- score.probabilities must contain exactly the ordered criteria labels; score
  must equal the probability-weighted index (0 = first criterion, N-1 = last).
- confidence reflects how decisive the evidence is (0 = none, 1 = certain).
- Answer every question key. Use only the criteria provided."""


MAX_INPUT_CHARS = 120_000  # ~30k tokens; prevents context overflow on any routed model

def _truncate_state(state, max_chars):
    """Truncate large state strings/JSON to stay under the token budget."""
    import json as _json
    raw = _json.dumps(state, ensure_ascii=False) if not isinstance(state, str) else state
    if len(raw) <= max_chars:
        return state
    if isinstance(state, str):
        return state[:max_chars] + "\n[...truncated for context window...]"
    # For dicts/lists, stringify, truncate, wrap
    return raw[:max_chars] + "\n[...truncated for context window...]"


def call_omniroute(state, questions) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": json.dumps({"state": _truncate_state(state, MAX_INPUT_CHARS - 2000), "questions": questions}, ensure_ascii=False),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def parse_json_content(resp: dict) -> dict:
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.S)
    if not content:
        raise ValueError("empty content from OmniRoute")
    return json.loads(content)


def _probs(d, keys):
    out = {}
    for k in keys:
        try:
            out[k] = max(0.0, float(d.get(k, 0.0)))
        except (TypeError, ValueError):
            out[k] = 0.0
    total = sum(out.values())
    if total > 0:
        out = {k: v / total for k, v in out.items()}
    else:
        out = {k: 1.0 / len(keys) for k in keys}
    return out


def normalize(questions: dict, raw: dict) -> dict:
    answers_in = raw.get("answers", raw)
    out = {}
    for qid, q in questions.items():
        a = answers_in.get(qid)
        if a is None:
            a = {}
        if isinstance(a, str):
            a = {"choice": a}
        if isinstance(a, (int, float)):
            a = {"noul": a, "score": a}
        qtype = q.get("type")
        if qtype == "noul":
            val = a.get("noul", a.get("probability", a.get("score", 0.5)))
            out[qid] = {"type": "noul", "noul": min(1.0, max(0.0, float(val)))}
        elif qtype == "choice":
            keys = list((q.get("criteria") or {}).keys())
            choice = str(a.get("choice", "")).strip()
            if choice not in keys:
                probs = _probs(a.get("probabilities") or {}, keys)
                choice = max(probs, key=probs.get) if probs else (keys[0] if keys else "")
            probs = _probs(a.get("probabilities") or {}, keys) if keys else {}
            if choice in probs and not a.get("probabilities"):
                probs = {k: (0.9 if k == choice else 0.1 / max(1, len(keys) - 1)) for k in keys}
            conf = a.get("confidence", 0.7)
            out[qid] = {
                "type": "choice",
                "choice": choice,
                "probabilities": probs,
                "confidence": min(1.0, max(0.0, float(conf))),
            }
        elif qtype == "score":
            criteria = list(q.get("criteria") or [])
            n = max(1, len(criteria))
            score = a.get("score")
            try:
                score = float(score)
            except (TypeError, ValueError):
                probs = _probs(a.get("probabilities") or {}, criteria)
                score = sum(i * probs.get(k, 0.0) for i, k in enumerate(criteria))
            score = min(float(n - 1), max(0.0, score))
            probs = _probs(a.get("probabilities") or {}, criteria) if criteria else {}
            legend = {str(i): k for i, k in enumerate(criteria)}
            conf = a.get("confidence", 0.7)
            out[qid] = {
                "type": "score",
                "score": score,
                "legend": legend,
                "probabilities": probs,
                "confidence": min(1.0, max(0.0, float(conf))),
            }
    return out


def decide(state, questions) -> tuple[dict, dict]:
    last_err = None
    for attempt in range(3):
        try:
            resp = call_omniroute(state, questions)
            raw = parse_json_content(resp)
            answers = normalize(questions, raw)
            usage = resp.get("usage") or {}
            return answers, {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "cost": usage.get("cost", 0.0) or 0.0,
            }
        except Exception as e:  # noqa: BLE001 - retry then surface
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"adapter failed after retries: {last_err}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter logs
        print(f"[adapter] {fmt % args}", flush=True)

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path in ("/health", "/healthz"):
            self._send(200, {"ok": True, "model": MODEL, "upstream": BASE})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path not in ("/decisions", "/v1/decisions"):
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
            questions = body.get("questions") or {}
            if not isinstance(questions, dict):
                raise ValueError("questions must be an object")
            answers, usage = decide(body.get("state"), questions)
            self._send(
                200,
                {
                    "id": str(uuid.uuid4()),
                    "model": f"omniroute/{MODEL}",
                    "provider": "omniroute-adapter",
                    "answers": answers,
                    "usage": usage,
                },
            )
        except Exception as e:  # noqa: BLE001
            self._send(502, {"error": str(e)[:400]})


def main() -> int:
    if not API_KEY:
        raise SystemExit("OMNIROUTE_API_KEY is missing (tools/jev-job-search/.env)")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[adapter] listening on http://127.0.0.1:{PORT}/decisions -> {BASE} ({MODEL})", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
