"""Reads rows, calls the deterministic engines, writes results.

This is the only layer that touches both the database and the engines. The
engines never see SQLAlchemy objects; they get plain dicts and return plain
dicts, which keeps them easy to test and impossible for the model to bypass.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import config
from .actions import audit
from .engines import allocation, expertise, grants, taxonomy
from .models import (BudgetLineRow, FundingOpportunityRow, GrantApplicationRow, GrantMilestoneRow,
                     GrantReportRow, HubProjectRow, HubRow, MemberAssignmentRow, MemberRow,
                     MemberSkillRow, ProjectMilestoneRow, ResourceRequestRow, SkillNeedRow)


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get(s: Session, cls, key, label):
    row = s.get(cls, key)
    if row is None:
        raise NotFound(f"No {label} {key}")
    return row


def _money(v) -> str:
    return str(Decimal(str(v)).quantize(Decimal("0.01")))


# ======================================================================================
# Hubs and projects (shared)
# ======================================================================================

def hub_dict(h: HubRow) -> dict:
    return {"id": h.id, "name": h.name, "region": h.region, "facilitator": h.facilitator,
            "working_agreement": h.working_agreement}


def project_facts(p: HubProjectRow) -> dict:
    return {"id": p.id, "hub_id": p.hub_id, "name": p.name, "value_stream": p.value_stream,
            "capability": p.capability, "summary": p.summary, "objectives": p.objectives,
            "beneficiaries": p.beneficiaries, "keywords": p.keywords, "start_date": p.start_date,
            "end_date": p.end_date, "lead_member_id": p.lead_member_id,
            "matching_funds": _money(p.matching_funds),
            "applicant": applicant_profile(p), "ip_status": p.ip_status or ""}


def _jsonable(d: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()}


def _budget_lines(s: Session, project_id: str) -> list[dict]:
    return [{"category": b.category, "budgeted": b.budgeted, "committed": b.committed,
             "spent": b.spent}
            for b in s.query(BudgetLineRow).filter_by(project_id=project_id).order_by(BudgetLineRow.id)]


def overview(s: Session) -> dict:
    hubs = []
    for h in s.query(HubRow).order_by(HubRow.id):
        projects = s.query(HubProjectRow).filter_by(hub_id=h.id).order_by(HubProjectRow.id).all()
        members = s.query(MemberRow).filter_by(hub_id=h.id, status="ACTIVE").count()
        plist = []
        for p in projects:
            pos = allocation.budget_position(_budget_lines(s, p.id))
            plist.append({"id": p.id, "name": p.name, "value_stream": p.value_stream,
                          "capability": p.capability, "budget": pos["totals"],
                          "used_pct": pos["used_pct"]})
        hubs.append({**hub_dict(h), "active_members": members, "projects": plist})
    pending = s.query(ResourceRequestRow).filter(
        ResourceRequestRow.status.in_(("SUBMITTED", "VALIDATED"))).count()
    awarded = s.query(GrantApplicationRow).filter_by(status="AWARDED").count()
    return {"coalition": config.COALITION_NAME, "status_date": config.STATUS_DATE.isoformat(),
            "hubs": hubs, "members_active": s.query(MemberRow).filter_by(status="ACTIVE").count(),
            "resource_requests_open": pending, "grants_awarded": awarded,
            "funding_opportunities": s.query(FundingOpportunityRow).count()}


def project_detail(s: Session, project_id: str) -> dict:
    p = _get(s, HubProjectRow, project_id, "project")
    ms = s.query(ProjectMilestoneRow).filter_by(project_id=p.id).order_by(ProjectMilestoneRow.due_date)
    needs = s.query(SkillNeedRow).filter_by(project_id=p.id).order_by(SkillNeedRow.id)
    return {**_jsonable(project_facts(p)), "applicant": _plain(applicant_profile(p)),
            "budget": allocation.budget_position(_budget_lines(s, p.id)),
            "milestones": [{"id": m.id, "name": m.name, "due_date": m.due_date.isoformat(),
                            "completed_date": m.completed_date.isoformat() if m.completed_date else None}
                           for m in ms],
            "skill_needs": [{"skill": n.skill, "min_level": n.min_level,
                             "hours_per_week": str(n.hours_per_week)} for n in needs]}


# ======================================================================================
# Creating projects and funders (so members can bring their own work in)
# ======================================================================================

from .engines.allocation import CATEGORIES as _CATEGORIES  # noqa: E402
from .engines.taxonomy import VALUE_STREAMS as _VALUE_STREAMS  # noqa: E402

ENTITY_TYPES = ("NONPROFIT_501C3", "COOPERATIVE", "LLC", "CORPORATION", "FISCALLY_SPONSORED")


def _next_id(s: Session, cls, prefix: str) -> str:
    n = 1
    while s.get(cls, f"{prefix}{n:02d}"):
        n += 1
    return f"{prefix}{n:02d}"


def create_project(s: Session, body: dict, actor: str) -> dict:
    """Create a hub project with its budget, milestones and skill needs in one step.

    The same shape is produced by export_project, so a saved project can be re-imported
    after the sandbox resets.
    """
    _get(s, HubRow, body["hub_id"], "hub")
    if body["value_stream"] not in _VALUE_STREAMS:
        raise Conflict(f"value_stream must be one of {', '.join(_VALUE_STREAMS)}.")
    if body["end_date"] <= body["start_date"]:
        raise Conflict("end_date must be after start_date.")
    a = body.get("applicant") or {}
    if a.get("entity_type") and a["entity_type"] not in ENTITY_TYPES:
        raise Conflict(f"applicant.entity_type must be one of {', '.join(ENTITY_TYPES)}.")
    lines = body.get("budget_lines") or []
    if not lines:
        raise Conflict("At least one budget line is required.")
    for b in lines:
        if b["category"] not in _CATEGORIES:
            raise Conflict(f"Budget category must be one of {', '.join(_CATEGORIES)}.")
        if Decimal(str(b["budgeted"])) <= 0:
            raise Conflict("Every budget line needs an amount greater than 0.")
    for n in body.get("skill_needs") or []:
        if n["skill"] not in taxonomy.TAXONOMY:
            raise Conflict(f"'{n['skill']}' is not a taxonomy skill.")
    if body.get("lead_member_id"):
        _get(s, MemberRow, body["lead_member_id"], "member")

    prefix = {"INSURANCE": "P-INS-", "AUTOMOTIVE": "P-AUT-", "AGRICULTURE": "P-AGR-",
              "MANUFACTURING": "P-MFG-"}[body["value_stream"]]
    pid = _next_id(s, HubProjectRow, prefix)
    p = HubProjectRow(
        id=pid, hub_id=body["hub_id"], name=body["name"].strip(), value_stream=body["value_stream"],
        capability=body.get("capability", "").strip(), summary=body["summary"].strip(),
        objectives=[o.strip() for o in body.get("objectives") or [] if o.strip()],
        beneficiaries=body.get("beneficiaries", "").strip(),
        keywords=[k.strip().lower() for k in body.get("keywords") or [] if k.strip()],
        start_date=body["start_date"], end_date=body["end_date"],
        lead_member_id=body.get("lead_member_id") or None,
        matching_funds=Decimal(str(body.get("matching_funds") or 0)),
        applicant_name=(a.get("name") or "").strip(), applicant_entity_type=a.get("entity_type") or "",
        applicant_sam_registered=a.get("sam_registered"), applicant_employees=a.get("employees"),
        applicant_us_owned_pct=(Decimal(str(a["us_owned_pct"])) if a.get("us_owned_pct") is not None else None),
        pi_employed_by_applicant=a.get("pi_employed"), ip_status=(body.get("ip_status") or "").strip())
    s.add(p)
    s.flush()
    for b in lines:
        s.add(BudgetLineRow(project_id=pid, category=b["category"], budgeted=Decimal(str(b["budgeted"])),
                            committed=Decimal(str(b.get("committed") or 0)),
                            spent=Decimal(str(b.get("spent") or 0))))
    for i, m in enumerate(body.get("milestones") or [], 1):
        s.add(ProjectMilestoneRow(id=f"MS-{pid[2:]}-{i}", project_id=pid, name=m["name"].strip(),
                                  due_date=m["due_date"], completed_date=m.get("completed_date")))
    for n in body.get("skill_needs") or []:
        s.add(SkillNeedRow(project_id=pid, skill=n["skill"], min_level=int(n.get("min_level", 3)),
                           hours_per_week=Decimal(str(n["hours_per_week"]))))
    audit(s, actor, "PROJECT_CREATED", {"project_id": pid, "hub_id": p.hub_id,
                                         "value_stream": p.value_stream, "applicant": p.applicant_name or "coalition"})
    s.commit()
    return project_detail(s, pid)


def export_project(s: Session, project_id: str) -> dict:
    """The project in the exact shape create_project accepts (save it; import it later)."""
    p = _get(s, HubProjectRow, project_id, "project")
    return {
        "hub_id": p.hub_id, "name": p.name, "value_stream": p.value_stream, "capability": p.capability,
        "summary": p.summary, "objectives": p.objectives, "beneficiaries": p.beneficiaries,
        "keywords": p.keywords, "start_date": p.start_date.isoformat(), "end_date": p.end_date.isoformat(),
        "lead_member_id": p.lead_member_id, "matching_funds": _money(p.matching_funds),
        "ip_status": p.ip_status or "",
        "applicant": None if not (p.applicant_name or p.applicant_entity_type) else {
            "name": p.applicant_name, "entity_type": p.applicant_entity_type,
            "sam_registered": p.applicant_sam_registered, "employees": p.applicant_employees,
            "us_owned_pct": None if p.applicant_us_owned_pct is None else str(p.applicant_us_owned_pct),
            "pi_employed": p.pi_employed_by_applicant},
        "budget_lines": [{"category": b.category, "budgeted": _money(b.budgeted),
                          "committed": _money(b.committed), "spent": _money(b.spent)}
                         for b in s.query(BudgetLineRow).filter_by(project_id=p.id).order_by(BudgetLineRow.id)],
        "milestones": [{"name": m.name, "due_date": m.due_date.isoformat(),
                        "completed_date": m.completed_date.isoformat() if m.completed_date else None}
                       for m in s.query(ProjectMilestoneRow).filter_by(project_id=p.id)
                       .order_by(ProjectMilestoneRow.due_date)],
        "skill_needs": [{"skill": n.skill, "min_level": n.min_level, "hours_per_week": str(n.hours_per_week)}
                        for n in s.query(SkillNeedRow).filter_by(project_id=p.id).order_by(SkillNeedRow.id)],
    }


def create_opportunity(s: Session, body: dict, actor: str) -> dict:
    if not set(body["value_streams"]) <= set(_VALUE_STREAMS) or not body["value_streams"]:
        raise Conflict(f"value_streams must be from {', '.join(_VALUE_STREAMS)}.")
    if not set(body["eligible_entities"]) <= set(ENTITY_TYPES) or not body["eligible_entities"]:
        raise Conflict(f"eligible_entities must be from {', '.join(ENTITY_TYPES)}.")
    if Decimal(str(body["max_award"])) < Decimal(str(body["min_award"])):
        raise Conflict("max_award must be at least min_award.")
    oid = _next_id(s, FundingOpportunityRow, "FO-")
    sections = body.get("required_sections") or [
        "Project Summary", "Statement of Need", "Project Narrative", "Budget Justification",
        "Team Qualifications", "Evaluation Plan"]
    o = FundingOpportunityRow(
        id=oid, funder=body["funder"].strip(), program=body["program"].strip(), funder_type=body["funder_type"],
        min_award=Decimal(str(body["min_award"])), max_award=Decimal(str(body["max_award"])),
        deadline=body["deadline"], performance_months=int(body["performance_months"]),
        eligible_entities=list(body["eligible_entities"]), value_streams=list(body["value_streams"]),
        focus_keywords=[k.strip().lower() for k in body.get("focus_keywords") or [] if k.strip()],
        cost_share_pct=Decimal(str(body.get("cost_share_pct") or 0)),
        indirect_cap_pct=(Decimal(str(body["indirect_cap_pct"])) if body.get("indirect_cap_pct") not in (None, "") else None),
        requires_sam=bool(body.get("requires_sam")), required_sections=sections,
        reporting=body.get("reporting") or "QUARTERLY",
        max_employees=body.get("max_employees"),
        min_us_ownership_pct=(Decimal(str(body["min_us_ownership_pct"]))
                              if body.get("min_us_ownership_pct") not in (None, "") else None),
        requires_pi_employed=bool(body.get("requires_pi_employed")),
        max_subcontract_pct=(Decimal(str(body["max_subcontract_pct"]))
                             if body.get("max_subcontract_pct") not in (None, "") else None))
    s.add(o)
    audit(s, actor, "FUNDING_OPPORTUNITY_CREATED", {"opportunity_id": oid, "program": o.program})
    s.commit()
    return next(x for x in list_opportunities(s) if x["id"] == oid)


# ======================================================================================
# Agent 1 -- Expertise Ingestion
# ======================================================================================

def _assigned_hours(s: Session, member_id: str) -> Decimal:
    return Decimal(str(s.query(func.coalesce(func.sum(MemberAssignmentRow.hours_per_week), 0))
                       .filter(MemberAssignmentRow.member_id == member_id).scalar() or 0))


def member_summary(s: Session, m: MemberRow) -> dict:
    top = (s.query(MemberSkillRow).filter_by(member_id=m.id)
           .order_by(MemberSkillRow.level.desc(), MemberSkillRow.skill).limit(4).all())
    assigned = _assigned_hours(s, m.id)
    return {"id": m.id, "name": m.name, "hub_id": m.hub_id, "headline": m.headline,
            "status": m.status, "consent_profiling": m.consent_profiling,
            "total_years": str(m.total_years), "seniority": m.seniority,
            "completeness_pct": m.completeness_pct,
            "top_skills": [{"skill": k.skill, "level": k.level} for k in top],
            "capacity_hours": str(m.capacity_hours), "assigned_hours": str(assigned),
            "available_hours": str(max(Decimal(str(m.capacity_hours)) - assigned, Decimal("0")))}


def list_members(s: Session, hub_id: str | None = None, skill: str | None = None,
                 min_level: int = 1) -> list[dict]:
    q = s.query(MemberRow)
    if hub_id:
        q = q.filter(MemberRow.hub_id == hub_id)
    if skill:
        ids = [r.member_id for r in s.query(MemberSkillRow)
               .filter(MemberSkillRow.skill == skill, MemberSkillRow.level >= min_level)]
        q = q.filter(MemberRow.id.in_(ids))
    return [member_summary(s, m) for m in q.order_by(MemberRow.id)]


def member_profile(s: Session, member_id: str) -> dict:
    m = _get(s, MemberRow, member_id, "member")
    skills = (s.query(MemberSkillRow).filter_by(member_id=m.id)
              .order_by(MemberSkillRow.level.desc(), MemberSkillRow.skill).all())
    assigns = s.query(MemberAssignmentRow).filter_by(member_id=m.id).all()
    return {**member_summary(s, m), "industries": m.industries, "certifications": m.certifications,
            "roles": m.roles, "unmapped_skills": m.unmapped_skills,
            "consent_at": m.consent_at.isoformat() if m.consent_at else None,
            "ingested_at": m.ingested_at.isoformat() if m.ingested_at else None,
            "skills": [{"skill": k.skill, "domain": k.domain, "raw_label": k.raw_label,
                        "years": str(k.years), "self_rating": k.self_rating, "certified": k.certified,
                        "level": k.level, "explanation": k.explanation} for k in skills],
            "assignments": [{"project_id": a.project_id, "skill": a.skill,
                             "hours_per_week": str(a.hours_per_week)} for a in assigns],
            "data_policy": "Profiles hold no contact details, no demographic data and no "
                           "compensation. Withdrawing consent removes every skill record."}


def _next_member_id(s: Session) -> str:
    n = s.query(MemberRow).count() + 1
    while s.get(MemberRow, f"M-{n:03d}"):
        n += 1
    return f"M-{n:03d}"


def ingest_member(s: Session, submission: dict, actor: str, member_id: str | None = None,
                  commit: bool = True) -> dict:
    """Run the ingestion engine and store the structured profile (new or re-ingested)."""
    if submission.get("hub_id"):
        _get(s, HubRow, submission["hub_id"], "hub")
    else:
        raise expertise.IngestionError("hub_id is required.")
    prof = expertise.ingest(submission, config.STATUS_DATE)
    if member_id:
        m = _get(s, MemberRow, member_id, "member")
        s.query(MemberSkillRow).filter_by(member_id=m.id).delete()
    else:
        m = MemberRow(id=_next_member_id(s))
        s.add(m)
    m.name, m.hub_id, m.headline = prof["name"], prof["hub_id"], prof["headline"]
    m.industries, m.certifications, m.roles = prof["industries"], prof["certifications"], prof["roles"]
    m.capacity_hours = Decimal(prof["capacity_hours"])
    m.consent_profiling, m.consent_at, m.status = True, _now(), "ACTIVE"
    m.total_years, m.seniority = Decimal(prof["total_years"]), prof["seniority"]
    m.completeness_pct, m.unmapped_skills, m.ingested_at = (prof["completeness_pct"],
                                                            prof["unmapped_skills"], _now())
    s.flush()
    for k in prof["skills"]:
        s.add(MemberSkillRow(member_id=m.id, skill=k["skill"], domain=k["domain"],
                             raw_label=k["raw_label"], years=Decimal(k["years"]),
                             self_rating=k["self_rating"], certified=k["certified"], level=k["level"],
                             explanation=k["explanation"]))
    audit(s, actor, "MEMBER_INGESTED", {"member_id": m.id, "skills": len(prof["skills"]),
                                         "unmapped": prof["unmapped_skills"],
                                         "completeness_pct": prof["completeness_pct"]})
    if commit:
        s.commit()
    return {"member_id": m.id, **prof}


def withdraw_consent(s: Session, member_id: str, actor: str) -> dict:
    m = _get(s, MemberRow, member_id, "member")
    removed = s.query(MemberSkillRow).filter_by(member_id=m.id).delete()
    m.consent_profiling, m.status = False, "WITHDRAWN"
    m.unmapped_skills, m.completeness_pct = [], 0
    audit(s, actor, "MEMBER_CONSENT_WITHDRAWN", {"member_id": m.id, "skills_removed": removed})
    s.commit()
    return {"member_id": m.id, "status": m.status, "skills_removed": removed,
            "note": "Skill records deleted. The member is excluded from matching and AI use. "
                    "Existing project assignments are kept until a hub facilitator reassigns them."}


def profiles_for_matching(s: Session) -> list[dict]:
    out = []
    for m in s.query(MemberRow).filter_by(status="ACTIVE", consent_profiling=True).order_by(MemberRow.id):
        skills = {k.skill: k.level for k in s.query(MemberSkillRow).filter_by(member_id=m.id)}
        avail = max(Decimal(str(m.capacity_hours)) - _assigned_hours(s, m.id), Decimal("0"))
        by_skill: dict[str, Decimal] = {}
        for a in s.query(MemberAssignmentRow).filter_by(member_id=m.id):
            by_skill[a.skill] = by_skill.get(a.skill, Decimal("0")) + Decimal(str(a.hours_per_week))
        out.append({"member_id": m.id, "name": m.name, "hub_id": m.hub_id, "skills": skills,
                    "available_hours": str(avail),
                    "assigned_by_skill": {k: str(v) for k, v in by_skill.items()}})
    return out


def coverage(s: Session, value_stream: str | None = None) -> dict:
    q = s.query(SkillNeedRow, HubProjectRow).join(HubProjectRow, SkillNeedRow.project_id == HubProjectRow.id)
    if value_stream:
        q = q.filter(HubProjectRow.value_stream == value_stream)
    needs = [{"project_id": n.project_id, "skill": n.skill, "min_level": n.min_level,
              "hours_per_week": n.hours_per_week} for n, _ in q]
    result = expertise.coverage(needs, profiles_for_matching(s))
    return {"value_stream": value_stream or "ALL", **result}


def skill_inventory(s: Session) -> list[dict]:
    rows = (s.query(MemberSkillRow.domain, MemberSkillRow.skill, func.count(MemberSkillRow.id),
                    func.max(MemberSkillRow.level))
            .join(MemberRow, MemberRow.id == MemberSkillRow.member_id)
            .filter(MemberRow.status == "ACTIVE")
            .group_by(MemberSkillRow.domain, MemberSkillRow.skill)
            .order_by(MemberSkillRow.domain, MemberSkillRow.skill))
    return [{"domain": d, "skill": k, "members": n, "highest_level": mx} for d, k, n, mx in rows]


# ======================================================================================
# Agent 2 -- Grant-Writing
# ======================================================================================

def coalition_profile() -> dict:
    return {"name": config.COALITION_NAME, "entity_type": config.COALITION_ENTITY_TYPE,
            "sam_registered": config.COALITION_SAM_REGISTERED,
            "indirect_rate_pct": config.COALITION_INDIRECT_RATE_PCT}


def applicant_profile(p: HubProjectRow) -> dict:
    """Who applies for this project's grants: its own named applicant, or the coalition."""
    base = coalition_profile()
    if not (p.applicant_name or p.applicant_entity_type):
        return {**base, "source": "coalition", "employees": None, "us_owned_pct": None,
                "pi_employed": None}
    return {"name": p.applicant_name or base["name"],
            "entity_type": p.applicant_entity_type or base["entity_type"],
            "sam_registered": bool(p.applicant_sam_registered),
            "indirect_rate_pct": base["indirect_rate_pct"],
            "employees": p.applicant_employees,
            "us_owned_pct": p.applicant_us_owned_pct,
            "pi_employed": p.pi_employed_by_applicant,
            "source": "project"}


