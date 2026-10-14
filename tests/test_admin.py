import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from wellness.app import mail
from wellness.app.admin import reports
from wellness.app.extensions import db
from wellness.app.models import AdminSession, Participant

from .conftest import onboard
from .test_assessment import answer_all

ADMIN = "elle@example.org"


def admin_sign_in(client, email=ADMIN):
    client.post("/admin/signin", data={"email": email})
    msgs = [m for m in mail.OUTBOX if m.subject.startswith("Admin sign-in code")]
    code = re.search(r"\b(\d{6})\b", msgs[-1].text).group(1)
    return client.post("/admin/verify", data={"code": code})


@pytest.fixture
def admin(app):
    c = app.test_client()
    assert admin_sign_in(c).headers["Location"].endswith("/admin/")
    return c


# --- access -------------------------------------------------------------------

def test_admin_pages_require_admin_sign_in(client):
    for path in ("/admin/", "/admin/funder", "/admin/feedback", "/admin/testimonials",
                 "/admin/program", "/admin/export/funder.csv"):
        assert client.get(path).headers["Location"].endswith("/admin/signin")


def test_participant_session_is_not_admin(client):
    onboard(client, ADMIN)  # even with an admin's email address
    assert client.get("/admin/").headers["Location"].endswith("/admin/signin")


def test_non_admin_email_gets_no_code_but_same_response(app):
    c = app.test_client()
    r = c.post("/admin/signin", data={"email": "random@example.com"})
    assert r.headers["Location"].endswith("/admin/verify")
    assert not [m for m in mail.OUTBOX if m.to == "random@example.com"]


def test_admin_sign_in_alerts_all_admins(admin):
    alerts = [m for m in mail.OUTBOX if m.subject == "Admin sign-in to Whole Woman Wellness"]
    assert sorted(m.to for m in alerts) == ["elle@example.org", "staff@example.org"]
    assert admin.get("/admin/").status_code == 200


def test_participant_code_does_not_work_for_admin(app):
    c = app.test_client()
    onboard(c, ADMIN)
    participant_code = re.search(r"\b(\d{6})\b", [m for m in mail.OUTBOX if "sign-in code" in m.subject][0].text).group(1)
    c2 = app.test_client()
    c2.post("/admin/signin", data={"email": ADMIN})
    r = c2.post("/admin/verify", data={"code": participant_code})
    assert r.status_code in (302, 400)
    if r.status_code == 302:  # astronomically unlikely: same 6 digits drawn twice
        pytest.skip("codes collided")
    assert db.session.query(AdminSession).count() == 0


def test_removed_admin_locked_out_immediately(admin, app):
    app.config["ADMIN_EMAILS"] = ["staff@example.org"]
    assert admin.get("/admin/").headers["Location"].endswith("/admin/signin")


def test_admin_signout(admin):
    admin.post("/admin/signout")
    assert admin.get("/admin/").status_code == 302
    assert db.session.scalar(select(AdminSession)).revoked_at is not None


def test_admin_pages_not_cached(admin):
    assert admin.get("/admin/").headers["Cache-Control"] == "no-store"


# --- reports ------------------------------------------------------------------

def test_poverty_categories():
    assert reports.poverty_category("1", "lt_15k") == "at_or_below"      # 7.5k vs 15,960
    assert reports.poverty_category("1", "25_35k") == "101_200"          # 30k vs 31,920
    assert reports.poverty_category("4", "25_35k") == "at_or_below"      # 30k vs 33,000
    assert reports.poverty_category("4", "50_75k") == "101_200"          # 62.5k vs 66,000
    assert reports.poverty_category("2", "100k_plus") == "above_200"
    assert reports.poverty_category("8_plus", "50_75k") == "101_200"
    assert reports.poverty_category("skip", "50_75k") == "unknown"
    assert reports.poverty_line(10) == 55_720 + 2 * 5_680


def test_percentages_always_total_100():
    for counts in ({"a": 1, "b": 1, "c": 1}, {"a": 2, "b": 5, "c": 0, "d": 0}, {"a": 7}):
        pct = reports._percentages(counts, sum(counts.values()))
        assert round(sum(pct.values()), 1) == 100.0


