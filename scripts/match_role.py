# -*- coding: utf-8 -*-
"""Pick CV folder. Domain mismatches only — not years on the JD.

Years / Manager / Head: do NOT refuse. See profile/facts.json experience_policy.
The applicant has ~4 years; ±3 stretch is normal. HR lists are wish-lists.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FACTS = json.loads((ROOT / "profile" / "facts.json").read_text(encoding="utf-8"))

SENIOR_JD = re.compile(r"\b(senior|lead|head|manager|principal|staff)\b", re.I)
# Engineering titles beat the generalist web/webmaster bucket (e.g. "WordPress
# Engineer" is software engineering, not a generalist web/data role).
SWE_TITLE = re.compile(
    r"\b(engineer|developer|full[- ]?stack|backend|frontend|front[- ]?end|software)\b", re.I
)
TEACH_JD = re.compile(
    r"\b(teacher|trainer|facilitator|instructor|tutor|lecturer|robotics|training intern|training internship|stem education)\b",
    re.I,
)
MH_JD = re.compile(r"\b(mental health|counsel|wellbeing|psychosocial|facilitator)\b", re.I)
MARKET_JD = re.compile(r"\b(marketing|social media|content creator|video editor|brand)\b", re.I)
SWE_JD = re.compile(
    r"\b(software|developer|engineer|full[- ]?stack|backend|frontend|front[- ]?end|java|sde|qa|test|cloud|"
    r"ict technician|ict intern|it officer|systems engineer|system developer|"
    r"information systems assistant|it manager|ict manager|head of it|"
    r"systems manager|ict.?data officer|data officer)\b",
    re.I,
)
IT_SUPPORT_JD = re.compile(
    r"\b(ict technician|ict intern|ict attachment|it officer|ict officer|it support|ict support|it intern|it attachment|hardware support|digital workplace|help ?desk|"
    r"desktop support|information systems assistant|it manager|ict manager|head of it|"
    r"systems manager|ict.?data|data officer|it lead|network support|it department|"
    r"head of ict|principal information communication technology|"
    r"field technician|network technician|fibre technician|telecommunications technician|"
    r"telecom technician|network installation technician)\b",
    re.I,
)
TEACH_TITLE = re.compile(r"\b(teacher|tutor|instructor|lecturer)\b", re.I)
AI_EVAL_JD = re.compile(
    r"\b(ai project|ai evaluation|llm|large language model|ai agents?|model training|sentiment analysis|data annotation)\b",
    re.I,
)
ENUM_JD = re.compile(
    r"\benumerators?\b|research assistant|community research|data collection|field interview",
    re.I,
)
DATA_DS_JD = re.compile(
    r"\b(data scientist|data science|data analyst|data analytics|"
    r"business analyst|business intelligence)\b",
    re.I,
)
GENERALIST_JD = re.compile(
    r"\b(digital transformation|transformation officer|data entry|data clerk|"
    r"web designer|webmaster|wordpress|data protection|privacy officer)\b",
    re.I,
)
GRADUATE_JD = re.compile(r"\bgraduate trainee\b|\bgraduate programme\b", re.I)


def years_stretch_note(jd_text: str) -> str:
    """Annotate DECISION only — never a refuse reason by itself."""
    policy = FACTS.get("experience_policy") or {}
    have = int(policy.get("years_in_field") or 4)
    flex = int(policy.get("flexibility_years") or 3)
    m = re.search(r"(\d+)\s*\+?\s*(?:-|to)?\s*(\d+)?\s*\+?\s*years?", jd_text.lower())
    if not m:
        return ""
    lo = int(m.group(1))
    hi = int(m.group(2) or m.group(1))
    if hi <= have + flex:
        return f"Years on JD ~{lo}-{hi}; applicant ~{have} (±{flex}) — stretch OK, still prepare."
    return (
        f"Years on JD ~{lo}-{hi}; applicant ~{have}. Soft stretch beyond ±{flex} — "
        "still prepare if tech overlap; do not refuse on years alone."
    )


def pick_base(job_title: str, jd_text: str) -> dict:
    blob = f"{job_title}\n{jd_text}".lower()
    if TEACH_TITLE.search(blob) and not SENIOR_JD.search(blob) and not SWE_JD.search(blob):
        return {"folder": "coding-teacher", "reason": "JD is a teacher / tutor / instructor role"}
    if MH_JD.search(blob) and not SWE_JD.search(blob):
        return {"folder": "mental-health", "reason": "JD is mental health / facilitation"}
    if TEACH_JD.search(blob) and not SENIOR_JD.search(blob) and not SWE_JD.search(blob):
        return {"folder": "coding-teacher", "reason": "JD is teaching / training / facilitating"}
    if MARKET_JD.search(blob) and not SWE_JD.search(blob) and not IT_SUPPORT_JD.search(blob):
        return {"folder": "marketing", "reason": "JD is marketing / social / video"}
    if DATA_DS_JD.search(blob):
        note = years_stretch_note(jd_text)
        return {
            "folder": "ai-evaluation",
            "reason": "Data science / analyst JD - AI-evaluation master has the Python, "
                      "SQL-adjacent and structured-evidence depth (not the generic SWE CV)",
            "stretch_note": note,
        }
    if GENERALIST_JD.search(blob) and not SWE_TITLE.search(job_title):
        note = years_stretch_note(jd_text)
        return {
            "folder": "ai-evaluation",
            "reason": "Generalist digital/web/data role - AI-evaluation master, AI and "
                      "delivery evidence first",
            "stretch_note": note,
        }
    if AI_EVAL_JD.search(blob) and not SWE_JD.search(blob):
        return {
            "folder": "ai-evaluation",
            "reason": "LLM/AI-agent evaluation or AI training role",
            "stretch_note": years_stretch_note(jd_text),
        }
    if ENUM_JD.search(blob) and not SWE_JD.search(blob):
        return {
            "folder": "data-annotation",
            "reason": "Enumerator / community research / field data collection",
            "stretch_note": years_stretch_note(jd_text),
        }
    # IT Manager / Head of IT / support: always SWE track (stretch seniority is fine)
    if IT_SUPPORT_JD.search(blob):
        note = years_stretch_note(jd_text)
        return {
            "folder": "software-engineer",
            "reason": "ICT/IT support or IT manager-style role — prepare; years are soft",
            "stretch_note": note,
        }
    if GRADUATE_JD.search(blob):
        # Generalist graduate programme (bank/consulting). The applicant's angle is IT/AI;
        # user confirmed the SWE master for Stanbic (2026-10-23).
        return {
            "folder": "software-engineer",
            "reason": "Generalist graduate trainee programme - SWE master has the AI/data depth the JD asks for",
            "stretch_note": years_stretch_note(jd_text),
        }
    if SENIOR_JD.search(blob) and SWE_JD.search(blob):
        note = years_stretch_note(jd_text)
        return {
            "folder": "software-engineer",
            "reason": "Senior/lead/manager software title — prepare on tech overlap, do not refuse on years",
            "stretch_note": note,
        }
    if SWE_JD.search(blob):
        if re.search(r"\bjava\b", blob) or "spring" in blob:
            return {
                "folder": "java-developer",
                "reason": "Java/Spring on the JD",
                "stretch_note": years_stretch_note(jd_text),
            }
        return {
            "folder": "software-engineer",
            "reason": "Software/engineering JD",
            "stretch_note": years_stretch_note(jd_text),
        }
    return {
        "folder": None,
        "reason": "Unknown role. Ask the user which base-cvs folder to use. Do not guess.",
        "stretch_note": years_stretch_note(jd_text),
    }


def assert_allowed(folder: str, job_title: str, jd_text: str):
    blob = f"{job_title}\n{jd_text}".lower()
    rules = FACTS.get("match_rules", {}).get(folder)
    if not rules:
        return
    for token in rules.get("forbid", []):
        if token.lower() in blob and folder in ("coding-teacher", "mental-health"):
            raise ValueError(
                f"Refusing {folder} for '{job_title}': forbid token '{token}'. "
                "Pick a software/marketing folder or ask the user."
            )
