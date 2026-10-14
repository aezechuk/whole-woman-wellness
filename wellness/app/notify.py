"""Scheduled and event emails.

`flask send-due-emails` runs every 15 minutes from cron. It's idempotent: each
participant gets each email at most once (EmailLog has a unique constraint),
and an email is only sent within SEND_WINDOW of its scheduled time, so someone
who joins in week 4 isn't sent weeks 1-3 all at once.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from flask import current_app, url_for
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select

from . import mail, program
from .extensions import db
from .models import EmailLog, Participant, utcnow

SEND_WINDOW = timedelta(hours=48)


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="unsubscribe")


def unsubscribe_token(p: Participant) -> str:
    return _serializer().dumps(str(p.id))


def participant_from_unsubscribe(token: str) -> Participant | None:
    try:
        pid = _serializer().loads(token)
    except BadSignature:
        return None
    import uuid
    try:
        return db.session.get(Participant, uuid.UUID(pid))
    except ValueError:
        return None


def link(endpoint: str, **values) -> str:
    base = current_app.config["PUBLIC_URL"].rstrip("/")
    return base + url_for(endpoint, **values)


def _already(p: Participant, kind: str) -> bool:
    return any(e.kind == kind for e in p.email_log)


def _log(p: Participant, kind: str) -> None:
    db.session.add(EmailLog(participant_id=p.id, kind=kind))
    db.session.commit()


def send_welcome(p: Participant) -> None:
    if _already(p, "welcome"):
        return
    mail.send(p.email, "Welcome to the Radical Self-Care Reset", "welcome",
              name=p.first_name, app_link=link("participant.home"))
    _log(p, "welcome")


def send_withdrawal_confirmation(email: str, first_name: str | None) -> None:
    mail.send(email, "Your data has been deleted", "withdrawal", name=first_name)


def _eligible(p: Participant, audience: str) -> bool:
    if not p.has_consented or not p.first_name:
        return False
    if audience == "everyone":
        return True
    if audience == "final_not_done":
        post = next((a for a in p.assessments if a.timepoint == "post"), None)
        return post is None or post.completed_at is None
    if audience == "not_downloaded":
        return p.downloaded_at is None
    raise ValueError(audience)


def due_emails(now: datetime | None = None) -> int:
    """Send everything that's due. Returns the number of emails sent."""
    # Runs from cron with no web request, so give url_for a request context.
    with current_app.test_request_context(base_url=current_app.config["PUBLIC_URL"]):
        return _due_emails(now or utcnow())


def _due_emails(now: datetime) -> int:
    prog = program.load()
    people = list(db.session.scalars(select(Participant)))
    sent = 0

    for week in prog.weeks:
        if not (week.opens_at <= now < week.opens_at + SEND_WINDOW):
            continue
        kind = f"weekly:{week.key}"
        for p in people:
            if p.weekly_emails and _eligible(p, "everyone") and not _already(p, kind):
                mail.send(p.email, f"Your {week.name} reflection is ready", "weekly",
                          name=p.first_name, week=week,
                          journal_link=link("reflect.journal_week", week_key=week.key),
                          unsubscribe_link=link("reflect.unsubscribe", token=unsubscribe_token(p)))
                _log(p, kind)
                sent += 1

    subjects = {
        "final_reminder": ("Six weeks later: see what's shifted", "final_reminder"),
        "final_followup": ("Your then-and-now is waiting", "final_followup"),
        "deletion_reminder": ("Save your results before February 28", "deletion_reminder"),
        "deletion_followup": ("Save your results before February 28", "deletion_reminder"),
    }
    for kind, (at, audience) in prog.emails.items():
        if not (at <= now < at + SEND_WINDOW):
            continue
        subject, template = subjects[kind]
        for p in people:
            if _eligible(p, audience) and not _already(p, kind):
                mail.send(p.email, subject, template, name=p.first_name,
                          final_link=link("assessment.begin", timepoint="post"),
                          download_link=link("reflect.settings"))
                _log(p, kind)
                sent += 1
    return sent
