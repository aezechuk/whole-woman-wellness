from datetime import timedelta

from sqlalchemy import select

from wellness.app import mail
from wellness.app.extensions import db
from wellness.app.models import AuthSession, Participant, SignInCode, utcnow

from .conftest import JOIN_CODE, last_code, onboard, sign_in


def test_wrong_join_code_sends_nothing(client):
    r = client.post("/start", data={"email": "a@example.com", "join_code": "NOPE"})
    assert r.status_code == 400
    assert mail.OUTBOX == []


def test_join_code_is_case_and_space_insensitive(client):
    r = client.post("/start", data={"email": "a@example.com", "join_code": " wiarts 26 "})
    assert r.status_code == 302 and len(mail.OUTBOX) == 1


def test_full_onboarding_flow(client):
    r = sign_in(client, "Maya@Example.com ")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert client.get("/").headers["Location"].endswith("/welcome")  # profile first
    client.post("/welcome", data={"first_name": "Maya", "mentee": "yes"})
    assert client.get("/").headers["Location"].endswith("/consent")  # then consent
    client.post("/consent", data={"agree": "yes"})
    r = client.get("/")
    assert r.status_code == 200 and b"hi, maya" in r.data
    p = db.session.scalar(select(Participant))
    assert p.email == "maya@example.com" and p.is_mentee is True and p.consent_version


def test_mentee_skip_stores_none(client):
    onboard(client, mentee="skip")
    assert db.session.scalar(select(Participant)).is_mentee is None


def test_consent_requires_checkbox(client):
    sign_in(client)
    client.post("/welcome", data={"first_name": "Maya"})
    assert client.post("/consent", data={}).status_code == 400


def test_codes_are_hashed_not_stored(client):
    client.post("/start", data={"email": "a@example.com", "join_code": JOIN_CODE})
    code = last_code()
    rec = db.session.scalar(select(SignInCode))
    assert code not in rec.code_hash


def test_wrong_code_then_lockout(client, app):
    client.post("/start", data={"email": "a@example.com", "join_code": JOIN_CODE})
    real = last_code()
    wrong = f"{(int(real) + 1) % 1_000_000:06d}"
    for _ in range(app.config["SIGNIN_CODE_MAX_ATTEMPTS"]):
        assert client.post("/verify", data={"code": wrong}).status_code == 400
    # Even the right code fails once attempts are used up.
    r = client.post("/verify", data={"code": real})
    assert r.status_code == 400 and b"Too many tries" in r.data


def test_expired_code_rejected(client):
    client.post("/start", data={"email": "a@example.com", "join_code": JOIN_CODE})
    code = last_code()
    rec = db.session.scalar(select(SignInCode))
    rec.expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert client.post("/verify", data={"code": code}).status_code == 400


def test_only_newest_code_works(client):
    client.post("/start", data={"email": "a@example.com", "join_code": JOIN_CODE})
    first = last_code()
    client.post("/start", data={"email": "a@example.com", "join_code": JOIN_CODE})
    second = last_code()
    if first != second:
        assert client.post("/verify", data={"code": first}).status_code == 400
    assert client.post("/verify", data={"code": second}).status_code == 302


def test_code_cannot_be_reused(client, app):
    client.post("/start", data={"email": "a@example.com", "join_code": JOIN_CODE})
    code = last_code()
    assert client.post("/verify", data={"code": code}).status_code == 302
    other = app.test_client()
    with other.session_transaction() as s:
        s["pending_email"] = "a@example.com"
    assert other.post("/verify", data={"code": code}).status_code == 400


def test_per_email_code_limit(client, app):
    limit = app.config["SIGNIN_CODES_PER_EMAIL_PER_HOUR"]
    for _ in range(limit):
        client.post("/start", data={"email": "a@example.com", "join_code": JOIN_CODE})
    r = client.post("/start", data={"email": "a@example.com", "join_code": JOIN_CODE})
    assert r.status_code == 400 and len(mail.OUTBOX) == limit


def test_same_response_for_new_and_existing_email(client, app):
    onboard(client, "known@example.com")
    c2 = app.test_client()
    known = c2.post("/start", data={"email": "known@example.com", "join_code": JOIN_CODE})
    c3 = app.test_client()
    new = c3.post("/start", data={"email": "new@example.com", "join_code": JOIN_CODE})
    assert known.status_code == new.status_code == 302
    assert known.headers["Location"] == new.headers["Location"]