def _plain(d: dict) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v.isoformat() if hasattr(v, "isoformat") else v)
            for k, v in d.items()}


def opp_dict(o: FundingOpportunityRow) -> dict:
    return {"id": o.id, "funder": o.funder, "program": o.program, "funder_type": o.funder_type,
            "min_award": o.min_award, "max_award": o.max_award, "deadline": o.deadline,
            "performance_months": o.performance_months, "eligible_entities": o.eligible_entities,
            "value_streams": o.value_streams, "focus_keywords": o.focus_keywords,
            "cost_share_pct": o.cost_share_pct, "indirect_cap_pct": o.indirect_cap_pct,
            "requires_sam": o.requires_sam, "required_sections": o.required_sections,
            "reporting": o.reporting, "max_employees": o.max_employees,
            "min_us_ownership_pct": o.min_us_ownership_pct,
            "requires_pi_employed": bool(o.requires_pi_employed),
            "max_subcontract_pct": o.max_subcontract_pct}


def list_opportunities(s: Session) -> list[dict]:
    out = []
    for o in s.query(FundingOpportunityRow).order_by(FundingOpportunityRow.deadline):
        d = opp_dict(o)
        out.append({**{k: (str(v) if isinstance(v, Decimal) else v) for k, v in _jsonable(d).items()},
                    "days_to_deadline": (o.deadline - config.STATUS_DATE).days})
    return out


