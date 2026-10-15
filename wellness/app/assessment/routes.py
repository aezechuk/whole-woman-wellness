from __future__ import annotations

from flask import abort, flash, g, redirect, render_template, request, url_for

from .. import countmein
from ..extensions import db
from ..models import DemographicResponse, utcnow
from ..participant.routes import login_required
from . import bp, service

LABELS = {"pre": "Beginning", "post": "Final"}


def _check_timepoint(timepoint: str) -> None:
    if timepoint not in service.TIMEPOINTS:
        abort(404)
    if timepoint == "post" and not service.final_is_open():
        abort(404)


def _results_url(timepoint: str) -> str:
    return url_for("assessment.results" if timepoint == "pre" else "assessment.final_results")


@bp.get("/assessment/<timepoint>")
@login_required
def begin(timepoint: str):
    _check_timepoint(timepoint)
    a = service.get_or_start(g.participant, timepoint)
    if a.is_complete:
        return redirect(_results_url(timepoint))
    return redirect(url_for("assessment.section", timepoint=timepoint,
                            n=service.first_incomplete_section(a) or 1))


@bp.route("/assessment/<timepoint>/<int:n>", methods=["GET", "POST"])
@login_required
def section(timepoint: str, n: int):
    _check_timepoint(timepoint)
    a = service.get_or_start(g.participant, timepoint)
    if a.is_complete:
        return redirect(_results_url(timepoint))
    inst = service.instrument(a.instrument_version)
    secs = service.sections(inst)
    if not 1 <= n <= len(secs):
        abort(404)
    sec = secs[n - 1]

    if request.method == "POST":
        missing = service.save_section(a, sec, request.form)
        if request.form.get("action") == "back" and n > 1:
            return redirect(url_for("assessment.section", timepoint=timepoint, n=n - 1))
        if missing:
            flash("Please answer each statement before moving on. Your other answers are saved.", "error")
            return render_template("assessment/section.html", a=a, inst=inst, sec=sec, n=n,
                                   total=len(secs), answers=service.section_answers(a, sec),
                                   missing=set(missing), label=LABELS[timepoint]), 400
        if n < len(secs):
            return redirect(url_for("assessment.section", timepoint=timepoint, n=n + 1))
        gap = service.first_incomplete_section(a)
        if gap is not None:
            return redirect(url_for("assessment.section", timepoint=timepoint, n=gap))
        if service.needs_extras(a):
            return redirect(url_for("assessment.extras"))
        service.complete(a)
        return redirect(_results_url(timepoint))

    return render_template("assessment/section.html", a=a, inst=inst, sec=sec, n=n, total=len(secs),
                           answers=service.section_answers(a, sec), missing=set(),
                           label=LABELS[timepoint])


@bp.route("/assessment/post/practices", methods=["GET", "POST"], endpoint="extras")
@login_required
def extras():
    _check_timepoint("post")
    a = service.get(g.participant, "post")
    if a is None or a.is_complete:
        return redirect(url_for("assessment.begin", timepoint="post"))
    gap = service.first_incomplete_section(a)
    if gap is not None:
        return redirect(url_for("assessment.section", timepoint="post", n=gap))
    copy = service.content(a.instrument_version)
    if request.method == "POST":
        service.save_extras(a, request.form)
        service.complete(a)
        return redirect(url_for("assessment.final_results"))
    return render_template("assessment/extras.html", copy=copy)


@bp.get("/results")
@login_required
def results():
    a = service.get(g.participant, "pre")
    if a is None or not a.is_complete:
        return redirect(url_for("assessment.begin", timepoint="pre"))
    return render_template("assessment/results.html", a=a, s=service.snapshot(a))


@bp.post("/results/focus")
@login_required
def save_focus():
    a = service.get(g.participant, "pre")
    if a is None or not a.is_complete:
        abort(404)
    if not service.set_focus(a, request.form.getlist("focus")):
        flash("Please choose up to two areas.", "error")
        return redirect(url_for("assessment.results") + "#focus")
    flash("Your focus areas are saved.", "info")
    return redirect(url_for("participant.home"))


@bp.get("/results/final")
@login_required
def final_results():
    post = service.get(g.participant, "post")
    if post is None or not post.is_complete:
        return redirect(url_for("participant.home"))
    pre = service.get(g.participant, "pre")
    cmp = service.comparison(pre, post) if pre is not None and pre.is_complete else None
    return render_template("assessment/final.html", s=service.snapshot(post), c=cmp)


@bp.route("/count-me-in", methods=["GET", "POST"])
@login_required
def count_me_in():
    p = g.participant
    if p.count_me_in_done_at is not None:
        return render_template("assessment/count_me_in_done.html")
    if request.method == "POST":
        if request.form.get("action") == "later":
            return redirect(url_for("participant.home"))
        answers = countmein.parse(request.form)
        db.session.add(DemographicResponse(cohort_id=p.cohort_id, **answers))
        p.count_me_in_done_at = utcnow()
        db.session.commit()
        flash("Thank you. You've been counted.", "info")
        return redirect(url_for("participant.home"))
    return render_template("assessment/count_me_in.html", race=countmein.RACE,
                           single=countmein.SINGLE, states=countmein.STATES,
                           state_question=countmein.STATE_QUESTION, first=request.args.get("first") == "1")
