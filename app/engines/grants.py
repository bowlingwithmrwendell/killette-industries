"""Agent 2 engine: Grant-Writing.

Three jobs, all deterministic:

  1. Gather project details  -- assemble a hub project's facts into a proposal packet.
  2. Align with funders      -- eligibility (pass/fail per rule) and an alignment score.
  3. Manage milestone reports -- milestone status, KPI attainment, spend vs time,
                                 report deadlines and an overall compliance status.

Claude only drafts the narrative sections, and every draft goes to the human
review queue. The figures in a proposal (budget, eligibility, scores) never
come from the model.

Rules, exactly as implemented
-----------------------------
Eligibility (all REQUIRED checks must pass)
  entity_type      coalition entity type is in the funder's eligible list
  sam_registration if the funder requires SAM.gov registration, the coalition has it
  deadline         status date is on or before the deadline
  value_stream     the project's value stream is one the funder funds
  minimum_award    the computed request is at least the funder's minimum
  cost_share       matching funds >= cost share required
  Small-business rules, checked only when the funder sets them (SBIR-style programs):
  small_business   applicant employees <= the funder's maximum
  us_ownership     applicant US ownership % >= the funder's minimum
  pi_employment    the principal investigator is employed by the applicant
  subcontract_cap  contractual (outside firms, e.g. the molder) <= the funder's maximum % of the need
The APPLICANT is the coalition by default; a project can name its own applicant
(e.g. a member's company) with its own entity type, SAM status, size and ownership.
Warnings (do not block): deadline within 14 days, request capped at the maximum,
  project not fully funded by request + match, project longer than the performance period.

Budget
  direct           sum of the project's budget lines
  MTDC             direct minus EQUIPMENT (modified total direct costs)
  indirect rate    the coalition's rate, capped at the funder's cap when it has one
  indirect         MTDC x rate
  total need       direct + indirect
  request          min(total need, maximum award)
  cost share req.  request x cost_share_pct

Alignment score (0-100)
  focus 40     share of the funder's focus keywords that the project also lists
  team 25      share of the project's skill needs the coalition can staff (level >= minimum)
  budget 20    20 if the need is within the award range, 10 if above it (partial), 0 if below
  timeline 15  15 if >= 30 days to deadline, 8 if 14-29, 3 if 0-13, 0 if passed
  Recommendation: >= 75 PURSUE · 55-74 CONSIDER · < 55 PASS · any failed check NOT ELIGIBLE

Compliance (awarded grants)
  milestone    COMPLETE · OVERDUE (past due, not done) · AT_RISK (due within 14 days) · ON_TRACK
  KPI          attainment = actual / target x 100 (inverted when lower is better); met at >= 100
  burn         spend % - elapsed % ; > +15 OVERSPENDING, < -15 UNDERSPENDING
  reports      OVERDUE (past due, not filed) · DUE_SOON (within 14 days)
  status       RED: overdue report, overdue milestone or overspending
               AMBER: at-risk milestone, report due soon, underspending or a missed KPI
               GREEN: none of the above
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .explain import explain

CENT = Decimal("0.01")
ONE_DP = Decimal("0.1")
SOON_DAYS = 14
BURN_TOLERANCE = Decimal("15")

NARRATIVE_SECTIONS = ("Project Summary", "Statement of Need", "Project Narrative",
                      "Evaluation Plan", "Sustainability Plan")
GENERATED_SECTIONS = ("Budget Justification", "Team Qualifications", "Timeline & Milestones",
                      "Eligibility Statement")


def _d(v) -> Decimal:
    return Decimal(str(v))


def _money(v: Decimal) -> str:
    return str(v.quantize(CENT, ROUND_HALF_UP))


def _pct(n: Decimal, d: Decimal) -> Decimal:
    if d == 0:
        return Decimal("0")
    return (n / d * 100).quantize(ONE_DP, ROUND_HALF_UP)


# --- budget ----------------------------------------------------------------------------

def budget(opp: dict, project: dict, budget_lines: list[dict], coalition_rate_pct) -> dict:
    direct = sum((_d(b["budgeted"]) for b in budget_lines), Decimal("0"))
    equipment = sum((_d(b["budgeted"]) for b in budget_lines if b["category"] == "EQUIPMENT"),
                    Decimal("0"))
    mtdc = direct - equipment
    rate = _d(coalition_rate_pct)
    cap = opp.get("indirect_cap_pct")
    rate_used = min(rate, _d(cap)) if cap is not None else rate
    indirect = (mtdc * rate_used / 100).quantize(CENT, ROUND_HALF_UP)
    total = direct + indirect
    request = min(total, _d(opp["max_award"]))
    cost_share = (request * _d(opp["cost_share_pct"]) / 100).quantize(CENT, ROUND_HALF_UP)
    match = _d(project.get("matching_funds", 0))
    by_cat: dict[str, Decimal] = {}
    for b in budget_lines:
        by_cat[b["category"]] = by_cat.get(b["category"], Decimal("0")) + _d(b["budgeted"])
    contractual = by_cat.get("CONTRACTUAL", Decimal("0"))
    subcontract_pct = _pct(contractual, total)
    return {
        "direct_costs": _money(direct),
        "by_category": {k: _money(v) for k, v in sorted(by_cat.items())},
        "equipment_excluded_from_mtdc": _money(equipment),
        "mtdc": _money(mtdc),
        "indirect_rate_pct": str(rate_used),
        "indirect_costs": _money(indirect),
        "total_need": _money(total),
        "request": _money(request),
        "capped_at_max": total > _d(opp["max_award"]),
        "cost_share_pct": str(_d(opp["cost_share_pct"])),
        "cost_share_required": _money(cost_share),
        "matching_funds": _money(match),
        "funded_total": _money(request + match),
        "contractual": _money(contractual),
        "subcontract_pct": str(subcontract_pct),
        "explanation": explain(
            "request = min(direct + MTDC x rate, max_award); cost share = request x pct",
            {"direct": _money(direct), "mtdc": _money(mtdc), "coalition_rate_pct": str(rate),
             "funder_cap_pct": None if cap is None else str(_d(cap)), "rate_used_pct": str(rate_used),
             "max_award": _money(_d(opp["max_award"]))},
            f"Direct costs {_money(direct)} plus indirect {_money(indirect)} "
            f"({rate_used}% of MTDC {_money(mtdc)}) gives a need of {_money(total)}; "
            f"the request is {_money(request)}"
            + (" (capped at the maximum award)." if total > _d(opp["max_award"]) else ".")),
    }


# --- eligibility -------------------------------------------------------------------------

def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) + (1 if b.day > a.day else 0)


def eligibility(opp: dict, project: dict, coalition: dict, bud: dict, as_of: date) -> dict:
    checks = []

    def check(name, passed, detail):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    who = coalition.get("name") or "The applicant"
    et = coalition["entity_type"]
    check("entity_type", et in opp["eligible_entities"],
          f"{who} is {et}; funder accepts {', '.join(opp['eligible_entities'])}.")
    if opp.get("requires_sam"):
        check("sam_registration", coalition["sam_registered"],
              f"Funder requires active SAM.gov registration (UEI); {who} is "
              + ("registered." if coalition["sam_registered"] else "NOT registered."))
    if opp.get("max_employees") is not None:
        emp = coalition.get("employees")
        check("small_business", emp is not None and int(emp) <= int(opp["max_employees"]),
              f"Funder limits applicants to {opp['max_employees']} employees; {who} has "
              + (f"{emp}." if emp is not None else "not stated a headcount."))
    if opp.get("min_us_ownership_pct") is not None:
        own = coalition.get("us_owned_pct")
        check("us_ownership", own is not None and _d(own) >= _d(opp["min_us_ownership_pct"]),
              f"Funder requires at least {_d(opp['min_us_ownership_pct'])}% US ownership; {who} is "
              + (f"{_d(own)}% US-owned." if own is not None else "not stated."))
    if opp.get("requires_pi_employed"):
        pi = coalition.get("pi_employed")
        check("pi_employment", pi is True,
              f"Funder requires the principal investigator to be employed by {who}; "
              + ("confirmed." if pi is True else "not confirmed."))
    if opp.get("max_subcontract_pct") is not None:
        check("subcontract_cap", _d(bud["subcontract_pct"]) <= _d(opp["max_subcontract_pct"]),
              f"Outside firms (CONTRACTUAL {bud['contractual']}) are {bud['subcontract_pct']}% of the "
              f"need; the funder allows at most {_d(opp['max_subcontract_pct'])}%.")
    days = (opp["deadline"] - as_of).days
    check("deadline", days >= 0,
          f"Deadline {opp['deadline'].isoformat()} is {days} day(s) from the status date."
          if days >= 0 else f"Deadline {opp['deadline'].isoformat()} passed {-days} day(s) ago.")
    check("value_stream", project["value_stream"] in opp["value_streams"],
          f"Project is {project['value_stream']}; funder funds {', '.join(opp['value_streams'])}.")
    req = _d(bud["request"])
    check("minimum_award", req >= _d(opp["min_award"]),
          f"Request {bud['request']} vs minimum award {_money(_d(opp['min_award']))}.")
    check("cost_share", _d(bud["matching_funds"]) >= _d(bud["cost_share_required"]),
          f"Matching funds {bud['matching_funds']} vs cost share required "
          f"{bud['cost_share_required']} ({bud['cost_share_pct']}% of the request).")

    warnings = []
    if 0 <= days < SOON_DAYS:
        warnings.append(f"Deadline is within {SOON_DAYS} days ({days} day(s) left).")
    if bud["capped_at_max"]:
        warnings.append(f"Need {bud['total_need']} exceeds the maximum award; the request is capped "
                        f"at {bud['request']} and the balance must come from other sources.")
    if _d(bud["funded_total"]) < _d(bud["total_need"]):
        warnings.append(f"Request plus matching funds ({bud['funded_total']}) does not cover the "
                        f"total need ({bud['total_need']}).")
    months = _months_between(max(as_of, project["start_date"]), project["end_date"])
    if months > opp["performance_months"]:
        warnings.append(f"Project runs {months} months; the funder's period of performance is "
                        f"{opp['performance_months']} months.")
    eligible = all(c["passed"] for c in checks)
    failed = [c["check"] for c in checks if not c["passed"]]
    return {"eligible": eligible, "checks": checks, "warnings": warnings,
            "explanation": explain("eligible = every required check passes",
                                   {"checks": len(checks), "failed": failed},
                                   "All required checks pass." if eligible
                                   else f"Not eligible: {', '.join(failed)}.")}


# --- alignment -----------------------------------------------------------------------------

def alignment(opp: dict, project: dict, bud: dict, elig: dict, staffable: list[bool],
              as_of: date) -> dict:
    fk = {k.lower() for k in opp["focus_keywords"]}
    pk = {k.lower() for k in project["keywords"]}
    overlap = sorted(fk & pk)
    focus = (Decimal(40) * len(overlap) / len(fk)).quantize(ONE_DP, ROUND_HALF_UP) if fk else Decimal("0")
    team = ((Decimal(25) * sum(staffable) / len(staffable)).quantize(ONE_DP, ROUND_HALF_UP)
            if staffable else Decimal("0"))
    need = _d(bud["total_need"])
    if need < _d(opp["min_award"]):
        bfit = Decimal("0")
    elif need > _d(opp["max_award"]):
        bfit = Decimal("10")
    else:
        bfit = Decimal("20")
    days = (opp["deadline"] - as_of).days
    tl = Decimal("15") if days >= 30 else Decimal("8") if days >= 14 else Decimal("3") if days >= 0 else Decimal("0")
    score = focus + team + bfit + tl
    if not elig["eligible"]:
        rec = "NOT ELIGIBLE"
    elif score >= 75:
        rec = "PURSUE"
    elif score >= 55:
        rec = "CONSIDER"
    else:
        rec = "PASS"
    return {
        "score": str(score), "recommendation": rec,
        "components": {"focus": str(focus), "team": str(team), "budget_fit": str(bfit),
                       "timeline": str(tl)},
        "matched_keywords": overlap,
        "explanation": explain(
            "score = focus(40) + team(25) + budget fit(20) + timeline(15)",
            {"focus_keywords_matched": f"{len(overlap)}/{len(fk)}",
             "skill_needs_staffable": f"{sum(staffable)}/{len(staffable)}",
             "need": bud["total_need"], "award_range": [_money(_d(opp['min_award'])), _money(_d(opp['max_award']))],
             "days_to_deadline": days},
            f"Focus {focus} + team {team} + budget fit {bfit} + timeline {tl} = {score} -> {rec}."),
    }


def assess(opp: dict, project: dict, budget_lines: list[dict], coalition: dict,
           staffable: list[bool], as_of: date) -> dict:
    bud = budget(opp, project, budget_lines, coalition["indirect_rate_pct"])
    elig = eligibility(opp, project, coalition, bud, as_of)
    align = alignment(opp, project, bud, elig, staffable, as_of)
    return {"opportunity_id": opp["id"], "funder": opp["funder"], "program": opp["program"],
            "funder_type": opp["funder_type"], "deadline": opp["deadline"].isoformat(),
            "project_id": project["id"], "eligibility": elig, "budget": bud, "alignment": align}


# --- proposal packet ------------------------------------------------------------------------

def section_state(section: str, human_sections: dict, review_status: str | None) -> str:
    if section in GENERATED_SECTIONS:
        return "GENERATED"
    if section in human_sections:
        return "HUMAN_AUTHORED"
    if review_status in ("APPROVED", "APPROVED_WITH_EDITS"):
        return "AI_DRAFT_APPROVED"
    if review_status == "PENDING_REVIEW":
        return "AI_DRAFT_PENDING_REVIEW"
    if review_status == "REJECTED":
        return "AI_DRAFT_REJECTED"
    return "MISSING"


READY_STATES = {"GENERATED", "HUMAN_AUTHORED", "AI_DRAFT_APPROVED"}


def readiness(required: list[str], states: dict[str, str], eligible: bool) -> dict:
    blocking = [s for s in required if states.get(s) not in READY_STATES]
    ready = eligible and not blocking
    reasons = []
    if not eligible:
        reasons.append("The project fails at least one eligibility check.")
    for s in blocking:
        reasons.append(f"Section '{s}' is {states.get(s, 'MISSING')}.")
    return {"ready": ready, "blocking": reasons,
            "explanation": explain("ready = eligible AND every required section is generated, "
                                   "human-authored or a human-approved AI draft",
                                   {"required": len(required), "blocking": len(blocking),
                                    "eligible": eligible},
                                   "Ready for submission." if ready else
                                   f"{len(reasons)} item(s) block submission.")}


# --- compliance and milestone reporting --------------------------------------------------------

def milestone_status(m: dict, as_of: date) -> dict:
    if m.get("completed_date"):
        st = "COMPLETE"
    elif m["due_date"] < as_of:
        st = "OVERDUE"
    elif (m["due_date"] - as_of).days <= SOON_DAYS:
        st = "AT_RISK"
    else:
        st = "ON_TRACK"
    kpi = None
    if m.get("kpi_target") is not None and m.get("kpi_actual") is not None:
        t, a = _d(m["kpi_target"]), _d(m["kpi_actual"])
        att = _pct(a, t) if m.get("higher_is_better", True) else _pct(t, a)
        kpi = {"name": m.get("kpi_name", ""), "target": str(t), "actual": str(a),
               "attainment_pct": str(att), "met": att >= 100,
               "explanation": explain("attainment = actual / target x 100" if m.get("higher_is_better", True)
                                      else "attainment = target / actual x 100 (lower is better)",
                                      {"target": str(t), "actual": str(a)},
                                      f"{m.get('kpi_name', 'KPI')}: {att}% of target"
                                      + (" (met)." if att >= 100 else " (not met)."))}
    days = (m["due_date"] - as_of).days
    return {"id": m["id"], "name": m["name"], "due_date": m["due_date"].isoformat(),
            "completed_date": m["completed_date"].isoformat() if m.get("completed_date") else None,
            "status": st, "days_to_due": days, "kpi": kpi}


def compliance(app: dict, milestones: list[dict], reports: list[dict], as_of: date) -> dict:
    ms = [milestone_status(m, as_of) for m in milestones]
    awarded = _d(app["awarded_amount"])
    spent = _d(app["grant_spent"])
    start, end = app["award_start"], app["award_end"]
    total_days = max((end - start).days, 1)
    elapsed_days = min(max((as_of - start).days, 0), total_days)
    elapsed_pct = _pct(Decimal(elapsed_days), Decimal(total_days))
    spend_pct = _pct(spent, awarded)
    variance = spend_pct - elapsed_pct
    burn = ("OVERSPENDING" if variance > BURN_TOLERANCE else
            "UNDERSPENDING" if variance < -BURN_TOLERANCE else "ON_PACE")

    rep = []
    for r in reports:
        if r.get("filed_date"):
            st = "FILED"
        elif r["due_date"] < as_of:
            st = "OVERDUE"
        elif (r["due_date"] - as_of).days <= SOON_DAYS:
            st = "DUE_SOON"
        else:
            st = "UPCOMING"
        rep.append({"period_end": r["period_end"].isoformat(), "due_date": r["due_date"].isoformat(),
                    "filed_date": r["filed_date"].isoformat() if r.get("filed_date") else None,
                    "status": st})

    red, amber = [], []
    for r in rep:
        if r["status"] == "OVERDUE":
            red.append(f"Progress report for the period ending {r['period_end']} was due {r['due_date']} "
                       "and has not been filed.")
        elif r["status"] == "DUE_SOON":
            amber.append(f"Progress report due {r['due_date']}.")
    for m in ms:
        if m["status"] == "OVERDUE":
            red.append(f"Milestone '{m['name']}' is overdue ({-m['days_to_due']} day(s)).")
        elif m["status"] == "AT_RISK":
            amber.append(f"Milestone '{m['name']}' is due in {m['days_to_due']} day(s) and not complete.")
        if m["kpi"] and m["status"] == "COMPLETE" and not m["kpi"]["met"]:
            amber.append(f"KPI '{m['kpi']['name']}' finished at {m['kpi']['attainment_pct']}% of target.")
    if burn == "OVERSPENDING":
        red.append(f"Spend is {spend_pct}% of the award with {elapsed_pct}% of the period elapsed.")
    elif burn == "UNDERSPENDING":
        amber.append(f"Spend is {spend_pct}% of the award with {elapsed_pct}% of the period elapsed.")
    status = "RED" if red else "AMBER" if amber else "GREEN"
    nxt = next((r for r in sorted(rep, key=lambda r: r["due_date"]) if r["status"] != "FILED"), None)
    return {
        "application_id": app["id"], "status": status, "reasons": red + amber,
        "milestones": ms,
        "milestone_summary": {k: sum(1 for m in ms if m["status"] == k)
                              for k in ("COMPLETE", "ON_TRACK", "AT_RISK", "OVERDUE")},
        "spend": {"awarded": _money(awarded), "spent": _money(spent),
                  "remaining": _money(awarded - spent), "spend_pct": str(spend_pct),
                  "elapsed_pct": str(elapsed_pct), "variance_pts": str(variance), "burn": burn,
                  "explanation": explain("variance = spend % - elapsed %; outside +/-15 points is flagged",
                                         {"spent": _money(spent), "awarded": _money(awarded),
                                          "elapsed_days": elapsed_days, "period_days": total_days},
                                         f"Spend {spend_pct}% vs time {elapsed_pct}% -> {burn}.")},
        "reports": rep,
        "next_report_due": nxt["due_date"] if nxt else None,
        "explanation": explain("RED if any red trigger, else AMBER if any amber trigger, else GREEN",
                               {"red_triggers": len(red), "amber_triggers": len(amber)},
                               f"{status}: " + ("; ".join((red + amber)[:3]) if red or amber
                                                else "no compliance issues.")),
    }


def compliance_markdown(c: dict, title: str) -> str:
    icon = {"GREEN": "🟢", "AMBER": "🟠", "RED": "🔴"}[c["status"]]
    L = [f"# Grant compliance report — {title}", "",
         f"**Overall status:** {icon} **{c['status']}**  ",
         f"**Next report due:** {c['next_report_due'] or 'none scheduled'}", ""]
    if c["reasons"]:
        L += ["## Items needing attention", ""] + [f"- {r}" for r in c["reasons"]] + [""]
    sp = c["spend"]
    L += ["## Spend vs time", "", "| Awarded | Spent | Remaining | Spend % | Time elapsed % | Pace |",
          "|---|---|---|---|---|---|",
          f"| ${_d(sp['awarded']):,.2f} | ${_d(sp['spent']):,.2f} | ${_d(sp['remaining']):,.2f} | "
          f"{sp['spend_pct']}% | {sp['elapsed_pct']}% | {sp['burn']} |", ""]
    L += ["## Milestones and KPIs", "", "| Milestone | Due | Status | KPI | Attainment |",
          "|---|---|---|---|---|"]
    for m in c["milestones"]:
        k = m["kpi"]
        L.append(f"| {m['name']} | {m['due_date']} | {m['status']} | "
                 f"{k['name'] + ': ' + k['actual'] + ' / ' + k['target'] if k else '—'} | "
                 f"{k['attainment_pct'] + '%' if k else '—'} |")
    L += ["", "## Funder reports", "", "| Period ending | Due | Filed | Status |", "|---|---|---|---|"]
    for r in c["reports"]:
        L.append(f"| {r['period_end']} | {r['due_date']} | {r['filed_date'] or '—'} | {r['status']} |")
    L += ["", "_Generated by deterministic engines. Fictional demonstration data._"]
    return "\n".join(L)
