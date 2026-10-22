from datetime import datetime, timezone

import pytest
from sqlalchemy import func, inspect, select

from wellness.app import program
from wellness.app.admin import reports
from wellness.app.extensions import db
from wellness.app.models import AnonCheckin, Intention, IntentionCheckin, Participant

from .conftest import onboard
from .test_admin import admin_sign_in

WEEK1 = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)   # The Reset is the current week
WEEK2 = datetime(2026, 10, 29, 15, tzinfo=timezone.utc)   # Embodiment is the current week
WALK = {"kind": "when", "behavior": "take a 10-minute walk", "time": "on my lunch break", "place": "around the block"}


@pytest.fixture
def at(monkeypatch):
    def set_time(when):
        monkeypatch.setattr(program, "utcnow", lambda: when)
    set_time(WEEK1)
    return set_time


@pytest.fixture
def maya(client):
    onboard(client)
    return client


def only(model):
    return db.session.scalar(select(model))


def test_set_intention_is_encrypted_and_shown(maya, at):
    r = maya.post("/intentions/reset", data=WALK)
    assert r.status_code == 302 and r.headers["Location"].endswith("/sessions/reset#intention")
    it = only(Intention)
    assert "walk" not in it.body and it.area_key == "reset" and it.week_key == "reset"
    sentence = "I will take a 10-minute walk on my lunch break around the block."
    assert sentence in maya.get("/sessions/reset").data.decode()
    assert sentence in maya.get("/").data.decode()
    assert sentence in maya.get("/intentions").data.decode()


def test_habit_stack_format(maya, at):
    maya.post("/intentions/nourishment", data={"kind": "after", "cue": "clean up after dinner", "behavior": "prep lunch"})
    assert "After I clean up after dinner, I will prep lunch." in maya.get("/").data.decode()


@pytest.mark.parametrize("data, msg", [
    ({"kind": "when", "behavior": "walk"}, "when or where"),
    ({"kind": "after", "behavior": "walk"}, "after"),
    ({"kind": "when", "time": "at noon"}, "what you will do"),
    ({"kind": "bogus", "behavior": "walk", "time": "noon"}, "format"),
])
def test_vague_intentions_are_rejected(maya, at, data, msg):
    maya.post("/intentions/reset", data=data)
    assert db.session.scalar(select(func.count()).select_from(Intention)) == 0
    assert msg in maya.get("/sessions/reset").data.decode()


def test_unknown_area_404(maya, at):
    assert maya.post("/intentions/nope", data=WALK).status_code == 404


def test_focus_area_tag(maya, at):
    from tests.test_admin import answer_all
    answer_all(maya)
    maya.post("/results/focus", data={"focus": ["embodiment"]})
    assert "One of your focus areas" in maya.get("/sessions/embodiment").data.decode()
    assert "One of your focus areas" not in maya.get("/sessions/play").data.decode()


def test_checkin_only_the_following_week(maya, at):
    maya.post("/intentions/reset", data=WALK)
    it = only(Intention)
    assert b"How many days did you do it?" not in maya.get("/sessions/reset").data
    maya.post(f"/intentions/{it.id}/checkin", data={"days": "3", "next": "keep"})
    assert db.session.scalar(select(func.count()).select_from(AnonCheckin)) == 0   # not due yet
    at(WEEK2)
    assert b"How many days did you do it?" in maya.get("/sessions/embodiment").data
    assert b"Check in on 1 intention" in maya.get("/").data
    r = maya.post(f"/intentions/{it.id}/checkin", data={"days": "3", "next": "keep"})
    assert r.headers["Location"].endswith("/sessions/embodiment#checkin")
    # Her private copy is encrypted; the anonymous copy has no link back to her.
    private = only(IntentionCheckin)
    assert private.week_key == "embodiment" and '"days"' not in private.body
    anon = only(AnonCheckin)
    assert (anon.days, anon.next_step, anon.area_key, anon.week_number) == (3, "keep", "reset", 1)
    cols = {c["name"] for c in inspect(db.engine).get_columns("anon_checkin")}
    assert not cols & {"participant_id", "intention_id", "created_at"}
    home = maya.get("/intentions").data.decode()
    assert "kept 1 week" in home and "3/7" in home
    # Only once per week.
    maya.post(f"/intentions/{it.id}/checkin", data={"days": "7", "next": "keep"})
    assert db.session.scalar(select(func.count()).select_from(AnonCheckin)) == 1