def _staffable(s: Session, project_id: str) -> list[bool]:
    profiles = profiles_for_matching(s)
    needs = s.query(SkillNeedRow).filter_by(project_id=project_id).all()
    return [any(p["skills"].get(n.skill, 0) >= n.min_level for p in profiles) for n in needs]


def assess(s: Session, project_id: str, opportunity_id: str) -> dict:
    p = _get(s, HubProjectRow, project_id, "project")
    o = _get(s, FundingOpportunityRow, opportunity_id, "funding opportunity")
    return grants.assess(opp_dict(o), project_facts(p), _budget_lines(s, p.id), applicant_profile(p),
                         _staffable(s, p.id), config.STATUS_DATE)


def match_opportunities(s: Session, project_id: str) -> dict:
    proj = _get(s, HubProjectRow, project_id, "project")
    ranked = [assess(s, project_id, o.id) for o in s.query(FundingOpportunityRow)]
    order = {"PURSUE": 0, "CONSIDER": 1, "PASS": 2, "NOT ELIGIBLE": 3}
    ranked.sort(key=lambda a: (order[a["alignment"]["recommendation"]],
                               -Decimal(a["alignment"]["score"]), a["opportunity_id"]))
    return {"project_id": project_id, "coalition": _jsonable({k: str(v) if isinstance(v, Decimal) else v
                                                              for k, v in coalition_profile().items()}),
            "applicant": _plain(applicant_profile(proj)), "opportunities": ranked}


