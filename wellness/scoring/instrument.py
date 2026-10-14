"""Load and validate a versioned assessment instrument from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

INSTRUMENT_DIR = Path(__file__).resolve().parent.parent / "instrument"


class InstrumentError(ValueError):
    """The instrument file is malformed."""


@dataclass(frozen=True)
class Item:
    key: str
    text: str


@dataclass(frozen=True)
class Scale:
    key: str
    name: str
    items: tuple[Item, ...]
    session_date: date | None = None  # domains only


@dataclass(frozen=True)
class Band:
    min: int
    label: str


@dataclass(frozen=True)
class Instrument:
    version: str
    scale_min: int
    scale_max: int
    scale_labels: dict[int, str]
    bands: tuple[Band, ...]
    domains: tuple[Scale, ...]
    indices: tuple[Scale, ...]
    second_highlight_within: int
    late_baseline_after: datetime

    @property
    def all_scales(self) -> tuple[Scale, ...]:
        return self.domains + self.indices

    @property
    def item_keys(self) -> tuple[str, ...]:
        return tuple(i.key for s in self.all_scales for i in s.items)

    def band_for(self, score: float) -> str:
        label = self.bands[0].label
        for band in self.bands:
            if score >= band.min:
                label = band.label
        return label

    def domain(self, key: str) -> Scale:
        for d in self.domains:
            if d.key == key:
                return d
        raise KeyError(key)


def _scale(raw: dict, is_domain: bool) -> Scale:
    items = tuple(Item(key=i["key"], text=i["text"].strip()) for i in raw["items"])
    if not items:
        raise InstrumentError(f"{raw['key']} has no items")
    session = raw.get("session_date") if is_domain else None
    if is_domain and session is None:
        raise InstrumentError(f"domain {raw['key']} needs a session_date")
    return Scale(key=raw["key"], name=raw["name"], items=items, session_date=session)


def load_instrument(version: str = "v1", directory: Path = INSTRUMENT_DIR) -> Instrument:
    path = directory / f"{version}.yaml"
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if raw.get("version") != version:
        raise InstrumentError(f"{path.name} declares version {raw.get('version')!r}")

    bands = tuple(sorted((Band(b["min"], b["label"]) for b in raw["bands"]), key=lambda b: b.min))
    if bands[0].min != 0:
        raise InstrumentError("the lowest band must start at 0")

    late = datetime.fromisoformat(str(raw["rules"]["late_baseline_after"]))
    if late.tzinfo is None:
        raise InstrumentError("late_baseline_after must include a UTC offset")

    instrument = Instrument(
        version=version,
        scale_min=raw["scale"]["min"],
        scale_max=raw["scale"]["max"],
        scale_labels={int(k): v for k, v in raw["scale"]["labels"].items()},
        bands=bands,
        domains=tuple(_scale(d, True) for d in raw["domains"]),
        indices=tuple(_scale(d, False) for d in raw.get("indices", [])),
        second_highlight_within=raw["rules"]["second_highlight_within"],
        late_baseline_after=late,
    )

    keys = instrument.item_keys
    if len(keys) != len(set(keys)):
        raise InstrumentError("item keys must be unique")
    scale_keys = [s.key for s in instrument.all_scales]
    if len(scale_keys) != len(set(scale_keys)):
        raise InstrumentError("domain and index keys must be unique")
    expected = set(range(instrument.scale_min, instrument.scale_max + 1))
    if set(instrument.scale_labels) != expected:
        raise InstrumentError("every scale point needs a label")
    return instrument
