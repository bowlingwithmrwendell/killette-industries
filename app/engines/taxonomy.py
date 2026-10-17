"""The coalition skill taxonomy.

Members describe their skills in their own words ("SM", "scrum master",
"agile coaching"). Matching only works if those words map to one canonical
name, so every skill passes through `normalize()` before it is stored.

A label that does not map is never guessed: it is kept on the profile as
UNMAPPED so a human can extend the taxonomy.
"""
from __future__ import annotations

import re

# canonical skill -> (domain, aliases). Aliases are matched case-insensitively
# as whole words, after punctuation is normalised.
TAXONOMY: dict[str, tuple[str, list[str]]] = {
    # --- Delivery & leadership
    "Agile Delivery": ("Delivery & Leadership", ["scrum master", "scrum", "sm", "agile", "kanban",
                                                 "agile delivery", "release train engineer", "rte",
                                                 "safe", "agile coach", "agile coaching"]),
    "Project Management": ("Delivery & Leadership", ["project management", "project manager", "pm",
                                                     "pmp", "program management", "pmo"]),
    "Product Management": ("Delivery & Leadership", ["product management", "product owner", "po",
                                                     "product manager", "backlog management"]),
    "Change Management": ("Delivery & Leadership", ["change management", "organizational change",
                                                    "transformation", "prosci"]),
    "Facilitation": ("Delivery & Leadership", ["facilitation", "workshop facilitation",
                                               "consensus building", "conflict resolution"]),
    # --- Technology
    "Software Engineering": ("Technology", ["software engineering", "software development",
                                            "developer", "python", "java", "c#", ".net",
                                            "full stack", "backend"]),
    "Mobile Development": ("Technology", ["mobile development", "ios", "android", "swift",
                                          "kotlin", "react native"]),
    "Cloud & DevOps": ("Technology", ["devops", "cloud", "aws", "azure", "gcp", "ci/cd",
                                      "kubernetes", "docker", "infrastructure"]),
    "Quality Engineering": ("Technology", ["qa", "quality assurance", "quality engineering",
                                           "test automation", "testing", "istqb"]),
    "Cybersecurity": ("Technology", ["cybersecurity", "security", "infosec", "cissp",
                                     "penetration testing", "soc 2"]),
    "IoT Systems": ("Technology", ["iot", "internet of things", "sensors", "embedded systems",
                                   "telemetry", "scada"]),
    "Electronics Engineering": ("Technology", ["electronics", "electronic engineering",
                                               "electrical engineering", "field engineering",
                                               "cctv", "low voltage"]),
    # --- Data & AI
    "Data Engineering": ("Data & AI", ["data engineering", "etl", "data pipelines", "sql",
                                       "data warehouse", "spark"]),
    "Machine Learning": ("Data & AI", ["machine learning", "ml", "predictive modeling",
                                       "data science", "deep learning"]),
    "Analytics & BI": ("Data & AI", ["analytics", "business intelligence", "bi", "tableau",
                                     "power bi", "reporting"]),
    "AI Governance": ("Data & AI", ["ai governance", "responsible ai", "model risk management",
                                    "ai ethics", "nist ai rmf"]),
    # --- Insurance
    "Claims Operations": ("Insurance", ["claims", "claims processing", "claims operations",
                                        "claims adjusting", "adjuster"]),
    "Underwriting": ("Insurance", ["underwriting", "underwriter", "risk selection"]),
    "Actuarial Analysis": ("Insurance", ["actuarial", "actuary", "loss reserving", "pricing models"]),
    "Fraud Detection": ("Insurance", ["fraud detection", "fraud analytics", "siu",
                                      "special investigations"]),
    "Insurance Compliance": ("Insurance", ["insurance compliance", "naic", "regulatory filings"]),
    # --- Automotive
    "Automotive Supply Chain": ("Automotive", ["automotive supply chain", "tier 1 supplier",
                                               "parts procurement", "jit", "just in time"]),
    "Vehicle Software": ("Automotive", ["vehicle software", "autosar", "ecu", "adas",
                                        "autonomous vehicles", "av testing"]),
    "Automotive Safety Standards": ("Automotive", ["iso 26262", "functional safety", "fmvss",
                                                   "nhtsa"]),
    "Manufacturing Operations": ("Automotive", ["manufacturing", "lean manufacturing",
                                                "six sigma", "plant operations"]),
    "Automotive Repair": ("Automotive", ["automotive repair", "auto mechanic", "ase",
                                         "diagnostics"]),
    # --- Agriculture
    "Agronomy": ("Agriculture", ["agronomy", "agronomist", "crop science", "soil science",
                                 "crop management"]),
    "Precision Agriculture": ("Agriculture", ["precision agriculture", "precision ag",
                                              "variable rate", "yield mapping", "ag drones"]),
    "Food Traceability": ("Agriculture", ["traceability", "food safety", "fsma",
                                          "farm to table", "chain of custody"]),
    "Farm Operations": ("Agriculture", ["farm operations", "farming", "ranching",
                                        "farm management"]),
    # --- Shared operations
    "Supply Chain Management": ("Operations", ["supply chain", "logistics", "inventory forecasting",
                                               "warehousing", "distribution"]),
    "Vendor Management": ("Operations", ["vendor management", "procurement", "sourcing",
                                         "contract management"]),
    "Grant Writing": ("Funding & Finance", ["grant writing", "grants", "proposal writing",
                                            "grant management", "federal grants"]),
    "Financial Management": ("Funding & Finance", ["financial management", "budgeting",
                                                   "accounting", "cpa", "financial planning",
                                                   "fp&a"]),
    "Regulatory Compliance": ("Funding & Finance", ["compliance", "regulatory compliance",
                                                    "audit", "2 cfr 200", "uniform guidance"]),
}