def gather_project_details(s: Session, project_id: str) -> dict:
    """Everything a proposal needs from the hub, in one place."""
    d = project_detail(s, project_id)
    profiles = {p["member_id"]: p for p in profiles_for_matching(s)}
    team = []
    for a in s.query(MemberAssignmentRow).filter_by(project_id=project_id):
        m = s.get(MemberRow, a.member_id)
        if m is None or m.status != "ACTIVE" or a.member_id not in profiles:
            continue
        team.append({"member_id": m.id, "name": m.name, "seniority": m.seniority,
                     "total_years": str(m.total_years), "role_skill": a.skill,
                     "level": profiles[m.id]["skills"].get(a.skill, 0),
                     "hours_per_week": str(a.hours_per_week)})
    hub = _get(s, HubRow, d["hub_id"], "hub")
    gaps = [n for n, ok in zip(d["skill_needs"], _staffable(s, project_id)) if not ok]
    return {**d, "hub": hub_dict(hub), "team": team, "unstaffable_skill_needs": gaps}


def _generated_section(name: str, details: dict, asmt: dict) -> str:
    b = asmt["budget"]
    if name == "Budget Justification":
        lines = ["| Category | Amount |", "|---|---|"]
        lines += [f"| {k.title()} | ${Decimal(v):,.2f} |" for k, v in b["by_category"].items()]
        lines += [f"| **Direct costs** | **${Decimal(b['direct_costs']):,.2f}** |",
                  f"| Indirect ({b['indirect_rate_pct']}% of MTDC ${Decimal(b['mtdc']):,.2f}) | "
                  f"${Decimal(b['indirect_costs']):,.2f} |",
                  f"| **Total project need** | **${Decimal(b['total_need']):,.2f}** |",
                  f"| **Amount requested** | **${Decimal(b['request']):,.2f}** |",
                  f"| Cost share required ({b['cost_share_pct']}%) | ${Decimal(b['cost_share_required']):,.2f} |",
                  f"| Matching funds committed | ${Decimal(b['matching_funds']):,.2f} |"]
        return "\n".join(lines)
    if name == "Team Qualifications":
        if not details["team"]:
            return "_No team members are assigned to this project yet._"
        lines = ["| Member | Seniority | Years | Project role skill | Level | Hours/week |",
                 "|---|---|---|---|---|---|"]
        lines += [f"| {t['name']} | {t['seniority']} | {t['total_years']} | {t['role_skill']} | "
                  f"{t['level']}/5 | {t['hours_per_week']} |" for t in details["team"]]
        return "\n".join(lines)
    if name == "Timeline & Milestones":
        lines = [f"Project period: {details['start_date']} to {details['end_date']}.", "",
                 "| Milestone | Due | Status |", "|---|---|---|"]
        lines += [f"| {m['name']} | {m['due_date']} | "
                  f"{'Complete ' + m['completed_date'] if m['completed_date'] else 'Planned'} |"
                  for m in details["milestones"]]
        return "\n".join(lines)
    if name == "Eligibility Statement":
        lines = [f"- {'✅' if c['passed'] else '❌'} **{c['check'].replace('_', ' ').title()}** — {c['detail']}"
                 for c in asmt["eligibility"]["checks"]]
        lines += [f"- ⚠️ {w}" for w in asmt["eligibility"]["warnings"]]
        return "\n".join(lines)
    return ""


