"""Journal, session feedback, testimonials, and participant settings.

Ownership rule: every record is looked up through g.participant, never by an
id alone, so one participant can never read or change another's data.
"""

from __future__ import annotations

import uuid

from flask import (Response, abort, current_app, flash, g, make_response, redirect,
                   render_template, request, url_for)
from sqlalchemy import delete, select

from .. import crypto, notify, program
from ..extensions import db
from ..models import (FeedbackDone, JournalEntry, Participant, SessionFeedback, SignInCode,
                      Testimonial, utcnow)
from ..participant.routes import login_required
from . import bp


def _context(p: Participant, week_key: str, index: int) -> dict[str, str]:
    return {"participant": str(p.id), "field": f"journal:{week_key}:{index}"}


def _open_week(week_key: str) -> program.Week:
    try:
        week = program.load().week(week_key)
    except KeyError:
        abort(404)
    return week  # every week stays open: thoughts come before sessions and long after


def _entries(p: Participant, week_key: str) -> dict[int, JournalEntry]:
    return {e.prompt_index: e for e in p.journal_entries if e.week_key == week_key}


# ---- journal ---------------------------------------------------------------------

@bp.get("/journal")
@login_required
def journal():
    p = g.participant
    prog = program.load()
    written = {e.week_key for e in p.journal_entries}
    weeks = [{"week": w, "upcoming": not w.is_open(), "written": w.key in written} for w in prog.weeks]
    current = prog.current_week()
    return render_template("reflect/journal.html", weeks=weeks, current=current)


@bp.route("/journal/<week_key>", methods=["GET", "POST"])
@login_required
def journal_week(week_key: str):
    p = g.participant
    week = _open_week(week_key)
    entries = _entries(p, week_key)
    limit = current_app.config["REFLECTION_MAX_CHARS"]
    autosave = request.headers.get("X-Autosave") == "1"

    if request.method == "POST":
        now = utcnow()
        for i, _ in enumerate(week.prompts):
            text = (request.form.get(f"prompt_{i}") or "").strip()
            if f"prompt_{i}" not in request.form:
                continue
            if len(text) > limit:
                if autosave:
                    return Response(status=413)
                flash(f"That reflection is longer than {limit:,} characters. Please trim it a little.", "error")
                return redirect(url_for("reflect.journal_week", week_key=week_key))
            entry = entries.get(i)
            if not text:
                if entry is not None:
                    db.session.delete(entry)  # clearing a reflection removes it
                continue
            sealed = crypto.seal(text, _context(p, week_key, i))
            if entry is None:
                db.session.add(JournalEntry(participant_id=p.id, week_key=week_key, prompt_index=i, body=sealed))
            else:
                entry.body, entry.updated_at = sealed, now
        db.session.commit()
        if autosave:
            return Response(status=204)
        flash("Your reflection is saved.", "info")
        return redirect(url_for("reflect.journal_week", week_key=week_key, saved=1))

    texts = {i: crypto.open_(e.body, _context(p, week_key, i)) for i, e in entries.items()}
    feedback_done = any(f.week_key == week_key for f in p.feedback_done)
    return render_template("reflect/journal_week.html", week=week, texts=texts, limit=limit,
                           saved=request.args.get("saved") == "1", feedback_done=feedback_done)


# ---- session feedback ------------------------------------------------------------

ATTENDANCE = [("live", "Live online"), ("in_person", "In person"),
              ("recording", "Recording"), ("none", "Didn't attend")]


def _rating(value: str | None) -> int | None:
    try:
        v = int(value or "")
    except ValueError:
        return None
    return v if 1 <= v <= 5 else None


@bp.route("/feedback/<week_key>", methods=["GET", "POST"])
@login_required
def feedback(week_key: str):
    p = g.participant
    week = _open_week(week_key)
    if any(f.week_key == week_key for f in p.feedback_done):
        return render_template("reflect/feedback_done.html", week=week)
    if request.method == "POST":
        attendance = request.form.get("attendance")
        if attendance not in dict(ATTENDANCE):
            flash("Please choose how you took part.", "error")
            return render_template("reflect/feedback.html", week=week, attendance=ATTENDANCE), 400
        cap = current_app.config["FEEDBACK_MAX_CHARS"]
        attended = attendance != "none"

        def text(field: str) -> str | None:
            value = (request.form.get(field) or "").strip()[:cap]
            return value or None

        db.session.add(SessionFeedback(
            cohort_id=p.cohort_id, week_key=week_key, attendance=attendance,
            valuable=_rating(request.form.get("valuable")) if attended else None,
            usable=_rating(request.form.get("usable")) if attended else None,
            most_helpful=text("most_helpful") if attended else None,
            make_better=text("make_better") if attended else None,
        ))
        db.session.add(FeedbackDone(participant_id=p.id, week_key=week_key))
        db.session.commit()
        flash("Thank you. Your feedback helps shape the program.", "info")
        return redirect(url_for("reflect.journal_week", week_key=week_key))
    return render_template("reflect/feedback.html", week=week, attendance=ATTENDANCE)


# ---- testimonials ----------------------------------------------------------------

CREDIT = [("full", "My full name"), ("first", "My first name only"),
          ("anonymous", "Anonymously, as “a program participant”")]
SHARE = [("funder", "Only in the report to our funder"),
         ("funder_and_materials", "In the funder report and in Arthentic program materials")]


