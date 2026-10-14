from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, select

from wellness.app.assessment import service
from wellness.app.extensions import db
from wellness.app.models import Assessment, DemographicResponse, Participant

from .conftest import onboard

INST = service.instrument("v1")
SECTIONS = service.sections(INST)


def answer_all(client, timepoint="pre", per_section=None):
    """Walk every section. per_section maps section number -> value for all its items."""
    per_section = per_section or {}
    r = None
    for n, sec in enumerate(SECTIONS, start=1):
        v = per_section.get(n, 3)
        data = {i.key: str(v) for i in sec.items}
        r = client.post(f"/assessment/{timepoint}/{n}", data=data)
    return r


@pytest.fixture
def signed_in(client):
    onboard(client)
    return client


def test_count_me_in_offered_after_consent(client):
    from .conftest import sign_in
    sign_in(client)
    client.post("/welcome", data={"first_name": "Maya"})
    r = client.post("/consent", data={"agree": "yes"})
    assert "/count-me-in?first=1" in r.headers["Location"]


def test_full_baseline_flow(signed_in):
    c = signed_in
    assert c.get("/assessment/pre").headers["Location"].endswith("/assessment/pre/1")
    r = answer_all(c, per_section={4: 1, 3: 5})  # play low, nourishment high
    assert r.headers["Location"].endswith("/results")
    page = c.get("/results").get_data(as_text=True)
    assert "your whole woman snapshot" in page
    assert "FLOWING MOST EASILY" in page and "Nourishment" in page
    assert "Play &amp; Pleasure (Nov 11) makes room for exactly that." in page
    assert "They aren&#39;t a grade" in page                      # intro copy renders
    assert "where would you most like to feel a meaningful" in page   # focus prompt renders
    a = db.session.scalar(select(Assessment))
    assert a.completed_at and a.result["engine_version"] == "1.0.0"


def test_missing_answer_keeps_saved_ones(signed_in):
    c = signed_in
    sec = SECTIONS[0]
    partial = {sec.items[0].key: "4", sec.items[1].key: "2"}
    r = c.post("/assessment/pre/1", data=partial)
    assert r.status_code == 400
    a = db.session.scalar(select(Assessment))
    assert a.responses == {sec.items[0].key: 4, sec.items[1].key: 2}
    # Coming back resumes at section 1, answers pre-filled.
    assert c.get("/assessment/pre").headers["Location"].endswith("/pre/1")
    assert 'value="4" checked' in c.get("/assessment/pre/1").get_data(as_text=True)


def test_invalid_values_ignored(signed_in):
    c = signed_in
    sec = SECTIONS[0]
    data = {i.key: "9" for i in sec.items}
    data[sec.items[0].key] = "abc"
    assert c.post("/assessment/pre/1", data=data).status_code == 400
    assert db.session.scalar(select(Assessment)).responses == {}


def test_back_saves_and_goes_back(signed_in):
    c = signed_in
    answer_first = {i.key: "3" for i in SECTIONS[0].items}
    c.post("/assessment/pre/1", data=answer_first)
    r = c.post("/assessment/pre/2", data={SECTIONS[1].items[0].key: "5", "action": "back"})
    assert r.headers["Location"].endswith("/pre/1")
    assert db.session.scalar(select(Assessment)).responses[SECTIONS[1].items[0].key] == 5


def test_skipping_ahead_redirects_to_gap(signed_in):
    c = signed_in
    data = {i.key: "3" for i in SECTIONS[-1].items}
    r = c.post(f"/assessment/pre/{len(SECTIONS)}", data=data)
    assert r.headers["Location"].endswith("/pre/1")
    assert db.session.scalar(select(Assessment)).completed_at is None


def test_completed_assessment_cannot_be_edited(signed_in):
    c = signed_in
    answer_all(c)
    r = c.post("/assessment/pre/1", data={i.key: "1" for i in SECTIONS[0].items})
    assert r.headers["Location"].endswith("/results")
    assert db.session.scalar(select(Assessment)).responses[SECTIONS[0].items[0].key] == 3


def test_out_of_range_section_404(signed_in):
    assert signed_in.get("/assessment/pre/0").status_code == 404
    assert signed_in.get(f"/assessment/pre/{len(SECTIONS) + 1}").status_code == 404
    assert signed_in.get("/assessment/mid").status_code == 404


def test_focus_areas(signed_in):
    c = signed_in
    answer_all(c)
    assert c.post("/results/focus", data={"focus": ["play", "reset"]}).status_code == 302
    assert db.session.scalar(select(Assessment)).focus_domains == ["reset", "play"]
    c.post("/results/focus", data={"focus": ["play", "reset", "dream"]})
    assert db.session.scalar(select(Assessment)).focus_domains == ["reset", "play"]  # unchanged
    c.post("/results/focus", data={"focus": ["bogus"]})
    assert db.session.scalar(select(Assessment)).focus_domains == []


def test_late_baseline_flag(signed_in, app, monkeypatch):
    from wellness.app.assessment import service as svc
    late = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(svc, "utcnow", lambda: late)
    answer_all(signed_in)
    assert db.session.scalar(select(Assessment)).is_late_baseline is True


def test_final_closed_before_nov_18(signed_in):
    assert signed_in.get("/assessment/post").status_code == 404
    assert b"final assessment" not in signed_in.get("/").data.lower()


def test_final_comparison(signed_in, app):
    c = signed_in
    answer_all(c, per_section={4: 2, 3: 4})       # play 25, nourishment 75
    c.post("/results/focus", data={"focus": ["play"]})
    app.config["FINAL_OPENS_AT"] = datetime(2000, 1, 1, tzinfo=timezone.utc)
    r = answer_all(c, "post", per_section={4: 4, 3: 3})  # play 75, nourishment 50
    assert r.headers["Location"].endswith("/results/final")
    page = c.get("/results/final").get_data(as_text=True)
    assert "then → now" in page
    assert "25 → 75" in page and "+50" in page                 # focus area
    assert "Nourishment feels different than it did at the beginning." in page
    assert "Play &amp; Pleasure" in page


def test_users_only_see_their_own_results(app):
    a, b = app.test_client(), app.test_client()
    onboard(a, "a@example.com", "Ana")
    onboard(b, "b@example.com", "Bea")
    answer_all(a, per_section={1: 5})
    # Bea has no assessment; her /results must not show Ana's.
    r = b.get("/results")
    assert r.status_code == 302 and r.headers["Location"].endswith("/assessment/pre")


def test_count_me_in_is_anonymous(signed_in):
    c = signed_in
    r = c.post("/count-me-in", data={"race": ["black", "hispanic", "bogus"], "gender": "female",
                                     "age": "25_54", "income_band": "50_75k", "household_size": "3",
                                     "orientation": "nope"})
    assert r.status_code == 302
    row = db.session.scalar(select(DemographicResponse))
    assert row.race == ["black", "hispanic"] and row.orientation == "skip" and row.transgender == "skip"
    cols = {col.name for col in inspect(DemographicResponse).columns}
    assert not any("participant" in c or c.endswith("_at") for c in cols)
    p = db.session.scalar(select(Participant))
    assert p.count_me_in_done_at is not None
    # Second submission is refused (shows the thank-you page instead).
    c.post("/count-me-in", data={"gender": "male"})
    assert db.session.query(DemographicResponse).count() == 1


def test_count_me_in_maybe_later_stores_nothing(signed_in):
    signed_in.post("/count-me-in", data={"action": "later", "gender": "female"})
    assert db.session.query(DemographicResponse).count() == 0
    assert db.session.scalar(select(Participant)).count_me_in_done_at is None
