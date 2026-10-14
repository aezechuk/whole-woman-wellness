"""Data model.

Privacy notes:
- Primary keys are random UUIDs; nothing sequential is exposed in URLs.
- Count Me In (demographics) and session feedback are stored with NO
  participant link. Participants only carry a "done" marker so the app doesn't
  ask twice. Those tables come in a later migration.
- Reflections (later) are encrypted with KMS and never readable by admins.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Cohort(db.Model):
    __tablename__ = "cohort"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    join_code_hash: Mapped[str] = mapped_column(String(128))
    join_open: Mapped[bool] = mapped_column(Boolean, default=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instrument_version: Mapped[str] = mapped_column(String(16), default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participants: Mapped[list[Participant]] = relationship(back_populates="cohort")


class Participant(db.Model):
    __tablename__ = "participant"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cohort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cohort.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)  # stored lowercased
    first_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # None = she skipped the question. Used only for group comparisons (n >= 5).
    is_mentee: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    consent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    count_me_in_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cohort: Mapped[Cohort] = relationship(back_populates="participants")
    assessments: Mapped[list[Assessment]] = relationship(
        back_populates="participant", cascade="all, delete-orphan")
    auth_sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="participant", cascade="all, delete-orphan")

    @property
    def profile_complete(self) -> bool:
        return bool(self.first_name)

    @property
    def has_consented(self) -> bool:
        return self.consented_at is not None


class SignInCode(db.Model):
    """A one-time 6-digit code. Only a keyed hash of the code is stored."""

    __tablename__ = "signin_code"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    cohort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cohort.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)


class AuthSession(db.Model):
    """Server-side session; the cookie holds a random token, the DB its hash."""

    __tablename__ = "auth_session"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participant.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    participant: Mapped[Participant] = relationship(back_populates="auth_sessions")


class Assessment(db.Model):
    __tablename__ = "assessment"
    __table_args__ = (UniqueConstraint("participant_id", "timepoint"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participant.id"), index=True)
    timepoint: Mapped[str] = mapped_column(String(8))  # "pre" | "post"
    instrument_version: Mapped[str] = mapped_column(String(16))
    responses: Mapped[dict] = mapped_column(JSON, default=dict)  # item key -> 1..5 (draft-safe)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # engine output, set on completion
    engine_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_late_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    focus_domains: Mapped[list] = mapped_column(JSON, default=list)  # pre only, up to 2

    participant: Mapped[Participant] = relationship(back_populates="assessments")

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None


class DemographicResponse(db.Model):
    """Count Me In answers. Deliberately has no participant id and no timestamp."""

    __tablename__ = "demographic_response"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cohort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cohort.id"), index=True)
    race: Mapped[list] = mapped_column(JSON, default=list)  # empty = skipped
    gender: Mapped[str] = mapped_column(String(16))
    transgender: Mapped[str] = mapped_column(String(16))
    orientation: Mapped[str] = mapped_column(String(16))
    disability: Mapped[str] = mapped_column(String(16))
    age: Mapped[str] = mapped_column(String(16))
    household_size: Mapped[str] = mapped_column(String(16))
    income_band: Mapped[str] = mapped_column(String(16))
