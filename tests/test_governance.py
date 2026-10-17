"""AI governance across all three agents: audit trail, human review gate, consent gate,
tool scoping, grounding, kill switch and limiter.

Every test uses a scripted fake Claude client, so no API key is needed.
"""
import importlib
import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from app import agent, config, governance
from app.models import AICallRow, AIReviewRow, AppendOnlyViolation, AuditRow


# --- fakes -------------------------------------------------------------------------

class FakeClaude:
    """One tool call, then a final answer. Reports token usage like the real SDK."""

    def __init__(self, final_text="Cybersecurity is a GAP: 6.00 hours of demand and no qualified member.",
                 tool="get_skill_coverage", tool_input=None, fail=False):
        self.final_text, self.tool, self.fail = final_text, tool, fail
        self.tool_input = tool_input if tool_input is not None else {}
        self.calls, self.kwargs = 0, []
        self.messages = self

    def create(self, **kw):
        self.kwargs.append(kw)
        if self.fail:
            raise RuntimeError("upstream 529 overloaded")
        self.calls += 1
        usage = SimpleNamespace(input_tokens=100 * self.calls, output_tokens=10 * self.calls)
        if self.calls == 1 and self.tool:
            return SimpleNamespace(stop_reason="tool_use", usage=usage, content=[SimpleNamespace(
                type="tool_use", id="t1", name=self.tool, input=self.tool_input)])
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text=self.final_text)])


class OneShot:
    """A single-message draft (grant section or member bio)."""

    def __init__(self, text):
        self.text, self.kwargs = text, []
        self.messages = self

    def create(self, **kw):
        self.kwargs.append(kw)
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=50, output_tokens=20),
                               content=[SimpleNamespace(type="text", text=self.text)])


@pytest.fixture()
def fake(monkeypatch):
    f = FakeClaude()
    monkeypatch.setattr(agent, "_client", lambda: f)
    return f


def ask(client, q="Where are our skill gaps?", ip="203.0.113.7", agent_name="expertise", **extra):
    return client.post(f"/api/agents/{agent_name}/ask", json={"question": q, **extra},
                       headers={"X-Forwarded-For": ip})


@pytest.fixture(autouse=True)
def trust_proxy(monkeypatch):
    # Treat X-Forwarded-For as the client address so tests can act as different visitors.
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 1)


def decide(client, rid, decision, reviewer="Wendell Killette", **kw):
    return client.post(f"/api/reviews/{rid}/decision",
                       json={"decision": decision, "reviewer": reviewer, **kw})


# =================================================================================
# 1. Audit trail
# =================================================================================

def test_every_ai_call_is_logged_with_who_when_model_tools_tokens_outcome(client, fake):
    r = ask(client, requested_by="Ruth (GP hub)").json()
    assert r["agent"] == "expertise" and r["grounded"] is True
    c = client.get("/api/ai-calls").json()[0]
    assert c["visitor"] == governance.visitor_id("203.0.113.7")
    assert c["requested_by"] == "Ruth (GP hub)" and c["at"] and c["kind"] == "EXPERTISE_ASK"
    assert c["model"] == config.ANTHROPIC_MODEL and c["tools"] == ["get_skill_coverage"]
    assert (c["input_tokens"], c["output_tokens"], c["model_calls"]) == (300, 30, 2)
    assert c["outcome"] == "OK" and c["review_id"] == r["review_id"]
    assert "203.0.113.7" not in str(client.get("/api/ai-calls").json())
    events = [e["event"] for e in client.get("/api/audit").json()]
    assert {"AI_CALL", "TOOL_CALL", "AI_OUTPUT_PENDING_REVIEW"} <= set(events)


def test_refused_and_failed_calls_are_logged_too(client, monkeypatch):
    assert ask(client).status_code == 503                         # no API key -> UNAVAILABLE
    monkeypatch.setattr(agent, "_client", lambda: FakeClaude(fail=True))
    assert ask(client).status_code == 502
    assert [c["outcome"] for c in client.get("/api/ai-calls").json()] == ["ERROR", "UNAVAILABLE"]
    assert client.get("/api/reviews").json() == []


def test_audit_and_ai_call_logs_are_append_only(session, fake):
    agent.ask(session, "expertise", "gaps?", visitor="v-test")
    call = session.query(AICallRow).first()
    call.outcome = "TAMPERED"
    with pytest.raises(AppendOnlyViolation):
        session.flush()
    session.rollback()
    with pytest.raises(AppendOnlyViolation):
        session.delete(session.query(AuditRow).first())
        session.flush()
    session.rollback()


