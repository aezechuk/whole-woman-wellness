"""Journal, session feedback, testimonials, and participant settings.

Ownership rule: every record is looked up through g.participant, never by an
id alone, so one participant can never read or change another's data.
"""

from __future__ import annotations

import uuid

from flask import (Response, abort, current_app, flash, g, make_response, redirect,
                   render_template, request, url_for)
from sqlalchemy import delete, select

from .. import calendar_ics, crypto, intentions, notify, program, wins
from ..extensions import db, limiter
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
        for i, _ in week.slots:
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


# ---- sessions ----------------------------------------------------------------------

def _session_rows(p: Participant) -> list[dict]:
    done = {f.week_key for f in p.feedback_done}
    current = program.load().current_week()
    return [{"week": w, "happened": w.is_open(), "current": current is not None and w.key == current.key,
             "feedback_done": w.key in done} for w in program.load().weeks]


@bp.get("/sessions")
@login_required
def sessions():
    return render_template("reflect/sessions.html", rows=_session_rows(g.participant))


def _focus_keys(p: Participant) -> set[str]:
    from ..assessment import service as assess
    pre = assess.get(p, "pre")
    return set(pre.focus_domains or []) if pre is not None else set()


@bp.get("/sessions/<week_key>")
@login_required
def session_week(week_key: str):
    p = g.participant
    week = _open_week(week_key)
    current = program.load().current_week()
    all_views = intentions.views(p)
    edit = next((v for v in all_views if v.id == request.args.get("edit")), None)
    added = next((v for v in all_views if v.id == request.args.get("added") and v.reminder and v.active), None)
    if added is not None and not added.on_calendar:
        # "Save & add": the page opens the calendar file as soon as it loads, so show it as added.
        intentions.mark_on_calendar(p, intentions.get(p, added.id))
        all_views = intentions.views(p)
    return render_template(
        "reflect/session_week.html", week=week, attendance=ATTENDANCE, happened=week.is_open(),
        feedback_done=any(f.week_key == week_key for f in p.feedback_done),
        is_current=current is not None and current.key == week.key,
        due=[v for v in all_views if v.due] if current is not None and current.key == week.key else [],
        mine=[v for v in all_views if v.active and v.area_key == week.key],
        edit=edit, added=added, nudge_win=request.args.get("win") == "1",
        week_wins=len(wins.feed(week.key)), is_focus=week.key in _focus_keys(p), next_steps=intentions.NEXT_STEPS,
        reminder_days=calendar_ics.DAYS, reminder_durations=calendar_ics.DURATIONS, reminder_until=calendar_ics.UNTIL)


# ---- intentions --------------------------------------------------------------------

@bp.post("/intentions/<week_key>")
@login_required
def intention_save(week_key: str):
    p = g.participant
    week = _open_week(week_key)
    current = program.load().current_week()
    wants_calendar = request.form.get("then") == "calendar"
    try:
        if wants_calendar and not request.form.getlist("rdays"):
            raise intentions.IntentionError("Pick at least one day for your calendar reminder.")
        it = intentions.save(p, area_key=week.key, week_key=(current or week).key, form=request.form,
                             intention_id=request.form.get("intention_id") or None)
    except KeyError:
        abort(404)
    except intentions.IntentionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("reflect.session_week", week_key=week.key) + "#intention")
    if wants_calendar:
        flash("Saved. Your calendar is opening. If it doesn't, tap Add to my calendar under your intention.", "info")
        return redirect(url_for("reflect.session_week", week_key=week.key, added=str(it.id)) + "#intention")
    flash("Your intention is saved.", "info")
    return redirect(url_for("reflect.session_week", week_key=week.key) + "#intention")


@bp.post("/intentions/<intention_id>/checkin")
@login_required
def intention_checkin(intention_id: str):
    p = g.participant
    current = program.load().current_week()
    try:
        it = intentions.checkin(p, intention_id, request.form.get("days"), request.form.get("next", ""))
    except KeyError:
        abort(404)
    except intentions.IntentionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("reflect.session_week", week_key=current.key if current else "reset") + "#checkin")
    if request.form.get("next") == "adjust":
        flash("Checked in. Adjust it below and save.", "info")
        return redirect(url_for("reflect.session_week", week_key=it.area_key, edit=str(it.id)) + "#intention")
    if request.form.get("days", "").isdigit() and int(request.form["days"]) >= 5:
        return redirect(url_for("reflect.session_week", week_key=current.key, win=1) + "#checkin")
    flash("Checked in. Thank you.", "info")
    return redirect(url_for("reflect.session_week", week_key=current.key) + "#checkin")


@bp.get("/intentions")
@login_required
def intentions_list():
    p = g.participant
    return render_template("reflect/intentions.html", views=intentions.views(p),
                           current=program.load().current_week(), focus=_focus_keys(p),
                           weeks=program.load().weeks)


@bp.get("/intentions/<intention_id>/calendar.ics")
@login_required
def intention_calendar(intention_id: str):
    """A calendar file for her reminder, made on request and never stored."""
    p = g.participant
    try:
        it = intentions.get(p, intention_id)
    except KeyError:
        abort(404)
    v = intentions.view(p, it, None)
    if not v.reminder or not v.active:
        abort(404)
    intentions.mark_on_calendar(p, it)
    now = utcnow()
    today = now.astimezone(program.load().tz).date()
    body = calendar_ics.build(v.id, v.reminder, v.text, v.fields.get("place", ""), today, now.replace(tzinfo=None))
    resp = make_response(body)
    resp.headers["Content-Type"] = "text/calendar; charset=utf-8"
    resp.headers["Content-Disposition"] = 'inline; filename="my-intention.ics"'
    return resp


