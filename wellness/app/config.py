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

    INSTRUMENT_VERSION = "v1"
    # Final assessment opens at the Care for the Dream session (Nov 18, Eastern).
    FINAL_OPENS_AT = datetime(2026, 11, 18, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    CONSENT_VERSION = "2026-10-v1"

    WTF_CSRF_TIME_LIMIT = None  # tied to the session instead of a clock


class DevConfig(Config):
    SESSION_COOKIE_SECURE = False
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-secret")


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    MAIL_BACKEND = "memory"


class ProdConfig(Config):
    pass


CONFIGS = {"dev": DevConfig, "test": TestConfig, "prod": ProdConfig}
