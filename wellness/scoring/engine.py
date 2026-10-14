"""Deterministic scoring for the Whole Woman Wellness Assessment.

Pure functions only: no database, no Flask, no clock. The same responses always
produce the same result, which is what makes pre/post comparison meaningful.

Scoring rules (instrument v1):
- Each scale's score = (sum - n*min) / (n*(max-min)) * 100, kept unrounded.
- Displayed scores are rounded half-up to whole numbers.
- Bands and highlights use the displayed (rounded) score, so what a participant
  sees on screen is always consistent with its label.
- Overall = mean of the six domain scores, equal weight. Indices
  (Connection & Belonging) are excluded from the overall score.
- Changes are computed from displayed scores, so "42 -> 68" always reads "+26".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from .instrument import Instrument, Scale

ENGINE_VERSION = "1.0.0"


class IncompleteResponses(ValueError):
    """Not every scored item has a valid answer; results can't be computed yet."""

    def __init__(self, missing: list[str], invalid: list[str]):
        self.missing = missing
        self.invalid = invalid
        parts = []
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        if invalid:
            parts.append(f"invalid: {', '.join(invalid)}")
        super().__init__("; ".join(parts))


def display(score: float) -> int:
    """Round half-up (Python's round() is banker's rounding)."""
    return int(Decimal(str(score)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class ScaleScore:
    key: str
    name: str
    raw: int
    score: float  # unrounded, 0-100
    display: int
    band: str


@dataclass(frozen=True)
class Result:
    instrument_version: str
    engine_version: str
    domains: tuple[ScaleScore, ...]
    indices: tuple[ScaleScore, ...]
    overall: float
    overall_display: int
    overall_band: str
    flowing_most_easily: tuple[str, ...]  # domain keys, 0-2
    asking_for_more_care: tuple[str, ...]  # domain keys, 0-2
    all_even: bool  # every domain displays the same score

    def domain(self, key: str) -> ScaleScore:
        return next(d for d in self.domains if d.key == key)

    def to_dict(self) -> dict:
        def s(x: ScaleScore) -> dict:
            return {"key": x.key, "name": x.name, "raw": x.raw, "score": x.score,
                    "display": x.display, "band": x.band}
        return {
            "instrument_version": self.instrument_version,
            "engine_version": self.engine_version,
            "domains": [s(d) for d in self.domains],
            "indices": [s(i) for i in self.indices],
            "overall": self.overall,
            "overall_display": self.overall_display,
            "overall_band": self.overall_band,
            "flowing_most_easily": list(self.flowing_most_easily),
            "asking_for_more_care": list(self.asking_for_more_care),
            "all_even": self.all_even,
        }


def validate(instrument: Instrument, responses: dict[str, int]) -> None:
    missing, invalid = [], []
    for key in instrument.item_keys:
        value = responses.get(key)
        if value is None:
            missing.append(key)
        elif isinstance(value, bool) or not isinstance(value, int) or not (
            instrument.scale_min <= value <= instrument.scale_max
        ):
            invalid.append(key)
    if missing or invalid:
        raise IncompleteResponses(missing, invalid)


def _score_scale(instrument: Instrument, scale: Scale, responses: dict[str, int]) -> ScaleScore:
    n = len(scale.items)
    raw = sum(responses[i.key] for i in scale.items)
    lo, hi = n * instrument.scale_min, n * instrument.scale_max
    score = (raw - lo) / (hi - lo) * 100
    shown = display(score)
    return ScaleScore(scale.key, scale.name, raw, score, shown, instrument.band_for(shown))


def _highlights(domains: tuple[ScaleScore, ...], within: int) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Top and bottom 1-2 domains. Ties break by session order (list order)."""
    values = [d.display for d in domains]
    if max(values) == min(values):
        return (), (), True

    order = {d.key: i for i, d in enumerate(domains)}
    high = sorted(domains, key=lambda d: (-d.display, order[d.key]))
    low = sorted(domains, key=lambda d: (d.display, order[d.key]))

    lowest, highest = min(values), max(values)

    # A second domain joins a card only if it's within `within` points of the
    # first AND still clearly on that side (never equal to the opposite extreme).
    top = [high[0]]
    if high[1].display >= highest - within and high[1].display > lowest:
        top.append(high[1])

    top_keys = {d.key for d in top}
    bottom = [low[0]]
    if (low[1].display <= lowest + within and low[1].display < highest
            and low[1].key not in top_keys):
        bottom.append(low[1])
    return tuple(d.key for d in top), tuple(d.key for d in bottom), False


def score(instrument: Instrument, responses: dict[str, int]) -> Result:
    """Score a complete set of responses. Raises IncompleteResponses otherwise."""
    validate(instrument, responses)
    domains = tuple(_score_scale(instrument, d, responses) for d in instrument.domains)
    indices = tuple(_score_scale(instrument, i, responses) for i in instrument.indices)
    overall = sum(d.score for d in domains) / len(domains)
    overall_display = display(overall)
    top, bottom, even = _highlights(domains, instrument.second_highlight_within)
    return Result(
        instrument_version=instrument.version,
        engine_version=ENGINE_VERSION,
        domains=domains,
        indices=indices,
        overall=overall,
        overall_display=overall_display,
        overall_band=instrument.band_for(overall_display),
        flowing_most_easily=top,
        asking_for_more_care=bottom,
        all_even=even,
    )


@dataclass(frozen=True)
class ScaleChange:
    key: str
    name: str
    before: int
    after: int
    change: int
    after_band: str

    @property
    def decreased(self) -> bool:
        return self.change < 0


@dataclass(frozen=True)
class Comparison:
    domains: tuple[ScaleChange, ...]
    indices: tuple[ScaleChange, ...]
    overall_before: int
    overall_after: int
    overall_change: int
    greatest_change: tuple[str, ...]  # domain keys with the largest increase; empty if none rose
    focus_domains: tuple[ScaleChange, ...] = field(default=())


def _change(before: ScaleScore, after: ScaleScore) -> ScaleChange:
    return ScaleChange(after.key, after.name, before.display, after.display,
                       after.display - before.display, after.band)


def compare(pre: Result, post: Result, focus: tuple[str, ...] = ()) -> Comparison:
    if pre.instrument_version != post.instrument_version:
        raise ValueError("pre and post must use the same instrument version")
    domains = tuple(_change(b, a) for b, a in zip(pre.domains, post.domains))
    indices = tuple(_change(b, a) for b, a in zip(pre.indices, post.indices))
    best = max(d.change for d in domains)
    greatest = tuple(d.key for d in domains if d.change == best) if best > 0 else ()
    by_key = {d.key: d for d in domains}
    return Comparison(
        domains=domains,
        indices=indices,
        overall_before=pre.overall_display,
        overall_after=post.overall_display,
        overall_change=post.overall_display - pre.overall_display,
        greatest_change=greatest,
        focus_domains=tuple(by_key[k] for k in focus if k in by_key),
    )


def is_late_baseline(instrument: Instrument, completed_at: datetime) -> bool:
    if completed_at.tzinfo is None:
        raise ValueError("completed_at must be timezone-aware")
    return completed_at >= instrument.late_baseline_after