@bp.post("/intentions/<intention_id>/on-calendar")
@login_required
def intention_on_calendar(intention_id: str):
    """She already added it (before this was tracked, or on another device): stop offering the button."""
    p = g.participant
    try:
        it = intentions.get(p, intention_id)
    except KeyError:
        abort(404)
    intentions.mark_on_calendar(p, it)
    back = request.form.get("back", "")
    return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("participant.home"))


@bp.post("/intentions/<intention_id>/let-go")
@login_required
def intention_let_go(intention_id: str):
    try:
        intentions.let_go(g.participant, intention_id)
    except KeyError:
        abort(404)
    flash("Let go. You can always set a new one.", "info")
    return redirect(url_for("reflect.intentions_list"))


# ---- wins wall ---------------------------------------------------------------------

@bp.get("/wins")
@login_required
def wins_wall():
    week = request.args.get("week") or None
    names = {w.key: w.name for w in program.load().weeks}
    current = program.load().current_week()
    return render_template("reflect/wins.html", posts=wins.feed(week if week in names else None), week=week,
                           names=names, weeks=program.load().weeks, current=current,
                           prefill=request.args.get("share") == "1", limit=wins.POST_MAX)


@bp.post("/wins")
@login_required
@limiter.limit("20 per hour")
def win_create():
    p = g.participant
    try:
        post = wins.create_post(p, request.form.get("body"), request.form.get("anonymous") == "1",
                                request.form.get("week_key"))
    except wins.WinError as exc:
        flash(str(exc), "error")
        return redirect(url_for("reflect.wins_wall"))
    flash("Your win is shared. 💜", "info")
    return redirect(url_for("reflect.win_post", post_id=str(post.id)))


def _post_or_404(post_id):
    try:
        return wins.get_post(post_id)
    except KeyError:
        abort(404)


@bp.get("/wins/<post_id>")
@login_required
def win_post(post_id: str):
    post = _post_or_404(post_id)
    return render_template("reflect/win_post.html", post=post, names={w.key: w.name for w in program.load().weeks},
                           limit=wins.COMMENT_MAX)


@bp.post("/wins/<post_id>/comment")
@login_required
@limiter.limit("60 per hour")
def win_comment(post_id: str):
    post = _post_or_404(post_id)
    try:
        wins.add_comment(g.participant, post, request.form.get("body"), request.form.get("anonymous") == "1")
    except wins.WinError as exc:
        flash(str(exc), "error")
    return redirect(url_for("reflect.win_post", post_id=post_id) + "#comments")


@bp.post("/wins/<post_id>/cheer")
@login_required
def win_cheer(post_id: str):
    wins.toggle_cheer(g.participant, _post_or_404(post_id))
    back = request.form.get("back", "")
    return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("reflect.wins_wall"))


@bp.route("/wins/<post_id>/edit", methods=["GET", "POST"])
@login_required
def win_edit(post_id: str):
    post = _post_or_404(post_id)
    if post.participant_id != g.participant.id:
        abort(404)
    if request.method == "POST":
        try:
            wins.edit_post(g.participant, post, request.form.get("body"), request.form.get("anonymous") == "1",
                           request.form.get("week_key"))
        except wins.WinError as exc:
            flash(str(exc), "error")
            return redirect(url_for("reflect.win_edit", post_id=post_id))
        flash("Your win is updated.", "info")
        return redirect(url_for("reflect.win_post", post_id=post_id))
    return render_template("reflect/win_edit.html", post=post, weeks=program.load().weeks, limit=wins.POST_MAX)


@bp.post("/wins/<post_id>/delete")
@login_required
def win_delete(post_id: str):
    try:
        wins.delete_post(g.participant, _post_or_404(post_id))
    except KeyError:
        abort(404)
    flash("Your post is deleted.", "info")
    return redirect(url_for("reflect.wins_wall"))


@bp.post("/wins/comments/<comment_id>/delete")
@login_required
def win_comment_delete(comment_id: str):
    try:
        post = wins.delete_comment(g.participant, comment_id)
    except KeyError:
        abort(404)
    flash("Your comment is deleted.", "info")
    return redirect(url_for("reflect.win_post", post_id=str(post.id)) + "#comments")


@bp.post("/wins/<post_id>/report")
@login_required
@limiter.limit("20 per hour")
def win_report(post_id: str):
    post = _post_or_404(post_id)
    try:
        wins.report(g.participant, request.form.get("reason"), post=post, comment_id=request.form.get("comment_id"))
    except KeyError:
        abort(404)
    flash("Thank you. The program team has been notified and will review it.", "info")
    return redirect(url_for("reflect.win_post", post_id=post_id))


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
    if not week.is_open():  # feedback opens once the session has happened
        return redirect(url_for("reflect.session_week", week_key=week_key))
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
        return redirect(url_for("reflect.session_week", week_key=week_key))
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
        p.comment_emails = request.form.get("comment_emails") == "on"
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
        for i, prompt in w.slots:
            e = entries.get((w.key, i))
            if e is not None:
                reflections.append((w, prompt, crypto.open_(e.body, _context(p, w.key, i))))
    results = []
    for tp in ("pre", "post"):
        a = assess.get(p, tp)
        if a is not None and a.is_complete:
            results.append((tp, a, assess.snapshot(a)))
    body = render_template("reflect/download.txt", p=p, reflections=reflections, results=results,
                           testimonials=p.testimonials, intents=intentions.views(p),
                           my_wins=p.win_posts, my_comments=p.win_comments)
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

