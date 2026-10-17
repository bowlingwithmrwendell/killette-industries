"""Claude as orchestrator -- never as calculator -- for the three coalition agents.

  expertise  Expertise Ingestion agent: who knows what, where the gaps are.
  grants     Grant-Writing agent: which funders fit, what a proposal needs,
             how awarded grants are tracking.
  resources  Resource Allocation agent: what hubs have asked for, whether it
             fits the budget, who could do the work.

Each agent reads deterministic engine results through read-only tools and
explains them. None of them can change data, approve anything or send
anything. The grants agent can only PROPOSE a submission, which a named human
must approve.

Guards on every AI call:
  1. Kill switch and limiter run first and fail closed (governance.guard).
  2. Every tool call is written to the audit log, and every AI call is logged
     with who, when, model, tools, tokens and outcome (ai_call_log).
  3. The answer is checked for numbers that did not come from a tool result
     (grounded = false). An ungrounded output cannot be approved as-is.
  4. The answer is a DRAFT stored PENDING_REVIEW. Only a named human can
     approve, edit-then-approve or reject it. The agents have no review tool.
  5. Members who have not consented to profiling are never sent to the model.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from . import actions, config, governance, repository
from .engines import grants as grants_engine
from .engines import taxonomy
from .engines.grounding import is_grounded
from .models import GrantApplicationRow, MemberRow

COMMON_RULES = """\
GOVERNANCE RULES (non-negotiable):
- You never calculate, estimate, round or invent a number. Every figure you state (levels,
  years, hours, dollars, scores, percentages, dates) must come from a tool result in this
  conversation, quoted as returned. If no tool provides it, say it is not available.
- Scores, eligibility, budget validation and compliance status come only from the tools. Explain
  the reasons the tools return; do not re-derive or second-guess them.
- You cannot change data, approve requests, release funds or send anything.
- Members who have not consented to profiling are excluded from matching; never speculate about them.
- The coalition data is a fictional demonstration. Do not present it as real.

COMMUNICATION:
- Professional, direct and collaborative: this coalition decides by consensus, so present
  options and trade-offs rather than orders. Lead with the answer.
