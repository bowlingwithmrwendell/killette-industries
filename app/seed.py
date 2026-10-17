"""Fictional demonstration coalition.

Every hub, member, project, funder and dollar figure here is invented for the
demo. Funder names are deliberately fictional and do not represent any real
agency or foundation. Members are ingested through the real ingestion engine,
so their levels and seniority are computed, not hard-coded.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from . import repository
from .models import (BudgetLineRow, FundingOpportunityRow, GrantApplicationRow, GrantMilestoneRow,
                     GrantReportRow, HubProjectRow, HubRow, MemberAssignmentRow, MemberRow,
                     ProjectMilestoneRow, SkillNeedRow)

D = Decimal

HUBS = [
    ("HUB-GC", "Gulf Coast Hub", "Houston to New Orleans corridor", "Danielle Fontenot",
     ["Decisions by consent: proposals pass unless someone raises a reasoned objection.",
      "Hub budget decisions stay in the hub; coalition-wide changes go to the cross-hub sync.",
      "Every resource request names the milestone it serves."]),
    ("HUB-GL", "Great Lakes Hub", "Detroit, Toledo and northern Ohio", "Marcus Oyelaran",
     ["Two-week cadence with a shared review open to every member.",
      "Conflicts go to a facilitated conversation within five working days.",
      "Members choose work; facilitators balance load."]),
    ("HUB-GP", "Great Plains Hub", "Kansas, Nebraska and western Missouri", "Ruth Lindqvist",
     ["Field work is planned around the growing season.",
      "Farmers on our projects have a seat in every review.",
      "Data collected on a farm belongs to that farm."]),
]

# Submissions go through the ingestion engine exactly as a new member's would.
MEMBERS = [
    # --- Gulf Coast (insurance) ---
    {"hub_id": "HUB-GC", "name": "Celeste Broussard", "headline": "Claims operations leader, 24 years",
     "industries": ["Insurance"], "certifications": ["AIC", "Six Sigma Green Belt"],
     "roles": [{"title": "Claims Adjuster", "organization": "Coastal Mutual", "industry": "Insurance",
                "start_year": 2002, "end_year": 2009},
               {"title": "Director, Claims Operations", "organization": "Bayou Indemnity",
                "industry": "Insurance", "start_year": 2009, "end_year": None}],
     "skills": [{"label": "Claims Processing", "years": 24, "self_rating": 5, "certified": True},
                {"label": "Fraud Analytics", "years": 9, "self_rating": 4},
                {"label": "Insurance Compliance", "years": 12, "self_rating": 4},
                {"label": "Lean Manufacturing", "years": 3, "self_rating": 3, "certified": True}],
     "capacity_hours": 20},
    {"hub_id": "HUB-GC", "name": "Tomás Villanueva", "headline": "Actuary turned ML practitioner",
     "industries": ["Insurance", "Financial Services"], "certifications": ["FCAS"],
     "roles": [{"title": "Pricing Actuary", "organization": "Lone Star Casualty", "industry": "Insurance",
                "start_year": 2004, "end_year": 2016},
               {"title": "Principal Data Scientist", "organization": "Riskline Analytics",
                "industry": "Insurance", "start_year": 2016, "end_year": None}],
     "skills": [{"label": "Actuarial", "years": 12, "self_rating": 5, "certified": True},
                {"label": "Machine Learning", "years": 10, "self_rating": 5},
                {"label": "Underwriting", "years": 6, "self_rating": 3},
                {"label": "Python", "years": 10, "self_rating": 4}],
     "capacity_hours": 15},
    {"hub_id": "HUB-GC", "name": "Aaliyah Mensah", "headline": "Agile delivery lead for regulated teams",
     "industries": ["Insurance", "Healthcare"], "certifications": ["PSM II", "PMP"],
     "roles": [{"title": "QA Analyst", "organization": "Pelican Health Plans", "industry": "Healthcare",
                "start_year": 2006, "end_year": 2011},
               {"title": "Scrum Master", "organization": "Coastal Mutual", "industry": "Insurance",
                "start_year": 2011, "end_year": 2019},
               {"title": "Release Train Engineer", "organization": "Bayou Indemnity", "industry": "Insurance",
                "start_year": 2019, "end_year": None}],
     "skills": [{"label": "Scrum Master", "years": 15, "self_rating": 5, "certified": True},
                {"label": "Project Management", "years": 10, "self_rating": 4, "certified": True},
                {"label": "QA", "years": 5, "self_rating": 3},
                {"label": "Facilitation", "years": 15, "self_rating": 5}],
     "capacity_hours": 20},
    {"hub_id": "HUB-GC", "name": "Robert Thibodeaux", "headline": "Integration engineer, claims platforms",
     "industries": ["Insurance", "Technology"], "certifications": ["AWS Solutions Architect"],
     "roles": [{"title": "Software Engineer", "organization": "Gulfstream Systems", "industry": "Technology",
                "start_year": 2008, "end_year": 2018},
               {"title": "Lead Integration Engineer", "organization": "Coastal Mutual", "industry": "Insurance",
                "start_year": 2018, "end_year": None}],
     "skills": [{"label": "Software Development", "years": 18, "self_rating": 5},
                {"label": "AWS", "years": 7, "self_rating": 4, "certified": True},
                {"label": "ETL", "years": 8, "self_rating": 4},
                {"label": "Cybersecurity", "years": 3, "self_rating": 4}],
     "capacity_hours": 25},
    {"hub_id": "HUB-GC", "name": "Imani Okafor", "headline": "Grants and compliance manager",
     "industries": ["Nonprofit", "Insurance"], "certifications": ["GPC", "CPA"],
     "roles": [{"title": "Staff Accountant", "organization": "Delta Community Fund", "industry": "Nonprofit",
                "start_year": 2007, "end_year": 2013},
               {"title": "Director of Grants", "organization": "Southern Housing Alliance",
                "industry": "Nonprofit", "start_year": 2013, "end_year": None}],
     "skills": [{"label": "Grant Writing", "years": 13, "self_rating": 5, "certified": True},
                {"label": "Uniform Guidance", "years": 11, "self_rating": 5},
                {"label": "Accounting", "years": 19, "self_rating": 4, "certified": True},
                {"label": "Beekeeping", "years": 4, "self_rating": 3}],
     "capacity_hours": 12},
    # --- Great Lakes (automotive) ---
    {"hub_id": "HUB-GL", "name": "Gregory Kowalczyk", "headline": "Tier 1 supply chain veteran",
     "industries": ["Automotive", "Manufacturing"], "certifications": ["CSCP", "Six Sigma Black Belt"],
     "roles": [{"title": "Buyer", "organization": "Lakeshore Components", "industry": "Automotive",
                "start_year": 1999, "end_year": 2008},
               {"title": "Director, Supplier Operations", "organization": "Maumee Drive Systems",
                "industry": "Automotive", "start_year": 2008, "end_year": None}],
     "skills": [{"label": "Tier 1 Supplier", "years": 27, "self_rating": 5},
                {"label": "Inventory Forecasting", "years": 15, "self_rating": 5, "certified": True},
                {"label": "Vendor Management", "years": 20, "self_rating": 5},
                {"label": "Six Sigma", "years": 12, "self_rating": 4, "certified": True}],
     "capacity_hours": 20},
    {"hub_id": "HUB-GL", "name": "Priya Raman", "headline": "ADAS software and functional safety",
     "industries": ["Automotive"], "certifications": ["ISO 26262 Functional Safety Engineer"],
     "roles": [{"title": "Embedded Engineer", "organization": "Huron Electronics", "industry": "Automotive",
                "start_year": 2009, "end_year": 2015},
               {"title": "ADAS Software Lead", "organization": "Wolverine Mobility", "industry": "Automotive",
                "start_year": 2015, "end_year": None}],
     "skills": [{"label": "ADAS", "years": 11, "self_rating": 5},
                {"label": "ISO 26262", "years": 8, "self_rating": 4, "certified": True},
                {"label": "Embedded Systems", "years": 17, "self_rating": 5},
                {"label": "Test Automation", "years": 6, "self_rating": 3}],
     "capacity_hours": 10},
    {"hub_id": "HUB-GL", "name": "Dwayne Hollins", "headline": "Plant operations and ASE master tech",
     "industries": ["Automotive"], "certifications": ["ASE Master Technician"],
     "roles": [{"title": "Master Technician", "organization": "Hollins Auto Care", "industry": "Automotive",
                "start_year": 1996, "end_year": 2010},
               {"title": "Plant Operations Manager", "organization": "Maumee Drive Systems",
                "industry": "Automotive", "start_year": 2010, "end_year": None}],
     "skills": [{"label": "Diagnostics", "years": 30, "self_rating": 5, "certified": True},
                {"label": "Plant Operations", "years": 16, "self_rating": 5},
                {"label": "Lean Manufacturing", "years": 10, "self_rating": 4},
                {"label": "Underwater Welding", "years": 2, "self_rating": 2}],
     "capacity_hours": 20},
    {"hub_id": "HUB-GL", "name": "Hannah Weiss", "headline": "Data engineer for manufacturing analytics",
     "industries": ["Automotive", "Technology"], "certifications": [],
     "roles": [{"title": "BI Developer", "organization": "Toledo Analytics", "industry": "Technology",
                "start_year": 2013, "end_year": 2018},
               {"title": "Senior Data Engineer", "organization": "Wolverine Mobility",
                "industry": "Automotive", "start_year": 2018, "end_year": None}],
     "skills": [{"label": "Data Engineering", "years": 8, "self_rating": 5},
                {"label": "Power BI", "years": 12, "self_rating": 4},
                {"label": "Kubernetes", "years": 4, "self_rating": 3},
                {"label": "Supply Chain", "years": 4, "self_rating": 3}],
     "capacity_hours": 30},
    {"hub_id": "HUB-GL", "name": "Leonard Ashworth", "headline": "Program manager, vehicle launches",
     "industries": ["Automotive"], "certifications": ["PMP"],
     "roles": [{"title": "Launch Program Manager", "organization": "Great Lakes Motor Works",
                "industry": "Automotive", "start_year": 2001, "end_year": None}],
     "skills": [{"label": "Program Management", "years": 25, "self_rating": 5, "certified": True},
                {"label": "Change Management", "years": 8, "self_rating": 4},
                {"label": "FMVSS", "years": 10, "self_rating": 3}],
     "capacity_hours": 15},
    # --- Great Plains (agriculture) ---
    {"hub_id": "HUB-GP", "name": "Ruth Lindqvist", "headline": "Agronomist and hub facilitator",
     "industries": ["Agriculture"], "certifications": ["Certified Crop Adviser"],
     "roles": [{"title": "Extension Agronomist", "organization": "Prairie State Extension",
                "industry": "Agriculture", "start_year": 1998, "end_year": 2014},
               {"title": "Agronomy Consultant", "organization": "Lindqvist Crop Advisory",
                "industry": "Agriculture", "start_year": 2014, "end_year": None}],
     "skills": [{"label": "Agronomy", "years": 28, "self_rating": 5, "certified": True},
                {"label": "Precision Ag", "years": 10, "self_rating": 4},
                {"label": "Facilitation", "years": 12, "self_rating": 4}],
     "capacity_hours": 15},
    {"hub_id": "HUB-GP", "name": "Joaquin Delgado", "headline": "IoT field systems engineer",
     "industries": ["Agriculture", "Technology"], "certifications": [],
     "roles": [{"title": "Field Engineer", "organization": "Plains Telemetry", "industry": "Technology",
                "start_year": 2010, "end_year": 2017},
               {"title": "IoT Systems Lead", "organization": "HarvestNet", "industry": "Agriculture",
                "start_year": 2017, "end_year": None}],
     "skills": [{"label": "IoT", "years": 9, "self_rating": 5},
                {"label": "Electronics", "years": 16, "self_rating": 5},
                {"label": "SCADA", "years": 5, "self_rating": 3},
                {"label": "Python", "years": 6, "self_rating": 3}],
     "capacity_hours": 25},
    {"hub_id": "HUB-GP", "name": "Evelyn Sorensen", "headline": "Food safety and traceability specialist",
     "industries": ["Agriculture", "Food Distribution"], "certifications": ["PCQI"],
     "roles": [{"title": "QA Manager", "organization": "Heartland Grain Co-op", "industry": "Agriculture",
                "start_year": 2006, "end_year": 2015},
               {"title": "Traceability Program Lead", "organization": "Midwest Fresh Distribution",
                "industry": "Food Distribution", "start_year": 2015, "end_year": None}],
     "skills": [{"label": "FSMA", "years": 10, "self_rating": 5, "certified": True},
                {"label": "Traceability", "years": 11, "self_rating": 5},
                {"label": "Logistics", "years": 8, "self_rating": 4},
                {"label": "Regulatory Compliance", "years": 14, "self_rating": 4}],
     "capacity_hours": 20},
    {"hub_id": "HUB-GP", "name": "Walter Pankratz", "headline": "Fourth-generation farm operator",
     "industries": ["Agriculture"], "certifications": [],
     "roles": [{"title": "Owner-Operator", "organization": "Pankratz Family Farms",
                "industry": "Agriculture", "start_year": 1990, "end_year": None}],
     "skills": [{"label": "Farm Management", "years": 36, "self_rating": 5},
                {"label": "Precision Agriculture", "years": 8, "self_rating": 4},
                {"label": "Budgeting", "years": 30, "self_rating": 4}],
     "capacity_hours": 8},
    {"hub_id": "HUB-GP", "name": "Naomi Achterberg", "headline": "ML engineer for agronomic models",
     "industries": ["Agriculture", "Technology"], "certifications": ["Google Professional ML Engineer"],
     "roles": [{"title": "Data Scientist", "organization": "AgriSight", "industry": "Agriculture",
                "start_year": 2014, "end_year": None}],
     "skills": [{"label": "Machine Learning", "years": 11, "self_rating": 5, "certified": True},
                {"label": "Data Engineering", "years": 7, "self_rating": 4},
                {"label": "Responsible AI", "years": 3, "self_rating": 4},
                {"label": "Soil Science", "years": 4, "self_rating": 3}],
     "capacity_hours": 20},
]

PROJECTS = [
    # id, hub, name, value stream, capability, summary, objectives, beneficiaries, keywords, start, end, match
    ("P-INS-01", "HUB-GC", "Automated Claims Intake Pilot", "INSURANCE", "Automated Claims Processing",
     "Build and pilot an automated first-notice-of-loss intake that verifies documents and routes "
     "claims for small regional insurers, cutting turnaround after coastal storms.",
     ["Cut median claim intake time for pilot carriers", "Route complex claims to adjusters automatically",
      "Publish an open intake data standard for small carriers"],
     "Policyholders of small regional carriers in storm-affected coastal communities.",
     ["claims", "automation", "consumer protection", "disaster recovery", "underserved"],
     date(2026, 7, 1), date(2027, 12, 31), D("60000")),
    ("P-INS-02", "HUB-GC", "Predictive Fraud & Risk Signals", "INSURANCE", "Predictive Risk Assessment",
     "Develop explainable risk and fraud signals that small carriers can use in underwriting without "
     "building their own data science teams.",
     ["Ship an explainable risk-signal library", "Validate fraud signals on de-identified claims data",
      "Document model governance for each signal"],
     "Small and mid-size carriers and the policyholders they serve.",
     ["fraud prevention", "underwriting", "machine learning", "model governance", "consumer protection"],
     date(2026, 9, 1), date(2027, 8, 31), D("30000")),
    ("P-AUT-01", "HUB-GL", "Regional Parts Supply Chain Visibility", "AUTOMOTIVE", "Supply Chain Optimization",
     "Give small Tier 2 and Tier 3 suppliers shared visibility into component demand and inventory so "
     "regional plants avoid line stoppages.",
     ["Connect suppliers to a shared demand forecast", "Reduce expedite freight for participating plants",
      "Train supplier staff on forecasting"],
     "Small suppliers and plant workers across northern Ohio and southeast Michigan.",
     ["supply chain", "inventory forecasting", "manufacturing", "small business", "resilience"],
     date(2025, 10, 1), date(2026, 12, 31), D("75000")),
    ("P-AUT-02", "HUB-GL", "AV Software Test Pipeline Standard", "AUTOMOTIVE",
     "Autonomous Vehicle Technology Integration",
     "Define and pilot a standard software test and release pipeline for autonomous-driving features "
     "that maps each test to safety and regulatory requirements.",
     ["Publish a reference test pipeline", "Map test evidence to ISO 26262 and FMVSS requirements",
      "Pilot the pipeline with two vehicle software teams"],
     "Vehicle software teams, regulators and the travelling public.",
     ["autonomous vehicles", "safety", "testing", "standards", "workforce"],
     date(2026, 6, 1), date(2027, 11, 30), D("40000")),
    ("P-AGR-01", "HUB-GP", "Soil & Weather Sensor Network", "AGRICULTURE", "Sustainable Crop Management",
     "Deploy low-cost soil and weather sensors on family farms and turn the readings into irrigation and "
     "fertilizer recommendations farmers can act on.",
     ["Install sensors on participating farms", "Deliver a farmer-facing recommendations dashboard",
      "Measure water and fertilizer savings"],
     "Family farms in Kansas and Nebraska.",
     ["precision agriculture", "soil health", "iot", "rural", "sustainability", "data"],
     date(2026, 1, 1), date(2027, 6, 30), D("45000")),
    ("P-AGR-02", "HUB-GP", "Farm-to-Distribution Traceability", "AGRICULTURE", "Supply Chain Transparency",
     "Implement verifiable lot tracking from farm to regional distributor so co-ops can meet FSMA "
     "traceability rules and prove product origin.",
     ["Tag lots at harvest for participating co-ops", "Integrate distributor receiving with lot records",
      "Pass a mock recall within four hours"],
     "Grain and produce co-ops, distributors and consumers.",
     ["traceability", "supply chain", "food safety", "resilience", "small business"],
     date(2026, 11, 1), date(2028, 4, 30), D("80000")),
]

BUDGETS = {
    "P-INS-01": [("PERSONNEL", 300000), ("CONTRACTUAL", 80000), ("EQUIPMENT", 40000), ("TRAVEL", 15000),
                 ("SUPPLIES", 10000), ("OTHER", 35000)],
    "P-INS-02": [("PERSONNEL", 240000), ("CONTRACTUAL", 60000), ("EQUIPMENT", 20000), ("TRAVEL", 10000),
                 ("OTHER", 20000)],
    "P-AUT-01": [("PERSONNEL", 260000), ("CONTRACTUAL", 90000), ("EQUIPMENT", 30000), ("TRAVEL", 25000),
                 ("SUPPLIES", 15000)],
    "P-AUT-02": [("PERSONNEL", 380000), ("EQUIPMENT", 120000), ("CONTRACTUAL", 70000), ("TRAVEL", 20000),
                 ("SUPPLIES", 10000)],
    "P-AGR-01": [("PERSONNEL", 180000), ("EQUIPMENT", 130000), ("TRAVEL", 30000), ("SUPPLIES", 35000),
                 ("OTHER", 15000)],
    "P-AGR-02": [("PERSONNEL", 160000), ("CONTRACTUAL", 50000), ("EQUIPMENT", 35000), ("TRAVEL", 15000),
                 ("SUPPLIES", 10000)],
}
# Spend and prior commitments so far (category -> (committed, spent))
SPEND = {
    "P-INS-01": {"PERSONNEL": (60000, 45000), "CONTRACTUAL": (20000, 5000), "TRAVEL": (0, 2500)},
    "P-AUT-01": {"PERSONNEL": (40000, 190000), "CONTRACTUAL": (10000, 70000), "EQUIPMENT": (0, 28000),
                 "TRAVEL": (0, 14000), "SUPPLIES": (0, 9000)},
    "P-AUT-02": {"PERSONNEL": (50000, 60000), "EQUIPMENT": (0, 35000)},
    "P-AGR-01": {"PERSONNEL": (30000, 70000), "EQUIPMENT": (0, 100000), "SUPPLIES": (0, 12000),
                 "TRAVEL": (0, 9000)},
}

MILESTONES = {
    "P-INS-01": [("MS-INS1-1", "Intake data standard v1", date(2026, 9, 30), date(2026, 9, 26)),
                 ("MS-INS1-2", "Pilot with first carrier", date(2026, 12, 15), None),
                 ("MS-INS1-3", "Storm-season readiness review", date(2027, 5, 31), None)],
    "P-INS-02": [("MS-INS2-1", "Signal library alpha", date(2027, 1, 31), None),
                 ("MS-INS2-2", "Governance documentation", date(2027, 4, 30), None)],
    "P-AUT-01": [("MS-AUT1-1", "Forecast feed live for 10 suppliers", date(2026, 3, 31), date(2026, 4, 10)),
                 ("MS-AUT1-2", "Expedite-freight baseline report", date(2026, 9, 30), None),
                 ("MS-AUT1-3", "Supplier training complete", date(2026, 11, 30), None)],
    "P-AUT-02": [("MS-AUT2-1", "Reference pipeline design", date(2026, 10, 31), None),
                 ("MS-AUT2-2", "Requirements traceability map", date(2027, 2, 28), None),
                 ("MS-AUT2-3", "Two-team pilot complete", date(2027, 9, 30), None)],
    "P-AGR-01": [("MS-AGR1-1", "Sensors installed", date(2026, 6, 30), date(2026, 7, 8)),
                 ("MS-AGR1-2", "Recommendations dashboard live", date(2026, 10, 24), None),
                 ("MS-AGR1-3", "Season results report", date(2027, 3, 31), None)],
    "P-AGR-02": [("MS-AGR2-1", "Harvest lot tagging live", date(2027, 7, 31), None),
                 ("MS-AGR2-2", "Distributor integration", date(2027, 12, 15), None),
                 ("MS-AGR2-3", "Mock recall passed", date(2028, 3, 31), None)],
}

SKILL_NEEDS = {
    "P-INS-01": [("Claims Operations", 4, 10), ("Software Engineering", 4, 20), ("Agile Delivery", 3, 8),
                 ("Cybersecurity", 4, 6)],
    "P-INS-02": [("Machine Learning", 4, 15), ("Actuarial Analysis", 4, 8), ("AI Governance", 3, 6),
                 ("Fraud Detection", 4, 8)],
    "P-AUT-01": [("Automotive Supply Chain", 4, 12), ("Data Engineering", 3, 15),
                 ("Supply Chain Management", 3, 8)],
    "P-AUT-02": [("Vehicle Software", 4, 20), ("Automotive Safety Standards", 4, 10),
                 ("Quality Engineering", 3, 15), ("Cloud & DevOps", 3, 10)],
    "P-AGR-01": [("IoT Systems", 4, 15), ("Agronomy", 4, 8), ("Machine Learning", 3, 10)],
    "P-AGR-02": [("Food Traceability", 4, 12), ("Software Engineering", 3, 15),
                 ("Supply Chain Management", 3, 6)],
}

# member name -> [(project, skill, hours)]
ASSIGNMENTS = [
    ("Celeste Broussard", "P-INS-01", "Claims Operations", 10),
    ("Robert Thibodeaux", "P-INS-01", "Software Engineering", 20),
    ("Aaliyah Mensah", "P-INS-01", "Agile Delivery", 8),
    ("Tomás Villanueva", "P-INS-02", "Machine Learning", 12),
    ("Gregory Kowalczyk", "P-AUT-01", "Automotive Supply Chain", 12),
    ("Hannah Weiss", "P-AUT-01", "Data Engineering", 15),
    ("Priya Raman", "P-AUT-02", "Vehicle Software", 10),
    ("Joaquin Delgado", "P-AGR-01", "IoT Systems", 15),
    ("Ruth Lindqvist", "P-AGR-01", "Agronomy", 8),
    ("Naomi Achterberg", "P-AGR-01", "Machine Learning", 10),
    ("Evelyn Sorensen", "P-AGR-02", "Food Traceability", 6),
]

ALL_SECTIONS_BASIC = ["Project Summary", "Statement of Need", "Project Narrative", "Budget Justification",
                      "Team Qualifications", "Evaluation Plan"]
FEDERAL_SECTIONS = ["Project Summary", "Statement of Need", "Project Narrative", "Eligibility Statement",
                    "Budget Justification", "Team Qualifications", "Timeline & Milestones",
                    "Evaluation Plan", "Sustainability Plan"]

# All funders are fictional.
OPPORTUNITIES = [
    dict(id="FO-01", funder="Heartland Rural Innovation Foundation (fictional)",
         program="Farm Data for Working Lands", funder_type="PRIVATE", min_award=D("100000"),
         max_award=D("400000"), deadline=date(2026, 12, 4), performance_months=24,
         eligible_entities=["NONPROFIT_501C3", "COOPERATIVE", "FISCALLY_SPONSORED"],
         value_streams=["AGRICULTURE"],
         focus_keywords=["precision agriculture", "soil health", "iot", "rural", "sustainability", "data"],
         cost_share_pct=D("10"), indirect_cap_pct=None, requires_sam=False,
         required_sections=ALL_SECTIONS_BASIC, reporting="SEMIANNUAL"),
    dict(id="FO-02", funder="Federal Supply Chain Resilience Initiative (fictional)",
         program="Regional Supplier Resilience Grants", funder_type="FEDERAL", min_award=D("250000"),
         max_award=D("750000"), deadline=date(2026, 11, 20), performance_months=36,
         eligible_entities=["NONPROFIT_501C3", "COOPERATIVE", "LLC"],
         value_streams=["AUTOMOTIVE", "AGRICULTURE"],
         focus_keywords=["supply chain", "traceability", "inventory forecasting", "resilience",
                         "manufacturing", "small business"],
         cost_share_pct=D("20"), indirect_cap_pct=D("10"), requires_sam=True,
         required_sections=FEDERAL_SECTIONS, reporting="QUARTERLY"),
    dict(id="FO-03", funder="Midwest Mobility Futures Fund (fictional)",
         program="Safe Autonomy Workforce Awards", funder_type="PRIVATE", min_award=D("150000"),
         max_award=D("500000"), deadline=date(2026, 10, 28), performance_months=18,
         eligible_entities=["NONPROFIT_501C3", "LLC"], value_streams=["AUTOMOTIVE"],
         focus_keywords=["autonomous vehicles", "safety", "workforce", "testing", "standards"],
         cost_share_pct=D("0"), indirect_cap_pct=D("12"), requires_sam=False,
         required_sections=ALL_SECTIONS_BASIC, reporting="QUARTERLY"),
    dict(id="FO-04", funder="Gulf States Consumer Protection Trust (fictional)",
         program="Fair Claims Innovation Grant", funder_type="PRIVATE", min_award=D("50000"),
         max_award=D("250000"), deadline=date(2027, 1, 15), performance_months=24,
         eligible_entities=["NONPROFIT_501C3"], value_streams=["INSURANCE"],
         focus_keywords=["claims", "fraud prevention", "consumer protection", "automation", "underserved"],
         cost_share_pct=D("25"), indirect_cap_pct=D("15"), requires_sam=False,
         required_sections=ALL_SECTIONS_BASIC, reporting="QUARTERLY"),
    dict(id="FO-05", funder="National Workforce Reskilling Challenge (fictional)",
         program="Experienced Worker Transitions", funder_type="FEDERAL", min_award=D("100000"),
         max_award=D("300000"), deadline=date(2026, 9, 30), performance_months=24,
         eligible_entities=["NONPROFIT_501C3", "COOPERATIVE"],
         value_streams=["INSURANCE", "AUTOMOTIVE", "AGRICULTURE"],
         focus_keywords=["workforce", "reskilling", "experienced workers", "apprenticeship"],
         cost_share_pct=D("0"), indirect_cap_pct=D("10"), requires_sam=True,
         required_sections=FEDERAL_SECTIONS, reporting="QUARTERLY"),
    dict(id="FO-06", funder="Community Cooperative Capital Fund (fictional)",
         program="Co-op Modernization Grants", funder_type="PRIVATE", min_award=D("25000"),
         max_award=D("150000"), deadline=date(2027, 2, 27), performance_months=12,
         eligible_entities=["COOPERATIVE"], value_streams=["AGRICULTURE", "INSURANCE"],
         focus_keywords=["cooperative", "traceability", "member-owned", "rural"],
         cost_share_pct=D("0"), indirect_cap_pct=None, requires_sam=False,
         required_sections=ALL_SECTIONS_BASIC, reporting="SEMIANNUAL"),
    # Earlier-cycle programs behind the two awarded grants
    dict(id="FO-07", funder="Heartland Rural Innovation Foundation (fictional)",
         program="Farm Data for Working Lands (2025 cycle)", funder_type="PRIVATE", min_award=D("100000"),
         max_award=D("300000"), deadline=date(2025, 11, 14), performance_months=18,
         eligible_entities=["NONPROFIT_501C3", "COOPERATIVE"], value_streams=["AGRICULTURE"],
         focus_keywords=["precision agriculture", "soil health", "iot", "rural"],
         cost_share_pct=D("10"), indirect_cap_pct=None, requires_sam=False,
         required_sections=ALL_SECTIONS_BASIC, reporting="QUARTERLY"),
    dict(id="FO-08", funder="Great Lakes Manufacturing Renewal Fund (fictional)",
         program="Supplier Modernization (2025 cycle)", funder_type="PRIVATE", min_award=D("150000"),
         max_award=D("350000"), deadline=date(2025, 8, 29), performance_months=15,
         eligible_entities=["NONPROFIT_501C3", "LLC"], value_streams=["AUTOMOTIVE"],
         focus_keywords=["supply chain", "manufacturing", "small business"],
         cost_share_pct=D("15"), indirect_cap_pct=D("10"), requires_sam=False,
         required_sections=ALL_SECTIONS_BASIC, reporting="QUARTERLY"),
]


def seed(s: Session) -> None:
    if s.query(HubRow).count():
        return
    for hid, name, region, fac, wa in HUBS:
        s.add(HubRow(id=hid, name=name, region=region, facilitator=fac, working_agreement=wa))
    s.flush()

    ids = {}
    for sub in MEMBERS:
        out = repository.ingest_member(s, {**sub, "consent_profiling": True}, actor="seed", commit=False)
        ids[sub["name"]] = out["member_id"]
    # A member who has joined but not yet consented: visible, never profiled or matched.
    s.add(MemberRow(id="M-016", name="Samuel Archuleta", hub_id="HUB-GP",
                    headline="Joined 2026-10-12; consent to profiling not yet given",
                    consent_profiling=False, status="ACTIVE"))

    for (pid, hub, name, vs, cap, summ, objs, ben, kw, start, end, match) in PROJECTS:
        s.add(HubProjectRow(id=pid, hub_id=hub, name=name, value_stream=vs, capability=cap, summary=summ,
                            objectives=objs, beneficiaries=ben, keywords=kw, start_date=start,
                            end_date=end, matching_funds=match))
    s.flush()
    for pid, lines in BUDGETS.items():
        for cat, amt in lines:
            c, sp = SPEND.get(pid, {}).get(cat, (0, 0))
            s.add(BudgetLineRow(project_id=pid, category=cat, budgeted=D(amt), committed=D(c), spent=D(sp)))
    for pid, ms in MILESTONES.items():
        for mid, name, due, done in ms:
            s.add(ProjectMilestoneRow(id=mid, project_id=pid, name=name, due_date=due, completed_date=done))
    for pid, needs in SKILL_NEEDS.items():
        for skill, lvl, hrs in needs:
            s.add(SkillNeedRow(project_id=pid, skill=skill, min_level=lvl, hours_per_week=D(hrs)))
    for name, pid, skill, hrs in ASSIGNMENTS:
        s.add(MemberAssignmentRow(member_id=ids[name], project_id=pid, skill=skill, hours_per_week=D(hrs)))
    leads = {"P-INS-01": "Celeste Broussard", "P-INS-02": "Tomás Villanueva", "P-AUT-01": "Gregory Kowalczyk",
             "P-AUT-02": "Priya Raman", "P-AGR-01": "Ruth Lindqvist", "P-AGR-02": "Evelyn Sorensen"}
    for pid, name in leads.items():
        s.get(HubProjectRow, pid).lead_member_id = ids[name]

    for o in OPPORTUNITIES:
        s.add(FundingOpportunityRow(**o))
    s.flush()

    # GA-001: awarded, mostly on track (AMBER: a missed KPI, a milestone due soon, a report due soon)
    s.add(GrantApplicationRow(id="GA-001", opportunity_id="FO-07", project_id="P-AGR-01", status="AWARDED",
                              requested_amount=D("240000"), awarded_amount=D("240000"),
                              award_start=date(2026, 1, 1), award_end=date(2027, 6, 30),
                              grant_spent=D("96000")))
    for gid, name, due, done, kpi, tgt, act in [
        ("GM-001", "Sensors installed on participating farms", date(2026, 6, 30), date(2026, 7, 8),
         "Farms with sensors", D("150"), D("120")),
        ("GM-002", "Recommendations dashboard live", date(2026, 10, 24), None, "Farmers onboarded", D("100"), None),
        ("GM-003", "Farmer training sessions delivered", date(2026, 12, 15), None, "Sessions", D("12"), D("5")),
        ("GM-004", "Season results report", date(2027, 5, 31), None, "Water saved (acre-feet)", D("400"), None)]:
        s.add(GrantMilestoneRow(id=gid, application_id="GA-001", name=name, due_date=due, completed_date=done,
                                kpi_name=kpi, kpi_target=tgt, kpi_actual=act))
    for pe, due, filed in [(date(2026, 3, 31), date(2026, 4, 30), date(2026, 4, 27)),
                           (date(2026, 6, 30), date(2026, 7, 30), date(2026, 7, 29)),
                           (date(2026, 9, 30), date(2026, 10, 30), None),
                           (date(2026, 12, 31), date(2027, 1, 30), None)]:
        s.add(GrantReportRow(application_id="GA-001", period_end=pe, due_date=due, filed_date=filed))

    # GA-002: awarded, RED (overdue report and an overdue milestone)
    s.add(GrantApplicationRow(id="GA-002", opportunity_id="FO-08", project_id="P-AUT-01", status="AWARDED",
                              requested_amount=D("300000"), awarded_amount=D("300000"),
                              award_start=date(2025, 10, 1), award_end=date(2026, 12, 31),
                              grant_spent=D("255000")))
    for gid, name, due, done, kpi, tgt, act, hib in [
        ("GM-101", "Forecast feed live for suppliers", date(2026, 3, 31), date(2026, 4, 10),
         "Suppliers connected", D("10"), D("12"), True),
        ("GM-102", "Expedite-freight baseline report", date(2026, 9, 30), None,
         "Expedite freight cost per unit ($)", D("1.80"), D("2.10"), False),
        ("GM-103", "Supplier training complete", date(2026, 11, 30), None, "Staff trained", D("60"), D("22"), True)]:
        s.add(GrantMilestoneRow(id=gid, application_id="GA-002", name=name, due_date=due, completed_date=done,
                                kpi_name=kpi, kpi_target=tgt, kpi_actual=act, higher_is_better=hib))
    for pe, due, filed in [(date(2025, 12, 31), date(2026, 1, 31), date(2026, 1, 30)),
                           (date(2026, 3, 31), date(2026, 4, 30), date(2026, 4, 30)),
                           (date(2026, 6, 30), date(2026, 7, 31), None),
                           (date(2026, 9, 30), date(2026, 10, 31), None)]:
        s.add(GrantReportRow(application_id="GA-002", period_end=pe, due_date=due, filed_date=filed))

    # GA-003: a draft in progress, one section written by a named human
    s.add(GrantApplicationRow(id="GA-003", opportunity_id="FO-02", project_id="P-AGR-02", status="DRAFT",
                              requested_amount=D("0"),
                              human_sections={"Project Summary": {
                                  "text": "The Great Plains Hub will give grain and produce co-ops verifiable "
                                          "lot tracking from harvest to regional distributor, so members can "
                                          "meet federal traceability rules and prove where their product came from.",
                                  "author": "Evelyn Sorensen", "at": "2026-10-14T15:00:00+00:00"}}))
    s.commit()
    pkt = repository.application_packet(s, "GA-003")
    s.get(GrantApplicationRow, "GA-003").requested_amount = D(pkt["assessment"]["budget"]["request"])

    # Resource requests -- every one goes through mandatory budget validation on submission.
    repository.submit_request(s, {
        "hub_id": "HUB-GL", "project_id": "P-AUT-02", "title": "Test rig and safety engineer for pipeline design",
        "requested_by": "Marcus Oyelaran",
        "lines": [{"category": "EQUIPMENT", "description": "Hardware-in-the-loop test rig", "amount": "45000"},
                  {"category": "PERSONNEL", "description": "Functional safety engineer, 12 weeks",
                   "amount": "36000", "skill": "Automotive Safety Standards", "min_level": 3,
                   "hours_per_week": "10"}],
        "needed_from": date(2026, 10, 20), "needed_to": date(2027, 1, 15),
        "milestone_ids": ["MS-AUT2-1", "MS-AUT2-2"],
        "justification": "The reference pipeline design milestone needs a rig to run hardware tests and "
                         "a safety engineer to map evidence to requirements."}, actor="Marcus Oyelaran")
    repository.submit_request(s, {
        "hub_id": "HUB-GP", "project_id": "P-AGR-01", "title": "Second-wave sensor kits",
        "requested_by": "Ruth Lindqvist",
        "lines": [{"category": "EQUIPMENT", "description": "60 soil and weather sensor kits", "amount": "54000"},
                  {"category": "TRAVEL", "description": "Install trips", "amount": "6000"}],
        "needed_from": date(2026, 11, 1), "needed_to": date(2027, 3, 31), "milestone_ids": ["MS-AGR1-3"],
        "justification": "Thirty farms are waiting for sensors after the first wave."}, actor="Ruth Lindqvist")
    repository.submit_request(s, {
        "hub_id": "HUB-GC", "project_id": "P-INS-02", "title": "Fraud analyst for signal validation",
        "requested_by": "Danielle Fontenot",
        "lines": [{"category": "PERSONNEL", "description": "Fraud analytics specialist, 16 weeks",
                   "amount": "38400", "skill": "Fraud Detection", "min_level": 4, "hours_per_week": "8"},
                  {"category": "CONTRACTUAL", "description": "De-identified claims data licence",
                   "amount": "12000"}],
        "needed_from": date(2026, 11, 2), "needed_to": date(2027, 2, 26), "milestone_ids": ["MS-INS2-1"],
        "justification": "Signals must be validated on real (de-identified) claims before the alpha."},
        actor="Danielle Fontenot")
    s.commit()
