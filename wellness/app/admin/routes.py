"""Admin area: email-code sign-in for allowlisted admins, then group-level reports.

Admins never see an individual's scores, reflections, Count Me In answers, or
who wrote which feedback. They do see testimonials (shareable by design).
"""

from __future__ import annotations

import csv
import hashlib
import io
import secrets

from flask import (abort, current_app, flash, g, make_response, redirect, render_template,
                   request, session, url_for)
from sqlalchemy import select

from .. import mail
from ..auth import emailcheck
from ..auth import service as auth
from ..extensions import db, limiter
from ..models import AdminSession, Cohort, utcnow
from . import bp, reports

COOKIE = "wwadmin"
OPEN_ENDPOINTS = {"admin.signin", "admin.verify"}


def _admins() -> set[str]:
    return {e.strip().lower() for e in current_app.config.get("ADMIN_EMAILS", []) if e.strip()}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@bp.before_request
def require_admin():
    g.admin = None
    token = request.cookies.get(COOKIE)
    if token:
        s = db.session.scalar(select(AdminSession).where(AdminSession.token_hash == _hash(token)))
        if (s and s.revoked_at is None and auth._aware(s.expires_at) > utcnow()
                and s.email in _admins()):  # removing someone from the allowlist locks them out at once
            g.admin = s.email
    if g.admin is None and request.endpoint not in OPEN_ENDPOINTS:
        return redirect(url_for("admin.signin"))


@bp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


# ---- sign-in -----------------------------------------------------------------------

