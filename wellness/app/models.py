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

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
    weekly_emails: Mapped[bool] = mapped_column(Boolean, default=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cohort: Mapped[Cohort] = relationship(back_populates="participants")
    assessments: Mapped[list[Assessment]] = relationship(
        back_populates="participant", cascade="all, delete-orphan")
    auth_sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="participant", cascade="all, delete-orphan")
    journal_entries: Mapped[list[JournalEntry]] = relationship(
        back_populates="participant", cascade="all, delete-orphan")
    testimonials: Mapped[list[Testimonial]] = relationship(
        back_populates="participant", cascade="all, delete-orphan")
    feedback_done: Mapped[list[FeedbackDone]] = relationship(cascade="all, delete-orphan")
    email_log: Mapped[list[EmailLog]] = relationship(cascade="all, delete-orphan")
    intentions: Mapped[list[Intention]] = relationship(
        back_populates="participant", cascade="all, delete-orphan", order_by="Intention.created_at")

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
    purpose: Mapped[str] = mapped_column(String(16), default="participant")  # participant | admin
    cohort_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cohort.id"), nullable=True)
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


class AdminSession(db.Model):
    """Admin sessions are separate from participant sessions and expire after 8 hours."""

    __tablename__ = "admin_session"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    # Final only: practice checklist + program-connection item. None = not yet answered.
    extras: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    participant: Mapped[Participant] = relationship(back_populates="assessments")

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None


class JournalEntry(db.Model):
    """A private reflection. `body` is envelope-encrypted (see crypto.py); never readable by admins."""

    __tablename__ = "journal_entry"
    __table_args__ = (UniqueConstraint("participant_id", "week_key", "prompt_index"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participant.id"), index=True)
    week_key: Mapped[str] = mapped_column(String(32))
    prompt_index: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)  # sealed JSON envelope
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participant: Mapped[Participant] = relationship(back_populates="journal_entries")


class FeedbackDone(db.Model):
    """Marks that a participant rated a session. The rating itself is anonymous."""

    __tablename__ = "feedback_done"
    __table_args__ = (UniqueConstraint("participant_id", "week_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participant.id"), index=True)
    week_key: Mapped[str] = mapped_column(String(32))


class SessionFeedback(db.Model):
    """Anonymous session feedback: no participant id, no timestamp."""

    __tablename__ = "session_feedback"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cohort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cohort.id"), index=True)
    week_key: Mapped[str] = mapped_column(String(32), index=True)
    attendance: Mapped[str] = mapped_column(String(16))  # live | in_person | recording | none
    valuable: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usable: Mapped[int | None] = mapped_column(Integer, nullable=True)
    most_helpful: Mapped[str | None] = mapped_column(Text, nullable=True)
    make_better: Mapped[str | None] = mapped_column(Text, nullable=True)


class Testimonial(db.Model):
    """Shareable by design: staff can read these. She chooses the credit and where it's shared."""

    __tablename__ = "testimonial"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participant.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    credit: Mapped[str] = mapped_column(String(16))  # full | first | anonymous
    credit_name: Mapped[str] = mapped_column(String(160))  # exactly what is shown
    share: Mapped[str] = mapped_column(String(32))  # funder | funder_and_materials
    week_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participant: Mapped[Participant] = relationship(back_populates="testimonials")


class EmailLog(db.Model):
    """One row per scheduled email sent, so nothing is sent twice."""

    __tablename__ = "email_log"
    __table_args__ = (UniqueConstraint("participant_id", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participant.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48))  # e.g. "weekly:reset", "final_reminder"
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    state: Mapped[str] = mapped_column(String(16), default="skip", server_default="skip")


class Intention(db.Model):
    """A habit intention ("I will X at Y in Z" / "After I X, I will Y"). The wording is
    envelope-encrypted and private to the participant; admins never see it."""

    __tablename__ = "intention"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participant.id"), index=True)
    area_key: Mapped[str] = mapped_column(String(32))      # the session/domain it belongs to
    week_key: Mapped[str] = mapped_column(String(32))      # program week it was set in
    body: Mapped[str] = mapped_column(Text)                # sealed JSON: {"kind", "fields"}
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participant: Mapped[Participant] = relationship(back_populates="intentions")
    checkins: Mapped[list[IntentionCheckin]] = relationship(
        back_populates="intention", cascade="all, delete-orphan", order_by="IntentionCheckin.created_at")


class IntentionCheckin(db.Model):
    """Her private copy of a weekly check-in (encrypted), so she can see her own history."""

    __tablename__ = "intention_checkin"
    __table_args__ = (UniqueConstraint("intention_id", "week_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    intention_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("intention.id"), index=True)
    week_key: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)                # sealed JSON: {"days", "next"}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    intention: Mapped[Intention] = relationship(back_populates="checkins")


class AnonCheckin(db.Model):
    """The anonymous copy for reporting: no participant or intention id, no timestamp."""

    __tablename__ = "anon_checkin"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cohort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cohort.id"), index=True)
    week_key: Mapped[str] = mapped_column(String(32))
    area_key: Mapped[str] = mapped_column(String(32))
    days: Mapped[int] = mapped_column(Integer)
    next_step: Mapped[str] = mapped_column(String(16))     # keep | adjust | let_go
    week_number: Mapped[int] = mapped_column(Integer)      # 1st, 2nd, ... check-in on that habit
