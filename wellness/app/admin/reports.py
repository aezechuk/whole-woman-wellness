"""Group-level reporting for admins. Nothing here returns an individual's answers.

Every number is shown with its n. Scores are recomputed from stored responses
with the same deterministic engine participants see.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from statistics import mean

from sqlalchemy import select

from ...scoring import score
from ..assessment import service as assess
from ..extensions import db
from ..models import DemographicResponse, Participant, SessionFeedback, Testimonial
from .. import program

GROUPS = {"all": "Everyone", "mentee": "Mentorship cohort", "public": "Wellness Initiative (public)"}

# 2026 HHS poverty guidelines, 48 contiguous states (Indiana). Published Jan 15, 2026.
POVERTY_2026 = {1: 15_960, 2: 21_640, 3: 27_320, 4: 33_000, 5: 38_680, 6: 44_360, 7: 50_040, 8: 55_720}
POVERTY_EXTRA_PERSON = 5_680

# Representative income for each band (midpoint; open-ended top band uses $125k).
INCOME_MIDPOINT = {"lt_15k": 7_500, "15_25k": 20_000, "25_35k": 30_000, "35_50k": 42_500,
                   "50_75k": 62_500, "75_100k": 87_500, "100k_plus": 125_000}


def r1(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def in_group(p: Participant, group: str) -> bool:
    if group == "mentee":
        return p.is_mentee is True
    if group == "public":
        return p.is_mentee is False
    return True


def _participants() -> list[Participant]:
    return [p for p in db.session.scalars(select(Participant)) if p.has_consented]


def _completed(p: Participant, tp: str):
    a = assess.get(p, tp)
    return a if a is not None and a.is_complete else None


# ---- overview ----------------------------------------------------------------------

def overview() -> dict:
    ps = _participants()
    pre = [p for p in ps if _completed(p, "pre")]
    post = [p for p in ps if _completed(p, "post")]
    return {
        "participants": len(ps),
        "mentee": sum(p.is_mentee is True for p in ps),
        "public": sum(p.is_mentee is False for p in ps),
        "unspecified": sum(p.is_mentee is None for p in ps),
        "baseline": len(pre),
        "late_baseline": sum(_completed(p, "pre").is_late_baseline for p in pre),
        "final": len(post),
        "paired": sum(1 for p in post if _completed(p, "pre")),
        "count_me_in": db.session.query(DemographicResponse).count(),
        "testimonials": db.session.query(Testimonial).count(),
    }


# ---- outcomes ----------------------------------------------------------------------

@dataclass
class OutcomeRow:
    name: str
    pre_mean: float | None
    pre_n: int
    post_mean: float | None
    post_n: int
    change_mean: float | None
    paired_n: int
    improved_pct: float | None  # % of paired who rose 10+ points


def outcomes(group: str = "all", include_late: bool = True) -> list[OutcomeRow]:
    inst = assess.instrument("v1")
    pre_scores: dict = {}
    post_scores: dict = {}
    for p in _participants():
        if not in_group(p, group):
            continue
        for tp, bucket in (("pre", pre_scores), ("post", post_scores)):
            a = _completed(p, tp)
            if a is None or (tp == "pre" and a.is_late_baseline and not include_late):
                continue
            r = score(inst, a.responses)
            row = {d.key: d.display for d in r.domains}
            row["overall"] = r.overall_display
            for i in r.indices:
                row[i.key] = i.display
            bucket[p.id] = row

    keys = [(d.key, d.name) for d in inst.domains] + [("overall", "Whole Woman overall")] + \
           [(i.key, i.name) for i in inst.indices]
    paired = [pid for pid in pre_scores if pid in post_scores]
    rows = []
    for key, name in keys:
        pre_v = [s[key] for s in pre_scores.values()]
        post_v = [s[key] for s in post_scores.values()]
        deltas = [post_scores[pid][key] - pre_scores[pid][key] for pid in paired]
        rows.append(OutcomeRow(
            name=name,
            pre_mean=r1(mean(pre_v)) if pre_v else None, pre_n=len(pre_v),
            post_mean=r1(mean(post_v)) if post_v else None, post_n=len(post_v),
            change_mean=r1(mean(deltas)) if deltas else None, paired_n=len(deltas),
            improved_pct=r1(100 * sum(d >= 10 for d in deltas) / len(deltas)) if deltas else None,
        ))
    return rows


def final_extras(group: str = "all") -> dict:
    copy = assess.content("v1")
    extras = [a.extras for p in _participants() if in_group(p, group)
              for a in [_completed(p, "post")] if a is not None and a.extras is not None]
    n = len(extras)
    counts = Counter(k for e in extras for k in e.get("practices", []))
    any_practice = sum(1 for e in extras if e.get("practices") or e.get("other"))
    conn = [e["program_connection"] for e in extras if e.get("program_connection")]
    return {
        "n": n,
        "practices": [(p["label"], counts[p["key"]], r1(100 * counts[p["key"]] / n) if n else None)
                      for p in copy["practices"]],
        "other": [e["other"] for e in extras if e.get("other")],
        "any_practice": any_practice,
        "any_practice_pct": r1(100 * any_practice / n) if n else None,
        "program_connection": copy["program_connection"],
        "connection_mean": r1(mean(conn)) if conn else None,
        "connection_n": len(conn),
        "connection_agree_pct": r1(100 * sum(c >= 4 for c in conn) / len(conn)) if conn else None,
    }


# ---- funder demographics -----------------------------------------------------------

def poverty_line(household: int) -> int:
    if household <= 8:
        return POVERTY_2026[household]
    return POVERTY_2026[8] + POVERTY_EXTRA_PERSON * (household - 8)


def poverty_category(household_size: str, income_band: str) -> str:
    if household_size == "skip" or income_band == "skip":
        return "unknown"
    size = 8 if household_size == "8_plus" else int(household_size)
    income = INCOME_MIDPOINT[income_band]
    line = poverty_line(size)
    if income <= line:
        return "at_or_below"
    if income <= 2 * line:
        return "101_200"
    return "above_200"


def _percentages(counts: dict[str, int], total: int) -> dict[str, float]:
    """Largest-remainder rounding to one decimal, so each table totals exactly 100.0%."""
    if total == 0:
        return {k: 0.0 for k in counts}
    raw = {k: 1000 * v / total for k, v in counts.items()}  # tenths of a percent
    floors = {k: int(v) for k, v in raw.items()}
    short = 1000 - sum(floors.values())
    for k in sorted(raw, key=lambda k: raw[k] - floors[k], reverse=True)[:short]:
        floors[k] += 1
    return {k: v / 10 for k, v in floors.items()}


FUNDER_TABLES = [
    ("Race and Ethnicity", "race", [
        ("asian", "Asian/Asian American"), ("black", "Black/African American"),
        ("hispanic", "Hispanic/Latino/Latina/Latinx"), ("mena", "Middle Eastern/North African"),
        ("native", "Native American/American Indian/Alaska Native/Indigenous"),
        ("pacific", "Native Hawaiian/Pacific Islander"), ("white", "White/Caucasian/European"),
        ("multi", "Multi-racial/Multi-ethnic"), ("another", "Prefer to identify with another race or identity"),
        ("unknown", "Unknown")]),
    ("Gender (Question 1)", "gender", [
        ("female", "Female"), ("male", "Male"),
        ("nonbinary", "Gender non-binary/Genderqueer/Gender non-conforming"),
        ("unknown", "Unknown or decline to state")]),
    ("Gender (Question 2)", "transgender", [
        ("yes", "Transgender"), ("no", "Cisgender (not transgender)"), ("unknown", "Unknown or decline to state")]),
    ("Sexual Orientation", "orientation", [
        ("straight", "Heterosexual or straight"), ("lgbtqia", "LGBTQIA+ community"),
        ("unknown", "Unknown or decline to state")]),
    ("Disability Status", "disability", [
        ("yes", "Person with a disability"), ("no", "Person without a disability"),
        ("unknown", "Unknown or decline to state")]),
    ("Age", "age", [
        ("65_plus", "Seniors (65+)"), ("55_64", "Seniors (55-64)"), ("25_54", "Adults (25-54)"),
        ("18_24", "Young Adult (18-24)"), ("youth", "Youth (11-17)"), ("children", "Children (6-10)"),
        ("infants", "Infants/Toddlers (Under 5)"), ("unknown", "Unknown or decline to state")]),
    ("Income Level", "income", [
        ("at_or_below", "At or below Federal Poverty Level"), ("101_200", "101% - 200% of Federal Poverty Level"),
        ("above_200", "Above 200% of Federal Poverty Level"), ("unknown", "Unknown or decline to state")]),
]


def _race_key(race: list[str]) -> str:
    if not race:
        return "unknown"
    if len(race) > 1:
        return "multi"
    return race[0]


def funder_report() -> dict:
    """Percentages in the funder's format. Everyone who didn't answer counts as Unknown."""
    responses = list(db.session.scalars(select(DemographicResponse)))
    total = max(len(_participants()), len(responses))
    tables = []
    for title, field, options in FUNDER_TABLES:
        counts = {k: 0 for k, _ in options}
        for r in responses:
            if field == "race":
                key = _race_key(r.race)
            elif field == "income":
                key = poverty_category(r.household_size, r.income_band)
            else:
                key = getattr(r, field)
                key = "unknown" if key == "skip" else key
            counts[key] = counts.get(key, 0) + 1
        counts["unknown"] += total - len(responses)  # never answered Count Me In
        pct = _percentages(counts, total)
        tables.append({"title": title, "rows": [(label, counts[k], pct[k]) for k, label in options],
                       "approximate": field == "income"})
    # Location: only the states people actually chose, most common first, then Unknown.
    from ..countmein import STATES
    names = dict(STATES)
    loc = Counter(r.state for r in responses if r.state in names)
    loc_counts = {k: n for k, n in sorted(loc.items(), key=lambda kv: (-kv[1], names[kv[0]]))}
    loc_counts["unknown"] = total - sum(loc_counts.values())
    pct = _percentages(loc_counts, total)
    tables.append({"title": "Location (State)", "approximate": False,
                   "rows": [(names.get(k, "Unknown or decline to state"), n, pct[k]) for k, n in loc_counts.items()]})
    return {"total": total, "responded": len(responses), "tables": tables}


# ---- session feedback --------------------------------------------------------------

def session_feedback() -> list[dict]:
    rows = list(db.session.scalars(select(SessionFeedback)))
    out = []
    for w in program.load().weeks:
        fb = [r for r in rows if r.week_key == w.key]
        val = [r.valuable for r in fb if r.valuable]
        use = [r.usable for r in fb if r.usable]
        out.append({
            "week": w, "n": len(fb),
            "attendance": Counter(r.attendance for r in fb),
            "valuable": r1(mean(val)) if val else None, "valuable_n": len(val),
            "usable": r1(mean(use)) if use else None, "usable_n": len(use),
            "helpful": [r.most_helpful for r in fb if r.most_helpful],
            "better": [r.make_better for r in fb if r.make_better],
        })
    return out


def testimonials() -> list[Testimonial]:
    return list(db.session.scalars(select(Testimonial).order_by(Testimonial.created_at.desc())))
