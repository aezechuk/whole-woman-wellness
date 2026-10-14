"""Sign-in logic, kept free of request handling so it's easy to test.

Flow: email + program (join) code -> 6-digit code by email -> verify -> session.
The same response is shown whether or not the email is already registered,
so the sign-in page can't be used to learn who is in the program.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import func, select

from ..extensions import db
from ..models import AuthSession, Cohort, Participant, SignInCode, utcnow


def _keyed_hash(value: str, purpose: str) -> str:
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def hash_join_code(code: str) -> str:
    return _keyed_hash(normalize_join_code(code), "join")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_join_code(code: str) -> str:
    return "".join(code.split()).upper()


def _aware(dt: datetime) -> datetime:
    # SQLite drops tzinfo; Postgres keeps it. Treat naive values as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def cohort_for_join_code(code: str) -> Cohort | None:
    if not code:
        return None
    candidate = hash_join_code(code)
    for cohort in db.session.scalars(select(Cohort).where(Cohort.join_open.is_(True))):
        if hmac.compare_digest(cohort.join_code_hash, candidate):
            return cohort
    return None


class SignInError(Exception):
    """User-facing message; safe to show."""


@dataclass(frozen=True)
class CodeRequest:
    email: str
    code: str  # plaintext, only to put in the email; never stored


def request_code(email: str, join_code: str) -> CodeRequest:
    email = normalize_email(email)
    cohort = cohort_for_join_code(join_code)
    if cohort is None:
        raise SignInError("That program code isn't right. Check the code from your welcome materials.")

    existing = db.session.scalar(select(Participant).where(Participant.email == email))
    if existing is None and cohort.capacity is not None:
        count = db.session.scalar(
            select(func.count()).select_from(Participant).where(Participant.cohort_id == cohort.id))
        if count >= cohort.capacity:
            raise SignInError("This program is full. Please reach out to the program team.")

    hour_ago = utcnow() - timedelta(hours=1)
    recent = db.session.scalar(
        select(func.count()).select_from(SignInCode)
        .where(SignInCode.email == email, SignInCode.created_at >= hour_ago))
    if recent >= current_app.config["SIGNIN_CODES_PER_EMAIL_PER_HOUR"]:
        raise SignInError("Too many codes requested. Please wait a little while and try again.")

    # Only the newest code works.
    for old in db.session.scalars(
            select(SignInCode).where(SignInCode.email == email, SignInCode.used_at.is_(None),
                                     SignInCode.superseded.is_(False))):
        old.superseded = True

    code = f"{secrets.randbelow(1_000_000):06d}"
    db.session.add(SignInCode(
        email=email,
        code_hash=_keyed_hash(f"{email}:{code}", "signin"),
        cohort_id=cohort.id,
        expires_at=utcnow() + current_app.config["SIGNIN_CODE_TTL"],
    ))
    db.session.commit()
    return CodeRequest(email=email, code=code)


def verify_code(email: str, code: str) -> Participant:
    """Check a code. Creates the participant on first sign-in."""
    email = normalize_email(email)
    code = "".join(ch for ch in code if ch.isdigit())
    record = db.session.scalar(
        select(SignInCode)
        .where(SignInCode.email == email, SignInCode.used_at.is_(None),
               SignInCode.superseded.is_(False))
        .order_by(SignInCode.created_at.desc()))

    generic = SignInError("That code didn't work. Check the most recent email, or request a new code.")
    if record is None or _aware(record.expires_at) < utcnow():
        raise generic
    if record.attempts >= current_app.config["SIGNIN_CODE_MAX_ATTEMPTS"]:
        raise SignInError("Too many tries. Please request a new code.")

    record.attempts += 1
    if not hmac.compare_digest(record.code_hash, _keyed_hash(f"{email}:{code}", "signin")):
        db.session.commit()
        raise generic

    record.used_at = utcnow()
    participant = db.session.scalar(select(Participant).where(Participant.email == email))
    if participant is None:
        participant = Participant(email=email, cohort_id=record.cohort_id)
        db.session.add(participant)
    db.session.commit()
    return participant


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(participant: Participant) -> str:
    """Create a fresh server-side session and return the cookie token."""
    token = secrets.token_urlsafe(32)
    db.session.add(AuthSession(
        participant_id=participant.id,
        token_hash=_token_hash(token),
        expires_at=utcnow() + current_app.config["AUTH_SESSION_LIFETIME"],
    ))
    db.session.commit()
    return token


def participant_for_token(token: str | None) -> Participant | None:
    if not token:
        return None
    sess = db.session.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if sess is None or sess.revoked_at is not None or _aware(sess.expires_at) < utcnow():
        return None
    return sess.participant


def end_session(token: str | None) -> None:
    if not token:
        return
    sess = db.session.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if sess and sess.revoked_at is None:
        sess.revoked_at = utcnow()
        db.session.commit()


def revoke_all_sessions(participant: Participant) -> int:
    count = 0
    for sess in participant.auth_sessions:
        if sess.revoked_at is None:
            sess.revoked_at = utcnow()
            count += 1
    db.session.commit()
    return count
