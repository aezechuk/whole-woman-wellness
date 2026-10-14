from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, select

from wellness.app import crypto, mail, notify, program
from wellness.app.extensions import db
from wellness.app.models import (EmailLog, FeedbackDone, JournalEntry, Participant, SessionFeedback,
                                 SignInCode, Testimonial)

from .conftest import onboard

PROG = program.load()
AFTER_ALL = datetime(2026, 12, 15, tzinfo=timezone.utc)


@pytest.fixture
def open_all(monkeypatch):
    """Pretend it's mid-December so every week is open."""
    monkeypatch.setattr(program, "utcnow", lambda: AFTER_ALL)


@pytest.fixture
def signed_in(client):
    onboard(client)
    return client


# --- calendar -----------------------------------------------------------------

def test_calendar_handles_daylight_saving():
    reset, play = PROG.week("reset"), PROG.week("play")
    assert reset.opens_at.utcoffset() == timedelta(hours=-4)   # EDT in October
    assert play.opens_at.utcoffset() == timedelta(hours=-5)    # EST after Nov 1
    assert len(PROG.weeks) == 6


def test_locked_week_is_404(signed_in, monkeypatch):
    monkeypatch.setattr(program, "utcnow", lambda: datetime(2026, 10, 22, tzinfo=timezone.utc))
    assert signed_in.get("/journal/reset").status_code == 200
    assert signed_in.get("/journal/play").status_code == 404
    assert signed_in.get("/journal/nope").status_code == 404


# --- encryption ---------------------------------------------------------------

def test_seal_roundtrip_and_context_binding(app):
    ctx = {"participant": "a", "field": "journal:reset:0"}
    sealed = crypto.seal("my private words", ctx)
    assert "private words" not in sealed
    assert crypto.open_(sealed, ctx) == "my private words"
    with pytest.raises(Exception):
        crypto.open_(sealed, {"participant": "b", "field": "journal:reset:0"})


def test_journal_saves_encrypted(signed_in, open_all):
    c = signed_in
    r = c.post("/journal/reset", data={"prompt_0": "Everything at once.", "prompt_1": "My mornings."})
    assert r.status_code == 302
    rows = db.session.scalars(select(JournalEntry)).all()
    assert len(rows) == 2
    assert all("Everything" not in e.body and "mornings" not in e.body for e in rows)
    page = c.get("/journal/reset").get_data(as_text=True)
    assert "Everything at once." in page and "My mornings." in page


def test_autosave_returns_204_and_clearing_deletes(signed_in, open_all):
    c = signed_in
    r = c.post("/journal/embodiment", data={"prompt_0": "rest"}, headers={"X-Autosave": "1"})
    assert r.status_code == 204
    c.post("/journal/embodiment", data={"prompt_0": "  "})
    assert db.session.query(JournalEntry).count() == 0


def test_journal_length_limit(signed_in, open_all, app):
    big = "x" * (app.config["REFLECTION_MAX_CHARS"] + 1)
    r = signed_in.post("/journal/reset", data={"prompt_0": big}, headers={"X-Autosave": "1"})
    assert r.status_code == 413 and db.session.query(JournalEntry).count() == 0


def test_journal_isolated_between_participants(app, open_all):
    a, b = app.test_client(), app.test_client()
    onboard(a, "a@example.com", "Ana")
    onboard(b, "b@example.com", "Bea")
    a.post("/journal/reset", data={"prompt_0": "Ana's secret"})
    assert "Ana's secret" not in b.get("/journal/reset").get_data(as_text=True)


# --- session feedback ---------------------------------------------------------

def test_feedback_is_anonymous_and_once(signed_in, open_all):
    c = signed_in
    r = c.post("/feedback/reset", data={"attendance": "live", "valuable": "5", "usable": "4",
                                        "most_helpful": "The energy audit", "make_better": ""})
    assert r.status_code == 302
    fb = db.session.scalar(select(SessionFeedback))
    assert (fb.valuable, fb.usable, fb.most_helpful, fb.make_better) == (5, 4, "The energy audit", None)
    cols = {col.name for col in inspect(SessionFeedback).columns}
    assert not any("participant" in col or col.endswith("_at") for col in cols)
    c.post("/feedback/reset", data={"attendance": "live", "valuable": "1"})
    assert db.session.query(SessionFeedback).count() == 1
    assert db.session.query(FeedbackDone).count() == 1


def test_feedback_didnt_attend_drops_ratings(signed_in, open_all):
    signed_in.post("/feedback/play", data={"attendance": "none", "valuable": "5", "most_helpful": "x"})
    fb = db.session.scalar(select(SessionFeedback))
    assert fb.attendance == "none" and fb.valuable is None and fb.most_helpful is None


def test_feedback_requires_attendance(signed_in, open_all):
    assert signed_in.post("/feedback/reset", data={"valuable": "5"}).status_code == 400


# --- testimonials -------------------------------------------------------------

def test_testimonial_credit_options(signed_in):
    c = signed_in
    c.post("/testimonials/new", data={"body": "Life-changing.", "credit": "first", "share": "funder"})
    c.post("/testimonials/new", data={"body": "Grateful.", "credit": "anonymous", "share": "funder"})
    c.post("/testimonials/new", data={"body": "Yes.", "credit": "full", "full_name": "Maya Johnson",
                                      "share": "funder_and_materials"})
    names = sorted(t.credit_name for t in db.session.scalars(select(Testimonial)))
    assert names == ["A program participant", "Maya", "Maya Johnson"]


