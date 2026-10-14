"""Configuration. Secrets come from the environment (Parameter Store in production)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///dev.db")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "Lax"
    AUTH_COOKIE_NAME = "wwsid"
    AUTH_SESSION_LIFETIME = timedelta(days=60)

    # Sign-in codes
    SIGNIN_CODE_TTL = timedelta(minutes=10)
    SIGNIN_CODE_MAX_ATTEMPTS = 5
    SIGNIN_CODES_PER_EMAIL_PER_HOUR = 5

    # Rate limiting (single instance, so in-memory storage is fine)
    RATELIMIT_STORAGE_URI = "memory://"
    RATELIMIT_HEADERS_ENABLED = False

    # Mail: "console" logs a redacted line (dev); "ses" sends via Amazon SES.
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "console")
    MAIL_FROM = os.environ.get("MAIL_FROM", "Radical Self-Care Reset <hello@example.org>")

    # Admins: an allowlist of emails, set in the environment, never editable in the app.
    ADMIN_EMAILS = [e for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
    ADMIN_SESSION_LIFETIME = timedelta(hours=8)

    # Who participants contact with questions or deletion requests (consent screen, emails).
    CONTACT_NAME = "Arthentic Arts"
    CONTACT_EMAIL = "arthenticarts@gmail.com"

    INSTRUMENT_VERSION = "v1"
    # Final assessment opens at the Care for the Dream session (Nov 18, Eastern).
    FINAL_OPENS_AT = datetime(2026, 11, 18, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    CONSENT_VERSION = "2026-10-v1"

    WTF_CSRF_TIME_LIMIT = None  # tied to the session instead of a clock

    # Public base URL used in email links, e.g. https://wellness.example.org
    PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:5000")

    # Reflection encryption: "kms" in production, "local" for dev/tests.
    JOURNAL_KEY_BACKEND = os.environ.get("JOURNAL_KEY_BACKEND", "local")
    JOURNAL_KMS_KEY_ID = os.environ.get("JOURNAL_KMS_KEY_ID")
    JOURNAL_LOCAL_KEY = os.environ.get("JOURNAL_LOCAL_KEY")
    REFLECTION_MAX_CHARS = 10_000
    TESTIMONIAL_MAX_CHARS = 2_000
    FEEDBACK_MAX_CHARS = 2_000


class DevConfig(Config):
    SESSION_COOKIE_SECURE = False
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-secret")
    JOURNAL_LOCAL_KEY = os.environ.get("JOURNAL_LOCAL_KEY", "ZGV2LW9ubHktbm90LWEtcmVhbC1rZXktMzJieXRlcyE=")


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    # Set TEST_DATABASE_URL to run the suite against PostgreSQL (as in production).
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", "sqlite://")
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    MAIL_BACKEND = "memory"
    JOURNAL_LOCAL_KEY = "dGVzdC1vbmx5LWtleS10aGlydHktdHdvLWJ5dGVzISE="
    PUBLIC_URL = "https://wellness.test"
    ADMIN_EMAILS = ["elle@example.org", "staff@example.org"]


class ProdConfig(Config):
    JOURNAL_KEY_BACKEND = "kms"  # never the local key in production
    MAIL_BACKEND = "ses"
    PREFERRED_URL_SCHEME = "https"


CONFIGS = {"dev": DevConfig, "test": TestConfig, "prod": ProdConfig}