- Use short Markdown tables where they help.
"""

AGENTS = {
    "expertise": {
        "title": "Expertise Ingestion Agent",
        "kind": "EXPERTISE_ASK",
        "system": "IDENTITY & ROLE:\nYou are the Expertise Ingestion Agent for the Killette Industries "
                  "Coalition, a self-organizing coalition of highly experienced professionals working "
                  "through regional hubs. You help hub facilitators understand members' structured "
                  "expertise profiles, find experts, and see where the coalition's skills fall short of "
                  "project demand.\n\n" + COMMON_RULES,
        "tools": ["list_members", "get_member_profile", "get_skill_coverage", "get_skill_inventory",
                  "find_experts", "get_taxonomy", "get_hubs_overview"],
    },
    "grants": {
        "title": "Grant-Writing Agent",
        "kind": "GRANTS_ASK",
        "system": "IDENTITY & ROLE:\nYou are the Grant-Writing Agent for the Killette Industries Coalition. "
                  "You gather project details from regional hubs, align hub projects with federal and "
                  "private funding opportunities, explain eligibility and alignment, track what each "
                  "proposal still needs, and report on milestones and compliance for awarded grants.\n\n"
                  + COMMON_RULES +
                  "- To submit an application, use propose_grant_submission. It only queues the request; "
                  "a named human must approve it. Tell the user it is awaiting approval.\n",
        "tools": ["list_funding_opportunities", "match_opportunities", "assess_opportunity",
                  "gather_project_details", "list_grant_applications", "get_application_packet",
                  "get_grant_compliance", "get_compliance_portfolio", "propose_grant_submission",
                  "get_hubs_overview"],
    },
    "resources": {
        "title": "Resource Allocation Agent",
        "kind": "RESOURCES_ASK",
        "system": "IDENTITY & ROLE:\nYou are the Resource Allocation Agent for the Killette Industries "
                  "Coalition. Regional hubs submit resource requests against their own project budgets. "
                  "Every request must pass mandatory budget validation before a named human can approve "
                  "it and release funds. You explain validation results, budget positions and expert "
                  "matches so facilitators can decide.\n\n" + COMMON_RULES,
        "tools": ["list_resource_requests", "get_resource_request", "get_budget_position",
                  "match_request", "find_experts", "get_project", "get_hubs_overview"],
    },
}

_P = {"type": "object", "properties": {}}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    out = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


_S = {"type": "string"}
_SKILL = {"type": "string", "description": "Canonical taxonomy skill name, e.g. 'Machine Learning'"}
_VS = {"type": "string", "enum": list(taxonomy.VALUE_STREAMS)}

TOOL_DEFS = {
    "list_members": ("Coalition members (summary): hub, seniority, top skills, available hours. Filter "
                     "by hub_id and/or skill with min_level.",
                     _obj({"hub_id": _S, "skill": _SKILL, "min_level": {"type": "integer", "minimum": 1,
                                                                        "maximum": 5}})),
    "get_member_profile": ("One member's full structured profile with every skill level and its "
                           "explanation.", _obj({"member_id": _S}, ["member_id"])),
    "get_skill_coverage": ("Skill supply vs project demand: GAP, SINGLE_POINT_OF_FAILURE or COVERED "
                           "per skill. Optional value_stream filter.", _obj({"value_stream": _VS})),
    "get_skill_inventory": ("Count of active members and highest level for every skill in use.", _P),
    "find_experts": ("Rank consenting members for a skill at a minimum level and weekly hours, "
                     "preferring the requesting hub.",
                     _obj({"skill": _SKILL, "min_level": {"type": "integer", "minimum": 1, "maximum": 5},
                           "hours_per_week": _S, "hub_id": _S},
                          ["skill", "min_level", "hours_per_week", "hub_id"])),
    "get_taxonomy": ("The coalition skill taxonomy: domains, canonical skills and aliases.", _P),
    "get_hubs_overview": ("Hubs, their projects, active members and budget use.", _P),
    "list_funding_opportunities": ("Every funding opportunity with award range, deadline and focus.", _P),
    "match_opportunities": ("Rank every funding opportunity for one hub project: eligibility, budget, "
                            "alignment score and recommendation.", _obj({"project_id": _S}, ["project_id"])),
    "assess_opportunity": ("Eligibility checks, budget build and alignment score for one project and "
                           "one opportunity.", _obj({"project_id": _S, "opportunity_id": _S},
                                                    ["project_id", "opportunity_id"])),
    "gather_project_details": ("Everything a proposal needs from a hub project: summary, objectives, "
                               "budget, milestones, team and unstaffed skill needs.",
                               _obj({"project_id": _S}, ["project_id"])),
    "list_grant_applications": ("Every grant application and its status.", _P),
    "get_application_packet": ("A grant application's proposal packet: each required section's state "
                               "and readiness for submission.", _obj({"application_id": _S}, ["application_id"])),
    "get_grant_compliance": ("Milestones, KPI attainment, spend vs time and report deadlines for an "
                             "awarded grant.", _obj({"application_id": _S}, ["application_id"])),
    "get_compliance_portfolio": ("Compliance status (RED/AMBER/GREEN) for every awarded grant.", _P),
    "propose_grant_submission": ("Queue a READY_FOR_SUBMISSION application for submission. Does NOT "
                                 "submit; a named human must approve.",
                                 _obj({"application_id": _S}, ["application_id"])),
    "list_resource_requests": ("Hub resource requests with validation status. Filter by hub_id or status "
                               "(SUBMITTED, VALIDATED, FAILED_VALIDATION, APPROVED, REJECTED).",
                               _obj({"hub_id": _S, "status": _S})),
    "get_resource_request": ("One resource request with every budget-validation check.",
                             _obj({"request_id": {"type": "integer"}}, ["request_id"])),
    "get_budget_position": ("A project's budget by category: budgeted, committed, spent, remaining.",
                            _obj({"project_id": _S}, ["project_id"])),
    "match_request": ("Ranked expert candidates for each personnel line of a resource request.",
                      _obj({"request_id": {"type": "integer"}}, ["request_id"])),
    "get_project": ("One hub project: summary, budget, milestones and skill needs.",
                    _obj({"project_id": _S}, ["project_id"])),
}


def tools_for(agent: str) -> list[dict]:
    return [{"name": n, "description": TOOL_DEFS[n][0], "input_schema": TOOL_DEFS[n][1]}
            for n in AGENTS[agent]["tools"]]


def run_tool(s: Session, agent: str, name: str, args: dict):
    if name not in AGENTS[agent]["tools"]:
        raise ValueError(f"Tool {name} is not available to the {agent} agent")
    a = args
    if name == "list_members":
        return repository.list_members(s, a.get("hub_id"), a.get("skill"), int(a.get("min_level") or 1))
    if name == "get_member_profile":
        m = s.get(MemberRow, a["member_id"])
        if m is not None and not m.consent_profiling:
            return {"member_id": m.id, "status": m.status,
                    "note": "This member has not consented to profiling; no profile is available."}
        return repository.member_profile(s, a["member_id"])
    if name == "get_skill_coverage":
        return repository.coverage(s, a.get("value_stream"))
    if name == "get_skill_inventory":
        return repository.skill_inventory(s)
    if name == "find_experts":
        return repository.match_skill(s, a["skill"], int(a["min_level"]), str(a["hours_per_week"]), a["hub_id"])
    if name == "get_taxonomy":
        return taxonomy.as_tree()
    if name == "get_hubs_overview":
        return repository.overview(s)
    if name == "list_funding_opportunities":
        return repository.list_opportunities(s)
    if name == "match_opportunities":
        return repository.match_opportunities(s, a["project_id"])
    if name == "assess_opportunity":
        return repository.assess(s, a["project_id"], a["opportunity_id"])
    if name == "gather_project_details":
        return repository.gather_project_details(s, a["project_id"])
    if name == "list_grant_applications":
        return repository.list_applications(s)
    if name == "get_application_packet":
        return repository.application_packet(s, a["application_id"])
    if name == "get_grant_compliance":
        c = repository.grant_compliance(s, a["application_id"])
        c.pop("markdown", None)
        return c
    if name == "get_compliance_portfolio":
        return repository.compliance_portfolio(s)
    if name == "propose_grant_submission":
        return actions.propose(s, "SUBMIT_GRANT_APPLICATION", {"application_id": a["application_id"]},
                               requested_by="agent")
    if name == "list_resource_requests":
        return repository.list_requests(s, a.get("hub_id"), a.get("status"))
    if name == "get_resource_request":
        return repository.get_request(s, int(a["request_id"]))
    if name == "get_budget_position":
        return repository.budget_for_project(s, a["project_id"])
    if name == "match_request":
        return repository.match_for_request(s, int(a["request_id"]))
    if name == "get_project":
        return repository.project_detail(s, a["project_id"])
    raise ValueError(f"Unknown tool {name}")


class AgentUnavailable(RuntimeError):
    pass


class AgentError(RuntimeError):
    pass


def _client():
    if not config.ANTHROPIC_API_KEY:
        raise AgentUnavailable("ANTHROPIC_API_KEY is not configured; every deterministic endpoint "
                               "still works.")
    from anthropic import Anthropic
    return Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _usage(resp) -> tuple[int, int]:
    u = getattr(resp, "usage", None)
    return (int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0))


def _unavailable(s, visitor, requested_by, kind, exc):
    governance.log_ai_call(s, visitor=visitor, requested_by=requested_by, kind=kind,
                           model=config.ANTHROPIC_MODEL, tools=[], model_calls=0, input_tokens=0,
                           output_tokens=0, outcome="UNAVAILABLE", detail=str(exc))
    s.commit()


def ask(s: Session, agent: str, question: str, max_turns: int | None = None, client=None,
        visitor: str = "local", requested_by: str = "") -> dict:
    """Answer a question with one of the three agents. The answer is a DRAFT:
    stored PENDING_REVIEW and published only after a named human approves it."""
    if agent not in AGENTS:
        raise ValueError(f"agent must be one of {', '.join(AGENTS)}")
    spec = AGENTS[agent]
    kind = spec["kind"]
    question = question[:config.MAX_QUESTION_CHARS]
    governance.guard(s, visitor, kind, requested_by)          # kill switch + limiter, fail closed
    try:
        client = client or _client()
    except AgentUnavailable as exc:
        _unavailable(s, visitor, requested_by, kind, exc)
        raise

    max_turns = max_turns or config.MAX_AGENT_TURNS
    messages = [{"role": "user", "content": question}]
    tool_trace: list[dict] = []
    facts: list = []
    tin = tout = calls = 0

    def log(outcome, detail="", review_id=None):
        governance.log_ai_call(s, visitor=visitor, requested_by=requested_by, kind=kind,
                               model=config.ANTHROPIC_MODEL, tools=[t["tool"] for t in tool_trace],
                               model_calls=calls, input_tokens=tin, output_tokens=tout,
                               outcome=outcome, detail=detail, review_id=review_id)

    try:
        answer = None
        for _ in range(max_turns):
            resp = client.messages.create(model=config.ANTHROPIC_MODEL, max_tokens=config.MAX_TOKENS,
                                          system=spec["system"], tools=tools_for(agent), messages=messages)
            calls += 1
            i, o = _usage(resp)
            tin, tout = tin + i, tout + o
            if resp.stop_reason != "tool_use":
                answer = "".join(b.text for b in resp.content if b.type == "text").strip()
                break

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                try:
                    out = run_tool(s, agent, block.name, dict(block.input))
                    is_error = False
                except Exception as exc:  # surfaced to the model, never swallowed silently
                    s.rollback()
                    out = {"error": str(exc)}
                    is_error = True
                facts.append(out)
                tool_trace.append({"tool": block.name, "input": dict(block.input), "error": is_error})
                actions.audit(s, "agent", "TOOL_CALL", {"agent": agent, "tool": block.name,
                                                        "input": dict(block.input), "error": is_error})
                s.commit()                     # the trail survives a later failure in this call
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, default=str), "is_error": is_error})
            messages.append({"role": "user", "content": results})
    except Exception as exc:
        s.rollback()
        log("ERROR", f"{type(exc).__name__}: {exc}")
        s.commit()
        raise AgentError("The model call failed; nothing was published.") from exc

    if answer is None:
        answer = "Stopped: the request needed more tool steps than allowed."
        grounded, unknown = True, []
    else:
        grounded, unknown = is_grounded(answer, {"question": question, "facts": facts})

    review = governance.create_review(
        s, kind=kind, prompt=question, ai_output=answer, model=config.ANTHROPIC_MODEL,
        tools_used=[t["tool"] for t in tool_trace], grounded=grounded, unverified_numbers=unknown,
        requested_by=requested_by, visitor=visitor, agent=agent)
    log("OK", "answer stored PENDING_REVIEW", review.id)
    s.commit()
    return {"agent": agent, "review_id": review.id, "status": review.status, "validated": False,
            "draft_answer": answer, "tools_used": tool_trace, "grounded": grounded,
            "unverified_numbers": unknown, "model": config.ANTHROPIC_MODEL,
            "usage": {"model_calls": calls, "input_tokens": tin, "output_tokens": tout},
            "notice": "Draft only. This AI output is PENDING_REVIEW and is not published until a "
                      "named human approves, edits or rejects it."}


def _single_draft(s: Session, *, kind: str, agent: str, subject_id: str, prompt: str, facts: dict,
                  visitor: str, requested_by: str, client=None) -> tuple[object, str, bool, list]:
    governance.guard(s, visitor, kind, requested_by)
    try:
        client = client or _client()
    except AgentUnavailable as exc:
        _unavailable(s, visitor, requested_by, kind, exc)
        raise
    full = prompt + "\n\nFACTS (JSON):\n" + json.dumps(facts, default=str)
    try:
        resp = client.messages.create(model=config.ANTHROPIC_MODEL, max_tokens=config.DRAFT_MAX_TOKENS,
                                      system=AGENTS[agent]["system"],
                                      messages=[{"role": "user", "content": full}])
    except Exception as exc:
        governance.log_ai_call(s, visitor=visitor, requested_by=requested_by, kind=kind,
                               model=config.ANTHROPIC_MODEL, tools=[], model_calls=1, input_tokens=0,
                               output_tokens=0, outcome="ERROR", detail=f"{type(exc).__name__}: {exc}")
        s.commit()
        raise AgentError("The model call failed; nothing was drafted.") from exc
    tin, tout = _usage(resp)
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    ok, unknown = is_grounded(text, facts)
    review = governance.create_review(
        s, kind=kind, prompt=prompt, ai_output=text, model=config.ANTHROPIC_MODEL, tools_used=[],
        grounded=ok and bool(text), unverified_numbers=unknown, requested_by=requested_by,
        visitor=visitor, agent=agent, subject_id=subject_id)
    governance.log_ai_call(s, visitor=visitor, requested_by=requested_by, kind=kind,
                           model=config.ANTHROPIC_MODEL, tools=[], model_calls=1, input_tokens=tin,
                           output_tokens=tout, outcome="OK", detail="draft stored PENDING_REVIEW",
                           review_id=review.id)
    return review, text, ok and bool(text), unknown


def draft_grant_section(s: Session, application_id: str, section: str, client=None,
                        visitor: str = "local", requested_by: str = "") -> dict:
    """Ask Claude to draft one narrative section. The draft goes to the review
    queue and only appears in the proposal packet after a human approves it."""
    app = s.get(GrantApplicationRow, application_id)
    if app is None:
        raise repository.NotFound(f"No grant application {application_id}")
    if section not in grants_engine.NARRATIVE_SECTIONS:
        raise repository.Conflict(f"'{section}' is not a narrative section. AI drafts are only "
                                  f"available for: {', '.join(grants_engine.NARRATIVE_SECTIONS)}.")
    packet = repository.application_packet(s, application_id)
    if section not in [x["section"] for x in packet["sections"]]:
        raise repository.Conflict(f"'{section}' is not required by this funder.")
    if app.status not in ("DRAFT", "READY_FOR_SUBMISSION"):
        raise repository.Conflict(f"Application {app.id} is {app.status}; sections are locked.")
    d = packet["project_details"]
    facts = {"funder": packet["funder"], "program": packet["program"], "section": section,
             "project": {k: d[k] for k in ("name", "value_stream", "capability", "summary", "objectives",
                                           "beneficiaries", "start_date", "end_date")},
             "hub": d["hub"]["name"], "region": d["hub"]["region"],
             "team": d["team"], "milestones": d["milestones"],
             "request": packet["assessment"]["budget"]["request"],
             "matched_focus_keywords": packet["assessment"]["alignment"]["matched_keywords"]}
    prompt = (f"Draft the '{section}' section of a grant proposal to {packet['funder']} for the "
              f"'{d['name']}' project. 150-250 words, plain prose, no headings. Use only facts in the "
              "JSON; use numbers exactly as written and add no other numbers. Where a fact the funder "
              "would expect is missing, write [NEEDS INPUT: ...] instead of inventing it.")
    review, text, ok, unknown = _single_draft(s, kind="GRANT_SECTION", agent="grants",
                                              subject_id=f"{application_id}:{section}", prompt=prompt,
                                              facts=facts, visitor=visitor, requested_by=requested_by,
                                              client=client)
    repository.link_section_review(s, application_id, section, review.id)
    s.commit()
    return {"application_id": application_id, "section": section, "review_id": review.id,
            "status": review.status, "grounded": ok, "unverified_numbers": unknown, "draft": text,
            "notice": "Draft only. It appears in the proposal packet after a named human approves it."}


def draft_member_bio(s: Session, member_id: str, client=None, visitor: str = "local",
                     requested_by: str = "") -> dict:
    """A short professional bio for proposals, drafted from the structured profile only."""
    m = s.get(MemberRow, member_id)
    if m is None:
        raise repository.NotFound(f"No member {member_id}")
    if not m.consent_profiling or m.status != "ACTIVE":
        governance.log_ai_call(s, visitor=visitor, requested_by=requested_by, kind="MEMBER_BIO",
                               model=config.ANTHROPIC_MODEL, tools=[], model_calls=0, input_tokens=0,
                               output_tokens=0, outcome="CONSENT_REQUIRED",
                               detail=f"member {member_id} has not consented")
        s.commit()
        raise governance.AIBlocked("This member has not consented to profiling; their data is never "
                                   "sent to the model.", 403)
    prof = repository.member_profile(s, member_id)
    facts = {k: prof[k] for k in ("name", "headline", "seniority", "total_years", "industries",
                                  "certifications", "roles")}
    facts["skills"] = [{"skill": k["skill"], "level": k["level"], "years": k["years"]} for k in prof["skills"]]
    prompt = ("Write a three-sentence professional bio for a grant proposal team section. Third person. "
              "Use only the facts in the JSON and numbers exactly as written.")
    review, text, ok, unknown = _single_draft(s, kind="MEMBER_BIO", agent="expertise", subject_id=member_id,
                                              prompt=prompt, facts=facts, visitor=visitor,
                                              requested_by=requested_by, client=client)
    s.commit()
    return {"member_id": member_id, "review_id": review.id, "status": review.status, "grounded": ok,
            "unverified_numbers": unknown, "draft": text,
            "notice": "Draft only. It is published after a named human approves it."}
