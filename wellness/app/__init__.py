"""Flask application factory."""

from __future__ import annotations

import os

import click
from flask import Flask, g, request

from .config import CONFIGS
from .extensions import csrf, db, limiter, migrate

# Strict by default. Fonts and scripts are self-hosted, so nothing third-party loads.
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'; object-src 'none'"
)


def create_app(env: str | None = None) -> Flask:
    env = env or os.environ.get("APP_ENV", "dev")
    app = Flask(__name__)
    app.config.from_object(CONFIGS[env])
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY must be set")

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    from . import models  # noqa: F401  (register tables)
    from .assessment import bp as assessment_bp
    from .auth import bp as auth_bp
    from .auth.service import participant_for_token
    from .participant import bp as participant_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(participant_bp)
    app.register_blueprint(assessment_bp)

    @app.before_request
    def load_participant():
        g.participant = participant_for_token(request.cookies.get(app.config["AUTH_COOKIE_NAME"]))

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if g.get("participant") is not None:
            response.headers["Cache-Control"] = "no-store"  # personal pages never cached
        return response

    @app.cli.command("create-cohort")
    @click.option("--name", required=True)
    @click.option("--join-code", required=True)
    @click.option("--capacity", type=int, default=None)
    def create_cohort(name: str, join_code: str, capacity: int | None):
        """Create a cohort with its program join code."""
        from .auth.service import hash_join_code
        from .models import Cohort
        db.session.add(Cohort(name=name, join_code_hash=hash_join_code(join_code), capacity=capacity))
        db.session.commit()
        click.echo(f"Created cohort {name!r}.")

    return app
