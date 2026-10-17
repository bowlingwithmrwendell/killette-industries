# Killette Industries Coalition Agents

**Three AI agents for a self-organizing, multi-industry coalition, built so that people stay in charge.**

Killette Industries is a coalition of highly experienced, multi-skilled professionals working through regional hubs under agile agreements, with decisions made by team consensus. This service is the coalition's AI infrastructure:

| # | Agent | What it does |
|---|---|---|
| 1 | **Expertise Ingestion** | Turns each member's own description of their background into a structured profile. It maps skills to a shared taxonomy, sets proficiency from evidence, merges overlapping roles and finds where the coalition's skills fall short of project demand. |
| 2 | **Grant-Writing** | Gathers project details from the hubs, checks eligibility against federal and private funders, scores alignment, builds the budget, assembles the proposal packet and tracks milestones and compliance for awarded grants. |
| 3 | **Resource Allocation** | Takes hub resource requests, runs **mandatory budget validation** against the project budget, matches members to the work, and releases funds only after a named human approves. |

Claude orchestrates, explains and drafts. Every number comes from deterministic, tested Python.

> **Killette Industries Coalition** · Architected by **Wendell Killette**, Coalition Architect and Founder. Companion to the [Equity Compensation Management Agent](https://github.com/bowlingwithmrwendell/equity-comp-agent) and the [PMO Delivery Agent](https://github.com/bowlingwithmrwendell/pmo-delivery-agent). **All data is fictional**, including every funder name.

---

## One platform, one data model

```
hubs ─┬─ members ── member_skills ── member_assignments     ← Agent 1 writes, Agents 2 & 3 read
      └─ hub_projects ─┬─ budget_lines                       ← Agent 3 validates and commits
                       ├─ project_milestones                 ← every resource request names one
                       ├─ skill_needs                        ← drives coverage, grant team scores, matching
                       ├─ grant_applications ─ grant_milestones, grant_reports   ← Agent 2
                       └─ resource_requests                  ← Agent 3

shared governance: audit_log · ai_call_log · ai_reviews · pending_actions · kill_switch_events
```

The agents feed each other. A newly ingested member can close a coverage gap, raise a project's grant **team** score, and appear as a candidate on a resource request, all without anyone re-entering data.

---

## The governance I applied

| Choice | What it means in this build |
|---|---|
| **Bounded authority** | Each agent can read only its own engines. None can change data, validate or approve a request, release funds or send anything. The grants agent can only *propose* a submission. |
| **Deterministic math** | Skill levels, experience years, coverage, eligibility, budgets, alignment scores, budget validation, match scores and compliance status are computed in Python with `Decimal`. The model is never asked for a figure. |
| **Consent gate** | No member is ingested, scored, matched or sent to the model without an explicit yes. Withdrawing consent deletes their skill records. Profiles hold no contact details, demographics or pay. |
| **Mandatory budget validation** | Every resource request is validated when it is submitted **and again at the moment of approval**, so money committed since cannot be spent twice. |
| **Grounding check** | Every number in an AI answer or draft is checked against the engine results it received. Invented figures are flagged, and an ungrounded output cannot be approved unchanged. |
| **Always human-validated** | Agent answers, grant-section drafts and member bios are all stored **`PENDING_REVIEW`**. A named human approves, edits then approves, or rejects them with a reason. AI drafts enter a proposal only after approval. The AI, agents, bots and automated systems are refused as reviewers and approvers. |
| **Audit trail** | Every AI call is logged with who (salted-hash visitor), when, model, tools, tokens and outcome. Every tool call, ingestion, consent change, validation, approval and decision is in the append-only audit log. |
| **Explainable** | Every score returns `explanation: {formula, inputs, reasoning}`. |
| **Limiter and kill switch** | Per-visitor rate limit and daily cap (shared by all three agents), global cap, token budget, `max_tokens`, turn limit, and request-size limits, all in `.env`. A kill switch can be set by environment or at runtime. Everything fails closed. |

---

## Agent 1 · Expertise Ingestion

| Endpoint | What it does |
|---|---|
| `POST /api/expertise/preview` | Run ingestion without saving (what the member sees first) |
| `POST /api/members/ingest` | Ingest a new member (**422 without consent**) |
| `PUT /api/members/{id}/profile` | Re-ingest after the member updates their skills |
| `POST /api/members/{id}/withdraw-consent` | Delete skill records; exclude from matching and AI |
| `GET /api/members`, `GET /api/members/{id}` | Profiles, with how every level was set |
| `GET /api/expertise/coverage` | Supply vs demand per skill: `GAP`, `SINGLE_POINT_OF_FAILURE`, `COVERED` |
| `GET /api/expertise/inventory`, `GET /api/taxonomy` | What the coalition knows; the shared skill vocabulary |
| `GET /api/expertise/match` | Rank members for a skill, level, hours and hub |
| `POST /api/members/{id}/bio-draft` | AI proposal bio from the structured profile (`PENDING_REVIEW`) |

**Rules, exactly as implemented**

* **Taxonomy.** Members write skills their own way ("SM", "scrum master", "RTE"). Each label is mapped to one canonical skill. Labels that don't map are kept as *unmapped* for a human to add, and are never guessed. Résumé text is scanned for skills, but those are only **suggestions** the member must confirm. Ambiguous words ("pm", "safe", "security") are not trusted in free text.
* **Evidence level** from years of hands-on use: under 2 → 1 · 2–4 → 2 · 4–7 → 3 · 7–12 → 4 · 12+ → 5. A certification adds 1 (maximum 5).
* **Proficiency** = `min(self_rating, evidence_level + 1)`. A member can rate themselves one level above the evidence. Evidence never raises a modest self-rating.
* **Experience.** Overlapping roles are merged so years aren't double-counted.
* **Seniority.** Under 10 years Experienced · 10–20 Senior · 20+ Distinguished.
* **Coverage.** A member is qualified if they consented and are at or above the project's minimum level. Supply is the hours they already give that skill plus their free hours. Fewer hours than demand is a `GAP`. Exactly one qualified person is a `SINGLE_POINT_OF_FAILURE`.

## Agent 2 · Grant-Writing

| Endpoint | What it does |
|---|---|
| `GET /api/grants/projects/{id}/details` | **Gather project details** from the hub: summary, objectives, budget, milestones, team, unstaffable needs |
| `GET /api/grants/projects/{id}/opportunities` | **Align with funders**: every opportunity ranked with eligibility, budget and score |
| `GET /api/grants/assess` | One project against one opportunity |
| `POST /api/grants/applications` | Start an application |
| `GET /api/grants/applications/{id}` | Proposal packet: each required section's state and readiness to submit |
| `POST /api/grants/applications/{id}/sections` | A named human writes a section |
| `POST /api/grants/applications/{id}/sections/draft` | Claude drafts a narrative section (`PENDING_REVIEW`; enters the packet only once approved) |
| `GET /api/grants/compliance` | **Milestone reports**: RED/AMBER/GREEN for every awarded grant |
| `GET /api/grants/applications/{id}/compliance?format=markdown` | Milestones, KPIs, spend vs time, report deadlines |

**Rules, exactly as implemented**

* **Eligibility** (each check is pass/fail): entity type accepted · SAM.gov registration if the funder requires it · deadline not passed · value stream funded · request at least the minimum award · **matching funds cover the cost share**. Warnings (deadline within 14 days, request capped at the maximum, project not fully funded, project longer than the period of performance) are shown but do not block.
* **Budget.** Direct costs come from the project budget. MTDC = direct costs minus equipment. The indirect rate is the coalition rate, capped at the funder's cap. Request = `min(direct + indirect, max award)`. Cost share = request × the funder's percentage.
* **Alignment (0–100):** focus keyword overlap 40 · share of skill needs the coalition can staff 25 · budget fit 20 · time to deadline 15. Scores of 75 or more are PURSUE, 55–74 CONSIDER, below 55 PASS. Any failed eligibility check is NOT ELIGIBLE.
* **Proposal packet.** Budget Justification, Team Qualifications, Timeline & Milestones and Eligibility Statement are **generated from data** and cannot be overwritten. Narrative sections are written by a named human or drafted by Claude and approved by a human. An application becomes `READY_FOR_SUBMISSION` only when it is eligible and every required section is ready. Submission is an outbound action that needs a named approver.
* **Compliance.** A milestone is OVERDUE when past due and incomplete, and AT_RISK when due within 14 days. KPI attainment is actual ÷ target, inverted when lower is better. Burn is spend % minus time-elapsed %, flagged beyond ±15 points. An overdue report, overdue milestone or overspending makes the grant RED. An at-risk milestone, report due soon, underspending or a missed KPI makes it AMBER.

## Agent 3 · Resource Allocation

| Endpoint | What it does |
|---|---|
| `POST /api/resources/requests` | Submit a request (project, resources, timeline, milestones). **Validation runs immediately.** |
| `GET /api/resources/requests[/{id}]` | Requests with every validation check |
| `POST /api/resources/requests/{id}/validate` | Re-run validation against the current budget |
| `GET /api/resources/requests/{id}/matches` | Ranked members for each personnel line |
| `POST /api/resources/requests/{id}/decision` | A named human approves (re-validates, commits budget, releases funds, records assignments) or rejects with a reason |
| `GET /api/resources/projects/{id}/budget` | Budgeted, committed, spent and remaining by category |

**Mandatory budget validation.** Every check must pass:

* **complete:** every line has a category, description and amount above 0
* **hub_owns_project:** the requesting hub owns the project, so hubs spend only their own budgets
* **timeline:** the period is in order, inside the project dates and not already over
* **milestones:** at least one is named, and all belong to the project
* **category_budget:** each category's request is at most budgeted − committed − spent
* **project_budget:** the total is within the project's remaining budget
* **skills:** personnel skills are taxonomy skills, with hours above 0

Only a `VALIDATED` request can be decided. Approval **re-validates first**, and if the budget has moved, approval is blocked and the request returns to `FAILED_VALIDATION`. Decisions use compare-and-set, so two approvers cannot both release funds. Assignments are checked for consent, level and free hours before anything is committed.

**Matching.** Score = `50 × level/5 + 20 if in the requesting hub + 30 × min(1, free hours ÷ hours needed)`. Only active, consenting members at or above the minimum level with free hours are considered.

---

## The three agents

`POST /api/agents/{expertise|grants|resources}/ask` returns a **draft** answer, stored `PENDING_REVIEW`. Each agent has its own system prompt and tool list. One agent cannot call another's tools, and none has a tool that writes. Review drafts at `/api/reviews` or on the **Human review** tab.

## Run it

```bash
cp .env.example .env            # add ANTHROPIC_API_KEY to enable the agents (optional)
docker compose up --build       # PostgreSQL + API on http://localhost:8000
```

or without Docker:

```bash
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload   # SQLite, seeded on start
python -m pytest -q             # 137 tests, no API key needed
```

* Console at `/`, with tabs for each agent, Human review, and Approvals & audit.
* Governance scorecard at `/governance`.
* Interactive API docs at `/docs`.

**Deploy:** `render.yaml` is a one-click Render Blueprint (free Docker web service). Set `ANTHROPIC_API_KEY` in the dashboard.

## Decisions for the founder

These are set in `.env` and are worth deciding before real use:

1. **Legal entity** (`COALITION_ENTITY_TYPE`). This decides which grants the coalition can apply for at all. The demo assumes a 501(c)(3). Switching to `COOPERATIVE` opens the co-op fund and closes others.
2. **SAM.gov registration and UEI** (`COALITION_SAM_REGISTERED`). This is required for every federal opportunity.
3. **Indirect cost rate** (`COALITION_INDIRECT_RATE_PCT`). This is a negotiated rate or the de minimis rate.
4. **Approval thresholds.** Today any named human can approve any request. A natural next step is dollar thresholds for hub-level versus coalition-level sign-off.

> The append-only rule is enforced in the application. For a production deployment, add a PostgreSQL `REVOKE UPDATE, DELETE` or a trigger on `audit_log`, `ai_call_log` and `kill_switch_events`.