def test_let_go_and_adjust(maya, at):
    maya.post("/intentions/reset", data=WALK)
    maya.post("/intentions/embodiment", data={"kind": "after", "cue": "brush my teeth", "behavior": "stretch"})
    by_area = {i.area_key: i for i in db.session.scalars(select(Intention))}
    walk, stretch = by_area["reset"], by_area["embodiment"]
    at(WEEK2)
    r = maya.post(f"/intentions/{walk.id}/checkin", data={"days": "1", "next": "let_go"})
    db.session.refresh(walk)
    assert not walk.active
    # Adjusting sends her to the edit form, prefilled; saving updates the same intention.
    at(datetime(2026, 11, 5, 15, tzinfo=timezone.utc))
    r = maya.post(f"/intentions/{stretch.id}/checkin", data={"days": "4", "next": "adjust"})
    assert f"edit={stretch.id}" in r.headers["Location"]
    page = maya.get(f"/sessions/embodiment?edit={stretch.id}").data.decode()
    assert 'value="brush my teeth"' in page and "Save changes" in page
    maya.post("/intentions/embodiment", data={"intention_id": str(stretch.id), "kind": "after",
                                              "cue": "brush my teeth", "behavior": "stretch for 5 minutes"})
    assert db.session.scalar(select(func.count()).select_from(Intention)) == 2
    assert "stretch for 5 minutes" in maya.get("/intentions").data.decode()


def test_let_go_from_list(maya, at):
    maya.post("/intentions/reset", data=WALK)
    it = only(Intention)
    maya.post(f"/intentions/{it.id}/let-go")
    db.session.refresh(it)
    assert not it.active and "LET GO" in maya.get("/intentions").data.decode()


def test_cannot_touch_someone_elses_intention(app, at):
    a, b = app.test_client(), app.test_client()
    onboard(a, "a@example.com")
    onboard(b, "b@example.com")
    a.post("/intentions/reset", data=WALK)
    it = only(Intention)
    at(WEEK2)
    assert b.post(f"/intentions/{it.id}/checkin", data={"days": "3", "next": "keep"}).status_code == 404
    assert b.post(f"/intentions/{it.id}/let-go").status_code == 404
    assert b.post("/intentions/reset", data={**WALK, "intention_id": str(it.id)}).status_code == 404
    page = b.get(f"/sessions/reset?edit={it.id}").data.decode()
    assert str(it.id) not in page and 'value="take a 10-minute walk"' not in page


def test_active_limit(maya, at):
    for i in range(6):
        maya.post("/intentions/reset", data={**WALK, "behavior": f"habit {i}"})
    maya.post("/intentions/reset", data={**WALK, "behavior": "one too many"})
    assert db.session.scalar(select(func.count()).select_from(Intention)) == 6


def test_admin_sees_group_numbers_never_wording(app, at):
    c = app.test_client()
    onboard(c)
    c.post("/intentions/reset", data=WALK)
    at(WEEK2)
    c.post(f"/intentions/{only(Intention).id}/checkin", data={"days": "5", "next": "keep"})
    h = reports.intentions_summary()
    assert (h["set_any"], h["set_any_pct"], h["checkins"], h["avg_days"], h["five_plus_pct"]) == (1, 100.0, 1, 5.0, 100.0)
    admin = app.test_client()
    admin_sign_in(admin)
    page = admin.get("/admin/").data.decode()
    assert "Intentions and habits" in page and "walk" not in page


def test_download_and_delete(maya, at):
    maya.post("/intentions/reset", data=WALK)
    at(WEEK2)
    maya.post(f"/intentions/{only(Intention).id}/checkin", data={"days": "2", "next": "keep"})
    body = maya.get("/settings/download").data.decode()
    assert "I will take a 10-minute walk" in body and "2 of 7 days" in body
    maya.post("/settings/delete", data={"confirm": "DELETE"})
    assert db.session.scalar(select(func.count()).select_from(Participant)) == 0
    assert db.session.scalar(select(func.count()).select_from(Intention)) == 0
    assert db.session.scalar(select(func.count()).select_from(IntentionCheckin)) == 0
    assert db.session.scalar(select(func.count()).select_from(AnonCheckin)) == 1   # anonymous, stays for the report


