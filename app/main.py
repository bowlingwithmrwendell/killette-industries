"""Killette Industries Coalition Agents -- FastAPI service.

Three agents on one shared platform:
  1. Expertise Ingestion   /api/members, /api/expertise/*
  2. Grant-Writing         /api/grants/*
  3. Resource Allocation   /api/resources/*
plus the shared governance layer (/api/reviews, /api/actions, /api/governance).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import actions, agent, config, governance, repository
from .db import Base, SessionLocal, engine, get_session
from .engines import expertise, taxonomy
from .engines.allocation import CATEGORIES
from .engines.grants import NARRATIVE_SECTIONS
from .models import AICallRow, AIReviewRow, AuditRow, MemberRow
from .seed import seed

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        seed(s)
    yield


app = FastAPI(
    title="Killette Industries Coalition Agents",
    version="1.0.0",
    description=("Three AI agents for a multi-industry coalition: Expertise Ingestion, Grant-Writing "
                 "and Resource Allocation. Every number comes from a deterministic engine; Claude "
                 "orchestrates and explains, and every AI output waits for a named human. "
                 "Fictional demo data."),
    lifespan=lifespan,
)
app.mount("/static/brand", StaticFiles(directory=STATIC / "brand"), name="brand")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS,
                   allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])


class RequestSizeLimit:
    """Reject any request body over MAX_REQUEST_BYTES with 413, whether or not
    the client sends Content-Length (streamed bodies are counted as they arrive)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = config.MAX_REQUEST_BYTES
        headers = dict(scope.get("headers") or [])
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = limit + 1
        if declared > limit:
            return await self._reject(send, limit)

        seen = 0

        async def counted():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise _TooLarge()
            return msg

        started = False

        async def tracking_send(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, counted, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(send, limit)

    @staticmethod
    async def _reject(send, limit):
        resp = JSONResponse({"detail": f"Request body exceeds the {limit}-byte limit."}, status_code=413)
        await send({"type": "http.response.start", "status": 413, "headers": resp.raw_headers})
        await send({"type": "http.response.body", "body": resp.body})


class _TooLarge(Exception):
    pass


app.add_middleware(RequestSizeLimit)


PUBLIC_PATHS = {"/", "/health", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/brand/",)


class ConsoleAccess:
    """The landing page is public; the agents console, API and docs are internal tools
    behind HTTP Basic auth. Fails closed when no password is configured."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not config.CONSOLE_AUTH:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await self.app(scope, receive, send)
        if scope.get("method") == "OPTIONS":
            return await self.app(scope, receive, send)
        if not config.CONSOLE_PASSWORD:
            return await self._send(send, 503, "Killette Industries tools are locked: no CONSOLE_PASSWORD is "
                                               "configured on this server.", basic=False)
        headers = dict(scope.get("headers") or [])
        if self._authorized(headers.get(b"authorization", b"").decode("latin-1")):
            return await self.app(scope, receive, send)
        return await self._send(send, 401, "Sign in to use the Killette Industries tools.", basic=True)

    @staticmethod
    def _authorized(header: str) -> bool:
        import base64
        import hmac
        if not header.lower().startswith("basic "):
            return False
        try:
            user, _, pw = base64.b64decode(header[6:].strip()).decode("utf-8").partition(":")
        except Exception:
            return False
        ok_user = hmac.compare_digest(user.encode(), config.CONSOLE_USERNAME.encode())
        ok_pw = hmac.compare_digest(pw.encode(), config.CONSOLE_PASSWORD.encode())
        return ok_user and ok_pw

    @staticmethod
    async def _send(send, status, message, basic):
        resp = JSONResponse({"detail": message}, status_code=status)
        headers = list(resp.raw_headers)
        if basic:
            headers.append((b"www-authenticate", b'Basic realm="Killette Industries tools", charset="UTF-8"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": resp.body})


app.add_middleware(ConsoleAccess)


def visitor(request: Request) -> str:
    return governance.visitor_id(
        governance.client_address(request.headers, request.client.host if request.client else None))


def _gov_http(exc: governance.GovernanceError) -> HTTPException:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return HTTPException(exc.status_code, str(exc), headers=headers)


def _call(fn, *a, **kw):
    """Map domain errors to HTTP status codes in one place."""
    try:
        return fn(*a, **kw)
    except repository.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except repository.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except expertise.IngestionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except governance.GovernanceError as exc:
        raise _gov_http(exc) from exc
    except agent.AgentUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except agent.AgentError as exc:
        raise HTTPException(502, str(exc)) from exc


# --- request models -----------------------------------------------------------------

class RoleIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    organization: str = Field(default="", max_length=120)
    industry: str = Field(default="", max_length=60)
    start_year: int = Field(ge=1950, le=2100)
    end_year: int | None = Field(default=None, ge=1950, le=2100)


class SkillIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    years: float = Field(ge=0, le=60)
    self_rating: int = Field(ge=1, le=5)
    certified: bool = False


class SubmissionIn(BaseModel):
    hub_id: str
    name: str = Field(min_length=2, max_length=120)
    headline: str = Field(default="", max_length=200)
    industries: list[str] = Field(default_factory=list, max_length=10)
    certifications: list[str] = Field(default_factory=list, max_length=20)
    roles: list[RoleIn] = Field(default_factory=list, max_length=20)
    skills: list[SkillIn] = Field(min_length=1, max_length=30)
    capacity_hours: float = Field(default=20, ge=0, le=60)
    consent_profiling: bool = Field(description="Must be true. No profiling without consent.")
    resume_text: str = Field(default="", max_length=6000,
                             description="Optional. Scanned for skills to SUGGEST; never added automatically.")
    submitted_by: str = Field(default="", max_length=120)


class WithdrawIn(BaseModel):
    confirmed_by: str = Field(min_length=2, max_length=120)


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=config.MAX_QUESTION_CHARS)
    requested_by: str = Field(default="", max_length=120)


class DraftIn(BaseModel):
    requested_by: str = Field(default="", max_length=120)


class CreateApplicationIn(BaseModel):
    project_id: str
    opportunity_id: str
    created_by: str = Field(min_length=2, max_length=120)


class HumanSectionIn(BaseModel):
    section: str = Field(min_length=3, max_length=60)
    text: str = Field(min_length=20, max_length=8000)
    author: str = Field(min_length=2, max_length=120)


class SectionDraftIn(BaseModel):
    section: Literal[NARRATIVE_SECTIONS]  # type: ignore[valid-type]
    requested_by: str = Field(default="", max_length=120)


class RequestLineIn(BaseModel):
    category: Literal[CATEGORIES]  # type: ignore[valid-type]
    description: str = Field(min_length=3, max_length=300)
    amount: str = Field(description="Dollars as a string, e.g. '12500.00'")
    skill: str | None = Field(default=None, max_length=80)
    min_level: int | None = Field(default=None, ge=1, le=5)
    hours_per_week: str | None = None


class ResourceRequestIn(BaseModel):
    hub_id: str
    project_id: str
    title: str = Field(min_length=3, max_length=200)
    requested_by: str = Field(min_length=2, max_length=120)
    lines: list[RequestLineIn] = Field(min_length=1, max_length=20)
    needed_from: date
    needed_to: date
    milestone_ids: list[str] = Field(default_factory=list, max_length=10)
    justification: str = Field(default="", max_length=3000)


class ApplicantIn(BaseModel):
    name: str = Field(default="", max_length=200)
    entity_type: Literal["NONPROFIT_501C3", "COOPERATIVE", "LLC", "CORPORATION", "FISCALLY_SPONSORED"]
    sam_registered: bool | None = None
    employees: int | None = Field(default=None, ge=0, le=1_000_000)
    us_owned_pct: float | None = Field(default=None, ge=0, le=100)
    pi_employed: bool | None = None


class BudgetLineIn(BaseModel):
    category: Literal[CATEGORIES]  # type: ignore[valid-type]
    budgeted: str
    committed: str = "0"
    spent: str = "0"


class MilestoneIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    due_date: date
    completed_date: date | None = None


class SkillNeedIn(BaseModel):
    skill: str = Field(max_length=80)
    min_level: int = Field(default=3, ge=1, le=5)
    hours_per_week: str


class ProjectIn(BaseModel):
    hub_id: str
    name: str = Field(min_length=3, max_length=200)
    value_stream: Literal["INSURANCE", "AUTOMOTIVE", "AGRICULTURE", "MANUFACTURING"]
    capability: str = Field(default="", max_length=120)
    summary: str = Field(min_length=10, max_length=3000)
    objectives: list[str] = Field(default_factory=list, max_length=10)
    beneficiaries: str = Field(default="", max_length=1000)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    start_date: date
    end_date: date
    lead_member_id: str | None = None
    matching_funds: str = "0"
    ip_status: str = Field(default="", max_length=500)
    applicant: ApplicantIn | None = Field(default=None, description="Leave out to apply as the coalition.")
    budget_lines: list[BudgetLineIn] = Field(min_length=1, max_length=20)
    milestones: list[MilestoneIn] = Field(default_factory=list, max_length=20)
    skill_needs: list[SkillNeedIn] = Field(default_factory=list, max_length=20)
    created_by: str = Field(default="", max_length=120)


class OpportunityIn(BaseModel):
    funder: str = Field(min_length=3, max_length=200)
    program: str = Field(min_length=3, max_length=200)
    funder_type: Literal["FEDERAL", "STATE", "PRIVATE"]
    min_award: str
    max_award: str
    deadline: date
    performance_months: int = Field(ge=1, le=120)
    eligible_entities: list[Literal["NONPROFIT_501C3", "COOPERATIVE", "LLC", "CORPORATION",
                                    "FISCALLY_SPONSORED"]] = Field(min_length=1)
    value_streams: list[Literal["INSURANCE", "AUTOMOTIVE", "AGRICULTURE", "MANUFACTURING"]] = Field(min_length=1)
    focus_keywords: list[str] = Field(default_factory=list, max_length=20)
    cost_share_pct: str = "0"
    indirect_cap_pct: str | None = None
    requires_sam: bool = False
    reporting: Literal["MONTHLY", "QUARTERLY", "SEMIANNUAL"] = "QUARTERLY"
    max_employees: int | None = Field(default=None, ge=1)
    min_us_ownership_pct: str | None = None
    requires_pi_employed: bool = False
    max_subcontract_pct: str | None = None
    created_by: str = Field(default="", max_length=120)


class AssignmentIn(BaseModel):
    line_index: int = Field(ge=0)
    member_id: str


class RequestDecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)
    assignments: list[AssignmentIn] = Field(default_factory=list, max_length=20)


class ProposeIn(BaseModel):
    action_type: Literal["SUBMIT_GRANT_APPLICATION", "POST_HUB_NOTICE"]
    project_id: str | None = None
    payload: dict = Field(default_factory=dict)
    requested_by: str = Field(min_length=1, max_length=60)


class DecisionIn(BaseModel):
    approve: bool
    decided_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)


class ReviewDecisionIn(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    reviewer: str = Field(min_length=1, max_length=120)
    edited_output: str | None = Field(default=None, max_length=20000)
    reason: str | None = Field(default=None, max_length=2000)


class KillSwitchIn(BaseModel):
    engaged: bool
    actor: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=3, max_length=500)


def _sub(body: SubmissionIn) -> dict:
    d = body.model_dump()
    d["roles"] = [r for r in d["roles"]]
    return d


# --- pages and coalition ---------------------------------------------------------------

@app.get("/", include_in_schema=False)
def landing():
    """The Killette Industries landing page."""
    return FileResponse(STATIC / "landing.html")


@app.get("/console", include_in_schema=False)
def console():
    """The agents console (Coalition, Expertise, Grants, Resources, review and approvals)."""
    return FileResponse(STATIC / "index.html")


@app.get("/governance", include_in_schema=False)
def governance_page():
    return FileResponse(STATIC / "governance.html")


@app.get("/health")
def health(s: Session = Depends(get_session)):
    ks = governance.kill_switch_state(s)
    return {"status": "ok", "status_date": config.STATUS_DATE.isoformat(),
            "agents_enabled": bool(config.ANTHROPIC_API_KEY) and not ks["engaged"],
            "kill_switch_engaged": ks["engaged"],
            "members": s.query(func.count()).select_from(MemberRow).scalar()}


@app.get("/api/overview", tags=["Coalition"])
def get_overview(s: Session = Depends(get_session)):
    return repository.overview(s)


@app.get("/api/meta", tags=["Coalition"])
def get_meta():
    """Choices for the console forms."""
    return {"value_streams": list(taxonomy.VALUE_STREAMS), "entity_types": list(repository.ENTITY_TYPES),
            "budget_categories": list(CATEGORIES), "skills": sorted(taxonomy.TAXONOMY)}


@app.post("/api/projects", status_code=201, tags=["Coalition"])
def create_project(body: ProjectIn, s: Session = Depends(get_session)):
    """Create a hub project (budget, milestones, skill needs). Also re-imports an exported project."""
    d = body.model_dump()
    return _call(repository.create_project, s, d, body.created_by or "console user")


@app.get("/api/projects/{project_id}/export", tags=["Coalition"])
def export_project(project_id: str, s: Session = Depends(get_session)):
    """Save this JSON; POST it back to /api/projects to restore the project after a sandbox reset."""
    return _call(repository.export_project, s, project_id)


@app.get("/api/projects/{project_id}", tags=["Coalition"])
def get_project(project_id: str, s: Session = Depends(get_session)):
    return _call(repository.project_detail, s, project_id)


@app.get("/api/coalition-profile", tags=["Coalition"])
def get_coalition_profile():
    p = repository.coalition_profile()
    return {**p, "indirect_rate_pct": str(p["indirect_rate_pct"]),
            "note": "Set COALITION_ENTITY_TYPE, COALITION_SAM_REGISTERED and COALITION_INDIRECT_RATE_PCT "
                    "in .env. The entity type decides which grants the coalition can apply for."}


# --- Agent 1: Expertise Ingestion ----------------------------------------------------------

@app.get("/api/taxonomy", tags=["1 · Expertise Ingestion"])
def get_taxonomy():
    return taxonomy.as_tree()


@app.get("/api/members", tags=["1 · Expertise Ingestion"])
def get_members(hub_id: str | None = None, skill: str | None = None,
                min_level: int = Query(1, ge=1, le=5), s: Session = Depends(get_session)):
    return repository.list_members(s, hub_id, skill, min_level)


@app.get("/api/members/{member_id}", tags=["1 · Expertise Ingestion"])
def get_member(member_id: str, s: Session = Depends(get_session)):
    return _call(repository.member_profile, s, member_id)


@app.post("/api/expertise/preview", tags=["1 · Expertise Ingestion"])
def preview_ingestion(body: SubmissionIn):
    """Run the ingestion engine without storing anything (what the member will see before saving)."""
    return _call(expertise.ingest, _sub(body), config.STATUS_DATE)


@app.post("/api/members/ingest", status_code=201, tags=["1 · Expertise Ingestion"])
def ingest_member(body: SubmissionIn, s: Session = Depends(get_session)):
    """Ingest a new member. Refused (422) unless consent_profiling is true."""
    return _call(repository.ingest_member, s, _sub(body), body.submitted_by or body.name)


@app.put("/api/members/{member_id}/profile", tags=["1 · Expertise Ingestion"])
def reingest_member(member_id: str, body: SubmissionIn, s: Session = Depends(get_session)):
    """Re-ingest an existing member (e.g. after they update their skills)."""
    return _call(repository.ingest_member, s, _sub(body), body.submitted_by or body.name, member_id)


@app.post("/api/members/{member_id}/withdraw-consent", tags=["1 · Expertise Ingestion"])
def withdraw(member_id: str, body: WithdrawIn, s: Session = Depends(get_session)):
    """Deletes the member's skill records and excludes them from matching and AI use."""
    return _call(repository.withdraw_consent, s, member_id, body.confirmed_by.strip())


@app.get("/api/expertise/coverage", tags=["1 · Expertise Ingestion"])
def get_coverage(value_stream: Literal["INSURANCE", "AUTOMOTIVE", "AGRICULTURE", "MANUFACTURING"] | None = None,
                 s: Session = Depends(get_session)):
    return repository.coverage(s, value_stream)


@app.get("/api/expertise/inventory", tags=["1 · Expertise Ingestion"])
def get_inventory(s: Session = Depends(get_session)):
    return repository.skill_inventory(s)


@app.get("/api/expertise/match", tags=["1 · Expertise Ingestion"])
def get_match(skill: str, hub_id: str, min_level: int = Query(3, ge=1, le=5),
              hours_per_week: str = "10", s: Session = Depends(get_session)):
    return _call(repository.match_skill, s, skill, min_level, hours_per_week, hub_id)


@app.post("/api/members/{member_id}/bio-draft", tags=["1 · Expertise Ingestion"])
def bio_draft(member_id: str, body: DraftIn, s: Session = Depends(get_session),
              who: str = Depends(visitor)):
    """AI-drafted proposal bio from the structured profile. Stored PENDING_REVIEW."""
    return _call(agent.draft_member_bio, s, member_id, visitor=who, requested_by=body.requested_by)


# --- Agent 2: Grant-Writing ------------------------------------------------------------------

@app.get("/api/grants/opportunities", tags=["2 · Grant-Writing"])
def get_opportunities(s: Session = Depends(get_session)):
    return repository.list_opportunities(s)


@app.post("/api/grants/opportunities", status_code=201, tags=["2 · Grant-Writing"])
def create_opportunity(body: OpportunityIn, s: Session = Depends(get_session)):
    """Add a funding opportunity with its rules (including SBIR-style small-business rules)."""
    return _call(repository.create_opportunity, s, body.model_dump(), body.created_by or "console user")


@app.get("/api/grants/projects/{project_id}/opportunities", tags=["2 · Grant-Writing"])
def match_opportunities(project_id: str, s: Session = Depends(get_session)):
    """Every opportunity ranked for this project: eligibility, budget build, alignment score."""
    return _call(repository.match_opportunities, s, project_id)


@app.get("/api/grants/assess", tags=["2 · Grant-Writing"])
def assess(project_id: str, opportunity_id: str, s: Session = Depends(get_session)):
    return _call(repository.assess, s, project_id, opportunity_id)


@app.get("/api/grants/projects/{project_id}/details", tags=["2 · Grant-Writing"])
def project_details(project_id: str, s: Session = Depends(get_session)):
    """Gather project details from the hub: everything a proposal needs."""
    return _call(repository.gather_project_details, s, project_id)


@app.get("/api/grants/applications", tags=["2 · Grant-Writing"])
def get_applications(s: Session = Depends(get_session)):
    return repository.list_applications(s)


@app.post("/api/grants/applications", status_code=201, tags=["2 · Grant-Writing"])
def create_application(body: CreateApplicationIn, s: Session = Depends(get_session)):
    return _call(repository.create_application, s, body.project_id, body.opportunity_id, body.created_by)


@app.get("/api/grants/applications/{application_id}", tags=["2 · Grant-Writing"])
def get_packet(application_id: str, s: Session = Depends(get_session)):
    """The proposal packet: every required section, its state, and readiness to submit."""
    return _call(repository.application_packet, s, application_id)


@app.post("/api/grants/applications/{application_id}/sections", tags=["2 · Grant-Writing"])
def write_section(application_id: str, body: HumanSectionIn, s: Session = Depends(get_session),
                  x_reviewer_token: str | None = Header(default=None)):
    """A named human writes a narrative section directly (no AI involved)."""
    _call(governance.check_reviewer_token, x_reviewer_token)
    return _call(repository.add_human_section, s, application_id, body.section, body.text, body.author)


@app.post("/api/grants/applications/{application_id}/sections/draft", tags=["2 · Grant-Writing"])
def draft_section(application_id: str, body: SectionDraftIn, s: Session = Depends(get_session),
                  who: str = Depends(visitor)):
    """AI-drafted narrative section. Stored PENDING_REVIEW; enters the packet only when approved."""
    return _call(agent.draft_grant_section, s, application_id, body.section, visitor=who,
                 requested_by=body.requested_by)


@app.get("/api/grants/compliance", tags=["2 · Grant-Writing"])
def get_compliance_portfolio(s: Session = Depends(get_session)):
    return repository.compliance_portfolio(s)


@app.get("/api/grants/applications/{application_id}/compliance", tags=["2 · Grant-Writing"])
def get_compliance(application_id: str, format: Literal["json", "markdown"] = "json",
                   s: Session = Depends(get_session)):
    """Milestone report for an awarded grant: milestones, KPIs, spend vs time, report deadlines."""
    c = _call(repository.grant_compliance, s, application_id)
    if format == "markdown":
        return PlainTextResponse(c["markdown"], media_type="text/markdown")
    return c


# --- Agent 3: Resource Allocation ------------------------------------------------------------

@app.get("/api/resources/requests", tags=["3 · Resource Allocation"])
def get_requests(hub_id: str | None = None, status: str | None = None, s: Session = Depends(get_session)):
    return repository.list_requests(s, hub_id, status)


@app.post("/api/resources/requests", status_code=201, tags=["3 · Resource Allocation"])
def submit_request(body: ResourceRequestIn, s: Session = Depends(get_session)):
    """Submit a hub resource request. Mandatory budget validation runs immediately."""
    d = body.model_dump()
    d["lines"] = [{k: v for k, v in ln.items() if v is not None} for ln in d["lines"]]
    return _call(repository.submit_request, s, d, body.requested_by)


@app.get("/api/resources/requests/{request_id}", tags=["3 · Resource Allocation"])
def get_request(request_id: int, s: Session = Depends(get_session)):
    return _call(repository.get_request, s, request_id)


@app.post("/api/resources/requests/{request_id}/validate", tags=["3 · Resource Allocation"])
def revalidate(request_id: int, s: Session = Depends(get_session)):
    """Re-run budget validation against the current budget."""
    return _call(repository.validate_request, s, request_id, "engine")


@app.get("/api/resources/requests/{request_id}/matches", tags=["3 · Resource Allocation"])
def request_matches(request_id: int, s: Session = Depends(get_session)):
    return _call(repository.match_for_request, s, request_id)


@app.post("/api/resources/requests/{request_id}/decision", tags=["3 · Resource Allocation"])
def decide_request(request_id: int, body: RequestDecisionIn, s: Session = Depends(get_session),
                   x_reviewer_token: str | None = Header(default=None)):
    """A named human approves (re-validates, commits the budget, releases funds) or rejects."""
    _call(governance.check_reviewer_token, x_reviewer_token)
    return _call(repository.decide_request, s, request_id, body.decision == "approve", body.reviewer,
                 body.reason, [a.model_dump() for a in body.assignments])


@app.get("/api/resources/projects/{project_id}/budget", tags=["3 · Resource Allocation"])
def get_budget(project_id: str, s: Session = Depends(get_session)):
    return _call(repository.budget_for_project, s, project_id)


# --- the three agents ------------------------------------------------------------------------

@app.get("/api/agents", tags=["Agents"])
def list_agents():
    return [{"agent": k, "title": v["title"], "tools": v["tools"]} for k, v in agent.AGENTS.items()]


@app.post("/api/agents/{agent_name}/ask", tags=["Agents"])
def ask_agent(agent_name: Literal["expertise", "grants", "resources"], body: AskIn,
              s: Session = Depends(get_session), who: str = Depends(visitor)):
    """Returns a DRAFT answer stored PENDING_REVIEW. It is published only after a named
    human approves, edits or rejects it via /api/reviews/{id}/decision."""
    return _call(agent.ask, s, agent_name, body.question, visitor=who, requested_by=body.requested_by)


# --- approvals for outbound actions ---------------------------------------------------------

@app.post("/api/actions", status_code=201, tags=["Approvals"])
def propose_action(body: ProposeIn, s: Session = Depends(get_session)):
    try:
        return actions.propose(s, body.action_type, dict(body.payload), body.requested_by, body.project_id)
    except actions.ActionError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/actions", tags=["Approvals"])
def get_actions(status: str | None = None, s: Session = Depends(get_session)):
    return actions.list_actions(s, status)


@app.post("/api/actions/{action_id}/decision", tags=["Approvals"])
def decide_action(action_id: int, body: DecisionIn, s: Session = Depends(get_session),
                  x_reviewer_token: str | None = Header(default=None)):
    _call(governance.check_reviewer_token, x_reviewer_token)
    try:
        return actions.decide(s, action_id, body.approve, body.decided_by, body.reason)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except actions.ActionError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/audit", tags=["Approvals"])
def get_audit(limit: int = Query(50, ge=1, le=500), s: Session = Depends(get_session)):
    rows = s.query(AuditRow).order_by(AuditRow.id.desc()).limit(limit)
    return [{"id": r.id, "at": r.at.isoformat(), "actor": r.actor, "event": r.event,
             "detail": r.detail} for r in rows]


# --- human review gate for AI output ---------------------------------------------------------

@app.get("/api/reviews", tags=["Human review"])
def get_reviews(status: str | None = None, agent_name: str | None = Query(None, alias="agent"),
                limit: int = Query(50, ge=1, le=200), s: Session = Depends(get_session)):
    return governance.list_reviews(s, status, limit, agent_name)


@app.get("/api/reviews/{review_id}", tags=["Human review"])
def get_review(review_id: int, s: Session = Depends(get_session)):
    row = s.get(AIReviewRow, review_id)
    if row is None:
        raise HTTPException(404, f"No AI output {review_id}")
    return governance.review_dict(row)


@app.post("/api/reviews/{review_id}/decision", tags=["Human review"])
def decide_review(review_id: int, body: ReviewDecisionIn, s: Session = Depends(get_session),
                  x_reviewer_token: str | None = Header(default=None)):
    """approve | edit (edit-then-approve) | reject (reason required). Decisions are final."""
    try:
        governance.check_reviewer_token(x_reviewer_token)
        return governance.decide_review(s, review_id, body.decision, body.reviewer,
                                        body.edited_output, body.reason)
    except governance.ReviewError as exc:
        raise _gov_http(exc) from exc


@app.get("/api/reviews/{review_id}/published", tags=["Human review"])
def get_published(review_id: int, s: Session = Depends(get_session)):
    """The human-approved text. 409 for anything pending or rejected (fails closed)."""
    row = s.get(AIReviewRow, review_id)
    try:
        text = governance.published_text(row)
    except governance.ReviewError as exc:
        raise _gov_http(exc) from exc
    return {"id": row.id, "status": row.status, "text": text, "reviewer": row.reviewer,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None}


# --- AI call log and governance scorecard -----------------------------------------------------

@app.get("/api/ai-calls", tags=["Governance"])
def get_ai_calls(limit: int = Query(50, ge=1, le=500), s: Session = Depends(get_session)):
    rows = s.query(AICallRow).order_by(AICallRow.id.desc()).limit(limit)
    return [governance.ai_call_dict(r) for r in rows]


@app.get("/api/governance", tags=["Governance"])
def get_governance(s: Session = Depends(get_session), who: str = Depends(visitor)):
    ks = governance.kill_switch_state(s)
    u = governance.usage(s, who)
    outcomes = dict(s.query(AICallRow.outcome, func.count(AICallRow.id)).group_by(AICallRow.outcome).all())
    return {
        "model_card": {
            "provider": "Anthropic",
            "model": config.ANTHROPIC_MODEL,
            "role": "Orchestrator and explainer for three agents. Chooses which deterministic engine "
                    "to call, explains the result, and drafts narrative text for human review.",
            "agents": [{"agent": k, "title": v["title"], "tools": v["tools"]} for k, v in agent.AGENTS.items()],
            "not_used_for": ["Calculating any figure (skill levels, eligibility, budgets, scores, "
                             "compliance status)", "Validating or approving resource requests",
                             "Releasing funds", "Changing member, project or budget data",
                             "Submitting grant applications or sending anything",
                             "Approving its own output or any outbound action",
                             "Processing members who have not consented to profiling"],
            "tool_permissions": "Read-only engine tools, plus propose_grant_submission which only "
                                "queues a request for a named human to approve.",
            "safeguards": ["Numeric grounding check on every output",
                           "Every output PENDING_REVIEW until a named human decides",
                           "Ungrounded output cannot be approved unchanged",
                           "Consent gate: non-consenting members never reach the model",
                           "Mandatory budget validation, re-run at approval time",
                           "Every AI call logged: who, when, model, tools, tokens, outcome",
                           "Kill switch and per-visitor limits, all fail closed"],
            "known_limits": ["Explanations can be incomplete or poorly prioritised; the engines' "
                             "explanation fields are the authoritative reasoning",
                             "Grounding checks numbers, not wording: a reviewer must still read it",
                             "Skill extraction from free text is keyword-based and only suggests",
                             "Demo coalition only; not validated on real member or funder data"],
        },
        "data_policy": {
            "data": "Fictional demonstration coalition. Funders, members, hubs and figures are invented.",
            "member_profiles": "Consent required before ingestion. Profiles hold no contact details, "
                               "demographics or compensation. Withdrawing consent deletes skill records.",
            "sent_to_model": "The question, the system prompt and the engine results the model "
                             "requests. No credentials, no visitor identifiers, no non-consenting members.",
            "visitor_identity": "Client address is salted and hashed for rate limiting; the raw "
                                "address is not stored.",
            "retention": "Sandbox data is reseeded each time the free instance restarts. Audit, "
                         "AI-call and review records are append-only while it runs.",
            "secrets": "ANTHROPIC_API_KEY is read from the environment only; never logged or returned.",
        },
        "limits": {
            "rate_limit_per_minute_per_visitor": config.RATE_LIMIT_PER_MINUTE,
            "daily_cap_per_visitor": config.DAILY_CAP_PER_VISITOR,
            "daily_cap_global": config.DAILY_CAP_GLOBAL,
            "daily_token_budget": config.DAILY_TOKEN_BUDGET,
            "max_tokens_per_call": config.MAX_TOKENS,
            "draft_max_tokens": config.DRAFT_MAX_TOKENS,
            "max_agent_turns": config.MAX_AGENT_TURNS,
            "max_request_bytes": config.MAX_REQUEST_BYTES,
            "max_question_chars": config.MAX_QUESTION_CHARS,
            "reset": "Daily caps reset 00:00 UTC",
        },
        "kill_switch": {**ks, "runtime_control": "enabled (admin token)" if config.ADMIN_TOKEN
                        else "locked (set ADMIN_TOKEN to enable; AI_ENABLED=false always works)"},
        "human_validation": governance.review_stats(s),
        "usage_today": {**u, "calls_by_outcome_all_time": outcomes},
        "review_gate": {"reviewer_token_required": bool(config.REVIEWER_TOKEN)},
    }


@app.post("/api/governance/kill-switch", tags=["Governance"])
def post_kill_switch(body: KillSwitchIn, s: Session = Depends(get_session),
                     x_admin_token: str | None = Header(default=None)):
    if not config.ADMIN_TOKEN:
        raise HTTPException(403, "Runtime kill switch is locked: set ADMIN_TOKEN, or set "
                                 "AI_ENABLED=false in the environment.")
    if x_admin_token != config.ADMIN_TOKEN:
        raise HTTPException(401, "Admin token required.")
    if governance.is_ai_identity(body.actor):
        raise HTTPException(403, "The kill switch must be operated by a named human.")
    return governance.set_kill_switch(s, body.engaged, body.actor.strip(), body.reason.strip())
