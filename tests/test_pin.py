from datetime import timedelta

import pytest
from sqlalchemy import select

from wellness.app import mail, pin
from wellness.app.extensions import db
from wellness.app.models import AuthSession, JournalEntry, Participant, utcnow

from .conftest import last_code, onboard, sign_in


@pytest.fixture
def maya(client):
    onboard(client)
    client.post("/journal/reset", data={"prompt_0": "My private thought"})
    return client


def me():
    return db.session.scalar(select(Participant))


def set_pin(c, value="2468"):
    return c.post("/pin/setup", data={"pin": value, "confirm": value})


def expire_unlock():
    for s in db.session.scalars(select(AuthSession)):
        s.pin_unlocked_until = utcnow() - timedelta(seconds=1)
    db.session.commit()


def test_without_pin_nothing_changes(maya):
    assert "My private thought" in maya.get("/journal/reset").data.decode()
    assert "Add a PIN" in maya.get("/journal").data.decode()
    assert maya.get("/pin").status_code == 302


def test_pin_is_hashed_and_validated(maya):
    assert set_pin(maya, "12").status_code == 400
    assert maya.post("/pin/setup", data={"pin": "1234", "confirm": "1235"}).status_code == 400
    assert me().pin_hash is None
    set_pin(maya)
    assert me().pin_hash and "2468" not in me().pin_hash


def test_locked_pages_need_pin_and_unlock_returns_there(maya):
    set_pin(maya)
    page = maya.get("/journal/reset").data.decode()               # she just set it: unlocked
    assert "My private thought" in page and "Lock now" in page and "data-private" in page
    maya.post("/pin/lock")
    for url in ("/journal/reset", "/results", "/results/final"):
        r = maya.get(url)
        assert r.status_code == 302 and "/pin?next=" in r.headers["Location"]
    assert "My private thought" not in maya.get("/journal/reset", follow_redirects=True).data.decode()
    assert maya.get("/settings/download").headers["Location"].endswith("/pin?next=/settings")
    assert maya.post("/journal/reset", data={"prompt_0": ""}, headers={"X-Autosave": "1"}).status_code == 423
    assert db.session.scalar(select(JournalEntry)) is not None      # locked POST can't erase it
    r = maya.post("/pin", data={"pin": "2468", "next": "/journal/reset"})
    assert r.headers["Location"].endswith("/journal/reset")
    assert "My private thought" in maya.get("/journal/reset").data.decode()


def test_unlock_expires_after_15_min_idle_and_activity_extends(maya):
    set_pin(maya)
    expire_unlock()
    assert maya.get("/journal/reset").status_code == 302
    maya.post("/pin", data={"pin": "2468"})
    sess = db.session.scalar(select(AuthSession))
    sess.pin_unlocked_until = utcnow() + timedelta(minutes=5)
    db.session.commit()
    maya.get("/")                                                    # any activity
    db.session.refresh(sess)
    assert pin._aware(sess.pin_unlocked_until) > utcnow() + timedelta(minutes=14)


def test_new_sign_in_on_another_device_starts_locked(app, maya):
    set_pin(maya)
    other = app.test_client()
    sign_in(other)
    assert other.get("/journal/reset").status_code == 302
    assert maya.get("/journal/reset").status_code == 200


def test_open_redirect_blocked(maya):
    set_pin(maya)
    maya.post("/pin/lock")
    r = maya.post("/pin", data={"pin": "2468", "next": "//evil.example"})
    assert r.headers["Location"].endswith("/journal")


def test_five_wrong_tries_then_reset_by_email(maya):
    set_pin(maya)
    maya.post("/pin/lock")
    for _ in range(5):
        assert maya.post("/pin", data={"pin": "0000"}).status_code == 400
    assert maya.post("/pin", data={"pin": "2468"}).status_code == 400   # right PIN no longer works
    assert maya.get("/journal/reset").status_code == 302
    assert "Too many tries" in maya.get("/pin").data.decode()
    mail.OUTBOX.clear()
    maya.post("/pin/forgot")
    assert "reset your journal PIN" in mail.OUTBOX[-1].text
    assert maya.post("/pin/reset", data={"code": "000000", "pin": "1357", "confirm": "1357"}).status_code == 400
    maya.post("/pin/reset", data={"code": last_code(), "pin": "1357", "confirm": "1357"})
    assert me().pin_failures == 0
    assert "My private thought" in maya.get("/journal/reset").data.decode()
    maya.post("/pin/lock")
    assert maya.post("/pin", data={"pin": "1357"}).status_code == 302


def test_change_needs_current_pin_and_turn_off(maya):
    set_pin(maya)
    maya.post("/pin/lock")
    assert "/pin?next=" in maya.get("/pin/setup").headers["Location"]
    assert maya.post("/pin/remove").status_code == 302 and me().pin_hash is not None
    maya.post("/pin", data={"pin": "2468"})
    maya.post("/pin/remove")
    assert me().pin_hash is None
    assert maya.get("/journal/reset").status_code == 200