# --- calendar reminders -------------------------------------------------------------

REMIND = {**WALK, "rdays": ["MO", "WE"], "rtime": "12:15", "rminutes": "15", "runtil": "program", "rtitle": "full"}


def test_reminder_saved_encrypted_and_calendar_file(maya, at):
    maya.post("/intentions/reset", data={**REMIND, "rtitle": "custom", "rcustom": "Walk, by the park; outside"})
    it = only(Intention)
    assert "12:15" not in it.body
    home = maya.get("/intentions").data.decode()
    assert "Mon, Wed at 12:15 PM · 15 min" in home and "Add to my calendar" in home
    r = maya.get(f"/intentions/{it.id}/calendar.ics")
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("text/calendar")
    assert r.headers["Cache-Control"] == "no-store"
    ics = r.data.decode()
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261026T121500" in ics          # first Monday on/after Thu Oct 22
    assert "DTEND:20261026T123000" in ics
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261130T235959" in ics
    assert "TRIGGER:-PT10M" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    unfolded = ics.replace("\r\n ", "")
    assert "SUMMARY:Walk\\, by the park\\; outside" in unfolded


def test_neutral_title_keeps_wording_out_of_calendar(maya, at):
    maya.post("/intentions/reset", data={**REMIND, "rtitle": "neutral", "runtil": "4"})
    ics = maya.get(f"/intentions/{only(Intention).id}/calendar.ics").data.decode().replace("\r\n ", "")
    assert "SUMMARY:Self-Care: My Intention" in ics and "walk" not in ics and "LOCATION" not in ics
    assert "UNTIL=20261119T235959" in ics           # 4 weeks from Oct 22


def test_reminder_needs_a_time(maya, at):
    maya.post("/intentions/reset", data={**REMIND, "rtime": ""})
    assert db.session.scalar(select(func.count()).select_from(Intention)) == 0
    assert "Pick a time" in maya.get("/sessions/reset").data.decode()


def test_no_reminder_no_calendar_and_owner_only(app, at):
    a, b = app.test_client(), app.test_client()
    onboard(a, "a@example.com")
    onboard(b, "b@example.com")
    a.post("/intentions/reset", data=WALK)
    plain = only(Intention)
    assert a.get(f"/intentions/{plain.id}/calendar.ics").status_code == 404
    a.post("/intentions/reset", data={**REMIND, "intention_id": str(plain.id)})
    assert a.get(f"/intentions/{plain.id}/calendar.ics").status_code == 200
    assert b.get(f"/intentions/{plain.id}/calendar.ics").status_code == 404


def test_editing_keeps_or_removes_reminder(maya, at):
    maya.post("/intentions/reset", data=REMIND)
    it = only(Intention)
    page = maya.get(f"/sessions/reset?edit={it.id}").data.decode()
    assert 'value="12:15"' in page and 'value="MO" checked' in page
    maya.post("/intentions/reset", data={**WALK, "intention_id": str(it.id)})   # no days = no reminder
    assert maya.get(f"/intentions/{it.id}/calendar.ics").status_code == 404


def test_no_end_date_and_custom_title(maya, at):
    maya.post("/intentions/reset", data={**REMIND, "runtil": "forever", "rtitle": "custom", "rcustom": "Lunch walk, outside"})
    it = only(Intention)
    assert "no end date" in maya.get("/intentions").data.decode()
    ics = maya.get(f"/intentions/{it.id}/calendar.ics").data.decode().replace("\r\n ", "")
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO,WE\r\n" in ics and "UNTIL" not in ics
    assert "SUMMARY:Lunch walk\\, outside" in ics and "LOCATION" not in ics


def test_custom_title_required_when_chosen(maya, at):
    maya.post("/intentions/reset", data={**REMIND, "rtitle": "custom", "rcustom": "  "})
    assert db.session.scalar(select(func.count()).select_from(Intention)) == 0
    assert "Add a title for your calendar event" in maya.get("/sessions/reset").data.decode()


