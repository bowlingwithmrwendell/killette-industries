"""Agent 1 engine: Expertise Ingestion.

Turns a member's own description of their background into a structured,
comparable profile. Everything here is deterministic: the same submission
always produces the same profile, and every computed value carries its
formula, inputs and reasoning.

Rules, exactly as implemented
-----------------------------
Consent gate     Nothing is ingested, scored or matched unless the member has
                 said yes to profiling. Withdrawing consent removes the skills.

Evidence level   from years of hands-on use of the skill:
                   < 2 yrs -> 1 · 2-<4 -> 2 · 4-<7 -> 3 · 7-<12 -> 4 · 12+ -> 5
                 A current certification adds 1 (maximum 5).

Proficiency      level = min(self_rating, evidence_level + 1)
                 A member may rate themselves at most one level above what the
                 evidence supports; evidence never raises a modest self-rating.

Experience       Role years are merged so overlapping roles are not double
                 counted. An open role runs to the status date's year. A role
                 that starts and ends in the same year counts as 0.5 years.

Seniority        < 10 years Experienced · 10-<20 Senior · 20+ Distinguished

Completeness     headline 10 · at least one role 20 · at least three skills 25
                 · an industry 15 · a certification 10 · capacity stated 10
                 · every skill maps to the taxonomy 10   (total 100)
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from . import taxonomy
from .explain import explain

EVIDENCE_BANDS = [(Decimal("2"), 1), (Decimal("4"), 2), (Decimal("7"), 3), (Decimal("12"), 4)]
SENIORITY_BANDS = [(Decimal("10"), "Experienced"), (Decimal("20"), "Senior")]
COMPLETENESS_WEIGHTS = {"headline": 10, "roles": 20, "skills": 25, "industries": 15,
                        "certifications": 10, "capacity": 10, "all_skills_mapped": 10}


class IngestionError(ValueError):
    pass


def _d(v) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception as exc:
        raise IngestionError(f"Not a number: {v!r}") from exc


# --- scoring -----------------------------------------------------------------------

def evidence_level(years, certified: bool = False) -> tuple[int, dict]:
    y = _d(years)
    if y < 0:
        raise IngestionError("Years of experience cannot be negative.")
    base = 5
    for limit, lvl in EVIDENCE_BANDS:
        if y < limit:
            base = lvl
            break
    lvl = min(5, base + (1 if certified else 0))
    return lvl, explain("evidence = band(years) + 1 if certified (max 5)",
                        {"years": str(y), "certified": certified, "band_level": base},
                        f"{y} years falls in band {base}" + ("; certification adds 1" if certified else "")
                        + f" -> evidence level {lvl}.")


def proficiency(years, self_rating: int, certified: bool = False) -> tuple[int, dict]:
    if isinstance(self_rating, bool) or not isinstance(self_rating, int) or not 1 <= self_rating <= 5:
        raise IngestionError("self_rating must be a whole number from 1 to 5.")
    ev, _ = evidence_level(years, certified)
    level = min(self_rating, ev + 1)
    if level < self_rating:
        why = (f"Self-rating {self_rating} is more than one level above the evidence ({ev}), "
               f"so it is capped at {ev + 1}.")
    else:
        why = f"Self-rating {self_rating} is supported by the evidence ({ev}); level {level}."
    return level, explain("level = min(self_rating, evidence_level + 1)",
                          {"years": str(_d(years)), "certified": certified,
                           "self_rating": self_rating, "evidence_level": ev}, why)


def total_experience(roles: list[dict], as_of: date) -> tuple[Decimal, dict]:
    spans = []
    for r in roles:
        try:
            start = int(r["start_year"])
        except Exception as exc:
            raise IngestionError("Every role needs a start_year.") from exc
        end = r.get("end_year")
        end = as_of.year if end in (None, "") else int(end)
        if end < start:
            raise IngestionError(f"Role '{r.get('title', '?')}' ends before it starts.")
        if start > as_of.year:
            raise IngestionError(f"Role '{r.get('title', '?')}' starts in the future.")
        spans.append((start, end))
    spans.sort()
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    total = sum((Decimal(b - a) if b > a else Decimal("0.5")) for a, b in merged)
    return total, explain("sum of merged role spans (overlaps counted once; same-year role = 0.5)",
                          {"roles": len(roles), "merged_spans": [f"{a}-{b}" for a, b in merged],
                           "as_of_year": as_of.year},
                          f"{len(roles)} role(s) merge into {len(merged)} span(s) totalling {total} years.")


def seniority(total_years: Decimal) -> tuple[str, dict]:
    band = "Distinguished"
    for limit, name in SENIORITY_BANDS:
        if total_years < limit:
            band = name
            break
    return band, explain("< 10 Experienced · 10-<20 Senior · 20+ Distinguished",
                         {"total_years": str(total_years)}, f"{total_years} years -> {band}.")


def completeness(sub: dict, mapped: int, unmapped: int) -> tuple[int, dict]:
    checks = {
        "headline": bool((sub.get("headline") or "").strip()),
        "roles": len(sub.get("roles") or []) >= 1,
        "skills": mapped >= 3,
        "industries": len(sub.get("industries") or []) >= 1,
        "certifications": len(sub.get("certifications") or []) >= 1,
        "capacity": sub.get("capacity_hours") not in (None, "", 0, "0"),
        "all_skills_mapped": unmapped == 0 and mapped > 0,
    }
    score = sum(COMPLETENESS_WEIGHTS[k] for k, ok in checks.items() if ok)
    missing = [k for k, ok in checks.items() if not ok]
    return score, explain("sum of weights for each check passed",
                          {"weights": COMPLETENESS_WEIGHTS, "passed": checks},
                          f"Profile is {score}% complete" +
                          (f"; missing: {', '.join(missing)}." if missing else "."))


# --- ingestion -----------------------------------------------------------------------

def ingest(sub: dict, as_of: date) -> dict:
    """Validate and structure one member submission.

    Raises IngestionError when consent is missing or the submission is malformed.
    Free text (resume_text) never adds skills by itself: matches are returned as
    `suggested_skills` for the member to confirm.
    """
    if sub.get("consent_profiling") is not True:
        raise IngestionError("Consent required: the member must agree to profiling before their "
                             "expertise is ingested, scored or matched.")
    name = (sub.get("name") or "").strip()
    if len(name) < 2:
        raise IngestionError("A member name is required.")
    raw_skills = sub.get("skills") or []
    if not raw_skills:
        raise IngestionError("At least one skill is required.")

    skills, unmapped, seen = [], [], set()
    for item in raw_skills:
        label = (item.get("label") or "").strip()
        if not label:
            raise IngestionError("Every skill needs a label.")
        canonical = taxonomy.normalize(label)
        if canonical is None:
            unmapped.append(label)
            continue
        if canonical in seen:                       # keep the strongest entry per skill
            prev = next(x for x in skills if x["skill"] == canonical)
            if _d(item.get("years", 0)) <= _d(prev["years"]):
                continue
            skills.remove(prev)
        certified = bool(item.get("certified", False))
        level, why = proficiency(item.get("years", 0), item.get("self_rating"), certified)
        skills.append({"skill": canonical, "domain": taxonomy.domain_of(canonical), "raw_label": label,
                       "years": str(_d(item.get("years", 0))), "self_rating": item["self_rating"],
                       "certified": certified, "level": level, "explanation": why})
        seen.add(canonical)

    years, years_why = total_experience(sub.get("roles") or [], as_of)
    band, band_why = seniority(years)
    pct, pct_why = completeness(sub, len(skills), len(unmapped))
    suggested = [f for f in taxonomy.find_in_text(sub.get("resume_text") or "")
                 if f["skill"] not in seen]

    return {
        "name": name,
        "hub_id": sub.get("hub_id"),
        "headline": (sub.get("headline") or "").strip(),
        "industries": list(sub.get("industries") or []),
        "certifications": list(sub.get("certifications") or []),
        "roles": list(sub.get("roles") or []),
        "capacity_hours": str(_d(sub.get("capacity_hours") or 0)),
        "skills": sorted(skills, key=lambda x: (-x["level"], x["skill"])),
        "unmapped_skills": unmapped,
        "suggested_skills": suggested,
        "total_years": str(years),
        "seniority": band,
        "completeness_pct": pct,
        "explanations": {"total_years": years_why, "seniority": band_why, "completeness": pct_why},
    }


# --- coalition coverage ----------------------------------------------------------------

def coverage(needs: list[dict], profiles: list[dict]) -> dict:
    """Supply of qualified, consenting members against project skill demand.

    needs:    [{"project_id", "skill", "min_level", "hours_per_week"}]
    profiles: [{"member_id", "hub_id", "skills": {skill: level}, "available_hours",
                "assigned_by_skill": {skill: hours}}]
              (only ACTIVE members with consent are passed in)

    Supply for a skill = hours qualified members already give that skill + their
    free hours. Free hours can count toward more than one skill, so supply is
    what the coalition COULD cover, not a plan.
    """
    by_skill: dict[str, dict] = {}
    for n in needs:
        row = by_skill.setdefault(n["skill"], {"skill": n["skill"],
                                               "domain": taxonomy.domain_of(n["skill"]),
                                               "demand_hours": Decimal("0"), "projects": [],
                                               "min_level": n["min_level"]})
        row["demand_hours"] += _d(n["hours_per_week"])
        row["projects"].append(n["project_id"])
        row["min_level"] = max(row["min_level"], n["min_level"])

    out = []
    for skill, row in sorted(by_skill.items()):
        qualified = [p for p in profiles if p["skills"].get(skill, 0) >= row["min_level"]]
        supply = sum((_d(p["available_hours"]) + _d(p.get("assigned_by_skill", {}).get(skill, 0))
                      for p in qualified), Decimal("0")).quantize(Decimal("0.01"))
        gap = row["demand_hours"] - supply
        status = ("GAP" if not qualified or gap > 0 else
                  "SINGLE_POINT_OF_FAILURE" if len(qualified) == 1 else "COVERED")
        out.append({
            **row, "demand_hours": str(row["demand_hours"]),
            "qualified_members": [p["member_id"] for p in qualified],
            "available_hours": str(supply),
            "shortfall_hours": str(gap if gap > 0 else Decimal("0")),
            "status": status,
            "explanation": explain(
                "qualified = consenting members at level >= min_level; supply = hours they already "
                "give this skill + their free hours; shortfall = demand - supply",
                {"min_level": row["min_level"], "demand_hours": str(row["demand_hours"]),
                 "qualified": len(qualified), "available_hours": str(supply)},
                {"GAP": f"{len(qualified)} qualified member(s) can offer {supply} h/week against "
                        f"{row['demand_hours']} h/week of demand.",
                 "SINGLE_POINT_OF_FAILURE": "Demand is covered, but by only one qualified member.",
                 "COVERED": f"{len(qualified)} qualified members cover the demand."}[status]),
        })
    summary = {s: sum(1 for r in out if r["status"] == s)
               for s in ("GAP", "SINGLE_POINT_OF_FAILURE", "COVERED")}
    return {"skills": out, "summary": summary}