def test_capacity_blocks_new_but_not_existing(client, app):
    from wellness.app.models import Cohort
    onboard(client, "first@example.com")
    cohort = db.session.scalar(select(Cohort))
    cohort.capacity = 1
    db.session.commit()
    r = app.test_client().post("/start", data={"email": "second@example.com", "join_code": JOIN_CODE})
    assert r.status_code == 400
    r = app.test_client().post("/start", data={"email": "first@example.com", "join_code": JOIN_CODE})
    assert r.status_code == 302


def test_auth_cookie_flags_and_db_token_hashed(client, app):
    r = sign_in(client)
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    token = cookie.split("=", 1)[1].split(";", 1)[0]
    stored = db.session.scalar(select(AuthSession))
    assert token != stored.token_hash and token not in stored.token_hash


def test_signout_revokes_session(client):
    onboard(client)
    assert client.get("/").status_code == 200
    client.post("/signout")
    assert client.get("/").status_code == 302
    assert db.session.scalar(select(AuthSession)).revoked_at is not None


def test_security_headers(client):
    r = client.get("/start")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_signed_in_pages_not_cached(client):
    onboard(client)
    assert client.get("/").headers["Cache-Control"] == "no-store"


def test_unauthenticated_redirects(client):
    for path in ("/", "/welcome", "/consent"):
        assert client.get(path).headers["Location"].endswith("/start")


def test_healthz(client):
    assert client.get("/healthz").get_json() == {"ok": True}


def test_prod_refuses_to_start_without_kms(monkeypatch):
    import pytest
    from wellness.app import create_app
    monkeypatch.setenv("SECRET_KEY", "x")
    with pytest.raises(RuntimeError, match="KMS"):
        create_app("prod")


def test_send_failure_shows_message_and_frees_the_slot(client, app, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("SES down")
    monkeypatch.setattr(mail, "send", boom)
    for _ in range(app.config["SIGNIN_CODES_PER_EMAIL_PER_HOUR"] + 1):
        r = client.post("/start", data={"email": "a@example.com", "join_code": JOIN_CODE})
        assert r.status_code == 503 and b"couldn" in r.data
    assert db.session.scalar(select(SignInCode)) is None  # failed sends don't count


def test_funder_named_on_pages_and_in_emails(client):
    assert b"Supported by the Women&#39;s Foundation of Indiana" in client.get("/start").data
    client.post("/start", data={"email": "a@example.com", "join_code": JOIN_CODE})
    assert "Supported by the Women's Foundation of Indiana" in mail.OUTBOX[-1].text


def test_email_format_check():
    from wellness.app.auth.emailcheck import is_valid, suggestion
    for ok in ("maya@example.com", "m.j+wiarts@sub.example.org", "a@b.co"):
        assert is_valid(ok), ok
    for bad in ("maya@gmail", "maya@", "@gmail.com", "maya gmail.com", "maya@@gmail.com", "maya@gmail..com",
                "maya.@gmail.com", "maya@gmail.c0m", "maya@-gmail.com"):
        assert not is_valid(bad), bad
    assert suggestion("maya@gmial.com") == "maya@gmail.com"
    assert suggestion("maya@gmail.con") == "maya@gmail.com"
    assert suggestion("maya@arthenticarts.ogr") == "maya@arthenticarts.org"
    assert suggestion("maya@gmail.com") is None


def test_incomplete_email_rejected_before_sending(client):
    r = client.post("/start", data={"email": "maya@gmail", "join_code": JOIN_CODE})
    assert r.status_code == 400 and b"looks incomplete" in r.data and mail.OUTBOX == []


def test_typo_suggested_then_original_can_be_confirmed(client):
    r = client.post("/start", data={"email": "maya@gmial.com", "join_code": JOIN_CODE})
    assert r.status_code == 400 and b"Did you mean maya@gmail.com?" in r.data
    assert b'value="maya@gmail.com"' in r.data and mail.OUTBOX == []
    r = client.post("/start", data={"email": "maya@gmial.com", "join_code": JOIN_CODE, "confirmed_email": "maya@gmial.com"})
    assert r.status_code == 302 and mail.OUTBOX[-1].to == "maya@gmial.com"


def test_admin_signin_rejects_incomplete_email(client):
    r = client.post("/admin/signin", data={"email": "elle@example"})
    assert r.status_code == 400 and mail.OUTBOX == []


def test_static_urls_are_versioned(client):
    html = client.get("/start").data.decode()
    assert "/static/css/app.css?v=" in html and "/static/img/arthentic-logo.png?v=" in html
