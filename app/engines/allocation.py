"""Agent 3 engine: Resource Allocation.

A hub asks for resources against one of its own projects. Before any human
can approve the request, it must pass MANDATORY BUDGET VALIDATION against
that project's budget. Approval re-runs validation (so a request validated
yesterday cannot spend money committed since), and only then commits the
amounts and releases the funds.

Rules, exactly as implemented
-----------------------------
Validation (every check must pass)
  complete        at least one line; every line has a category, a description and an amount > 0
  hub_owns_project the requesting hub owns the project (hub autonomy: hubs spend their own budgets)
  timeline        needed_from <= needed_to, inside the project's dates, and not already over
  milestones      at least one milestone is named and every one belongs to the project
  category_budget for each category: requested <= remaining
                  remaining = budgeted - committed - spent
  project_budget  total requested <= total remaining across the project
  skills          every PERSONNEL line with a skill names a taxonomy skill and hours > 0

Matching (PERSONNEL lines that name a skill)
  eligible        ACTIVE, consenting members at level >= min_level with hours free
  score           50 x level/5 + 20 if in the requesting hub + 30 x min(1, available / needed)
                  ties broken by more available hours, then member id
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from . import taxonomy
from .explain import explain

CATEGORIES = ("PERSONNEL", "EQUIPMENT", "TRAVEL", "SUPPLIES", "CONTRACTUAL", "OTHER")
CENT = Decimal("0.01")
ONE_DP = Decimal("0.1")


def _d(v) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("NaN")


def _money(v: Decimal) -> str:
    return str(v.quantize(CENT, ROUND_HALF_UP))


def remaining_by_category(budget_lines: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for b in budget_lines:
        row = out.setdefault(b["category"], {"budgeted": Decimal("0"), "committed": Decimal("0"),
                                             "spent": Decimal("0")})
        row["budgeted"] += _d(b["budgeted"])
        row["committed"] += _d(b["committed"])
        row["spent"] += _d(b["spent"])
    for row in out.values():
        row["remaining"] = row["budgeted"] - row["committed"] - row["spent"]
    return out


def budget_position(budget_lines: list[dict]) -> dict:
    cats = remaining_by_category(budget_lines)
    tot = {k: sum((c[k] for c in cats.values()), Decimal("0"))
           for k in ("budgeted", "committed", "spent", "remaining")}
    used_pct = (((tot["committed"] + tot["spent"]) / tot["budgeted"] * 100).quantize(ONE_DP, ROUND_HALF_UP)
                if tot["budgeted"] else Decimal("0"))
    return {"by_category": {k: {kk: _money(vv) for kk, vv in v.items()} for k, v in sorted(cats.items())},
            "totals": {k: _money(v) for k, v in tot.items()}, "used_pct": str(used_pct),
            "explanation": explain("remaining = budgeted - committed - spent",
                                   {k: _money(v) for k, v in tot.items()},
                                   f"{used_pct}% of the project budget is committed or spent.")}


def validate(request: dict, project: dict, budget_lines: list[dict], milestone_ids: set[str],
             as_of: date) -> dict:
    checks = []

    def check(name, passed, detail):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    lines = request.get("lines") or []
    bad = []
    for i, ln in enumerate(lines, 1):
        amt = _d(ln.get("amount"))
        if ln.get("category") not in CATEGORIES:
            bad.append(f"line {i}: category must be one of {', '.join(CATEGORIES)}")
        if not (ln.get("description") or "").strip():
            bad.append(f"line {i}: description is required")
        if amt.is_nan() or amt <= 0:
            bad.append(f"line {i}: amount must be greater than 0")
    check("complete", lines and not bad,
          "Every line is complete." if lines and not bad else
          ("; ".join(bad) if bad else "The request has no lines."))

    check("hub_owns_project", request["hub_id"] == project["hub_id"],
          f"Hub {request['hub_id']} requests against project {project['id']} owned by {project['hub_id']}.")

    nf, nt = request["needed_from"], request["needed_to"]
    tl_ok = nf <= nt and nf >= project["start_date"] and nt <= project["end_date"] and nt >= as_of
    tl_detail = (f"{nf.isoformat()} to {nt.isoformat()} sits inside the project "
                 f"({project['start_date'].isoformat()} to {project['end_date'].isoformat()}).")
    if nf > nt:
        tl_detail = "needed_from is after needed_to."
    elif nf < project["start_date"] or nt > project["end_date"]:
        tl_detail = (f"{nf.isoformat()} to {nt.isoformat()} falls outside the project dates "
                     f"({project['start_date'].isoformat()} to {project['end_date'].isoformat()}).")
    elif nt < as_of:
        tl_detail = f"The requested period ended before the status date ({as_of.isoformat()})."
    check("timeline", tl_ok, tl_detail)

    ms = list(request.get("milestone_ids") or [])
    foreign = [m for m in ms if m not in milestone_ids]
    check("milestones", ms and not foreign,
          "No milestone is linked; every request must name the milestone(s) it serves." if not ms else
          f"Not milestones of this project: {', '.join(foreign)}." if foreign else
          f"Linked to {len(ms)} project milestone(s).")

    remaining = remaining_by_category(budget_lines)
    asked: dict[str, Decimal] = {}
    for ln in lines:
        amt = _d(ln.get("amount"))
        if ln.get("category") in CATEGORIES and not amt.is_nan() and amt > 0:
            asked[ln["category"]] = asked.get(ln["category"], Decimal("0")) + amt
    cat_rows, over = [], []
    for cat, amt in sorted(asked.items()):
        rem = remaining.get(cat, {}).get("remaining")
        ok = rem is not None and amt <= rem
        cat_rows.append({"category": cat, "requested": _money(amt),
                         "remaining_before": None if rem is None else _money(rem),
                         "remaining_after": None if rem is None else _money(rem - amt), "passed": ok})
        if rem is None:
            over.append(f"{cat} has no budget line in this project")
        elif not ok:
            over.append(f"{cat} asks {_money(amt)} but only {_money(rem)} remains")
    check("category_budget", asked and not over,
          "; ".join(over) if over else
          "Every category is within its remaining budget." if asked else "No valid lines to check.")

    total_asked = sum(asked.values(), Decimal("0"))
    total_rem = sum((c["remaining"] for c in remaining.values()), Decimal("0"))
    check("project_budget", total_asked > 0 and total_asked <= total_rem,
          f"Total {_money(total_asked)} vs project remaining {_money(total_rem)}.")

    skill_bad = []
    for i, ln in enumerate(lines, 1):
        if ln.get("category") == "PERSONNEL" and ln.get("skill"):
            if ln["skill"] not in taxonomy.TAXONOMY:
                skill_bad.append(f"line {i}: '{ln['skill']}' is not a taxonomy skill")
            if _d(ln.get("hours_per_week", 0)).is_nan() or _d(ln.get("hours_per_week", 0)) <= 0:
                skill_bad.append(f"line {i}: hours_per_week must be greater than 0")
    check("skills", not skill_bad, "; ".join(skill_bad) or "Personnel skills are valid.")

    valid = all(c["passed"] for c in checks)
    failed = [c["check"] for c in checks if not c["passed"]]
    return {"valid": valid, "status": "VALIDATED" if valid else "FAILED_VALIDATION",
            "checks": checks, "by_category": cat_rows,
            "total_requested": _money(total_asked), "project_remaining_before": _money(total_rem),
            "project_remaining_after": _money(total_rem - total_asked),
            "as_of": as_of.isoformat(),
            "explanation": explain("valid = every check passes (mandatory before approval)",
                                   {"checks": len(checks), "failed": failed,
                                    "total_requested": _money(total_asked),
                                    "project_remaining": _money(total_rem)},
                                   "Passed budget validation." if valid else
                                   f"Failed budget validation: {', '.join(failed)}.")}


def match(skill: str, min_level: int, hours_needed, hub_id: str, profiles: list[dict],
          limit: int = 5) -> dict:
    """profiles: [{"member_id", "name", "hub_id", "skills": {skill: level}, "available_hours"}]"""
    need = _d(hours_needed)
    ranked = []
    for p in profiles:
        level = p["skills"].get(skill, 0)
        avail = _d(p["available_hours"])
        if level < min_level or avail <= 0:
            continue
        cover = min(Decimal("1"), avail / need) if need > 0 else Decimal("1")
        local = p["hub_id"] == hub_id
        score = (Decimal(50) * level / 5 + (Decimal(20) if local else Decimal(0))
                 + Decimal(30) * cover).quantize(ONE_DP, ROUND_HALF_UP)
        ranked.append({"member_id": p["member_id"], "name": p["name"], "hub_id": p["hub_id"],
                       "level": level, "available_hours": str(avail), "same_hub": local,
                       "score": str(score),
                       "explanation": explain(
                           "score = 50 x level/5 + 20 if same hub + 30 x min(1, available/needed)",
                           {"level": level, "same_hub": local, "available_hours": str(avail),
                            "hours_needed": str(need)},
                           f"Level {level}, {'same hub' if local else 'another hub'}, "
                           f"{avail} h/week free of {need} needed -> {score}.")})
    ranked.sort(key=lambda r: (-Decimal(r["score"]), -Decimal(r["available_hours"]), r["member_id"]))
    return {"skill": skill, "min_level": min_level, "hours_needed": str(need),
            "requesting_hub": hub_id, "candidates": ranked[:limit],
            "eligible_count": len(ranked),
            "note": "Only ACTIVE members who consented to profiling are considered."}