# =================================================================================
# 2. Tool scoping: each agent sees only its own read-only tools
# =================================================================================

@pytest.mark.parametrize("name", ["expertise", "grants", "resources"])
def test_no_agent_has_a_tool_that_changes_data_or_approves(name):
    tools = agent.AGENTS[name]["tools"]
    forbidden = ("approve", "decide", "review", "publish", "release", "validate", "ingest", "withdraw",
                 "update", "delete", "write", "submit_request", "create")
    assert not [t for t in tools if any(w in t for w in forbidden)]
    assert [t["name"] for t in agent.tools_for(name)] == tools


def test_agents_cannot_call_each_others_tools(session):
    with pytest.raises(ValueError, match="not available to the grants agent"):
        agent.run_tool(session, "grants", "get_resource_request", {"request_id": 1})
    with pytest.raises(ValueError, match="not available to the expertise agent"):
        agent.run_tool(session, "expertise", "propose_grant_submission", {"application_id": "GA-003"})


def test_the_only_outbound_tool_only_proposes(session):
    # GA-003 is a DRAFT: even the proposal is refused, and nothing is ever submitted by the agent
    with pytest.raises(Exception, match="DRAFT"):
        agent.run_tool(session, "grants", "propose_grant_submission", {"application_id": "GA-003"})


def test_tool_errors_reach_the_model_and_the_trail(client, monkeypatch):
    f = FakeClaude(tool="get_member_profile", tool_input={"member_id": "M-999"},
                   final_text="That member does not exist.")
    monkeypatch.setattr(agent, "_client", lambda: f)
    r = ask(client).json()
    assert r["tools_used"][0]["error"] is True
    sent = f.kwargs[1]["messages"][-1]["content"][0]
    assert sent["is_error"] and "No member M-999" in sent["content"]


# =================================================================================
# 3. Consent gate: non-consenting members never reach the model
# =================================================================================

def test_profile_tool_hides_non_consenting_member(session):
    out = agent.run_tool(session, "expertise", "get_member_profile", {"member_id": "M-016"})
    assert set(out) == {"member_id", "status", "note"} and "not consented" in out["note"]


def test_bio_draft_refused_without_consent_and_model_never_called(client, monkeypatch):
    one = OneShot("Should never be written.")
    monkeypatch.setattr(agent, "_client", lambda: one)
    r = client.post("/api/members/M-016/bio-draft", json={})
    assert r.status_code == 403 and one.kwargs == []
    assert client.get("/api/ai-calls").json()[0]["outcome"] == "CONSENT_REQUIRED"
    client.post("/api/members/M-013/withdraw-consent", json={"confirmed_by": "Evelyn Sorensen"})
    assert client.post("/api/members/M-013/bio-draft", json={}).status_code == 403


def test_bio_draft_goes_to_review_and_is_grounded(client, monkeypatch):
    monkeypatch.setattr(agent, "_client", lambda: OneShot(
        "Evelyn Sorensen is a Distinguished traceability specialist with 20.0 years of experience."))
    r = client.post("/api/members/M-013/bio-draft", json={"requested_by": "Ruth Lindqvist"}).json()
    assert r["status"] == "PENDING_REVIEW" and r["grounded"] is True
    rev = client.get(f"/api/reviews/{r['review_id']}").json()
    assert rev["agent"] == "expertise" and rev["subject_id"] == "M-013" and rev["kind"] == "MEMBER_BIO"


# =================================================================================
# 4. Human review gate
# =================================================================================

def test_every_ai_output_starts_pending_review_and_is_not_published(client, fake):
    r = ask(client).json()
    assert r["status"] == "PENDING_REVIEW" and r["validated"] is False
    assert client.get(f"/api/reviews/{r['review_id']}/published").status_code == 409


