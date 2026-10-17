"""Deterministic engines: every rule in the module docstrings, checked with real arithmetic."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.engines import allocation, expertise, grants, taxonomy

AS_OF = date(2026, 10, 17)


# =====================================================================================
# Agent 1 -- Expertise Ingestion
# =====================================================================================

@pytest.mark.parametrize("label,expected", [
    ("Scrum Master", "Agile Delivery"), ("  SAFe ", "Agile Delivery"), ("RTE", "Agile Delivery"),
    ("PMP", "Project Management"), ("Power BI", "Analytics & BI"), ("ISO 26262", "Automotive Safety Standards"),
    ("FSMA", "Food Traceability"), ("Uniform Guidance", "Regulatory Compliance"),
    ("Insert Molding", "Injection Molding"), ("UL 514", "Product Safety Certification"),
    ("Mold Design", "Mold Design & Tooling"), ("Underwater Welding", None), ("Beekeeping", None)])
def test_normalize_maps_aliases_and_never_guesses(label, expected):
    assert taxonomy.normalize(label) == expected


def test_free_text_suggestions_skip_ambiguous_words_and_keep_evidence():
    found = taxonomy.find_in_text("Release Train Engineer for six years. Meeting at 3 pm; keep it safe. "
                                  "Ran predictive modeling for claims processing.")
    skills = {f["skill"]: f for f in found}
    assert set(skills) == {"Agile Delivery", "Machine Learning", "Claims Operations"}
    assert skills["Agile Delivery"]["matched"] == "release train engineer"     # longest alias wins
    assert "Release Train Engineer" in skills["Agile Delivery"]["evidence"]
    assert "Project Management" not in skills                                   # "pm" is not trusted in prose


@pytest.mark.parametrize("years,cert,expected", [
    (0, False, 1), ("1.9", False, 1), (2, False, 2), ("3.9", False, 2), (4, False, 3), ("6.9", False, 3),
    (7, False, 4), ("11.9", False, 4), (12, False, 5), (30, False, 5),
    (4, True, 4), (12, True, 5), (0, True, 2)])
def test_evidence_level_bands(years, cert, expected):
    assert expertise.evidence_level(years, cert)[0] == expected


@pytest.mark.parametrize("years,self_rating,cert,expected", [
    (3, 5, False, 3),      # evidence 2 -> capped at 3
    (24, 3, False, 3),     # modest self-rating is respected
    (24, 5, True, 5),
    (4, 5, True, 5),       # evidence 4 (3 + cert) -> 5 allowed
    (0, 1, False, 1),
])
def test_proficiency_caps_self_rating_at_one_above_evidence(years, self_rating, cert, expected):
    level, why = expertise.proficiency(years, self_rating, cert)
    assert level == expected
    assert why["formula"] == "level = min(self_rating, evidence_level + 1)"


def test_proficiency_rejects_bad_ratings():
    for bad in (0, 6, True, "4", None):
        with pytest.raises(expertise.IngestionError):
            expertise.proficiency(5, bad)


def test_experience_merges_overlapping_roles():
    roles = [{"start_year": 2006, "end_year": 2011}, {"start_year": 2011, "end_year": 2019},
             {"start_year": 2019, "end_year": None}]
    years, why = expertise.total_experience(roles, AS_OF)
    assert years == D("20")
    assert why["inputs"]["merged_spans"] == ["2006-2026"]


def test_experience_counts_gaps_and_same_year_roles():
    years, _ = expertise.total_experience([{"start_year": 2000, "end_year": 2005},
                                           {"start_year": 2010, "end_year": 2012},
                                           {"start_year": 2020, "end_year": 2020}], AS_OF)
    assert years == D("7.5")


def test_experience_rejects_impossible_roles():
    with pytest.raises(expertise.IngestionError):
        expertise.total_experience([{"start_year": 2015, "end_year": 2010}], AS_OF)
    with pytest.raises(expertise.IngestionError):
        expertise.total_experience([{"start_year": 2030}], AS_OF)


@pytest.mark.parametrize("years,band", [("9.5", "Experienced"), ("10", "Senior"), ("19.5", "Senior"),
                                        ("20", "Distinguished"), ("36", "Distinguished")])
def test_seniority_bands(years, band):
    assert expertise.seniority(D(years))[0] == band


SUB = {"name": "Test Member", "hub_id": "HUB-GC", "headline": "Claims and data", "consent_profiling": True,
       "industries": ["Insurance"], "certifications": ["AIC"], "capacity_hours": 20,
       "roles": [{"title": "Adjuster", "start_year": 2010, "end_year": None}],
       "skills": [{"label": "claims processing", "years": 16, "self_rating": 5},
                  {"label": "Claims", "years": 3, "self_rating": 3},          # duplicate, weaker: dropped
                  {"label": "ETL", "years": 5, "self_rating": 5},             # evidence 3 -> capped at 4
                  {"label": "Basket Weaving", "years": 9, "self_rating": 4}],
       "resume_text": "Built fraud analytics dashboards in Tableau."}


def test_ingest_structures_a_submission():
    p = expertise.ingest(SUB, AS_OF)
    by = {k["skill"]: k for k in p["skills"]}
    assert set(by) == {"Claims Operations", "Data Engineering"}
    assert by["Claims Operations"]["years"] == "16" and by["Claims Operations"]["level"] == 5
    assert by["Data Engineering"]["level"] == 4
    assert p["unmapped_skills"] == ["Basket Weaving"]
    assert p["total_years"] == "16" and p["seniority"] == "Senior"
    # free text only SUGGESTS skills
    assert {x["skill"] for x in p["suggested_skills"]} == {"Fraud Detection", "Analytics & BI"}
    # completeness: everything except "skills >= 3" (25) and "all mapped" (10)
    assert p["completeness_pct"] == 65


def test_ingest_requires_explicit_consent():
    for consent in (False, None, "yes", 1):
        with pytest.raises(expertise.IngestionError, match="Consent required"):
            expertise.ingest({**SUB, "consent_profiling": consent}, AS_OF)


def test_coverage_statuses():
    needs = [{"project_id": "P1", "skill": "Agronomy", "min_level": 4, "hours_per_week": "10"},
             {"project_id": "P2", "skill": "Agronomy", "min_level": 3, "hours_per_week": "5"},
             {"project_id": "P1", "skill": "IoT Systems", "min_level": 3, "hours_per_week": "8"},
             {"project_id": "P1", "skill": "Machine Learning", "min_level": 3, "hours_per_week": "4"}]
    profiles = [
        {"member_id": "A", "hub_id": "H", "skills": {"Agronomy": 5, "IoT Systems": 4}, "available_hours": "6",
         "assigned_by_skill": {"Agronomy": "4"}},
        {"member_id": "B", "hub_id": "H", "skills": {"Agronomy": 3, "IoT Systems": 3}, "available_hours": "10"},
        {"member_id": "C", "hub_id": "H", "skills": {"Machine Learning": 2}, "available_hours": "20"}]
    c = {r["skill"]: r for r in expertise.coverage(needs, profiles)["skills"]}
    ag = c["Agronomy"]
    assert ag["min_level"] == 4 and ag["demand_hours"] == "15"        # strictest level, summed demand
    assert ag["qualified_members"] == ["A"] and ag["available_hours"] == "10.00"
    assert ag["status"] == "GAP" and ag["shortfall_hours"] == "5.00"
    assert c["IoT Systems"]["status"] == "COVERED"                     # A 6 + B 10 >= 8
    assert c["Machine Learning"]["status"] == "GAP"                    # nobody at level 3
    single = expertise.coverage([{"project_id": "P", "skill": "Agronomy", "min_level": 5,
                                  "hours_per_week": "2"}], profiles)["skills"][0]
    assert single["status"] == "SINGLE_POINT_OF_FAILURE"


# =====================================================================================
# Agent 2 -- Grant-Writing
# =====================================================================================

OPP = {"id": "FO-X", "funder": "Fictional Fund", "program": "Test", "funder_type": "FEDERAL",
       "min_award": D("250000"), "max_award": D("750000"), "deadline": date(2026, 11, 20),
       "performance_months": 36, "eligible_entities": ["NONPROFIT_501C3", "COOPERATIVE"],
       "value_streams": ["AGRICULTURE"],
       "focus_keywords": ["supply chain", "traceability", "inventory forecasting", "resilience",
                          "manufacturing", "small business"],
       "cost_share_pct": D("20"), "indirect_cap_pct": D("10"), "requires_sam": True,
       "required_sections": ["Project Summary", "Budget Justification"]}
PROJ = {"id": "P-X", "hub_id": "HUB-GP", "value_stream": "AGRICULTURE",
        "keywords": ["traceability", "supply chain", "food safety", "resilience", "small business"],
        "start_date": date(2026, 11, 1), "end_date": date(2028, 4, 30), "matching_funds": "80000"}
LINES = [{"category": "PERSONNEL", "budgeted": D("160000")}, {"category": "CONTRACTUAL", "budgeted": D("50000")},
         {"category": "EQUIPMENT", "budgeted": D("35000")}, {"category": "TRAVEL", "budgeted": D("15000")},
         {"category": "SUPPLIES", "budgeted": D("10000")}]
COALITION = {"entity_type": "NONPROFIT_501C3", "sam_registered": True, "indirect_rate_pct": D("15")}


def test_budget_build_uses_mtdc_and_the_funders_cap():
    b = grants.budget(OPP, PROJ, LINES, D("15"))
    assert b["direct_costs"] == "270000.00"
    assert b["mtdc"] == "235000.00"                       # equipment excluded
    assert b["indirect_rate_pct"] == "10"                 # min(15, cap 10)
    assert b["indirect_costs"] == "23500.00"
    assert b["total_need"] == "293500.00"
    assert b["request"] == "293500.00" and not b["capped_at_max"]
    assert b["cost_share_required"] == "58700.00"         # 20% of the request


def test_budget_without_cap_and_capped_request():
    opp = {**OPP, "indirect_cap_pct": None, "max_award": D("300000")}
    b = grants.budget(opp, PROJ, LINES, D("15"))
    assert b["indirect_costs"] == "35250.00" and b["total_need"] == "305250.00"
    assert b["request"] == "300000.00" and b["capped_at_max"]


def test_eligibility_passes_and_alignment_scores():
    a = grants.assess(OPP, PROJ, LINES, COALITION, [True, True, True], AS_OF)
    assert a["eligibility"]["eligible"] and a["eligibility"]["warnings"] == []
    al = a["alignment"]
    assert al["components"] == {"focus": "26.7", "team": "25.0", "budget_fit": "20", "timeline": "15"}
    assert al["score"] == "86.7" and al["recommendation"] == "PURSUE"
    assert al["matched_keywords"] == ["resilience", "small business", "supply chain", "traceability"]


@pytest.mark.parametrize("change,failed", [
    ({"coalition": {**COALITION, "entity_type": "LLC"}}, "entity_type"),
    ({"coalition": {**COALITION, "sam_registered": False}}, "sam_registration"),
    ({"opp": {**OPP, "deadline": date(2026, 10, 16)}}, "deadline"),
    ({"project": {**PROJ, "value_stream": "INSURANCE"}}, "value_stream"),
    ({"opp": {**OPP, "min_award": D("300000")}}, "minimum_award"),
    ({"project": {**PROJ, "matching_funds": "58699.99"}}, "cost_share"),
])
def test_each_eligibility_rule_can_fail_on_its_own(change, failed):
    a = grants.assess(change.get("opp", OPP), change.get("project", PROJ), LINES,
                      change.get("coalition", COALITION), [True], AS_OF)
    assert not a["eligibility"]["eligible"]
    assert [c["check"] for c in a["eligibility"]["checks"] if not c["passed"]] == [failed]
    assert a["alignment"]["recommendation"] == "NOT ELIGIBLE"


def test_sam_is_only_checked_when_required():
    a = grants.assess({**OPP, "requires_sam": False}, PROJ, LINES,
                      {**COALITION, "sam_registered": False}, [True], AS_OF)
    assert a["eligibility"]["eligible"]


def test_warnings_do_not_block():
    a = grants.assess({**OPP, "deadline": date(2026, 10, 28), "performance_months": 12,
                       "max_award": D("280000")}, PROJ, LINES, COALITION, [True], AS_OF)
    w = " ".join(a["eligibility"]["warnings"])
    assert a["eligibility"]["eligible"]
    assert "within 14 days" in w and "capped" in w and "period of performance" in w
    assert a["alignment"]["components"]["timeline"] == "3" and a["alignment"]["components"]["budget_fit"] == "10"


def test_section_states_and_readiness():
    hs = {"Statement of Need": {"text": "x"}}
    assert grants.section_state("Budget Justification", hs, None) == "GENERATED"
    assert grants.section_state("Statement of Need", hs, "PENDING_REVIEW") == "HUMAN_AUTHORED"
    assert grants.section_state("Project Narrative", hs, "PENDING_REVIEW") == "AI_DRAFT_PENDING_REVIEW"
    assert grants.section_state("Project Narrative", hs, "APPROVED_WITH_EDITS") == "AI_DRAFT_APPROVED"
    assert grants.section_state("Project Narrative", hs, "REJECTED") == "AI_DRAFT_REJECTED"
    assert grants.section_state("Evaluation Plan", hs, None) == "MISSING"
    req = ["Budget Justification", "Project Narrative"]
    assert not grants.readiness(req, {"Budget Justification": "GENERATED",
                                      "Project Narrative": "AI_DRAFT_PENDING_REVIEW"}, True)["ready"]
    assert grants.readiness(req, {"Budget Justification": "GENERATED",
                                  "Project Narrative": "AI_DRAFT_APPROVED"}, True)["ready"]
    assert not grants.readiness(req, {"Budget Justification": "GENERATED",
                                      "Project Narrative": "HUMAN_AUTHORED"}, False)["ready"]


def _app(spent, start=date(2026, 1, 1), end=date(2027, 6, 30)):
    return {"id": "GA-T", "awarded_amount": D("240000"), "grant_spent": D(spent), "award_start": start,
            "award_end": end}


def test_compliance_spend_pace_and_kpis():
    ms = [{"id": "1", "name": "Sensors", "due_date": date(2026, 6, 30), "completed_date": date(2026, 7, 8),
           "kpi_name": "Farms", "kpi_target": D("150"), "kpi_actual": D("120"), "higher_is_better": True},
          {"id": "2", "name": "Freight", "due_date": date(2027, 1, 30), "completed_date": None,
           "kpi_name": "Cost/unit", "kpi_target": D("1.80"), "kpi_actual": D("2.10"), "higher_is_better": False}]
    c = grants.compliance(_app("96000"), ms, [], AS_OF)
    assert c["spend"]["elapsed_pct"] == "53.0"          # 289 of 545 days
    assert c["spend"]["spend_pct"] == "40.0" and c["spend"]["burn"] == "ON_PACE"
    assert c["milestones"][0]["kpi"]["attainment_pct"] == "80.0" and not c["milestones"][0]["kpi"]["met"]
    assert c["milestones"][1]["kpi"]["attainment_pct"] == "85.7"   # lower is better: 1.80 / 2.10
    assert c["status"] == "AMBER"                       # completed milestone missed its KPI


@pytest.mark.parametrize("spent,burn,status", [("170000", "OVERSPENDING", "RED"),
                                               ("80000", "UNDERSPENDING", "AMBER"),
                                               ("127200", "ON_PACE", "GREEN")])
def test_compliance_burn_tolerance(spent, burn, status):
    c = grants.compliance(_app(spent), [], [], AS_OF)
    assert c["spend"]["burn"] == burn and c["status"] == status


def test_compliance_reports_and_milestones():
    reps = [{"period_end": date(2026, 6, 30), "due_date": date(2026, 7, 31), "filed_date": None},
            {"period_end": date(2026, 9, 30), "due_date": date(2026, 10, 30), "filed_date": None},
            {"period_end": date(2026, 3, 31), "due_date": date(2026, 4, 30), "filed_date": date(2026, 4, 29)}]
    ms = [{"id": "a", "name": "Late", "due_date": date(2026, 10, 1), "completed_date": None},
          {"id": "b", "name": "Soon", "due_date": date(2026, 10, 31), "completed_date": None},
          {"id": "c", "name": "Later", "due_date": date(2026, 11, 1), "completed_date": None}]
    c = grants.compliance(_app("127200"), ms, reps, AS_OF)
    assert [r["status"] for r in c["reports"]] == ["OVERDUE", "DUE_SOON", "FILED"]
    assert [m["status"] for m in c["milestones"]] == ["OVERDUE", "AT_RISK", "ON_TRACK"]
    assert c["status"] == "RED" and c["next_report_due"] == "2026-07-31"
    assert "Overall status" in grants.compliance_markdown(c, "Test")


# =====================================================================================
# Agent 3 -- Resource Allocation
# =====================================================================================

BUDGET = [{"category": "PERSONNEL", "budgeted": D("380000"), "committed": D("50000"), "spent": D("60000")},
          {"category": "EQUIPMENT", "budgeted": D("120000"), "committed": D("0"), "spent": D("35000")},
          {"category": "TRAVEL", "budgeted": D("20000"), "committed": D("0"), "spent": D("0")}]
PRJ = {"id": "P-AUT-02", "hub_id": "HUB-GL", "start_date": date(2026, 6, 1), "end_date": date(2027, 11, 30)}
MS = {"MS-1", "MS-2"}
REQ = {"hub_id": "HUB-GL", "needed_from": date(2026, 10, 20), "needed_to": date(2027, 1, 15),
       "milestone_ids": ["MS-1"],
       "lines": [{"category": "EQUIPMENT", "description": "Rig", "amount": "45000"},
                 {"category": "PERSONNEL", "description": "Engineer", "amount": "36000",
                  "skill": "Automotive Safety Standards", "hours_per_week": "10"}]}


def test_budget_position():
    p = allocation.budget_position(BUDGET)
    assert p["by_category"]["PERSONNEL"]["remaining"] == "270000.00"
    assert p["by_category"]["EQUIPMENT"]["remaining"] == "85000.00"
    assert p["totals"]["remaining"] == "375000.00"
    assert p["used_pct"] == "27.9"                    # 145000 / 520000 = 27.88


def test_valid_request_passes_every_check():
    v = allocation.validate(REQ, PRJ, BUDGET, MS, AS_OF)
    assert v["valid"] and v["status"] == "VALIDATED"
    assert {c["check"] for c in v["checks"]} == {"complete", "hub_owns_project", "timeline", "milestones",
                                                  "category_budget", "project_budget", "skills"}
    eq = next(r for r in v["by_category"] if r["category"] == "EQUIPMENT")
    assert eq["remaining_before"] == "85000.00" and eq["remaining_after"] == "40000.00"
    assert v["total_requested"] == "81000.00" and v["project_remaining_after"] == "294000.00"


def _failed(req, budget=BUDGET):
    v = allocation.validate(req, PRJ, budget, MS, AS_OF)
    assert not v["valid"] and v["status"] == "FAILED_VALIDATION"
    return {c["check"] for c in v["checks"] if not c["passed"]}


def test_category_overrun_fails():
    req = {**REQ, "lines": [{"category": "EQUIPMENT", "description": "Rig", "amount": "85000.01"}]}
    assert _failed(req) == {"category_budget"}


def test_exactly_the_remaining_amount_passes():
    req = {**REQ, "lines": [{"category": "EQUIPMENT", "description": "Rig", "amount": "85000"}]}
    assert allocation.validate(req, PRJ, BUDGET, MS, AS_OF)["valid"]


def test_category_without_a_budget_line_fails():
    req = {**REQ, "lines": [{"category": "OTHER", "description": "Misc", "amount": "10"}]}
    assert _failed(req) == {"category_budget"}


def test_project_total_can_fail_even_when_categories_pass():
    tight = [{**b, "committed": D(b["budgeted"]) - D(b["spent"])} for b in BUDGET]
    tight[2]["committed"] = D("0")                           # only TRAVEL has room: 20000
    req = {**REQ, "lines": [{"category": "TRAVEL", "description": "Trips", "amount": "20000.01"}]}
    assert _failed(req, tight) == {"category_budget", "project_budget"}


def test_hub_cannot_spend_another_hubs_project():
    assert _failed({**REQ, "hub_id": "HUB-GC"}) == {"hub_owns_project"}


@pytest.mark.parametrize("nf,nt", [(date(2026, 5, 1), date(2026, 12, 1)),     # before project start
                                   (date(2026, 11, 1), date(2028, 1, 1)),     # after project end
                                   (date(2026, 12, 1), date(2026, 11, 1)),    # reversed
                                   (date(2026, 7, 1), date(2026, 9, 30))])    # already over
def test_timeline_rules(nf, nt):
    assert _failed({**REQ, "needed_from": nf, "needed_to": nt}) == {"timeline"}


def test_milestones_are_mandatory_and_must_belong_to_the_project():
    assert _failed({**REQ, "milestone_ids": []}) == {"milestones"}
    assert _failed({**REQ, "milestone_ids": ["MS-1", "MS-OTHER"]}) == {"milestones"}


def test_incomplete_lines_and_bad_skills():
    assert "complete" in _failed({**REQ, "lines": []})
    assert "complete" in _failed({**REQ, "lines": [{"category": "TRAVEL", "description": "", "amount": "-5"}]})
    assert _failed({**REQ, "lines": [{"category": "PERSONNEL", "description": "x", "amount": "10",
                                      "skill": "Juggling", "hours_per_week": "5"}]}) == {"skills"}


def test_matching_scores_and_ordering():
    profiles = [
        {"member_id": "M-1", "name": "A", "hub_id": "HUB-GL", "skills": {"Vehicle Software": 3}, "available_hours": "15"},
        {"member_id": "M-2", "name": "B", "hub_id": "HUB-GC", "skills": {"Vehicle Software": 5}, "available_hours": "5"},
        {"member_id": "M-3", "name": "C", "hub_id": "HUB-GL", "skills": {"Vehicle Software": 2}, "available_hours": "40"},
        {"member_id": "M-4", "name": "D", "hub_id": "HUB-GL", "skills": {"Vehicle Software": 5}, "available_hours": "0"}]
    m = allocation.match("Vehicle Software", 3, "10", "HUB-GL", profiles)
    assert [(c["member_id"], c["score"]) for c in m["candidates"]] == [("M-1", "80.0"), ("M-2", "65.0")]
    assert m["eligible_count"] == 2      # below level and zero availability are excluded


def test_grounding_check_reports_plain_numbers():
    from app.engines.grounding import is_grounded
    ok, unknown = is_grounded("Request 293,500.00 for 80 farms over 1.50 years.", {"request": "293500.00", "x": 1.5})
    assert not ok and unknown == ["80"]


def test_manufacturing_is_a_value_stream_with_its_own_skills():
    assert "MANUFACTURING" in taxonomy.VALUE_STREAMS
    mfg = {s for s, (d, _) in taxonomy.TAXONOMY.items() if d == "Manufacturing"}
    assert {"Injection Molding", "Mold Design & Tooling", "Product Safety Certification",
            "Electrical Product Design", "Manufacturing Operations"} <= mfg
    found = {f["skill"] for f in taxonomy.find_in_text("Ran insert molding of contacts for wiring devices.")}
    assert found == {"Injection Molding", "Electrical Product Design"}


def test_a_manufacturing_project_is_assessed_against_a_manufacturing_funder():
    opp = {**OPP, "id": "FO-M", "value_streams": ["MANUFACTURING"], "requires_sam": False,
           "focus_keywords": ["injection molding", "tooling", "small business"], "cost_share_pct": D("0")}
    proj = {**PROJ, "id": "P-MFG", "value_stream": "MANUFACTURING",
            "keywords": ["injection molding", "tooling", "electrical"], "matching_funds": "0"}
    a = grants.assess(opp, proj, LINES, {**COALITION, "entity_type": "LLC", "sam_registered": False},
                      [True], AS_OF)
    assert a["eligibility"]["eligible"] is False                       # LLC not in OPP's list
    a = grants.assess({**opp, "eligible_entities": ["LLC"]}, proj, LINES,
                      {**COALITION, "entity_type": "LLC"}, [True], AS_OF)
    assert a["eligibility"]["eligible"] and a["alignment"]["matched_keywords"] == ["injection molding", "tooling"]
    vs = next(c for c in a["eligibility"]["checks"] if c["check"] == "value_stream")
    assert vs["passed"] and "MANUFACTURING" in vs["detail"]