def application_packet(s: Session, application_id: str, review_lookup=None) -> dict:
    from .models import AIReviewRow
    app = _get(s, GrantApplicationRow, application_id, "grant application")
    asmt = assess(s, app.project_id, app.opportunity_id)
    details = gather_project_details(s, app.project_id)
    opp = s.get(FundingOpportunityRow, app.opportunity_id)
    sections, states = [], {}
    for name in opp.required_sections:
        rid = (app.section_reviews or {}).get(name)
        review = s.get(AIReviewRow, rid) if rid else None
        state = grants.section_state(name, app.human_sections or {}, review.status if review else None)
        states[name] = state
        if state == "GENERATED":
            content, source = _generated_section(name, details, asmt), "deterministic engine"
        elif state == "HUMAN_AUTHORED":
            h = app.human_sections[name]
            content, source = h["text"], f"written by {h['author']}"
        elif state == "AI_DRAFT_APPROVED":
            content, source = review.final_output, f"AI draft #{review.id}, approved by {review.reviewer}"
        elif review is not None:
            content, source = None, f"AI draft #{review.id} is {review.status}; not shown until approved"
        else:
            content, source = None, "not started"
        sections.append({"section": name, "state": state, "source": source, "content": content,
                         "review_id": rid})
    ready = grants.readiness(opp.required_sections, states, asmt["eligibility"]["eligible"])
    if app.status in ("DRAFT", "READY_FOR_SUBMISSION"):
        new = "READY_FOR_SUBMISSION" if ready["ready"] else "DRAFT"
        if new != app.status:
            app.status = new
            audit(s, "engine", "GRANT_APPLICATION_STATUS", {"application_id": app.id, "status": new})
            s.commit()
    return {"application_id": app.id, "status": app.status, "project_id": app.project_id,
            "opportunity_id": app.opportunity_id, "funder": opp.funder, "program": opp.program,
            "deadline": opp.deadline.isoformat(), "assessment": asmt,
            "project_details": details, "sections": sections, "readiness": ready}