def test_testimonial_full_name_required(signed_in):
    r = signed_in.post("/testimonials/new", data={"body": "Hi", "credit": "full", "share": "funder"})
    assert r.status_code == 400 and db.session.query(Testimonial).count() == 0


def test_testimonial_edit_and_withdraw_only_own(app):
    a, b = app.test_client(), app.test_client()
    onboard(a, "a@example.com", "Ana")
    onboard(b, "b@example.com", "Bea")
    a.post("/testimonials/new", data={"body": "Ana's words", "credit": "first", "share": "funder"})
    t = db.session.scalar(select(Testimonial))
    assert b.get(f"/testimonials/{t.id}/edit").status_code == 404
    assert b.post(f"/testimonials/{t.id}/withdraw").status_code == 404
    assert b.get("/testimonials/not-a-uuid/edit").status_code == 404
    a.post(f"/testimonials/{t.id}/edit", data={"body": "Edited", "credit": "anonymous", "share": "funder"})
    db.session.refresh(t)
    assert t.body == "Edited" and t.credit_name == "A program participant"
    a.post(f"/testimonials/{t.id}/withdraw")
    assert db.session.query(Testimonial).count() == 0


# --- settings, download, delete -----------------------------------------------

def test_download_contains_decrypted_reflections(signed_in, open_all):
    c = signed_in
    c.post("/journal/reset", data={"prompt_0": "My words here"})
    r = c.get("/settings/download")
    assert r.headers["Content-Disposition"].startswith("attachment")
    assert "My words here" in r.get_data(as_text=True)
    assert db.session.scalar(select(Participant)).downloaded_at is not None


def test_delete_my_data(signed_in, open_all):
    c = signed_in
    c.post("/journal/reset", data={"prompt_0": "x"})
    c.post("/testimonials/new", data={"body": "y", "credit": "first", "share": "funder"})
    c.post("/feedback/reset", data={"attendance": "live"})
    assert c.post("/settings/delete", data={"confirm": "nope"}).status_code == 400
    mail.OUTBOX.clear()
    r = c.post("/settings/delete", data={"confirm": "DELETE"})
    assert r.status_code == 200
    for model in (Participant, JournalEntry, Testimonial, FeedbackDone, SignInCode):
        assert db.session.query(model).count() == 0
    assert db.session.query(SessionFeedback).count() == 1  # anonymous, unlinkable
    assert mail.OUTBOX[-1].subject == "Your data has been deleted"
    assert c.get("/").status_code == 302  # signed out


def test_email_toggle(signed_in):
    signed_in.post("/settings", data={})
    assert db.session.scalar(select(Participant)).weekly_emails is False
    signed_in.post("/settings", data={"weekly_emails": "on"})
    assert db.session.scalar(select(Participant)).weekly_emails is True


# --- emails -------------------------------------------------------------------

def test_welcome_email_sent_once(client):
    onboard(client)
    welcomes = [m for m in mail.OUTBOX if m.subject.startswith("Welcome")]
    assert len(welcomes) == 1 and "Hi Maya" in welcomes[0].text


def test_weekly_email_once_within_window(app, client):
    onboard(client)
    reset = PROG.week("reset").opens_at
    mail.OUTBOX.clear()
    assert notify.due_emails(reset - timedelta(minutes=1)) == 0
    assert notify.due_emails(reset + timedelta(minutes=5)) == 1
    assert notify.due_emails(reset + timedelta(minutes=20)) == 0          # no duplicates
    msg = mail.OUTBOX[-1]
    assert msg.subject == "Your The Reset reflection is ready"
    assert "What feels most unsustainable" in msg.text and "/unsubscribe/" in msg.text
    assert notify.due_emails(reset + timedelta(days=3)) == 0              # outside window


def test_unsubscribe_link(app, client):
    onboard(client)
    p = db.session.scalar(select(Participant))
    with app.test_request_context():
        token = notify.unsubscribe_token(p)
    anon = app.test_client()
    assert anon.get(f"/unsubscribe/{token}").status_code == 200
    assert db.session.scalar(select(Participant)).weekly_emails is True   # GET alone does nothing
    anon.post(f"/unsubscribe/{token}")
    assert db.session.scalar(select(Participant)).weekly_emails is False
    assert anon.get("/unsubscribe/forged-token").status_code == 404
    reset = PROG.week("reset").opens_at
    assert notify.due_emails(reset + timedelta(minutes=5)) == 0


def test_final_reminder_skips_finishers(app, client):
    onboard(client)
    at, _ = PROG.emails["final_reminder"]
    assert notify.due_emails(at + timedelta(minutes=1)) >= 1
    assert db.session.scalar(select(EmailLog).where(EmailLog.kind == "final_reminder")) is not None


def test_emails_never_include_results(app, client):
    onboard(client)
    for m in mail.OUTBOX:
        assert "score" not in m.text.lower()


def test_contact_shown_on_consent_and_emails(client):
    from .conftest import sign_in
    sign_in(client)
    client.post("/welcome", data={"first_name": "Maya"})
    page = client.get("/consent").get_data(as_text=True)
    assert "Arthentic Arts" in page and "arthenticarts@gmail.com" in page
    assert all("arthenticarts@gmail.com" in m.text for m in mail.OUTBOX)
