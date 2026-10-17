"""Human-in-the-loop gate for every outbound action.

Nothing leaves this service on its own. The agents (or a user) can only
PROPOSE an action; a named human must approve it before it runs. Rejected
and approved decisions are both written to the audit log.

Action types:
  * SUBMIT_GRANT_APPLICATION -- marks a READY_FOR_SUBMISSION application as SUBMITTED.
                                 v1 is a recorded submission: no funder portal is called.
  * POST_HUB_NOTICE          -- posts a notice to the hubs' Slack channel if
                                 SLACK_WEBHOOK_URL is configured; otherwise a dry run.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from . import config
from .models import AuditRow, GrantApplicationRow, PendingActionRow

ACTION_TYPES = ("SUBMIT_GRANT_APPLICATION", "POST_HUB_NOTICE")


class ActionError(ValueError):
    pass


def audit(s: Session, actor: str, event: str, detail: dict) -> None:
    s.add(AuditRow(actor=actor, event=event, detail=detail))


def _as_dict(r: PendingActionRow) -> dict:
    return {"id": r.id, "action_type": r.action_type, "project_id": r.project_id,
            "payload": r.payload, "status": r.status, "requested_by": r.requested_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "decided_by": r.decided_by,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "result": r.result}


def propose(s: Session, action_type: str, payload: dict, requested_by: str,
            project_id: str | None = None) -> dict:
    if action_type not in ACTION_TYPES:
        raise ActionError(f"action_type must be one of {ACTION_TYPES}")
    if action_type == "SUBMIT_GRANT_APPLICATION":
        app = s.get(GrantApplicationRow, payload.get("application_id", ""))
        if app is None:
            raise ActionError("payload.application_id must name an existing grant application")
        if app.status != "READY_FOR_SUBMISSION":
            raise ActionError(f"Application {app.id} is {app.status}; only an application whose "
                              "required sections are all human-approved can be proposed for submission.")
        project_id = app.project_id
    row = PendingActionRow(action_type=action_type, payload=payload,
                           requested_by=requested_by, project_id=project_id)
    s.add(row)
    s.flush()
    audit(s, requested_by, "ACTION_PROPOSED", {"action_id": row.id, "type": action_type})
    s.commit()
    return _as_dict(row)


def list_actions(s: Session, status: str | None = None) -> list[dict]:
    q = s.query(PendingActionRow)
    if status:
        q = q.filter(PendingActionRow.status == status)
    return [_as_dict(r) for r in q.order_by(PendingActionRow.id.desc())]


def decide(s: Session, action_id: int, approve: bool, decided_by: str, reason: str = "") -> dict:
    from .governance import is_ai_identity  # local import: governance imports this module
    if not decided_by or not decided_by.strip():
        raise ActionError("decided_by (a named human approver) is required")
    if is_ai_identity(decided_by):
        raise ActionError("An action cannot be approved by the agent, the AI or the system")
    if not approve and len((reason or "").strip()) < 5:
        raise ActionError("A reason (at least 5 characters) is required to reject")
    row = s.get(PendingActionRow, action_id)
    if row is None:
        raise LookupError(f"No action {action_id}")
    if row.status != "PENDING":
        raise ActionError(f"Action {action_id} is already {row.status}")

    row.decided_by = decided_by.strip()
    row.decided_at = datetime.now(timezone.utc)
    if not approve:
        row.status = "REJECTED"
        row.result = f"Rejected by approver; nothing was sent. Reason: {reason.strip()}"
    else:
        row.status = "EXECUTED"
        row.result = _execute(s, row)
    audit(s, row.decided_by, "ACTION_APPROVED" if approve else "ACTION_REJECTED",
          {"action_id": row.id, "type": row.action_type, "result": row.result})
    s.commit()
    return _as_dict(row)


def _execute(s: Session, row: PendingActionRow) -> str:
    if row.action_type == "SUBMIT_GRANT_APPLICATION":
        app = s.get(GrantApplicationRow, row.payload.get("application_id", ""))
        if app is None or app.status != "READY_FOR_SUBMISSION":
            return "NOT SUBMITTED: the application is no longer ready for submission."
        app.status = "SUBMITTED"
        return (f"Application {app.id} marked SUBMITTED (recorded submission; v1 does not call a "
                "funder portal).")
    if row.action_type == "POST_HUB_NOTICE":
        if not config.SLACK_WEBHOOK_URL:
            return "DRY RUN: SLACK_WEBHOOK_URL not configured; notice recorded but not sent."
        text = row.payload.get("markdown") or row.payload.get("text") or ""
        resp = httpx.post(config.SLACK_WEBHOOK_URL, json={"text": text[:39000]}, timeout=10)
        return f"Posted to Slack (HTTP {resp.status_code})."
    return "Unknown action type; nothing executed."