def test_approve_edit_reject(client, fake):
    rid = ask(client).json()["review_id"]
    ok = decide(client, rid, "approve").json()
    assert ok["status"] == "APPROVED" and ok["reviewer"] == "Wendell Killette"
    assert client.get(f"/api/reviews/{rid}/published").json()["text"] == fake.final_text
    fake.calls = 0
    rid2 = ask(client).json()["review_id"]
    e = decide(client, rid2, "edit", edited_output="Cybersecurity has no qualified member.").json()
    assert e["status"] == "APPROVED_WITH_EDITS" and e["ai_output"] == fake.final_text
    fake.calls = 0
    rid3 = ask(client).json()["review_id"]
    assert decide(client, rid3, "reject").status_code == 422
    assert decide(client, rid3, "reject", reason="Outdated").json()["status"] == "REJECTED"
    assert client.get(f"/api/reviews/{rid3}/published").status_code == 409


def test_decisions_are_final(client, fake):
    rid = ask(client).json()["review_id"]
    decide(client, rid, "approve")
    assert decide(client, rid, "reject", reason="Second thoughts").status_code == 409


@pytest.mark.parametrize("name", ["Claude", "claude-sonnet-5-5", "agent", "Grant Bot", "system",
                                  "auto-approver", "Expertise AI"])
def test_ai_cannot_approve_its_own_work(client, fake, name):
    rid = ask(client).json()["review_id"]
    assert decide(client, rid, "approve", reviewer=name).status_code == 403


def test_no_bypass_route_exists():
    from app.main import app
    review_writes = [(r.path, m) for r in app.routes for m in getattr(r, "methods", set())
                     if "/reviews" in r.path and m not in ("GET", "HEAD")]
    assert review_writes == [("/api/reviews/{review_id}/decision", "POST")]
    assert "status" not in inspect.signature(governance.create_review).parameters


def test_ungrounded_output_cannot_be_approved_unchanged(client, monkeypatch):
    monkeypatch.setattr(agent, "_client",
                        lambda: FakeClaude("We could close 87% of the gap with 913 new hires."))
    r = ask(client).json()
    assert r["grounded"] is False and r["unverified_numbers"] == ["87", "913"]
    rid = r["review_id"]
    assert decide(client, rid, "approve").status_code == 422
    assert decide(client, rid, "edit", edited_output="We could close 87% of it.").status_code == 422
    ok = decide(client, rid, "edit", edited_output="Recruiting cybersecurity members would close the gap.")
    assert ok.json()["status"] == "APPROVED_WITH_EDITS"


def test_publishing_fails_closed_on_any_unexpected_state(session, fake):
    rid = agent.ask(session, "expertise", "q?", visitor="v1")["review_id"]
    row = session.get(AIReviewRow, rid)
    session.execute(update(AIReviewRow).where(AIReviewRow.id == rid)
                    .values(status="APPROVED", final_output="text"))
    session.commit()
    session.refresh(row)
    with pytest.raises(governance.ReviewError):
        governance.published_text(row)                  # no reviewer -> refused
    for status in ("PENDING_REVIEW", "REJECTED", "approved", "", "UNKNOWN"):
        row.status = status
        with pytest.raises(governance.ReviewError):
            governance.published_text(row)
    session.rollback()


def test_a_concurrent_decision_cannot_overwrite_the_first(session, fake):
    rid = agent.ask(session, "expertise", "q?", visitor="v1")["review_id"]
    session.get(AIReviewRow, rid)
    from app.db import SessionLocal
    with SessionLocal() as other:
        governance.decide_review(other, rid, "reject", "Pat Lee", reason="Out of date")
    with pytest.raises(governance.ReviewError) as e:
        governance.decide_review(session, rid, "approve", "Wendell Killette")
    assert e.value.status_code == 409


def test_reviewer_token_enforced_on_every_human_gate(client, fake, monkeypatch):
    monkeypatch.setattr(config, "REVIEWER_TOKEN", "s3cret")
    rid = ask(client).json()["review_id"]
    assert decide(client, rid, "approve").status_code == 401
    assert client.post("/api/resources/requests/1/decision",
                       json={"decision": "approve", "reviewer": "Marcus Oyelaran"}).status_code == 401
    assert client.post("/api/grants/applications/GA-003/sections",
                       json={"section": "Evaluation Plan", "text": "x" * 30, "author": "Evelyn Sorensen"}
                       ).status_code == 401
    ok = client.post(f"/api/reviews/{rid}/decision", headers={"X-Reviewer-Token": "s3cret"},
                     json={"decision": "approve", "reviewer": "Wendell Killette"})
    assert ok.status_code == 200


# =================================================================================
# 5. Grant sections: AI drafts enter the proposal only after human approval
# =================================================================================

