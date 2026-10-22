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
    app.config["SECRET_KEY"] = app.config.get("SECRET_KEY") or os.environ.get("SECRET_KEY")
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY must be set")

    if env == "prod":
        # Caddy terminates TLS and forwards one hop; trust exactly that hop.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        if app.config["JOURNAL_KEY_BACKEND"] != "kms" or not app.config["JOURNAL_KMS_KEY_ID"]:
            raise RuntimeError("Production requires JOURNAL_KMS_KEY_ID (KMS encryption for reflections)")
        if not app.config["ADMIN_EMAILS"]:
            raise RuntimeError("Production requires ADMIN_EMAILS")

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    from . import models  # noqa: F401  (register tables)
    from .admin import bp as admin_bp
    from .assessment import bp as assessment_bp
    from .auth import bp as auth_bp
    from .auth.service import participant_for_token
    from .participant import bp as participant_bp
    from .reflect import bp as reflect_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(participant_bp)
    app.register_blueprint(assessment_bp)
    app.register_blueprint(reflect_bp)
    app.register_blueprint(admin_bp)

    # Cache-busting: every static URL carries a short hash of the file, so phones
    # fetch fresh CSS/JS/images after each deploy instead of reusing a stale copy.
    import hashlib
    from functools import lru_cache

    @lru_cache(maxsize=256)
    def _static_version(filename: str) -> str:
        try:
            with app.open_resource(f"static/{filename}") as f:
                return hashlib.sha256(f.read()).hexdigest()[:10]
        except (FileNotFoundError, OSError):
            return ""

    @app.url_defaults
    def _bust_static(endpoint, values):
        if endpoint == "static" and "filename" in values and "v" not in values:
            v = _static_version(values["filename"])
            if v:
                values["v"] = v

    @app.get("/healthz")
    @limiter.exempt
    def healthz():
        from sqlalchemy import text
        db.session.execute(text("SELECT 1"))
        return {"ok": True}

    @app.template_filter("local")
    def local_time(dt):
        """Show stored UTC times in the program's time zone (Indianapolis)."""
        from datetime import timezone as _tz
        from . import program
        if dt is None:
            return dt
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt.astimezone(program.load().tz)

    @app.context_processor
    def contact():
        return {"contact_name": app.config["CONTACT_NAME"], "contact_email": app.config["CONTACT_EMAIL"],
                "funder_name": app.config["FUNDER_NAME"]}

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

    @app.cli.command("send-due-emails")
    def send_due_emails():
        """Send scheduled emails that are due. Safe to run repeatedly (cron, every 15 min)."""
        from .notify import due_emails
        click.echo(f"Sent {due_emails()} email(s).")

    @app.cli.command("purge-program-data")
    @click.option("--confirm", is_flag=True, help="Required. Permanently deletes all participant data.")
    def purge_program_data(confirm: bool):
        """Delete all program data after the deletion date (Feb 28, 2027)."""
        from . import program
        from .models import (WinCheer, WinComment, WinPost, WinReport, AnonCheckin, Assessment, AuthSession, DemographicResponse, EmailLog, FeedbackDone,
                             Intention, IntentionCheckin, JournalEntry, Participant, SessionFeedback,
                             SignInCode, Testimonial, utcnow)
        if utcnow() < program.load().data_deletion:
            raise click.ClickException("The deletion date hasn't arrived yet.")
        if not confirm:
            raise click.ClickException("Re-run with --confirm. Export the funder report first.")
        for model in (WinReport, WinCheer, WinComment, WinPost, IntentionCheckin, Intention, AnonCheckin, JournalEntry, Testimonial, FeedbackDone, EmailLog, AuthSession, Assessment,
                      SignInCode, Participant, SessionFeedback, DemographicResponse):
            db.session.query(model).delete()
        db.session.commit()
        click.echo("All program data deleted.")

    return app