def list_applications(s: Session) -> list[dict]:
    out = []
    for a in s.query(GrantApplicationRow).order_by(GrantApplicationRow.id):
        o = s.get(FundingOpportunityRow, a.opportunity_id)
        p = s.get(HubProjectRow, a.project_id)
        out.append({"id": a.id, "status": a.status, "project_id": a.project_id, "project": p.name,
                    "hub_id": p.hub_id, "opportunity_id": a.opportunity_id, "funder": o.funder,
                    "program": o.program,
                    "awarded_amount": _money(a.awarded_amount) if a.awarded_amount is not None else None})
    return out


def create_application(s: Session, project_id: str, opportunity_id: str, actor: str) -> dict:
    _get(s, HubProjectRow, project_id, "project")
    _get(s, FundingOpportunityRow, opportunity_id, "funding opportunity")
    existing = s.query(GrantApplicationRow).filter_by(project_id=project_id,
                                                      opportunity_id=opportunity_id).first()
    if existing:
        raise Conflict(f"Application {existing.id} already exists for this project and opportunity.")
    asmt = assess(s, project_id, opportunity_id)
    n = s.query(GrantApplicationRow).count() + 1
    while s.get(GrantApplicationRow, f"GA-{n:03d}"):
        n += 1
    app = GrantApplicationRow(id=f"GA-{n:03d}", project_id=project_id, opportunity_id=opportunity_id,
                              status="DRAFT", requested_amount=Decimal(asmt["budget"]["request"]))
    s.add(app)
    audit(s, actor, "GRANT_APPLICATION_CREATED", {"application_id": app.id, "project_id": project_id,
                                                   "opportunity_id": opportunity_id,
                                                   "eligible": asmt["eligibility"]["eligible"]})
    s.commit()
    return application_packet(s, app.id)


def add_human_section(s: Session, application_id: str, section: str, text: str, author: str) -> dict:
    from .governance import validate_reviewer
    author = validate_reviewer(author)
    app = _get(s, GrantApplicationRow, application_id, "grant application")
    opp = s.get(FundingOpportunityRow, app.opportunity_id)
    if section not in opp.required_sections:
        raise Conflict(f"'{section}' is not a required section for this funder.")
    if section in grants.GENERATED_SECTIONS:
        raise Conflict(f"'{section}' is generated from engine data and cannot be overwritten.")
    if app.status not in ("DRAFT", "READY_FOR_SUBMISSION"):
        raise Conflict(f"Application {app.id} is {app.status}; sections are locked.")
    text = (text or "").strip()
    if len(text) < 20:
        raise Conflict("Section text must be at least 20 characters.")
    hs = dict(app.human_sections or {})
    hs[section] = {"text": text, "author": author, "at": _now().isoformat()}
    app.human_sections = hs
    audit(s, author, "GRANT_SECTION_WRITTEN", {"application_id": app.id, "section": section})
    s.commit()
    return application_packet(s, app.id)


def link_section_review(s: Session, application_id: str, section: str, review_id: int) -> None:
    app = _get(s, GrantApplicationRow, application_id, "grant application")
    sr = dict(app.section_reviews or {})
    sr[section] = review_id
    app.section_reviews = sr