def test_ai_section_draft_lifecycle(client, monkeypatch):
    one = OneShot("Co-ops in the Great Plains need verifiable lot tracking. The hub requests 293500.00 "
                  "to [NEEDS INPUT: number of participating co-ops].")
    monkeypatch.setattr(agent, "_client", lambda: one)
    r = client.post("/api/grants/applications/GA-003/sections/draft",
                    json={"section": "Statement of Need", "requested_by": "Evelyn Sorensen"}).json()
    assert r["status"] == "PENDING_REVIEW" and r["grounded"] is True
    assert one.kwargs[0]["max_tokens"] == config.DRAFT_MAX_TOKENS
    sec = lambda: next(x for x in client.get("/api/grants/applications/GA-003").json()["sections"]
                       if x["section"] == "Statement of Need")
    assert sec()["state"] == "AI_DRAFT_PENDING_REVIEW" and sec()["content"] is None
    decide(client, r["review_id"], "edit", reviewer="Evelyn Sorensen",
           edited_output="Co-ops in the Great Plains need verifiable lot tracking for every harvest.")
    s = sec()
    assert s["state"] == "AI_DRAFT_APPROVED" and "verifiable lot tracking" in s["content"]
    assert "Evelyn Sorensen" in s["source"]


def test_drafts_only_for_narrative_sections(client, monkeypatch):
    monkeypatch.setattr(agent, "_client", lambda: OneShot("x"))
    r = client.post("/api/grants/applications/GA-003/sections/draft", json={"section": "Budget Justification"})
    assert r.status_code == 422                          # not a narrative section: schema refuses it
    r = client.post("/api/grants/applications/GA-001/sections/draft", json={"section": "Project Narrative"})
    assert r.status_code == 409                          # awarded application: sections locked


# =================================================================================
# 6. Scorecard, kill switch, limiter
# =================================================================================

def test_scorecard_page_and_data(client, fake):
    page = client.get("/governance")
    assert page.status_code == 200
    g = client.get("/api/governance").json()
    assert [a["agent"] for a in g["model_card"]["agents"]] == ["expertise", "grants", "resources"]
    assert g["data_policy"]["data"].startswith("Fictional")
    assert "Consent required" in g["data_policy"]["member_profiles"]
    assert g["human_validation"]["pct_human_validated"] is None


def test_scorecard_percent_human_validated_is_live(client, fake):
    a = ask(client).json()["review_id"]
    fake.calls = 0
    ask(client, "Second question?", agent_name="resources")
    hv = client.get("/api/governance").json()["human_validation"]
    assert hv["total_ai_outputs"] == 2 and hv["pct_human_validated"] == 0.0
    decide(client, a, "approve")
    hv = client.get("/api/governance").json()["human_validation"]
    assert hv["pct_human_validated"] == 50.0 and hv["pct_published_human_validated"] == 100.0
    assert [r["agent"] for r in client.get("/api/reviews", params={"agent": "resources"}).json()] == ["resources"]


def test_kill_switch_runtime_control(client, fake, monkeypatch):
    body = {"engaged": True, "actor": "Wendell Killette", "reason": "Cost spike"}
    assert client.post("/api/governance/kill-switch", json=body).status_code == 403
    monkeypatch.setattr(config, "ADMIN_TOKEN", "adm")
    assert client.post("/api/governance/kill-switch", json=body).status_code == 401
    h = {"X-Admin-Token": "adm"}
    assert client.post("/api/governance/kill-switch", headers=h, json={**body, "actor": "Claude"}).status_code == 403
    assert client.post("/api/governance/kill-switch", headers=h, json=body).json()["engaged"] is True
    for name in ("expertise", "grants", "resources"):
        assert ask(client, agent_name=name).status_code == 503
    assert client.get("/health").json()["agents_enabled"] is False
    assert fake.kwargs == []
    # the deterministic engines keep working
    assert client.get("/api/expertise/coverage").status_code == 200
    client.post("/api/governance/kill-switch", headers=h,
                json={"engaged": False, "actor": "Wendell Killette", "reason": "Resolved"})
    assert ask(client).status_code == 200