def test_save_and_add_to_calendar_in_one_step(maya, at):
    r = maya.post("/intentions/reset", data={**REMIND, "then": "calendar"})
    it = only(Intention)
    assert r.headers["Location"].endswith(f"/sessions/reset?added={it.id}#intention")
    page = maya.get(f"/sessions/reset?added={it.id}").data.decode()
    assert f'data-open-calendar="/intentions/{it.id}/calendar.ics"' in page   # opens automatically
    assert "ONE MORE TAP" not in page and page.count("I will take a 10-minute walk") == 1
    assert "✓ On your calendar" in page and "Add to my calendar</a>" not in page


def test_save_and_add_needs_days(maya, at):
    maya.post("/intentions/reset", data={**WALK, "then": "calendar"})
    assert db.session.scalar(select(func.count()).select_from(Intention)) == 0
    assert "Pick at least one day" in maya.get("/sessions/reset").data.decode()


def test_intention_without_reminder_offers_to_add_one(maya, at):
    maya.post("/intentions/reset", data=WALK)
    it = only(Intention)
    for page in (maya.get("/sessions/reset").data.decode(), maya.get("/intentions").data.decode()):
        assert "Add a calendar reminder" in page and f"edit={it.id}" in page
    form = maya.get(f"/sessions/reset?edit={it.id}&remind=1").data.decode()
    assert 'id="reminder" open' in form


def test_calendar_button_disappears_once_added(maya, at):
    maya.post("/intentions/reset", data=REMIND)
    it = only(Intention)
    assert "Add to my calendar</a>" in maya.get("/intentions").data.decode()
    maya.get(f"/intentions/{it.id}/calendar.ics")
    home = maya.get("/intentions").data.decode()
    assert "✓ On your calendar" in home and "Add to my calendar</a>" not in home
    assert "Add to my calendar</a>" not in maya.get("/intentions").data.decode()
    # Re-saving unchanged keeps it on the calendar ...
    maya.post("/intentions/reset", data={**REMIND, "intention_id": str(it.id)})
    assert "✓ On your calendar" in maya.get("/intentions").data.decode()
    # ... and so does new wording (the event title doesn't include it).
    maya.post("/intentions/reset", data={**REMIND, "behavior": "take a 15-minute walk", "intention_id": str(it.id)})
    assert "✓ On your calendar" in maya.get("/intentions").data.decode()
    maya.post("/intentions/reset", data={**REMIND, "rtime": "13:00", "intention_id": str(it.id)})
    home = maya.get("/intentions").data.decode()
    assert "✓ On your calendar" not in home and "Add to my calendar</a>" in home


def test_mark_already_on_calendar_without_download(app, at):
    a, b = app.test_client(), app.test_client()
    onboard(a, "a@example.com")
    onboard(b, "b@example.com")
    a.post("/intentions/reset", data=REMIND)
    it = only(Intention)
    assert b.post(f"/intentions/{it.id}/on-calendar").status_code == 404
    r = a.post(f"/intentions/{it.id}/on-calendar", data={"back": "/intentions"})
    assert r.headers["Location"].endswith("/intentions")
    assert "✓ On your calendar" in a.get("/intentions").data.decode()
    assert a.post(f"/intentions/{it.id}/on-calendar", data={"back": "https://evil.example"}).headers["Location"].endswith("/")


def test_title_choices_are_neutral_or_custom(maya, at):
    page = maya.get("/sessions/reset").data.decode()
    assert 'value="neutral" checked' in page and 'value="custom"' in page and 'value="full"' not in page
    maya.post("/intentions/reset", data={k: v for k, v in REMIND.items() if k != "rtitle"})
    ics = maya.get(f"/intentions/{only(Intention).id}/calendar.ics").data.decode()
    assert "SUMMARY:Self-Care: My Intention" in ics


def test_home_shows_a_short_preview(maya, at):
    for i in range(5):
        maya.post("/intentions/reset", data={**WALK, "behavior": f"habit number {i}"})
    home = maya.get("/").data.decode()
    assert "<strong>5</strong> going" in home and "+ 3 more" in home and "See all my intentions" in home
    assert "habit number 4" in home and "habit number 3" in home and "habit number 0" not in home   # newest first
    assert "Add to my calendar" not in home and "Add a calendar reminder" not in home