def grant_compliance(s: Session, application_id: str) -> dict:
    app = _get(s, GrantApplicationRow, application_id, "grant application")
    if app.status != "AWARDED":
        raise Conflict(f"Application {app.id} is {app.status}; compliance tracking starts at award.")
    ms = [{"id": m.id, "name": m.name, "due_date": m.due_date, "completed_date": m.completed_date,
           "kpi_name": m.kpi_name, "kpi_target": m.kpi_target, "kpi_actual": m.kpi_actual,
           "higher_is_better": m.higher_is_better}
          for m in s.query(GrantMilestoneRow).filter_by(application_id=app.id).order_by(GrantMilestoneRow.due_date)]
    reps = [{"period_end": r.period_end, "due_date": r.due_date, "filed_date": r.filed_date}
            for r in s.query(GrantReportRow).filter_by(application_id=app.id).order_by(GrantReportRow.due_date)]
    c = grants.compliance({"id": app.id, "awarded_amount": app.awarded_amount,
                           "grant_spent": app.grant_spent, "award_start": app.award_start,
                           "award_end": app.award_end}, ms, reps, config.STATUS_DATE)
    p = s.get(HubProjectRow, app.project_id)
    o = s.get(FundingOpportunityRow, app.opportunity_id)
    c.update({"project_id": p.id, "project": p.name, "hub_id": p.hub_id, "funder": o.funder,
              "program": o.program})
    c["markdown"] = grants.compliance_markdown(c, f"{p.name} ({o.program})")
    return c


def compliance_portfolio(s: Session) -> list[dict]:
    out = []
    for a in s.query(GrantApplicationRow).filter_by(status="AWARDED").order_by(GrantApplicationRow.id):
        c = grant_compliance(s, a.id)
        out.append({k: c[k] for k in ("application_id", "project", "hub_id", "program", "status",
                                      "reasons", "next_report_due", "milestone_summary")}
                   | {"spend_pct": c["spend"]["spend_pct"], "elapsed_pct": c["spend"]["elapsed_pct"]})
    return out


# ======================================================================================
# Agent 3 -- Resource Allocation
# ======================================================================================