@bp.route("/signin", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def signin():
    if request.method == "POST":
        email = auth.normalize_email(request.form.get("email", ""))
        if not emailcheck.is_valid(email):
            flash("That email looks incomplete. Please check it and try again.", "error")
            return render_template("admin/signin.html"), 400
        if email in _admins():
            code = auth.issue_code(email, "admin")
            try:
                mail.send(email, f"Admin sign-in code: {code}", "admin_code", code=code)
            except Exception:
                current_app.logger.exception("Admin sign-in email failed to send")
                auth.withdraw_unsent_code(email, "admin")
                flash("We couldn't send your code just now. Please try again in a few minutes.", "error")
                return render_template("admin/signin.html"), 503
        # Same response either way, so this page can't reveal who the admins are.
        session["admin_pending"] = email
        return redirect(url_for("admin.verify"))
    return render_template("admin/signin.html")


@bp.route("/verify", methods=["GET", "POST"])
@limiter.limit("20 per hour", methods=["POST"])
def verify():
    email = session.get("admin_pending")
    if not email:
        return redirect(url_for("admin.signin"))
    if request.method == "POST":
        try:
            if email not in _admins():
                raise auth.SignInError("That code didn't work. Check the most recent email, or request a new code.")
            auth.check_code(email, request.form.get("code", ""), "admin")
        except auth.SignInError as exc:
            flash(str(exc), "error")
            return render_template("admin/verify.html", email=email), 400
        session.pop("admin_pending", None)
        token = secrets.token_urlsafe(32)
        db.session.add(AdminSession(email=email, token_hash=_hash(token),
                                    expires_at=utcnow() + current_app.config["ADMIN_SESSION_LIFETIME"]))
        db.session.commit()
        when = utcnow().astimezone(reports.program.load().tz).strftime("%b %d, %Y at %I:%M %p %Z")
        for admin in sorted(_admins()):
            try:
                mail.send(admin, "Admin sign-in to Whole Woman Wellness", "admin_alert",
                          who=email, when=when, agent=(request.user_agent.string or "")[:120])
            except Exception:
                current_app.logger.exception("Admin sign-in alert failed to send")
        response = make_response(redirect(url_for("admin.dashboard")))
        response.set_cookie(COOKIE, token, httponly=True, samesite="Strict", path="/admin",
                            secure=current_app.config["SESSION_COOKIE_SECURE"],
                            max_age=int(current_app.config["ADMIN_SESSION_LIFETIME"].total_seconds()))
        return response
    return render_template("admin/verify.html", email=email)


@bp.post("/signout")
def signout():
    token = request.cookies.get(COOKIE)
    s = db.session.scalar(select(AdminSession).where(AdminSession.token_hash == _hash(token or "")))
    if s:
        s.revoked_at = utcnow()
        db.session.commit()
    response = make_response(redirect(url_for("admin.signin")))
    response.delete_cookie(COOKIE, path="/admin")
    return response


# ---- reports -----------------------------------------------------------------------

def _group() -> str:
    group = request.args.get("group", "all")
    return group if group in reports.GROUPS else "all"


@bp.get("/")
def dashboard():
    group = _group()
    include_late = request.args.get("exclude_late") != "1"
    return render_template("admin/dashboard.html", o=reports.overview(), group=group, groups=reports.GROUPS,
                           include_late=include_late, rows=reports.outcomes(group, include_late),
                           extras=reports.final_extras(group))


@bp.get("/funder")
def funder():
    return render_template("admin/funder.html", f=reports.funder_report(),
                           testimonials=[t for t in reports.testimonials()])


@bp.get("/feedback")
def feedback():
    return render_template("admin/feedback.html", weeks=reports.session_feedback())


@bp.get("/testimonials")
def testimonials():
    return render_template("admin/testimonials.html", testimonials=reports.testimonials(),
                           weeks={w.key: w.name for w in reports.program.load().weeks})


@bp.route("/program", methods=["GET", "POST"])
def program_settings():
    cohort = db.session.scalar(select(Cohort))
    if cohort is None:
        abort(404)
    if request.method == "POST":
        new_code = (request.form.get("join_code") or "").strip()
        if new_code:
            if len(auth.normalize_join_code(new_code)) < 6:
                flash("Use at least 6 characters for the program code.", "error")
                return redirect(url_for("admin.program_settings"))
            cohort.join_code_hash = auth.hash_join_code(new_code)
        cohort.join_open = request.form.get("join_open") == "on"
        cap = request.form.get("capacity", "").strip()
        cohort.capacity = int(cap) if cap.isdigit() and int(cap) > 0 else None
        db.session.commit()
        flash("Program settings saved.", "info")
        return redirect(url_for("admin.program_settings"))
    return render_template("admin/program.html", cohort=cohort, o=reports.overview())


# ---- exports -----------------------------------------------------------------------

def _cell(value) -> str:
    """Neutralize spreadsheet formulas (CSV injection)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(name: str, header: list[str], rows: list[list]) -> object:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for row in rows:
        w.writerow([_cell(v) for v in row])
    response = make_response(buf.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@bp.get("/export/funder.csv")
def export_funder():
    f = reports.funder_report()
    rows = [[t["title"], label, n, pct] for t in f["tables"] for label, n, pct in t["rows"]]
    rows.append(["Total participants", "", f["total"], 100.0])
    return _csv("funder-demographics.csv", ["Category", "Option", "Count", "Percent"], rows)


@bp.get("/export/outcomes.csv")
def export_outcomes():
    group = _group()
    rows = [[r.name, r.pre_mean, r.pre_n, r.post_mean, r.post_n, r.change_mean, r.paired_n, r.improved_pct]
            for r in reports.outcomes(group)]
    return _csv(f"outcomes-{group}.csv", ["Area", "Beginning mean", "Beginning n", "Final mean", "Final n",
                                          "Mean change (paired)", "Paired n", "% improved 10+ pts"], rows)


@bp.get("/export/testimonials.csv")
def export_testimonials():
    rows = [[t.body, t.credit_name, t.share, t.week_key, t.created_at.date().isoformat()]
            for t in reports.testimonials()]
    return _csv("testimonials.csv", ["Testimonial", "Credit", "May share in", "Week", "Date"], rows)
