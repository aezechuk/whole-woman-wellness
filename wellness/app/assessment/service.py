"""Assessment flow: sections, autosave, completion, and view models for results.

Every function takes the signed-in participant and only ever touches that
participant's own assessments. No assessment ids come from the client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml
from flask import current_app

from ...scoring import compare, is_late_baseline, load_instrument, score
from ...scoring.engine import ENGINE_VERSION, ScaleScore
from ...scoring.instrument import Instrument, Scale
from ..extensions import db
from ..models import Assessment, Participant, utcnow

TIMEPOINTS = ("pre", "post")
CONTENT_DIR = Path(__file__).resolve().parents[2] / "content"


@lru_cache(maxsize=4)
def instrument(version: str = "v1") -> Instrument:
    return load_instrument(version)


@lru_cache(maxsize=4)
def content(version: str = "v1") -> dict:
    with (CONTENT_DIR / f"{version}.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def sections(inst: Instrument) -> list[Scale]:
    """One screen per domain, then Connection & Belonging."""
    return list(inst.domains) + list(inst.indices)


def final_is_open(now: datetime | None = None) -> bool:
    opens = current_app.config["FINAL_OPENS_AT"]
    return (now or utcnow()) >= opens


def get(participant: Participant, timepoint: str) -> Assessment | None:
    return next((a for a in participant.assessments if a.timepoint == timepoint), None)


def get_or_start(participant: Participant, timepoint: str) -> Assessment:
    a = get(participant, timepoint)
    if a is None:
        a = Assessment(participant=participant, timepoint=timepoint,
                       instrument_version=participant.cohort.instrument_version, responses={})
        db.session.add(a)
        db.session.commit()
    return a


def section_answers(a: Assessment, section: Scale) -> dict[str, int]:
    return {i.key: a.responses[i.key] for i in section.items if i.key in a.responses}


def save_section(a: Assessment, section: Scale, form) -> list[str]:
    """Save whatever valid answers were submitted. Returns keys still unanswered."""
    inst = instrument(a.instrument_version)
    updated = dict(a.responses)
    for item in section.items:
        raw = form.get(item.key)
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if inst.scale_min <= value <= inst.scale_max:
            updated[item.key] = value
    a.responses = updated  # reassign so the JSON column is marked dirty
    db.session.commit()
    return [i.key for i in section.items if i.key not in updated]


def first_incomplete_section(a: Assessment) -> int | None:
    inst = instrument(a.instrument_version)
    for n, section in enumerate(sections(inst), start=1):
        if any(i.key not in a.responses for i in section.items):
            return n
    return None


def complete(a: Assessment) -> None:
    inst = instrument(a.instrument_version)
    result = score(inst, a.responses)  # raises if anything is missing
    a.result = result.to_dict()
    a.engine_version = ENGINE_VERSION
    a.completed_at = utcnow()
    if a.timepoint == "pre":
        a.is_late_baseline = is_late_baseline(inst, a.completed_at)
    db.session.commit()


def set_focus(a: Assessment, keys: list[str]) -> bool:
    inst = instrument(a.instrument_version)
    valid = [d.key for d in inst.domains]
    chosen = [k for k in valid if k in set(keys)]  # keep session order
    if len(chosen) > 2:
        return False
    a.focus_domains = chosen
    db.session.commit()
    return True


# ---- view models ---------------------------------------------------------------

@dataclass
class DomainCard:
    key: str
    name: str
    display: int
    band: str
    text: str


def _result(a: Assessment):
    """Rebuild the engine result from stored responses (deterministic)."""
    return score(instrument(a.instrument_version), a.responses)


def snapshot(a: Assessment) -> dict:
    inst = instrument(a.instrument_version)
    copy = content(a.instrument_version)
    r = _result(a)
    is_final = a.timepoint == "post"

    def strong(d: ScaleScore) -> DomainCard:
        return DomainCard(d.key, d.name, d.display, d.band, copy["domains"][d.key]["strong"].strip())

    def care(d: ScaleScore) -> DomainCard:
        c = copy["domains"][d.key]
        text = c["care"].strip() if is_final else f"{c['care'].strip()} {c['care_session'].strip()}"
        return DomainCard(d.key, d.name, d.display, d.band, text)

    return {
        "domains": r.domains,
        "connection": r.indices[0] if r.indices else None,
        "overall": r.overall_display,
        "overall_band": r.overall_band,
        "strong": [strong(r.domain(k)) for k in r.flowing_most_easily],
        "care": [care(r.domain(k)) for k in r.asking_for_more_care],
        "all_even": r.all_even,
        "focus": list(a.focus_domains or []),
        "domain_choices": [(d.key, d.name) for d in inst.domains],
        "text": copy,
    }


def comparison(pre: Assessment, post: Assessment) -> dict:
    copy = content(post.instrument_version)
    pre_r, post_r = _result(pre), _result(post)
    c = compare(pre_r, post_r, focus=tuple(pre.focus_domains or ()))
    names = {d.key: d.name for d in post_r.domains}
    return {
        "domains": c.domains,
        "connection": c.indices[0] if c.indices else None,
        "focus": c.focus_domains,
        "overall_before": c.overall_before,
        "overall_after": c.overall_after,
        "overall_change": c.overall_change,
        "greatest": [names[k] for k in c.greatest_change],
        "greatest_change": max((d.change for d in c.domains), default=0),
        "strong_now": [post_r.domain(k) for k in post_r.flowing_most_easily],
        "care_now": [post_r.domain(k) for k in post_r.asking_for_more_care],
        "decreases": [copy["decrease"].strip().format(name=d.name) for d in c.domains if d.decreased],
        "pre_completed": pre.completed_at,
        "post_completed": post.completed_at,
    }
