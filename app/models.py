"""Database schema (PostgreSQL in Docker / Render, SQLite for local tests).

One shared data model serves all three coalition agents:

  hubs ─┬─ members ── member_skills            (Agent 1: Expertise Ingestion)
        │     └── member_assignments
        └─ hub_projects ─┬─ budget_lines       (shared by Agents 2 and 3)
                         ├─ project_milestones
                         ├─ skill_needs
                         ├─ grant_applications ── grant_milestones   (Agent 2)
                         └─ resource_requests                       (Agent 3)

Money is stored as NUMERIC(14,2) and read back as Decimal -- never float.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- structure ---------------------------------------------------------------------

class HubRow(Base):
    __tablename__ = "hubs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(120))
    facilitator: Mapped[str] = mapped_column(String(120))
    # The hub's working agreement, kept short: the agile agreement that preserves autonomy.
    working_agreement: Mapped[list] = mapped_column(JSON, default=list)


# --- Agent 1: expertise ---------------------------------------------------------------

class MemberRow(Base):
    """A coalition member. Contact details are deliberately NOT stored here
    (data minimisation): the profile holds only what matching needs."""
    __tablename__ = "members"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    hub_id: Mapped[str] = mapped_column(ForeignKey("hubs.id"), index=True)
    headline: Mapped[str] = mapped_column(String(200), default="")
    industries: Mapped[list] = mapped_column(JSON, default=list)
    certifications: Mapped[list] = mapped_column(JSON, default=list)
    # Role history as [{"title", "organization", "industry", "start_year", "end_year"|null}]
    roles: Mapped[list] = mapped_column(JSON, default=list)
    capacity_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("20"))
    # Consent gate: no profiling, matching or AI use without an explicit yes.
    consent_profiling: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")   # ACTIVE | WITHDRAWN
    # Engine outputs, recomputed on every ingestion
    total_years: Mapped[Decimal] = mapped_column(Numeric(5, 1), default=Decimal("0"))
    seniority: Mapped[str] = mapped_column(String(24), default="")
    completeness_pct: Mapped[int] = mapped_column(Integer, default=0)
    unmapped_skills: Mapped[list] = mapped_column(JSON, default=list)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemberSkillRow(Base):
    __tablename__ = "member_skills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), index=True)
    skill: Mapped[str] = mapped_column(String(80))           # canonical taxonomy name
    domain: Mapped[str] = mapped_column(String(60))
    raw_label: Mapped[str] = mapped_column(String(120))     # what the member wrote
    years: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    self_rating: Mapped[int] = mapped_column(Integer)
    certified: Mapped[bool] = mapped_column(Boolean, default=False)
    level: Mapped[int] = mapped_column(Integer)             # engine-computed 1-5
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)


class MemberAssignmentRow(Base):
    __tablename__ = "member_assignments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("hub_projects.id"), index=True)
    skill: Mapped[str] = mapped_column(String(80))
    hours_per_week: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    source_request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


# --- projects and budgets (shared) ------------------------------------------------------

class HubProjectRow(Base):
    __tablename__ = "hub_projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    hub_id: Mapped[str] = mapped_column(ForeignKey("hubs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    value_stream: Mapped[str] = mapped_column(String(40))    # INSURANCE | AUTOMOTIVE | AGRICULTURE
    capability: Mapped[str] = mapped_column(String(120))     # e.g. "Automated Claims Processing"
    summary: Mapped[str] = mapped_column(Text)
    objectives: Mapped[list] = mapped_column(JSON, default=list)
    beneficiaries: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    lead_member_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Non-grant funds pledged to the project (partner cash, in-kind member hours).
    # Grant cost-share requirements are checked against this.
    matching_funds: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))


class BudgetLineRow(Base):
    """One line of a hub project budget. remaining = budgeted - committed - spent."""
    __tablename__ = "budget_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("hub_projects.id"), index=True)
    category: Mapped[str] = mapped_column(String(24))   # PERSONNEL | EQUIPMENT | TRAVEL | SUPPLIES | CONTRACTUAL | OTHER
    budgeted: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    committed: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    spent: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))


class ProjectMilestoneRow(Base):
    __tablename__ = "project_milestones"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("hub_projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[date] = mapped_column(Date)
    completed_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class SkillNeedRow(Base):
    __tablename__ = "skill_needs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("hub_projects.id"), index=True)
    skill: Mapped[str] = mapped_column(String(80))
    min_level: Mapped[int] = mapped_column(Integer, default=3)
    hours_per_week: Mapped[Decimal] = mapped_column(Numeric(6, 2))


# --- Agent 2: grants -------------------------------------------------------------------

class FundingOpportunityRow(Base):
    __tablename__ = "funding_opportunities"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    funder: Mapped[str] = mapped_column(String(200))
    program: Mapped[str] = mapped_column(String(200))
    funder_type: Mapped[str] = mapped_column(String(16))     # FEDERAL | STATE | PRIVATE
    min_award: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    max_award: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    deadline: Mapped[date] = mapped_column(Date)
    performance_months: Mapped[int] = mapped_column(Integer)
    eligible_entities: Mapped[list] = mapped_column(JSON, default=list)
    value_streams: Mapped[list] = mapped_column(JSON, default=list)
    focus_keywords: Mapped[list] = mapped_column(JSON, default=list)
    cost_share_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    indirect_cap_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    requires_sam: Mapped[bool] = mapped_column(Boolean, default=False)
    required_sections: Mapped[list] = mapped_column(JSON, default=list)
    reporting: Mapped[str] = mapped_column(String(16), default="QUARTERLY")   # MONTHLY | QUARTERLY | SEMIANNUAL


class GrantApplicationRow(Base):
    __tablename__ = "grant_applications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("funding_opportunities.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("hub_projects.id"), index=True)
    # DRAFT | READY_FOR_SUBMISSION | SUBMITTED | AWARDED | DECLINED
    status: Mapped[str] = mapped_column(String(24), default="DRAFT")
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    awarded_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    award_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    award_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    grant_spent: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # {section_name: review_id} -- AI-drafted narrative sections live in the review queue
    section_reviews: Mapped[dict] = mapped_column(JSON, default=dict)
    # {section_name: {"text", "author", "at"}} -- sections written directly by a named human
    human_sections: Mapped[dict] = mapped_column(JSON, default=dict)


class GrantMilestoneRow(Base):
    __tablename__ = "grant_milestones"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("grant_applications.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[date] = mapped_column(Date)
    completed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    kpi_name: Mapped[str] = mapped_column(String(120), default="")
    kpi_target: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    kpi_actual: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    higher_is_better: Mapped[bool] = mapped_column(Boolean, default=True)


class GrantReportRow(Base):
    """Funder progress reports that have been filed (or are owed)."""
    __tablename__ = "grant_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("grant_applications.id"), index=True)
    period_end: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    filed_date: Mapped[date | None] = mapped_column(Date, nullable=True)


# --- Agent 3: resource requests ----------------------------------------------------------

class ResourceRequestRow(Base):
    """A hub's request for resources against one of its projects.

    lines: [{"category", "description", "amount", "skill"?, "hours_per_week"?, "min_level"?}]
    Lifecycle:
      SUBMITTED ─► validate ─┬─► VALIDATED ─► named human ─┬─► APPROVED (budget committed, funds released)
                             │                             └─► REJECTED (reason)
                             └─► FAILED_VALIDATION (reasons; can be revised and resubmitted)
    """
    __tablename__ = "resource_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hub_id: Mapped[str] = mapped_column(ForeignKey("hubs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("hub_projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    requested_by: Mapped[str] = mapped_column(String(120))
    lines: Mapped[list] = mapped_column(JSON, default=list)
    needed_from: Mapped[date] = mapped_column(Date)
    needed_to: Mapped[date] = mapped_column(Date)
    milestone_ids: Mapped[list] = mapped_column(JSON, default=list)
    justification: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="SUBMITTED", index=True)
    validation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignments: Mapped[list] = mapped_column(JSON, default=list)


# --- governance (shared) --------------------------------------------------------------------

class PendingActionRow(Base):
    """Every outbound action (grant submission, hub notice) waits here for a human."""
    __tablename__ = "pending_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_type: Mapped[str] = mapped_column(String(40))
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    requested_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditRow(Base):
    """Append-only trail of every agent tool call, data change and approval decision."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    actor: Mapped[str] = mapped_column(String(120))
    event: Mapped[str] = mapped_column(String(60))
    detail: Mapped[dict] = mapped_column(JSON)


