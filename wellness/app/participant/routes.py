from __future__ import annotations

from pathlib import Path

from functools import wraps

from flask import current_app, flash, g, redirect, render_template, request, url_for

from ..extensions import db
from ..models import utcnow
from . import bp


def login_required(view):
    """Signed in, profile done, consent given, in that order."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        p = g.participant
        if p is None:
            return redirect(url_for("auth.start"))
        if not p.profile_complete and request.endpoint != "participant.profile":
            return redirect(url_for("participant.profile"))
        if (p.profile_complete and not p.has_consented
                and request.endpoint not in ("participant.consent", "participant.profile")):
            return redirect(url_for("participant.consent"))
        return view(*args, **kwargs)
    return wrapped


MENTEE_CHOICES = {"yes": True, "no": False, "skip": None}


@bp.route("/welcome", methods=["GET", "POST"], endpoint="profile")
@login_required
def profile():
    p = g.participant
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        mentee = request.form.get("mentee", "skip")
        if not first_name or len(first_name) > 80:
            flash("Please tell us what to call you.", "error")
            return render_template("participant/profile.html"), 400
        if mentee not in MENTEE_CHOICES:
            mentee = "skip"
        new = not p.first_name
        p.first_name = first_name
        p.is_mentee = MENTEE_CHOICES[mentee]
        db.session.commit()
        if new:
            from ..notify import send_welcome
            send_welcome(p)
        return redirect(url_for("participant.consent"))
    return render_template("participant/profile.html")


@bp.route("/consent", methods=["GET", "POST"])
@login_required
def consent():
    p = g.participant
    if p.has_consented:
        return redirect(url_for("participant.home"))
    if request.method == "POST":
        if request.form.get("agree") != "yes":
            flash("Please check the box to continue.", "error")
            return render_template("participant/consent.html"), 400
        p.consented_at = utcnow()
        p.consent_version = current_app.config["CONSENT_VERSION"]
        db.session.commit()
        # Offer Count Me In once, right after consent, with a "Maybe later".
        return redirect(url_for("assessment.count_me_in", first=1))
    return render_template("participant/consent.html")


@bp.route("/")
@login_required
def home():
    from .. import intentions, program
    from ..assessment import service
    p = g.participant
    pre, post = service.get(p, "pre"), service.get(p, "post")
    hero_photo = (Path(current_app.static_folder) / "img" / "home-hero.jpg").exists()
    return render_template("participant/home.html", participant=p, pre=pre, post=post,
                           final_open=service.final_is_open(), week=program.load().current_week(),
                           hero_photo=hero_photo, intents=[v for v in intentions.views(p) if v.active])
