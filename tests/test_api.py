"""End-to-end workflows for the three agents through the HTTP API."""
from decimal import Decimal as D

from app.models import BudgetLineRow, MemberAssignmentRow, MemberSkillRow


def test_health_and_overview(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["status_date"] == "2026-10-17" and not h["agents_enabled"]
    o = client.get("/api/overview").json()
    assert [x["id"] for x in o["hubs"]] == ["HUB-GC", "HUB-GL", "HUB-GP"]
    assert o["members_active"] == 16 and o["funding_opportunities"] == 10 and o["grants_awarded"] == 2
    home = client.get("/")
    assert home.status_code == 200 and "Stronger together. Greater impact." in home.text
    assert client.get("/console").status_code == 200 and client.get("/governance").status_code == 200
    for asset in ("logo.png", "banner.jpg", "killette-industries-overview.jpg"):
        assert client.get(f"/static/brand/{asset}").status_code == 200
    assert 'href="/console"' in home.text


# =====================================================================================
# Agent 1 -- Expertise Ingestion
# =====================================================================================

NEW = {"hub_id": "HUB-GL", "name": "Marisol Vega", "headline": "Cybersecurity lead for connected vehicles",
       "industries": ["Automotive"], "certifications": ["CISSP"], "capacity_hours": 20,
       "consent_profiling": True,
       "roles": [{"title": "Security Engineer", "start_year": 2009, "end_year": 2017},
                 {"title": "Vehicle Security Lead", "start_year": 2015, "end_year": None}],
       "skills": [{"label": "CISSP", "years": 14, "self_rating": 5, "certified": True},
                  {"label": "AUTOSAR", "years": 6, "self_rating": 4},
                  {"label": "Kubernetes", "years": 2, "self_rating": 5}],
       "resume_text": "Led ISO 26262 safety cases for ECU software.", "submitted_by": "Marcus Oyelaran"}


def test_preview_does_not_store(client):
    r = client.post("/api/expertise/preview", json=NEW).json()
    assert r["total_years"] == "17" and r["seniority"] == "Senior"
    assert {k["skill"]: k["level"] for k in r["skills"]} == {"Cybersecurity": 5, "Vehicle Software": 4,
                                                              "Cloud & DevOps": 3}
    assert [s["skill"] for s in r["suggested_skills"]] == ["Automotive Safety Standards"]
    assert len(client.get("/api/members").json()) == 16


def test_ingest_member_closes_a_coverage_gap(client):
    before = {k["skill"]: k for k in client.get("/api/expertise/coverage").json()["skills"]}
    assert before["Cybersecurity"]["status"] == "GAP"
    r = client.post("/api/members/ingest", json=NEW)
    assert r.status_code == 201 and r.json()["member_id"] == "M-017"
    after = {k["skill"]: k for k in client.get("/api/expertise/coverage").json()["skills"]}
    assert after["Cybersecurity"]["qualified_members"] == ["M-017"]
    assert after["Cybersecurity"]["status"] == "SINGLE_POINT_OF_FAILURE"
    prof = client.get("/api/members/M-017").json()
    assert prof["consent_profiling"] and prof["skills"][0]["explanation"]["formula"]
    assert "email" not in str(prof).lower() and "phone" not in str(prof).lower()


def test_ingest_without_consent_is_refused(client):
    r = client.post("/api/members/ingest", json={**NEW, "consent_profiling": False})
    assert r.status_code == 422 and "Consent required" in r.text
    assert len(client.get("/api/members").json()) == 16


def test_ingest_validates_hub(client):
    assert client.post("/api/members/ingest", json={**NEW, "hub_id": "HUB-XX"}).status_code == 404


def test_reingest_replaces_skills(client):
    r = client.put("/api/members/M-009/profile", json={**NEW, "name": "Hannah Weiss", "hub_id": "HUB-GL"})
    assert r.status_code == 200
    skills = {k["skill"] for k in client.get("/api/members/M-009").json()["skills"]}
    assert skills == {"Cybersecurity", "Vehicle Software", "Cloud & DevOps"}


def test_withdrawing_consent_removes_skills_and_matching(client, session):
    m = client.get("/api/expertise/match", params={"skill": "Food Traceability", "hub_id": "HUB-GP",
                                                    "min_level": 4, "hours_per_week": "4"}).json()
    assert [c["member_id"] for c in m["candidates"]] == ["M-013"]
    r = client.post("/api/members/M-013/withdraw-consent", json={"confirmed_by": "Evelyn Sorensen"}).json()
    assert r["status"] == "WITHDRAWN" and r["skills_removed"] == 3   # FSMA + Traceability merged
    assert session.query(MemberSkillRow).filter_by(member_id="M-013").count() == 0
    m = client.get("/api/expertise/match", params={"skill": "Food Traceability", "hub_id": "HUB-GP",
                                                    "min_level": 4, "hours_per_week": "4"}).json()
    assert m["candidates"] == []
    cov = {k["skill"]: k for k in client.get("/api/expertise/coverage").json()["skills"]}
    assert cov["Food Traceability"]["status"] == "GAP"


def test_non_consenting_member_is_listed_but_never_profiled(client):
    m = client.get("/api/members/M-016").json()
    assert not m["consent_profiling"] and m["skills"] == []


def test_taxonomy_inventory_and_filters(client):
    tax = client.get("/api/taxonomy").json()
    assert {d["domain"] for d in tax} >= {"Insurance", "Automotive", "Agriculture"}
    inv = client.get("/api/expertise/inventory").json()
    assert any(r["skill"] == "Machine Learning" and r["members"] == 2 for r in inv)
    ml = client.get("/api/members", params={"skill": "Machine Learning", "min_level": 5}).json()
    assert {m["id"] for m in ml} == {"M-002", "M-015"}
    assert len(client.get("/api/members", params={"hub_id": "HUB-GP"}).json()) == 6
    cov = client.get("/api/expertise/coverage", params={"value_stream": "AGRICULTURE"}).json()
    assert cov["value_stream"] == "AGRICULTURE"


def test_match_unknown_skill_is_409(client):
    r = client.get("/api/expertise/match", params={"skill": "Juggling", "hub_id": "HUB-GP"})
    assert r.status_code == 409


# =====================================================================================
# Agent 2 -- Grant-Writing
# =====================================================================================

def test_opportunities_ranked_for_a_project(client):
    r = client.get("/api/grants/projects/P-AGR-02/opportunities").json()
    top = r["opportunities"][0]
    assert top["opportunity_id"] == "FO-02" and top["alignment"]["recommendation"] == "PURSUE"
    assert top["budget"]["request"] == "293500.00"
    recs = {o["opportunity_id"]: o["alignment"]["recommendation"] for o in r["opportunities"]}
    assert recs["FO-06"] == recs["FO-05"] == "NOT ELIGIBLE"      # co-ops only; deadline passed
    failed = {c["check"] for c in next(o for o in r["opportunities"] if o["opportunity_id"] == "FO-06")
              ["eligibility"]["checks"] if not c["passed"]}
    assert failed == {"entity_type"}


def test_gather_project_details(client):
    d = client.get("/api/grants/projects/P-AGR-01/details").json()
    assert d["hub"]["id"] == "HUB-GP" and len(d["milestones"]) == 3
    assert {t["member_id"] for t in d["team"]} == {"M-011", "M-012", "M-015"}
    assert d["budget"]["totals"]["budgeted"] == "390000.00"


def test_full_application_workflow_to_human_approved_submission(client):
    pkt = client.get("/api/grants/applications/GA-003").json()
    assert pkt["status"] == "DRAFT" and not pkt["readiness"]["ready"]
    states = {x["section"]: x["state"] for x in pkt["sections"]}
    assert states["Project Summary"] == "HUMAN_AUTHORED" and states["Budget Justification"] == "GENERATED"
    missing = [k for k, v in states.items() if v == "MISSING"]
    assert missing == ["Statement of Need", "Project Narrative", "Evaluation Plan", "Sustainability Plan"]

    # an AI identity cannot author a section
    r = client.post("/api/grants/applications/GA-003/sections",
                    json={"section": "Statement of Need", "text": "x" * 40, "author": "Claude"})
    assert r.status_code == 403
    for sec in missing:
        r = client.post("/api/grants/applications/GA-003/sections",
                        json={"section": sec, "text": f"{sec} written by the hub team for the funder.",
                              "author": "Evelyn Sorensen"})
        assert r.status_code == 200
    pkt = client.get("/api/grants/applications/GA-003").json()
    assert pkt["status"] == "READY_FOR_SUBMISSION" and pkt["readiness"]["ready"]
    budget = next(x for x in pkt["sections"] if x["section"] == "Budget Justification")["content"]
    assert "$293,500.00" in budget and "$58,700.00" in budget

    # generated sections cannot be overwritten
    r = client.post("/api/grants/applications/GA-003/sections",
                    json={"section": "Budget Justification", "text": "y" * 40, "author": "Evelyn Sorensen"})
    assert r.status_code == 409

    a = client.post("/api/actions", json={"action_type": "SUBMIT_GRANT_APPLICATION",
                                          "payload": {"application_id": "GA-003"},
                                          "requested_by": "Evelyn Sorensen"})
    assert a.status_code == 201 and a.json()["status"] == "PENDING"
    assert client.get("/api/grants/applications/GA-003").json()["status"] == "READY_FOR_SUBMISSION"
    bad = client.post(f"/api/actions/{a.json()['id']}/decision", json={"approve": True, "decided_by": "agent"})
    assert bad.status_code == 409
    ok = client.post(f"/api/actions/{a.json()['id']}/decision",
                     json={"approve": True, "decided_by": "Ruth Lindqvist"}).json()
    assert ok["status"] == "EXECUTED" and "SUBMITTED" in ok["result"]
    assert client.get("/api/grants/applications/GA-003").json()["status"] == "SUBMITTED"


def test_a_draft_application_cannot_be_proposed_for_submission(client):
    r = client.post("/api/actions", json={"action_type": "SUBMIT_GRANT_APPLICATION",
                                          "payload": {"application_id": "GA-003"}, "requested_by": "x"})
    assert r.status_code == 422 and "DRAFT" in r.text


def test_create_application_and_duplicates(client):
    r = client.post("/api/grants/applications", json={"project_id": "P-INS-01", "opportunity_id": "FO-04",
                                                      "created_by": "Danielle Fontenot"})
    assert r.status_code == 201
    body = r.json()
    assert body["application_id"] == "GA-004"
    # $250,000 request x 25% cost share = $62,500, but the project has only $60,000 of match pledged
    elig = body["assessment"]["eligibility"]
    assert not elig["eligible"] and [c["check"] for c in elig["checks"] if not c["passed"]] == ["cost_share"]
    assert body["assessment"]["budget"]["cost_share_required"] == "62500.00"
    again = client.post("/api/grants/applications", json={"project_id": "P-INS-01", "opportunity_id": "FO-04",
                                                          "created_by": "Danielle Fontenot"})
    assert again.status_code == 409


def test_compliance_portfolio_and_report(client):
    port = {c["application_id"]: c for c in client.get("/api/grants/compliance").json()}
    assert port["GA-001"]["status"] == "AMBER" and port["GA-002"]["status"] == "RED"
    c = client.get("/api/grants/applications/GA-002/compliance").json()
    assert c["milestone_summary"]["OVERDUE"] == 1
    assert any("has not been filed" in r for r in c["reasons"])
    md = client.get("/api/grants/applications/GA-001/compliance", params={"format": "markdown"})
    assert md.status_code == 200 and "Spend vs time" in md.text
    assert client.get("/api/grants/applications/GA-003/compliance").status_code == 409


# =====================================================================================
# Agent 3 -- Resource Allocation
# =====================================================================================

def test_seeded_requests_were_validated_on_submission(client):
    reqs = {r["id"]: r for r in client.get("/api/resources/requests").json()}
    assert reqs[1]["status"] == "VALIDATED" and reqs[3]["status"] == "VALIDATED"
    assert reqs[2]["status"] == "FAILED_VALIDATION"
    cat = next(c for c in reqs[2]["validation"]["checks"] if c["check"] == "category_budget")
    assert "EQUIPMENT asks 54000.00 but only 30000.00 remains" in cat["detail"]


def test_approve_commits_budget_assigns_member_and_releases_funds(client, session):
    m = client.get("/api/resources/requests/1/matches").json()["matches"][0]
    assert m["candidates"][0]["member_id"] == "M-010"
    before = client.get("/api/resources/projects/P-AUT-02/budget").json()
    r = client.post("/api/resources/requests/1/decision",
                    json={"decision": "approve", "reviewer": "Marcus Oyelaran",
                          "assignments": [{"line_index": 1, "member_id": "M-010"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "APPROVED" and body["decided_by"] == "Marcus Oyelaran"
    assert body["assignments"] == [{"member_id": "M-010", "skill": "Automotive Safety Standards",
                                    "hours_per_week": "10"}]
    after = client.get("/api/resources/projects/P-AUT-02/budget").json()
    assert D(after["by_category"]["EQUIPMENT"]["committed"]) - D(before["by_category"]["EQUIPMENT"]["committed"]) == D("45000")
    assert D(after["by_category"]["PERSONNEL"]["committed"]) - D(before["by_category"]["PERSONNEL"]["committed"]) == D("36000")
    assert session.query(MemberAssignmentRow).filter_by(source_request_id=1).count() == 1
    events = [e["event"] for e in client.get("/api/audit").json()]
    assert "RESOURCE_REQUEST_APPROVED_FUNDS_RELEASED" in events
    # decisions are final
    again = client.post("/api/resources/requests/1/decision", json={"decision": "reject", "reviewer": "Danielle Fontenot",
                                                                     "reason": "changed my mind"})
    assert again.status_code == 409


def test_failed_validation_cannot_be_approved(client):
    r = client.post("/api/resources/requests/2/decision", json={"decision": "approve", "reviewer": "Ruth Lindqvist"})
    assert r.status_code == 409 and "FAILED_VALIDATION" in r.text


def test_ai_cannot_approve_and_reject_needs_reason(client):
    for name in ("Claude", "agent", "Allocation Bot", "auto-approver"):
        r = client.post("/api/resources/requests/3/decision", json={"decision": "approve", "reviewer": name})
        assert r.status_code == 403
    r = client.post("/api/resources/requests/3/decision", json={"decision": "reject", "reviewer": "Danielle Fontenot"})
    assert r.status_code == 422
    r = client.post("/api/resources/requests/3/decision",
                    json={"decision": "reject", "reviewer": "Danielle Fontenot", "reason": "Data licence not needed yet"})
    assert r.json()["status"] == "REJECTED"


def test_approval_revalidates_against_the_current_budget(client, session):
    """Two requests each fit the remaining TRAVEL budget alone, but not together."""
    base = {"hub_id": "HUB-GL", "project_id": "P-AUT-02", "requested_by": "Marcus Oyelaran",
            "needed_from": "2026-11-01", "needed_to": "2027-02-01", "milestone_ids": ["MS-AUT2-2"]}
    a = client.post("/api/resources/requests", json={**base, "title": "Trips A",
                    "lines": [{"category": "TRAVEL", "description": "Supplier visits", "amount": "15000"}]}).json()
    b = client.post("/api/resources/requests", json={**base, "title": "Trips B",
                    "lines": [{"category": "TRAVEL", "description": "Test-track visits", "amount": "8000"}]}).json()
    assert a["status"] == b["status"] == "VALIDATED"
    assert client.post(f"/api/resources/requests/{a['id']}/decision",
                       json={"decision": "approve", "reviewer": "Marcus Oyelaran"}).status_code == 200
    r = client.post(f"/api/resources/requests/{b['id']}/decision",
                    json={"decision": "approve", "reviewer": "Marcus Oyelaran"})
    assert r.status_code == 409 and "no longer passes budget validation" in r.text
    after = client.get(f"/api/resources/requests/{b['id']}").json()
    assert after["status"] == "FAILED_VALIDATION"
    travel = session.query(BudgetLineRow).filter_by(project_id="P-AUT-02", category="TRAVEL").one()
    assert travel.committed == D("15000")


def test_assignment_rules_on_approval(client):
    bad_level = client.post("/api/resources/requests/1/decision",
                            json={"decision": "approve", "reviewer": "Marcus Oyelaran",
                                  "assignments": [{"line_index": 1, "member_id": "M-008"}]})
    assert bad_level.status_code == 422
    no_consent = client.post("/api/resources/requests/1/decision",
                             json={"decision": "approve", "reviewer": "Marcus Oyelaran",
                                   "assignments": [{"line_index": 1, "member_id": "M-016"}]})
    assert no_consent.status_code == 422 and "consenting" in no_consent.text
    not_personnel = client.post("/api/resources/requests/1/decision",
                                json={"decision": "approve", "reviewer": "Marcus Oyelaran",
                                      "assignments": [{"line_index": 0, "member_id": "M-010"}]})
    assert not_personnel.status_code == 422
    assert client.get("/api/resources/requests/1").json()["status"] == "VALIDATED"   # nothing changed


def test_submitting_an_invalid_request_shows_every_reason(client):
    r = client.post("/api/resources/requests", json={
        "hub_id": "HUB-GC", "project_id": "P-AGR-01", "title": "Wrong hub", "requested_by": "Danielle Fontenot",
        "needed_from": "2027-05-01", "needed_to": "2027-09-01", "milestone_ids": [],
        "lines": [{"category": "OTHER", "description": "Something", "amount": "999999"}]})
    assert r.status_code == 201
    failed = {c["check"] for c in r.json()["validation"]["checks"] if not c["passed"]}
    assert failed == {"hub_owns_project", "timeline", "milestones", "category_budget", "project_budget"}


def test_explanations_everywhere(client):
    req = client.get("/api/resources/requests/1").json()
    assert set(req["validation"]["explanation"]) == {"formula", "inputs", "reasoning"}
    g = client.get("/api/grants/assess", params={"project_id": "P-AGR-02", "opportunity_id": "FO-02"}).json()
    for part in (g["budget"], g["eligibility"], g["alignment"]):
        assert set(part["explanation"]) == {"formula", "inputs", "reasoning"}
    cov = client.get("/api/expertise/coverage").json()["skills"][0]
    assert set(cov["explanation"]) == {"formula", "inputs", "reasoning"}


# =====================================================================================
# Bringing your own project: applicants, SBIR-style rules, add / export / import
# =====================================================================================

ROUGH = {
    "hub_id": "HUB-GC", "name": "Integrated Branch-Circuit Rough-In Box", "value_stream": "MANUFACTURING",
    "capability": "Product-to-Production",
    "summary": "Injection-molded electrical rough-in box with insert-molded bus contacts so devices snap in.",
    "objectives": ["Freeze a moldable design", "Pass safety listing", "Pilot run with a molder"],
    "keywords": ["injection molding", "tooling", "product safety", "innovation"],
    "start_date": "2026-12-01", "end_date": "2027-11-30", "matching_funds": "10000",
    "ip_status": "Patent pending",
    "applicant": {"name": "Example Innovations LLC", "entity_type": "LLC", "sam_registered": True,
                  "employees": 1, "us_owned_pct": 100, "pi_employed": True},
    "budget_lines": [{"category": "PERSONNEL", "budgeted": "90000"},
                     {"category": "CONTRACTUAL", "budgeted": "45000"},
                     {"category": "EQUIPMENT", "budgeted": "40000"},
                     {"category": "OTHER", "budgeted": "15000"}],
    "milestones": [{"name": "Design freeze", "due_date": "2027-01-29"},
                   {"name": "T1 samples", "due_date": "2027-04-30"}],
    "skill_needs": [{"skill": "Injection Molding", "min_level": 4, "hours_per_week": "8"}],
    "created_by": "Wendell Killette",
}


def test_create_project_with_its_own_applicant_and_sbir_checks(client):
    r = client.post("/api/projects", json=ROUGH)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["id"] == "P-MFG-02" and p["applicant"]["name"] == "Example Innovations LLC"
    assert [m["id"] for m in p["milestones"]] == ["MS-MFG-02-1", "MS-MFG-02-2"]
    sbir = client.get("/api/grants/assess", params={"project_id": "P-MFG-02", "opportunity_id": "FO-10"}).json()
    checks = {c["check"]: c["passed"] for c in sbir["eligibility"]["checks"]}
    assert checks == {"entity_type": True, "sam_registration": True, "small_business": True,
                      "us_ownership": True, "pi_employment": True, "subcontract_cap": True,
                      "deadline": True, "value_stream": True, "minimum_award": True, "cost_share": True}
    assert sbir["eligibility"]["eligible"]
    # the same project applying as the coalition (a 501(c)(3)) fails the for-profit rule
    as_coalition = client.post("/api/projects", json={**ROUGH, "applicant": None}).json()
    a = client.get("/api/grants/assess", params={"project_id": as_coalition["id"], "opportunity_id": "FO-10"}).json()
    failed = {c["check"] for c in a["eligibility"]["checks"] if not c["passed"]}
    assert {"entity_type", "small_business", "us_ownership", "pi_employment"} <= failed


def test_subcontract_cap_catches_too_much_outsourcing(client):
    heavy = {**ROUGH, "budget_lines": [{"category": "PERSONNEL", "budgeted": "40000"},
                                       {"category": "CONTRACTUAL", "budgeted": "120000"}]}
    pid = client.post("/api/projects", json=heavy).json()["id"]
    a = client.get("/api/grants/assess", params={"project_id": pid, "opportunity_id": "FO-10"}).json()
    cap = next(c for c in a["eligibility"]["checks"] if c["check"] == "subcontract_cap")
    assert not cap["passed"] and "33.00%" in cap["detail"]


def test_export_and_reimport_round_trip(client):
    exp = client.get("/api/projects/P-MFG-01/export").json()
    assert exp["applicant"]["entity_type"] == "LLC" and len(exp["budget_lines"]) == 5
    again = client.post("/api/projects", json=exp)
    assert again.status_code == 201
    assert client.get(f"/api/projects/{again.json()['id']}/export").json() == exp


def test_project_validation(client):
    assert client.post("/api/projects", json={**ROUGH, "hub_id": "HUB-XX"}).status_code == 404
    assert client.post("/api/projects", json={**ROUGH, "end_date": "2026-01-01"}).status_code == 409
    bad_skill = {**ROUGH, "skill_needs": [{"skill": "Juggling", "hours_per_week": "2"}]}
    assert client.post("/api/projects", json=bad_skill).status_code == 409
    assert client.post("/api/projects", json={**ROUGH, "budget_lines": []}).status_code == 422
    assert client.post("/api/projects", json={**ROUGH, "value_stream": "MINING"}).status_code == 422


def test_create_funding_opportunity(client):
    body = {"funder": "Example State Fund", "program": "Manufacturing Matching Grant", "funder_type": "STATE",
            "min_award": "25000", "max_award": "150000", "deadline": "2027-02-15", "performance_months": 12,
            "eligible_entities": ["LLC"], "value_streams": ["MANUFACTURING"],
            "focus_keywords": ["Injection Molding", "tooling"], "cost_share_pct": "50",
            "max_employees": 100, "max_subcontract_pct": "40"}
    r = client.post("/api/grants/opportunities", json=body)
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["id"] == "FO-11" and o["focus_keywords"] == ["injection molding", "tooling"]
    assert o["max_employees"] == 100 and o["requires_pi_employed"] is False
    bad = client.post("/api/grants/opportunities", json={**body, "max_award": "1000"})
    assert bad.status_code == 409


def test_meta_lists_form_choices(client):
    m = client.get("/api/meta").json()
    assert "MANUFACTURING" in m["value_streams"] and "CORPORATION" in m["entity_types"]
    assert "Injection Molding" in m["skills"]
