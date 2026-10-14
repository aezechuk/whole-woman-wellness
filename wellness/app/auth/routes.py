from __future__ import annotations

from flask import (current_app, flash, g, make_response, redirect, render_template, request,
                   session, url_for)

from .. import mail
from ..extensions import limiter
from . import bp, service


def _set_auth_cookie(response, token: str):
    cfg = current_app.config
    response.set_cookie(
        cfg["AUTH_COOKIE_NAME"], token,
        max_age=int(cfg["AUTH_SESSION_LIFETIME"].total_seconds()),
        httponly=True, secure=cfg["SESSION_COOKIE_SECURE"], samesite="Lax", path="/",
    )
    return response


@bp.route("/start", methods=["GET", "POST"])
@limiter.limit("20 per hour", methods=["POST"])
def start():
    if g.participant:
        return redirect(url_for("participant.home"))
    if request.method == "POST":
        email = request.form.get("email", "")
        join_code = request.form.get("join_code", "")
        if "@" not in email or len(email) > 254:
            flash("Please enter a valid email address.", "error")
            return render_template("auth/start.html", email=email), 400
        try:
            req = service.request_code(email, join_code)
        except service.SignInError as exc:
            flash(str(exc), "error")
            return render_template("auth/start.html", email=email), 400
        mail.send(req.email, f"Your sign-in code: {req.code}", "signin_code", code=req.code)
        session["pending_email"] = req.email
        return redirect(url_for("auth.verify"))
    return render_template("auth/start.html")


@bp.route("/verify", methods=["GET", "POST"])
@limiter.limit("30 per hour", methods=["POST"])
def verify():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("auth.start"))
    if request.method == "POST":
        try:
            participant = service.verify_code(email, request.form.get("code", ""))
        except service.SignInError as exc:
            flash(str(exc), "error")
            return render_template("auth/verify.html", email=email), 400
        session.clear()  # fresh session on sign-in
        token = service.start_session(participant)
        response = make_response(redirect(url_for("participant.home")))
        return _set_auth_cookie(response, token)
    return render_template("auth/verify.html", email=email)


@bp.post("/signout")
def signout():
    service.end_session(request.cookies.get(current_app.config["AUTH_COOKIE_NAME"]))
    session.clear()
    response = make_response(redirect(url_for("auth.start")))
    response.delete_cookie(current_app.config["AUTH_COOKIE_NAME"], path="/")
    return response