def test_funder_report_counts_non_respondents_as_unknown(app):
    a, b, c = app.test_client(), app.test_client(), app.test_client()
    onboard(a, "a@example.com", "Ana")
    onboard(b, "b@example.com", "Bea")
    onboard(c, "c@example.com", "Cy")
    a.post("/count-me-in", data={"race": ["black", "white"], "gender": "female", "age": "25_54",
                                 "household_size": "1", "income_band": "lt_15k"})
    b.post("/count-me-in", data={"race": ["hispanic"], "gender": "female"})
    f = reports.funder_report()
    assert f["total"] == 3 and f["responded"] == 2
    tables = {t["title"]: {label: (n, pct) for label, n, pct in t["rows"]} for t in f["tables"]}
    race = tables["Race and Ethnicity"]
    assert race["Multi-racial/Multi-ethnic"][0] == 1 and race["Hispanic/Latino/Latina/Latinx"][0] == 1
    assert race["Unknown"][0] == 1                       # Cy never answered
    assert tables["Gender (Question 1)"]["Female"] == (2, 66.7)
    assert tables["Income Level"]["At or below Federal Poverty Level"][0] == 1
    assert tables["Income Level"]["Unknown or decline to state"][0] == 2
    assert tables["Age"]["Youth (11-17)"] == (0, 0.0)
    for t in f["tables"]:
        assert round(sum(p for _, _, p in t["rows"]), 1) == 100.0


def test_outcomes_paired_change_and_groups(app):
    a, b = app.test_client(), app.test_client()
    onboard(a, "a@example.com", "Ana", mentee="yes")
    onboard(b, "b@example.com", "Bea", mentee="no")
    answer_all(a, per_section={4: 2})   # play 25
    answer_all(b, per_section={4: 3})   # play 50
    app.config["FINAL_OPENS_AT"] = datetime(2000, 1, 1, tzinfo=timezone.utc)
    answer_all(a, "post", per_section={4: 4})  # play 75
    a.post("/assessment/post/practices", data={"practices": ["movement"], "program_connection": "5"})
    rows = {r.name: r for r in reports.outcomes("all")}
    play = rows["Play & Pleasure"]
    assert (play.pre_mean, play.pre_n) == (37.5, 2)
    assert (play.post_mean, play.post_n, play.change_mean, play.paired_n, play.improved_pct) == (75.0, 1, 50.0, 1, 100.0)
    mentee = {r.name: r for r in reports.outcomes("mentee")}
    assert mentee["Play & Pleasure"].pre_n == 1 and mentee["Play & Pleasure"].pre_mean == 25.0
    ex = reports.final_extras("all")
    assert ex["n"] == 1 and ex["any_practice"] == 1 and ex["connection_mean"] == 5.0


def test_dashboard_never_shows_individual_text(app, admin):
    p = app.test_client()
    onboard(p, "p@example.com", "Pat")
    from wellness.app import program
    program.load.cache_clear()
    page = admin.get("/admin/").get_data(as_text=True)
    assert "p@example.com" not in page and "Pat" not in page


def test_csv_export_neutralizes_formulas(app, admin):
    p = app.test_client()
    onboard(p, "p@example.com", "Pat")
    p.post("/testimonials/new", data={"body": "=HYPERLINK(\"http://evil\")", "credit": "first", "share": "funder"})
    body = admin.get("/admin/export/testimonials.csv").get_data(as_text=True)
    assert "'=HYPERLINK" in body


def test_program_settings_change_join_code(app, admin):
    admin.post("/admin/program", data={"join_code": "NEWCODE1", "join_open": "on", "capacity": "30"})
    r = app.test_client().post("/start", data={"email": "x@example.com", "join_code": "WIARTS26"})
    assert r.status_code == 400
    r = app.test_client().post("/start", data={"email": "x@example.com", "join_code": "newcode1"})
    assert r.status_code == 302
    admin.post("/admin/program", data={})  # unchecking closes sign-ups
    r = app.test_client().post("/start", data={"email": "y@example.com", "join_code": "NEWCODE1"})
    assert r.status_code == 400
