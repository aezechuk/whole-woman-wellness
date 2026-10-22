from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from wellness.app import mail, program
from wellness.app.extensions import db
from wellness.app.models import Intention, Participant, WinCheer, WinComment, WinPost, WinReport

from .conftest import onboard
from .test_admin import admin_sign_in

NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(program, "utcnow", lambda: NOW)


@pytest.fixture
def two(app):
    a, b = app.test_client(), app.test_client()
    onboard(a, "ana@example.com", "Ana")
    onboard(b, "bea@example.com", "Bea")
    mail.OUTBOX.clear()
    return a, b


def count(model):
    return db.session.scalar(select(func.count()).select_from(model))


def only(model):
    return db.session.scalar(select(model))


def test_post_with_name_or_anonymously(two):
    a, b = two
    r = a.post("/wins", data={"body": "Walked 4 days this week!", "anonymous": "0", "week_key": "reset"})
    assert r.status_code == 302
    a.post("/wins", data={"body": "I said no to a gig that drained me.", "anonymous": "1"})
    page = b.get("/wins").data.decode()
    assert "Walked 4 days this week!" in page and "Ana" in page
    assert "A sister in the program" in page and "I said no to a gig" in page
    assert "Walked 4 days" in b.get("/wins?week=reset").data.decode()
    assert "I said no" not in b.get("/wins?week=reset").data.decode()


def test_posts_are_escaped_and_validated(two):
    a, _ = two
    a.post("/wins", data={"body": "<script>alert(1)</script>"})
    page = a.get("/wins").data.decode()
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    a.post("/wins", data={"body": "   "})
    a.post("/wins", data={"body": "x" * 501})
    assert count(WinPost) == 1


def test_cheer_toggles(two):
    a, b = two
    a.post("/wins", data={"body": "Win"})
    post = only(WinPost)
    b.post(f"/wins/{post.id}/cheer")
    assert count(WinCheer) == 1
    b.post(f"/wins/{post.id}/cheer")
    assert count(WinCheer) == 0


def test_comment_emails_poster_without_content_and_throttles(two):
    a, b = two
    a.post("/wins", data={"body": "Win"})
    post = only(WinPost)
    b.post(f"/wins/{post.id}/comment", data={"body": "So proud of you, sis!", "anonymous": "0"})
    assert len(mail.OUTBOX) == 1
    m = mail.OUTBOX[0]
    assert m.to == "ana@example.com" and "commented on your win" in m.subject
    assert "So proud" not in m.text and f"/wins/{post.id}" in m.text
    b.post(f"/wins/{post.id}/comment", data={"body": "Again!"})            # within 4 hours
    a.post(f"/wins/{post.id}/comment", data={"body": "Thank you!"})        # her own
    assert len(mail.OUTBOX) == 1
    assert "So proud of you, sis!" in b.get(f"/wins/{post.id}").data.decode()


def test_comment_emails_can_be_turned_off(two):
    a, b = two
    a.post("/settings", data={"weekly_emails": "on"})                       # comment_emails unchecked
    a.post("/wins", data={"body": "Win"})
    b.post(f"/wins/{only(WinPost).id}/comment", data={"body": "Yay"})
    assert mail.OUTBOX == []


def test_delete_own_only(two):
    a, b = two
    a.post("/wins", data={"body": "Win"})
    post = only(WinPost)
    b.post(f"/wins/{post.id}/comment", data={"body": "Yay"})
    comment = only(WinComment)
    assert b.post(f"/wins/{post.id}/delete").status_code == 404
    assert a.post(f"/wins/comments/{comment.id}/delete").status_code == 404
    b.post(f"/wins/comments/{comment.id}/delete")
    assert count(WinComment) == 0
    a.post(f"/wins/{post.id}/delete")
    assert count(WinPost) == 0


def test_report_alerts_all_admins_once_and_admin_can_delete(app, two):
    a, b = two
    a.post("/wins", data={"body": "Something unkind"})
    post = only(WinPost)
    b.post(f"/wins/{post.id}/report", data={"reason": "Not kind"})
    b.post(f"/wins/{post.id}/report", data={"reason": "Again"})             # no duplicate alert
    alerts = [m for m in mail.OUTBOX if "reported" in m.subject]
    assert sorted(m.to for m in alerts) == sorted(app.config["ADMIN_EMAILS"])
    assert all("unkind" not in m.text for m in alerts) and count(WinReport) == 1
    assert b.get("/admin/wins").status_code in (302, 401, 403, 404)          # participants can't moderate
    admin = app.test_client()
    admin_sign_in(admin)
    page = admin.get("/admin/wins").data.decode()
    assert "Reported (1)" in page and "Something unkind" in page and "Not kind" in page
    admin.post(f"/admin/wins/posts/{post.id}/delete")
    assert count(WinPost) == 0 and count(WinReport) == 0


def test_report_comment_and_dismiss(app, two):
    a, b = two
    a.post("/wins", data={"body": "Win"})
    post = only(WinPost)
    b.post(f"/wins/{post.id}/comment", data={"body": "meh"})
    comment = only(WinComment)
    a.post(f"/wins/{post.id}/report", data={"comment_id": str(comment.id)})
    admin = app.test_client()
    admin_sign_in(admin)
    admin.post(f"/admin/wins/reports/{only(WinReport).id}/dismiss")
    assert only(WinReport).resolved and count(WinComment) == 1
    assert "Reported (0)" in admin.get("/admin/wins").data.decode()
    admin.post(f"/admin/wins/comments/{comment.id}/delete")
    assert count(WinComment) == 0


def test_strong_checkin_nudges_to_share(app, monkeypatch):
    c = app.test_client()
    onboard(c)
    c.post("/intentions/reset", data={"kind": "when", "behavior": "walk", "time": "at noon"})
    monkeypatch.setattr(program, "utcnow", lambda: datetime(2026, 10, 29, 15, tzinfo=timezone.utc))
    r = c.post(f"/intentions/{only(Intention).id}/checkin", data={"days": "6", "next": "keep"})
    assert "win=1" in r.headers["Location"]
    assert "THAT'S A WIN" in c.get("/sessions/embodiment?win=1").data.decode()


def test_deleting_account_removes_her_wins(two):
    a, b = two
    a.post("/wins", data={"body": "Mine"})
    b.post("/wins", data={"body": "Hers"})
    b.post(f"/wins/{db.session.scalar(select(WinPost).where(WinPost.body == 'Mine')).id}/comment", data={"body": "Yay"})
    b.post(f"/wins/{db.session.scalar(select(WinPost).where(WinPost.body == 'Mine')).id}/cheer")
    a.post("/settings/delete", data={"confirm": "DELETE"})
    assert count(Participant) == 1 and count(WinComment) == 0 and count(WinCheer) == 0
    assert [p.body for p in db.session.scalars(select(WinPost))] == ["Hers"]


def test_download_includes_her_wins(two):
    a, _ = two
    a.post("/wins", data={"body": "Downloaded win", "anonymous": "1"})
    body = a.get("/settings/download").data.decode()
    assert "WINS WALL" in body and "Downloaded win" in body and "(anonymous)" in body