def request_dict(r: ResourceRequestRow) -> dict:
    return {"id": r.id, "hub_id": r.hub_id, "project_id": r.project_id, "title": r.title,
            "requested_by": r.requested_by, "lines": r.lines,
            "needed_from": r.needed_from.isoformat(), "needed_to": r.needed_to.isoformat(),
            "milestone_ids": r.milestone_ids, "justification": r.justification, "status": r.status,
            "validation": r.validation,
            "validated_at": r.validated_at.isoformat() if r.validated_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "decided_by": r.decided_by, "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "decision_reason": r.decision_reason, "assignments": r.assignments}


def _validate_row(s: Session, r: ResourceRequestRow) -> dict:
    p = _get(s, HubProjectRow, r.project_id, "project")
    ms = {m.id for m in s.query(ProjectMilestoneRow).filter_by(project_id=p.id)}
    req = {"hub_id": r.hub_id, "lines": r.lines, "needed_from": r.needed_from,
           "needed_to": r.needed_to, "milestone_ids": r.milestone_ids}
    return allocation.validate(req, project_facts(p), _budget_lines(s, p.id), ms, config.STATUS_DATE)


def submit_request(s: Session, body: dict, actor: str) -> dict:
    """Store the request and run MANDATORY budget validation straight away."""
    _get(s, HubRow, body["hub_id"], "hub")
    _get(s, HubProjectRow, body["project_id"], "project")
    r = ResourceRequestRow(hub_id=body["hub_id"], project_id=body["project_id"], title=body["title"],
                           requested_by=body["requested_by"], lines=body["lines"],
                           needed_from=body["needed_from"], needed_to=body["needed_to"],
                           milestone_ids=body.get("milestone_ids") or [],
                           justification=body.get("justification", ""), status="SUBMITTED")
    s.add(r)
    s.flush()
    audit(s, actor, "RESOURCE_REQUEST_SUBMITTED", {"request_id": r.id, "hub_id": r.hub_id,
                                                    "project_id": r.project_id})
    return validate_request(s, r.id, actor)


def validate_request(s: Session, request_id: int, actor: str = "engine") -> dict:
    r = _get(s, ResourceRequestRow, request_id, "resource request")
    if r.status not in ("SUBMITTED", "VALIDATED", "FAILED_VALIDATION"):
        raise Conflict(f"Request {r.id} is {r.status}; it can no longer be re-validated.")
    v = _validate_row(s, r)
    r.validation, r.validated_at, r.status = v, _now(), v["status"]
    audit(s, actor, "RESOURCE_REQUEST_VALIDATED", {"request_id": r.id, "valid": v["valid"],
                                                    "failed": v["explanation"]["inputs"]["failed"]})
    s.commit()
    return request_dict(r)


def list_requests(s: Session, hub_id: str | None = None, status: str | None = None) -> list[dict]:
    q = s.query(ResourceRequestRow)
    if hub_id:
        q = q.filter(ResourceRequestRow.hub_id == hub_id)
    if status:
        q = q.filter(ResourceRequestRow.status == status)
    return [request_dict(r) for r in q.order_by(ResourceRequestRow.needed_from, ResourceRequestRow.id)]


def get_request(s: Session, request_id: int) -> dict:
    return request_dict(_get(s, ResourceRequestRow, request_id, "resource request"))


def match_for_request(s: Session, request_id: int) -> dict:
    r = _get(s, ResourceRequestRow, request_id, "resource request")
    profiles = profiles_for_matching(s)
    out = []
    for i, ln in enumerate(r.lines):
        if ln.get("category") == "PERSONNEL" and ln.get("skill") in taxonomy.TAXONOMY:
            out.append({"line_index": i, "description": ln.get("description"),
                        **allocation.match(ln["skill"], int(ln.get("min_level", 3)),
                                           ln.get("hours_per_week", 0), r.hub_id, profiles)})
    return {"request_id": r.id, "matches": out}


def match_skill(s: Session, skill: str, min_level: int, hours: str, hub_id: str) -> dict:
    if skill not in taxonomy.TAXONOMY:
        raise Conflict(f"'{skill}' is not a taxonomy skill.")
    return allocation.match(skill, min_level, hours, hub_id, profiles_for_matching(s))


def decide_request(s: Session, request_id: int, approve: bool, reviewer: str, reason: str = "",
                   assignments: list[dict] | None = None) -> dict:
    """Approve (commit budget, release funds, record assignments) or reject.

    The reviewer must be a named human. Approval is refused unless the request
    passes budget validation AGAIN, right now, against the current budget.
    """
    from .governance import ReviewError, validate_reviewer
    reviewer = validate_reviewer(reviewer)
    r = _get(s, ResourceRequestRow, request_id, "resource request")
    if r.status != "VALIDATED":
        raise ReviewError(f"Request {r.id} is {r.status}; only a VALIDATED request can be decided.", 409)
    if not approve:
        if len((reason or "").strip()) < 5:
            raise ReviewError("A reason (at least 5 characters) is required to reject.", 422)
        r.status, r.decided_by, r.decided_at, r.decision_reason = "REJECTED", reviewer, _now(), reason.strip()
        audit(s, reviewer, "RESOURCE_REQUEST_REJECTED", {"request_id": r.id, "reason": reason.strip()})
        s.commit()
        return request_dict(r)

    v = _validate_row(s, r)                           # mandatory re-validation at approval time
    if not v["valid"]:
        r.validation, r.validated_at, r.status = v, _now(), "FAILED_VALIDATION"
        audit(s, reviewer, "RESOURCE_REQUEST_APPROVAL_BLOCKED",
              {"request_id": r.id, "failed": v["explanation"]["inputs"]["failed"]})
        s.commit()
        raise ReviewError("Approval blocked: the request no longer passes budget validation "
                          f"({v['explanation']['reasoning']}).", 409)

    # validate proposed member assignments before touching the budget
    planned = []
    profiles = {p["member_id"]: p for p in profiles_for_matching(s)}
    for a in assignments or []:
        idx = a.get("line_index")
        if not isinstance(idx, int) or not 0 <= idx < len(r.lines):
            raise ReviewError(f"Assignment line_index {idx!r} does not exist.", 422)
        ln = r.lines[idx]
        if ln.get("category") != "PERSONNEL" or not ln.get("skill"):
            raise ReviewError(f"Line {idx} is not a personnel line with a skill.", 422)
        p = profiles.get(a.get("member_id"))
        if p is None:
            raise ReviewError(f"Member {a.get('member_id')} is not an active, consenting member.", 422)
        if p["skills"].get(ln["skill"], 0) < int(ln.get("min_level", 3)):
            raise ReviewError(f"Member {p['member_id']} is below level {ln.get('min_level', 3)} "
                              f"in {ln['skill']}.", 422)
        hours = Decimal(str(ln.get("hours_per_week", 0)))
        if Decimal(p["available_hours"]) < hours:
            raise ReviewError(f"Member {p['member_id']} has {p['available_hours']} h/week free; "
                              f"the line needs {hours}.", 422)
        p["available_hours"] = str(Decimal(p["available_hours"]) - hours)
        planned.append((p["member_id"], ln["skill"], hours))

    # compare-and-set so two approvers cannot both release funds
    from sqlalchemy import update
    res = s.execute(update(ResourceRequestRow)
                    .where(ResourceRequestRow.id == r.id, ResourceRequestRow.status == "VALIDATED")
                    .values(status="APPROVED", decided_by=reviewer, decided_at=_now(),
                            decision_reason=(reason or "").strip() or None)
                    .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        s.rollback()
        raise ReviewError(f"Request {request_id} was decided by someone else.", 409)

    lines = s.query(BudgetLineRow).filter_by(project_id=r.project_id).order_by(BudgetLineRow.id).all()
    for ln in r.lines:
        amt = Decimal(str(ln["amount"]))
        for b in lines:                                # commit against the category's lines in order
            if b.category != ln["category"] or amt <= 0:
                continue
            room = Decimal(str(b.budgeted)) - Decimal(str(b.committed)) - Decimal(str(b.spent))
            take = min(room, amt)
            if take > 0:
                b.committed = Decimal(str(b.committed)) + take
                amt -= take
    made = []
    for member_id, skill, hours in planned:
        s.add(MemberAssignmentRow(member_id=member_id, project_id=r.project_id, skill=skill,
                                  hours_per_week=hours, source_request_id=r.id))
        made.append({"member_id": member_id, "skill": skill, "hours_per_week": str(hours)})
    s.refresh(r)
    r.validation, r.assignments = v, made
    audit(s, reviewer, "RESOURCE_REQUEST_APPROVED_FUNDS_RELEASED",
          {"request_id": r.id, "total": v["total_requested"], "assignments": made})
    s.commit()
    return request_dict(r)


def budget_for_project(s: Session, project_id: str) -> dict:
    p = _get(s, HubProjectRow, project_id, "project")
    return {"project_id": p.id, "project": p.name, "hub_id": p.hub_id,
            **allocation.budget_position(_budget_lines(s, p.id))}
