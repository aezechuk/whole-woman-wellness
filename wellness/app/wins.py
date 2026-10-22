"""Wins wall: share wins, cheer, comment. Visible to everyone in the program.

Moderation: anyone can report a post or comment (all admins are emailed); admins can
delete anything; authors can delete their own. Comment notifications go to the poster
at most once every few hours per post, and never include the comment itself.
"""

from __future__ import annotations

from datetime import timedelta

from flask import current_app
from sqlalchemy import select

from . import mail, program
from .extensions import db
from .models import Participant, WinCheer, WinComment, WinPost, WinReport, utcnow

POST_MAX = 500
COMMENT_MAX = 500
REASON_MAX = 300
ANON_NAME = "A sister in the program"
NOTIFY_EVERY = timedelta(hours=4)


class WinError(ValueError):
    pass


def display_name(p: Participant, anonymous: bool) -> str:
    return ANON_NAME if anonymous else (p.first_name or "A participant")


def _clean(text: str | None, limit: int, what: str) -> str:
    text = (text or "").strip()
    if not text:
        raise WinError(f"Write something to share your {what}.")
    if len(text) > limit:
        raise WinError(f"Please keep it under {limit} characters.")
    return text


def create_post(p: Participant, body: str | None, anonymous: bool, week_key: str | None) -> WinPost:
    keys = {w.key for w in program.load().weeks}
    post = WinPost(cohort_id=p.cohort_id, participant_id=p.id, body=_clean(body, POST_MAX, "win"),
                   anonymous=bool(anonymous), week_key=week_key if week_key in keys else None)
    db.session.add(post)
    db.session.commit()
    return post


def edit_post(p: Participant, post: WinPost, body: str | None, anonymous: bool, week_key: str | None) -> None:
    if post.participant_id != p.id:
        raise KeyError(post.id)
    keys = {w.key for w in program.load().weeks}
    text = _clean(body, POST_MAX, "win")
    week = week_key if week_key in keys else None
    if (text, bool(anonymous), week) != (post.body, post.anonymous, post.week_key):
        post.body, post.anonymous, post.week_key, post.edited_at = text, bool(anonymous), week, utcnow()
        db.session.commit()


def get_post(post_id) -> WinPost:
    post = db.session.get(WinPost, _uuid(post_id))
    if post is None:
        raise KeyError(post_id)
    return post


def _uuid(value):
    import uuid
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise KeyError(value) from None


def feed(week_key: str | None = None) -> list[WinPost]:
    q = select(WinPost).order_by(WinPost.created_at.desc())
    if week_key:
        q = q.where(WinPost.week_key == week_key)
    return list(db.session.scalars(q))


def add_comment(p: Participant, post: WinPost, body: str | None, anonymous: bool) -> WinComment:
    c = WinComment(post_id=post.id, participant_id=p.id, body=_clean(body, COMMENT_MAX, "comment"),
                   anonymous=bool(anonymous))
    db.session.add(c)
    db.session.commit()
    _notify_poster(post, commenter=p)
    return c


def _notify_poster(post: WinPost, commenter: Participant) -> None:
    from . import notify
    author = post.author
    if author.id == commenter.id or not author.comment_emails:
        return
    now = utcnow()
    last = post.notified_at
    if last is not None:
        if last.tzinfo is None:  # SQLite drops the zone; values are stored in UTC
            last = last.replace(tzinfo=now.tzinfo)
        if last > now - NOTIFY_EVERY:
            return
    post.notified_at = now
    db.session.commit()
    try:
        mail.send(author.email, "Someone commented on your win 💜", "win_comment", name=author.first_name,
                  post_link=notify.link("reflect.win_post", post_id=str(post.id)),
                  settings_link=notify.link("reflect.settings"))
    except Exception:
        current_app.logger.exception("Win comment email failed to send")


def toggle_cheer(p: Participant, post: WinPost) -> bool:
    mine = next((c for c in post.cheers if c.participant_id == p.id), None)
    if mine:
        db.session.delete(mine)
    else:
        db.session.add(WinCheer(post_id=post.id, participant_id=p.id))
    db.session.commit()
    return mine is None


def delete_post(p: Participant, post: WinPost) -> None:
    if post.participant_id != p.id:
        raise KeyError(post.id)
    db.session.delete(post)
    db.session.commit()


def delete_comment(p: Participant, comment_id) -> WinPost:
    c = db.session.get(WinComment, _uuid(comment_id))
    if c is None or c.participant_id != p.id:
        raise KeyError(comment_id)
    post = c.post
    db.session.delete(c)
    db.session.commit()
    return post


def report(p: Participant, reason: str | None, post: WinPost | None = None, comment_id=None) -> None:
    from . import notify
    comment = None
    if comment_id:
        comment = db.session.get(WinComment, _uuid(comment_id))
        if comment is None:
            raise KeyError(comment_id)
        post = comment.post
    already = db.session.scalar(select(WinReport).where(
        WinReport.reporter_id == p.id, WinReport.resolved.is_(False),
        (WinReport.comment_id == comment.id) if comment else
        (WinReport.post_id == post.id) & WinReport.comment_id.is_(None)))
    if already:
        return
    db.session.add(WinReport(post_id=None if comment else post.id, comment_id=comment.id if comment else None,
                             reporter_id=p.id, reason=(reason or "").strip()[:REASON_MAX] or None))
    db.session.commit()
    what = "comment" if comment else "post"
    for admin in sorted(current_app.config["ADMIN_EMAILS"]):
        try:
            mail.send(admin.strip().lower(), f"A {what} on the wins wall was reported", "win_report",
                      what=what, review_link=notify.link("admin.wins"))
        except Exception:
            current_app.logger.exception("Win report alert failed to send")


# ---- admin ------------------------------------------------------------------------

def open_reports() -> list[WinReport]:
    return list(db.session.scalars(
        select(WinReport).where(WinReport.resolved.is_(False)).order_by(WinReport.created_at)))


def admin_delete_post(post_id) -> None:
    db.session.delete(get_post(post_id))
    db.session.commit()


def admin_delete_comment(comment_id) -> None:
    c = db.session.get(WinComment, _uuid(comment_id))
    if c is None:
        raise KeyError(comment_id)
    db.session.delete(c)
    db.session.commit()


def dismiss_report(report_id) -> None:
    r = db.session.get(WinReport, _uuid(report_id))
    if r is None:
        raise KeyError(report_id)
    r.resolved = True
    db.session.commit()


def summary() -> dict:
    posts = list(db.session.scalars(select(WinPost)))
    comments = db.session.scalar(select(db.func.count()).select_from(WinComment)) or 0
    cheers = db.session.scalar(select(db.func.count()).select_from(WinCheer)) or 0
    return {"posts": len(posts), "posters": len({p.participant_id for p in posts}),
            "comments": comments, "cheers": cheers}
