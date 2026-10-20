"""Habit intentions (Atomic Habits): set, keep, adjust, let go, with weekly check-ins.

Privacy:
- The intention's wording and her own check-in history are envelope-encrypted and
  visible only to her (same protection as the journal).
- Each check-in also writes an AnonCheckin row with no participant or intention id
  and no timestamp, which is all the admin report ever reads.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from . import crypto, program
from .extensions import db
from .models import AnonCheckin, Intention, IntentionCheckin, Participant, utcnow

KINDS = {
    "when": ("behavior", "time", "place"),   # I will [behavior] [time] [place].
    "after": ("cue", "behavior"),            # After I [cue], I will [behavior].
}
NEXT_STEPS = {"keep": "Keep it", "adjust": "Adjust it", "let_go": "Let it go"}
FIELD_MAX = 120
MAX_ACTIVE = 6


class IntentionError(ValueError):
    pass


def _ctx(p: Participant, intention_id, field: str) -> dict[str, str]:
    return {"participant": str(p.id), "field": f"{field}:{intention_id}"}


def sentence(kind: str, f: dict) -> str:
    if kind == "after":
        return f"After I {f.get('cue', '')}, I will {f.get('behavior', '')}."
    parts = [f.get("behavior", ""), f.get("time", ""), f.get("place", "")]
    return "I will " + " ".join(x for x in parts if x).rstrip(".") + "."


def parse(form) -> tuple[str, dict]:
    kind = form.get("kind", "when")
    if kind not in KINDS:
        raise IntentionError("Please choose a format.")
    fields = {k: " ".join((form.get(k) or "").split())[:FIELD_MAX] for k in KINDS[kind]}
    if not fields["behavior"]:
        raise IntentionError("Add what you will do.")
    if kind == "when" and not (fields["time"] or fields["place"]):
        raise IntentionError("Add when or where you'll do it. Specific beats general.")
    if kind == "after" and not fields["cue"]:
        raise IntentionError("Add what you'll do it after.")
    return kind, fields


def _seal(p: Participant, intention_id, kind: str, fields: dict) -> str:
    return crypto.seal(json.dumps({"kind": kind, "fields": fields}), _ctx(p, intention_id, "intention"))


def save(p: Participant, area_key: str, week_key: str, form, intention_id: str | None = None) -> Intention:
    kind, fields = parse(form)
    if intention_id:
        it = get(p, intention_id)
        it.body, it.updated_at, it.active = _seal(p, it.id, kind, fields), utcnow(), True
    else:
        if sum(1 for i in p.intentions if i.active) >= MAX_ACTIVE:
            raise IntentionError(f"You have {MAX_ACTIVE} intentions going. Let one go before adding another.")
        program.load().week(area_key)  # KeyError for an unknown area
        it = Intention(id=uuid.uuid4(), participant_id=p.id, area_key=area_key, week_key=week_key, body="")
        it.body = _seal(p, it.id, kind, fields)
        db.session.add(it)
    db.session.commit()
    return it


def get(p: Participant, intention_id) -> Intention:
    for it in p.intentions:
        if str(it.id) == str(intention_id):
            return it
    raise KeyError(intention_id)


def let_go(p: Participant, intention_id) -> None:
    get(p, intention_id).active = False
    db.session.commit()


@dataclass
class View:
    id: str
    area_key: str
    area_name: str
    kind: str
    fields: dict
    text: str
    active: bool
    set_in: str
    history: list[dict]          # [{"week_key", "week_name", "days", "next"}]
    due: bool

    @property
    def weeks_kept(self) -> int:
        return len(self.history)


def _week_index(key: str) -> int:
    return [w.key for w in program.load().weeks].index(key)


def view(p: Participant, it: Intention, current: program.Week | None) -> View:
    data = json.loads(crypto.open_(it.body, _ctx(p, it.id, "intention")))
    names = {w.key: w.name for w in program.load().weeks}
    history = []
    for c in it.checkins:
        d = json.loads(crypto.open_(c.body, _ctx(p, it.id, f"checkin:{c.week_key}")))
        history.append({"week_key": c.week_key, "week_name": names.get(c.week_key, c.week_key), **d})
    due = bool(it.active and current is not None
               and _week_index(current.key) > _week_index(it.week_key)
               and all(c.week_key != current.key for c in it.checkins))
    return View(str(it.id), it.area_key, names.get(it.area_key, it.area_key), data["kind"], data["fields"],
                sentence(data["kind"], data["fields"]), it.active, it.week_key, history, due)


def views(p: Participant) -> list[View]:
    current = program.load().current_week()
    return [view(p, it, current) for it in p.intentions]


def checkin(p: Participant, intention_id, days, next_step: str) -> Intention:
    current = program.load().current_week()
    it = get(p, intention_id)
    if not view(p, it, current).due:
        raise IntentionError("This intention doesn't need a check-in right now.")
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise IntentionError("Choose how many days, 0 to 7.") from None
    if not 0 <= days <= 7 or next_step not in NEXT_STEPS:
        raise IntentionError("Choose how many days, and what's next for it.")
    week_number = len(it.checkins) + 1
    # Her private copy (encrypted) ...
    db.session.add(IntentionCheckin(intention_id=it.id, week_key=current.key, body=crypto.seal(
        json.dumps({"days": days, "next": next_step}), _ctx(p, it.id, f"checkin:{current.key}"))))
    # ... and the anonymous copy the report reads: no participant, no intention, no timestamp.
    db.session.add(AnonCheckin(cohort_id=p.cohort_id, week_key=current.key, area_key=it.area_key,
                               days=days, next_step=next_step, week_number=week_number))
    if next_step == "let_go":
        it.active = False
    db.session.commit()
    return it