_CLEAN = re.compile(r"[^a-z0-9+#/&.\s]")


def _clean(label: str) -> str:
    return " ".join(_CLEAN.sub(" ", label.lower()).split())


# alias -> canonical, longest aliases first so "agile coach" beats "agile"
_ALIAS: dict[str, str] = {}
for _skill, (_domain, _aliases) in TAXONOMY.items():
    _ALIAS[_clean(_skill)] = _skill
    for _a in _aliases:
        _ALIAS[_clean(_a)] = _skill
_ALIASES_LONGEST_FIRST = sorted(_ALIAS, key=len, reverse=True)

# Aliases that are fine as a whole label ("PM", "SAFe") but too ambiguous to
# trust inside free text ("3 pm", "keep it safe"). Free-text scanning skips
# them and anything shorter than 3 characters.
_TEXT_UNSAFE = {"safe", "security", "reporting", "audit", "testing", "distribution", "developer",
                "cloud", "grants", "claims", "agile", "scrum", "sensors", "infrastructure"}


def domain_of(skill: str) -> str:
    return TAXONOMY[skill][0]


def normalize(label: str) -> str | None:
    """Exact (cleaned) match of a whole label to a canonical skill, or None."""
    return _ALIAS.get(_clean(label))


def find_in_text(text: str) -> list[dict]:
    """Every taxonomy alias that appears as a whole word or phrase in free text.

    Returns [{"skill", "domain", "matched", "evidence"}], one entry per skill,
    with the sentence the match came from as evidence. Deterministic: the same
    text always yields the same skills.
    """
    cleaned_text = _clean(text)
    sentences = [x.strip() for x in re.split(r"(?<=[.;!?])\s+|\n+", text) if x.strip()]
    found: dict[str, dict] = {}
    taken: list[tuple[int, int]] = []
    for alias in _ALIASES_LONGEST_FIRST:
        if len(alias) < 3 or alias in _TEXT_UNSAFE:
            continue
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", cleaned_text):
            span = (m.start(), m.end())
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue                       # inside a longer alias already matched
            taken.append(span)
            skill = _ALIAS[alias]
            if skill in found:
                continue
            evidence = next((sent for sent in sentences
                             if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", _clean(sent))),
                            "")
            found[skill] = {"skill": skill, "domain": domain_of(skill), "matched": alias,
                            "evidence": evidence[:240]}
    return sorted(found.values(), key=lambda d: (d["domain"], d["skill"]))


def as_tree() -> list[dict]:
    tree: dict[str, list[dict]] = {}
    for skill, (domain, aliases) in TAXONOMY.items():
        tree.setdefault(domain, []).append({"skill": skill, "aliases": aliases})
    return [{"domain": d, "skills": v} for d, v in tree.items()]