class AICallRow(Base):
    """One row per model call attempt: who, when, model, tools, tokens, outcome."""
    __tablename__ = "ai_call_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    visitor: Mapped[str] = mapped_column(String(64), index=True)   # salted hash of the client address
    requested_by: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(32))                  # EXPERTISE_ASK | GRANTS_ASK | RESOURCES_ASK | GRANT_SECTION | MEMBER_BIO
    model: Mapped[str] = mapped_column(String(80))
    tools: Mapped[list] = mapped_column(JSON, default=list)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # OK | ERROR | RATE_LIMITED | CAP_REACHED | KILL_SWITCH | UNAVAILABLE | CONSENT_REQUIRED
    outcome: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")
    review_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AIReviewRow(Base):
    """Every AI output waits here, PENDING_REVIEW, until a named human decides.

    `ai_output` is the model's original text and is never changed. A reviewer
    may approve it, edit-then-approve (the edit goes to `final_output`), or
    reject it with a reason. Only APPROVED / APPROVED_WITH_EDITS outputs are
    ever published.
    """
    __tablename__ = "ai_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    kind: Mapped[str] = mapped_column(String(32))
    agent: Mapped[str] = mapped_column(String(24), default="")
    subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt: Mapped[str] = mapped_column(Text)
    ai_output: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(80))
    tools_used: Mapped[list] = mapped_column(JSON, default=list)
    grounded: Mapped[bool] = mapped_column(Boolean, default=False)
    unverified_numbers: Mapped[list] = mapped_column(JSON, default=list)
    requested_by: Mapped[str] = mapped_column(String(120), default="")
    visitor: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(24), default="PENDING_REVIEW", index=True)
    final_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class KillSwitchEventRow(Base):
    """Append-only history of runtime kill-switch changes. Latest row wins."""
    __tablename__ = "kill_switch_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    engaged: Mapped[bool] = mapped_column(Boolean)
    actor: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text, default="")


# --- append-only enforcement ---------------------------------------------------
# The ORM refuses to UPDATE or DELETE log rows. (Tests drop and recreate tables,
# which is DDL and unaffected.)
from sqlalchemy import event  # noqa: E402


class AppendOnlyViolation(RuntimeError):
    pass


def _refuse(kind):
    def handler(mapper, connection, target):
        raise AppendOnlyViolation(f"{type(target).__tablename__} is append-only; {kind} refused")
    return handler


for _cls in (AuditRow, AICallRow, KillSwitchEventRow):
    event.listen(_cls, "before_update", _refuse("UPDATE"))
    event.listen(_cls, "before_delete", _refuse("DELETE"))
