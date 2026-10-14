"""Program calendar: which week is open, and when things happen."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .models import utcnow

PATH = Path(__file__).resolve().parents[1] / "content" / "program.yaml"


# Journal slot for each week's free-write. Fixed so adding prompts never moves it.
FREE_WRITE = 100


@dataclass(frozen=True)
class Week:
    key: str
    name: str
    number: int
    opens_at: datetime
    prompt: str
    extra_prompts: tuple[str, ...]
    overview: str = ""

    def is_open(self, now: datetime | None = None) -> bool:
        return (now or utcnow()) >= self.opens_at

    @property
    def prompts(self) -> tuple[str, ...]:
        return (self.prompt,) + self.extra_prompts

    @property
    def slots(self) -> list[tuple[int, str]]:
        """Every journal field for the week: (index, label), free-write last."""
        return list(enumerate(self.prompts)) + [(FREE_WRITE, "Free thoughts")]


@dataclass(frozen=True)
class Program:
    tz: ZoneInfo
    weeks: tuple[Week, ...]
    emails: dict[str, tuple[datetime, str]]
    data_deletion: datetime

    def week(self, key: str) -> Week:
        for w in self.weeks:
            if w.key == key:
                return w
        raise KeyError(key)

    def open_weeks(self, now: datetime | None = None) -> list[Week]:
        return [w for w in self.weeks if w.is_open(now)]

    def current_week(self, now: datetime | None = None) -> Week | None:
        opened = self.open_weeks(now)
        return opened[-1] if opened else None


@lru_cache(maxsize=1)
def load() -> Program:
    raw = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    tz = ZoneInfo(raw["timezone"])

    def at(s: str) -> datetime:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=tz)

    weeks = tuple(
        Week(w["key"], w["name"], w["week"], at(w["opens"]), w["prompt"].strip(),
             tuple(p.strip() for p in w.get("extra_prompts") or []), (w.get("overview") or "").strip())
        for w in raw["weeks"])
    emails = {k: (at(v["at"]), v["to"]) for k, v in raw["emails"].items()}
    return Program(tz=tz, weeks=weeks, emails=emails, data_deletion=at(raw["data_deletion"]))