def test_kill_switch_from_environment_and_fails_closed(client, fake, monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    assert ask(client).status_code == 503

    class Broken:
        def query(self, *a, **k): raise RuntimeError("db down")
        def rollback(self): pass
    monkeypatch.setattr(config, "AI_ENABLED", True)
    assert governance.kill_switch_state(Broken())["engaged"] is True


def test_per_visitor_rate_limit_spans_all_agents(client, fake, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 2)
    for name in ("expertise", "grants"):
        fake.calls = 0
        assert ask(client, agent_name=name).status_code == 200
    r = ask(client, agent_name="resources")
    assert r.status_code == 429 and r.headers["Retry-After"] == "60"
    fake.calls = 0
    assert ask(client, ip="198.51.100.9").status_code == 200


def test_daily_caps_and_token_budget(client, fake, monkeypatch):
    monkeypatch.setattr(config, "DAILY_CAP_PER_VISITOR", 1)
    assert ask(client).status_code == 200
    assert ask(client).status_code == 429
    monkeypatch.setattr(config, "DAILY_CAP_PER_VISITOR", 100)
    monkeypatch.setattr(config, "DAILY_CAP_GLOBAL", 1)
    assert ask(client, ip="198.51.100.20").status_code == 429
    monkeypatch.setattr(config, "DAILY_CAP_GLOBAL", 100)
    monkeypatch.setattr(config, "DAILY_TOKEN_BUDGET", 300)
    assert ask(client, ip="198.51.100.21").status_code == 429
    assert [c["outcome"] for c in client.get("/api/ai-calls").json()].count("CAP_REACHED") == 3


def test_drafts_count_toward_the_same_limits(client, monkeypatch):
    monkeypatch.setattr(agent, "_client", lambda: OneShot("Draft."))
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 1)
    assert client.post("/api/members/M-013/bio-draft", json={}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 200
    r = client.post("/api/grants/applications/GA-003/sections/draft", json={"section": "Evaluation Plan"},
                    headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 429


def test_max_tokens_and_turns_come_from_config(session, monkeypatch):
    monkeypatch.setattr(config, "MAX_TOKENS", 333)
    monkeypatch.setattr(config, "MAX_AGENT_TURNS", 1)
    f = FakeClaude()
    r = agent.ask(session, "grants", "q?", client=f, visitor="v1")
    assert [k["max_tokens"] for k in f.kwargs] == [333]
    assert r["draft_answer"].startswith("Stopped")


def test_request_size_and_question_length_limits(client, fake, monkeypatch):
    monkeypatch.setattr(config, "MAX_REQUEST_BYTES", 2048)
    big = client.post("/api/agents/expertise/ask", content=b'{"question": "' + b"x" * 5000 + b'"}',
                      headers={"Content-Type": "application/json"})
    assert big.status_code == 413
    assert ask(client, q="x" * (config.MAX_QUESTION_CHARS + 1)).status_code == 422
    assert client.post("/api/agents/unknown/ask", json={"question": "hello"}).status_code == 422
    assert fake.kwargs == []


def test_limits_are_read_from_environment(monkeypatch):
    for k, v in {"RATE_LIMIT_PER_MINUTE": "7", "DAILY_CAP_PER_VISITOR": "9", "DAILY_CAP_GLOBAL": "11",
                 "MAX_TOKENS": "512", "MAX_REQUEST_BYTES": "4096", "DAILY_TOKEN_BUDGET": "1000",
                 "AI_ENABLED": "false", "COALITION_ENTITY_TYPE": "COOPERATIVE"}.items():
        monkeypatch.setenv(k, v)
    c = importlib.reload(config)
    try:
        assert (c.RATE_LIMIT_PER_MINUTE, c.DAILY_CAP_PER_VISITOR, c.DAILY_CAP_GLOBAL, c.MAX_TOKENS,
                c.MAX_REQUEST_BYTES, c.DAILY_TOKEN_BUDGET, c.AI_ENABLED, c.COALITION_ENTITY_TYPE) == \
               (7, 9, 11, 512, 4096, 1000, False, "COOPERATIVE")
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_entity_type_changes_grant_eligibility(client, monkeypatch):
    monkeypatch.setattr(config, "COALITION_ENTITY_TYPE", "COOPERATIVE")
    recs = {o["opportunity_id"]: o for o in
            client.get("/api/grants/projects/P-AGR-02/opportunities").json()["opportunities"]}
    assert recs["FO-06"]["eligibility"]["eligible"] is True        # co-op fund opens up
    assert not next(c for c in recs["FO-03"]["eligibility"]["checks"] if c["check"] == "entity_type")["passed"]
