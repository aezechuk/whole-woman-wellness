"""Turn an intention's reminder into a standard calendar file (.ics, RFC 5545).

The file is generated on request and never stored. Times are "floating" (no time
zone), so 12:15 PM means 12:15 PM wherever her phone is. No calendar account is
connected and nothing about her calendar comes back to the app.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

DAYS = [("MO", "M", "Mon"), ("TU", "T", "Tue"), ("WE", "W", "Wed"), ("TH", "T", "Thu"),
        ("FR", "F", "Fri"), ("SA", "S", "Sat"), ("SU", "S", "Sun")]
DAY_CODES = [d for d, _, _ in DAYS]
DURATIONS = [10, 15, 20, 30, 45, 60]
UNTIL = {"program": "Until the program ends (Nov 30)", "4": "For 4 weeks", "8": "For 8 weeks"}
PROGRAM_END = date(2026, 11, 30)
NEUTRAL_TITLE = "Self-care: my intention"
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ReminderError(ValueError):
    pass


def parse(form) -> dict | None:
    """The optional reminder fields from the intention form. None = no reminder."""
    days = [d for d in DAY_CODES if d in form.getlist("rdays")]
    if not days:
        return None
    time = (form.get("rtime") or "").strip()
    if not _TIME.match(time):
        raise ReminderError("Pick a time for your calendar reminder.")
    try:
        minutes = int(form.get("rminutes") or 15)
    except ValueError:
        minutes = 15
    return {"days": days, "time": time, "minutes": minutes if minutes in DURATIONS else 15,
            "until": form.get("runtil") if form.get("runtil") in UNTIL else "program",
            "title": "neutral" if form.get("rtitle") == "neutral" else "full"}


def describe(r: dict) -> str:
    names = dict((d, n) for d, _, n in DAYS)
    days = "Every day" if len(r["days"]) == 7 else ", ".join(names[d] for d in r["days"])
    h, m = map(int, r["time"].split(":"))
    clock = f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
    return f"{days} at {clock} · {r['minutes']} min"


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Lines longer than 75 octets are folded (RFC 5545 3.1)."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 75:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def build(uid: str, r: dict, sentence: str, place: str, today: date, now: datetime) -> str:
    h, m = map(int, r["time"].split(":"))
    weekday = {d: i for i, d in enumerate(DAY_CODES)}
    first = min((today + timedelta(days=k) for k in range(7)
                 if DAY_CODES[(today + timedelta(days=k)).weekday()] in r["days"]))
    start = datetime(first.year, first.month, first.day, h, m)
    end = start + timedelta(minutes=r["minutes"])
    last = PROGRAM_END if r["until"] == "program" else today + timedelta(weeks=int(r["until"]))
    if last < first:
        last = first
    until = datetime(last.year, last.month, last.day, 23, 59, 59)
    full = r["title"] == "full"
    fmt = "%Y%m%dT%H%M%S"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Arthentic Arts//Radical Self-Care Reset//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}@wellness.arthenticarts.org",
        f"DTSTAMP:{now.strftime(fmt)}Z",
        f"DTSTART:{start.strftime(fmt)}",
        f"DTEND:{end.strftime(fmt)}",
        f"RRULE:FREQ=WEEKLY;BYDAY={','.join(sorted(r['days'], key=weekday.get))};UNTIL={until.strftime(fmt)}",
        f"SUMMARY:{_escape(sentence if full else NEUTRAL_TITLE)}",
        f"DESCRIPTION:{_escape((sentence + chr(10) + chr(10) if full else '') + 'From your Radical Self-Care Reset app.')}",
    ]
    if full and place:
        lines.append(f"LOCATION:{_escape(place)}")
    lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(sentence if full else NEUTRAL_TITLE)}",
              "TRIGGER:-PT10M", "END:VALARM", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"