def _own_testimonial(tid: str) -> Testimonial:
    try:
        tid_uuid = uuid.UUID(tid)
    except ValueError:
        abort(404)
    t = db.session.scalar(select(Testimonial).where(
        Testimonial.id == tid_uuid, Testimonial.participant_id == g.participant.id))
    if t is None:
        abort(404)
    return t


def _testimonial_form(t: Testimonial | None):
    p = g.participant
    body = (request.form.get("body") or "").strip()
    credit = request.form.get("credit")
    share = request.form.get("share")
    full_name = (request.form.get("full_name") or "").strip()[:160]
    errors = []
    if not body:
        errors.append("Please write a few words to share.")
    if len(body) > current_app.config["TESTIMONIAL_MAX_CHARS"]:
        errors.append("Please keep it under 2,000 characters.")
    if credit not in dict(CREDIT):
        errors.append("Please choose how we should credit you.")
    if share not in dict(SHARE):
        errors.append("Please choose where we may share it.")
    if credit == "full" and not full_name:
        errors.append("Please enter your full name as you'd like it to appear.")
    if errors:
        for e in errors:
            flash(e, "error")
        return None
    name = {"full": full_name, "first": p.first_name, "anonymous": "A program participant"}[credit]
    current = program.load().current_week()
    if t is None:
        t = Testimonial(participant_id=p.id, week_key=current.key if current else None)
        db.session.add(t)
    t.body, t.credit, t.credit_name, t.share, t.updated_at = body, credit, name, share, utcnow()
    db.session.commit()
    return t


@bp.get("/testimonials")
@login_required
def testimonials():
    mine = sorted(g.participant.testimonials, key=lambda t: t.created_at, reverse=True)
    return render_template("reflect/testimonials.html", testimonials=mine, credit=dict(CREDIT), share=dict(SHARE))


@bp.route("/testimonials/new", methods=["GET", "POST"])
@login_required
def testimonial_new():
    if request.method == "POST" and _testimonial_form(None):
        flash("Thank you for sharing. You can edit or withdraw it anytime before February 28, 2027.", "info")
        return redirect(url_for("reflect.testimonials"))
    return render_template("reflect/testimonial_form.html", t=None, credit=CREDIT, share=SHARE,
                           form=request.form), (400 if request.method == "POST" else 200)


@bp.route("/testimonials/<tid>/edit", methods=["GET", "POST"])
@login_required
def testimonial_edit(tid: str):
    t = _own_testimonial(tid)
    if request.method == "POST" and _testimonial_form(t):
        flash("Your testimonial is updated.", "info")
        return redirect(url_for("reflect.testimonials"))
    return render_template("reflect/testimonial_form.html", t=t, credit=CREDIT, share=SHARE,
                           form=request.form), (400 if request.method == "POST" else 200)


@bp.post("/testimonials/<tid>/withdraw")
@login_required
def testimonial_withdraw(tid: str):
    db.session.delete(_own_testimonial(tid))
    db.session.commit()
    flash("Your testimonial has been withdrawn.", "info")
    return redirect(url_for("reflect.testimonials"))


# ---- settings, download, delete, unsubscribe ---------------------------------------

@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    p = g.participant
    if request.method == "POST":
        p.weekly_emails = request.form.get("weekly_emails") == "on"
        db.session.commit()
        flash("Your email settings are saved.", "info")
        return redirect(url_for("reflect.settings"))
    return render_template("reflect/settings.html", participant=p)


@bp.get("/settings/download")
@login_required
def download():
    from ..assessment import service as assess
    p = g.participant
    prog = program.load()
    entries = {(e.week_key, e.prompt_index): e for e in p.journal_entries}
    reflections = []
    for w in prog.weeks:
        for i, prompt in enumerate(w.prompts):
            e = entries.get((w.key, i))
            if e is not None:
                reflections.append((w, prompt, crypto.open_(e.body, _context(p, w.key, i))))
    results = []
    for tp in ("pre", "post"):
        a = assess.get(p, tp)
        if a is not None and a.is_complete:
            results.append((tp, a, assess.snapshot(a)))
    body = render_template("reflect/download.txt", p=p, reflections=reflections, results=results,
                           testimonials=p.testimonials)
    p.downloaded_at = utcnow()
    db.session.commit()
    response = make_response(body)
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="my-radical-self-care-reset.txt"'
    return response


@bp.route("/settings/delete", methods=["GET", "POST"])
@login_required
def delete_account():
    p = g.participant
    if request.method == "POST":
        if request.form.get("confirm") != "DELETE":
            flash("Type DELETE to confirm.", "error")
            return render_template("reflect/delete.html"), 400
        email, name = p.email, p.first_name
        notify.send_withdrawal_confirmation(email, name)  # needs the address, so send first
        db.session.delete(p)  # cascades: assessments, journal, testimonials, sessions, logs
        db.session.execute(delete(SignInCode).where(SignInCode.email == email))
        db.session.commit()
        response = make_response(render_template("reflect/deleted.html"))
        response.delete_cookie(current_app.config["AUTH_COOKIE_NAME"], path="/")
        return response
    return render_template("reflect/delete.html")


@bp.route("/unsubscribe/<token>", methods=["GET", "POST"])
def unsubscribe(token: str):
    p = notify.participant_from_unsubscribe(token)
    if p is None:
        abort(404)
    if request.method == "POST":  # POST so link scanners can't unsubscribe people
        p.weekly_emails = False
        db.session.commit()
        return render_template("reflect/unsubscribed.html")
    return render_template("reflect/unsubscribe.html", token=token)

