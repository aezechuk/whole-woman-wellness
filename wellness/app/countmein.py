"""Count Me In: anonymous demographics for the funder report.

Answers are stored in DemographicResponse with NO participant id and no
timestamp, so a response can't be traced back to anyone. The participant row
only records that she has submitted, so the app doesn't ask twice.

Option keys are stable; labels are what participants see.
"""

from __future__ import annotations

SKIP = "skip"

RACE = [
    ("asian", "Asian/Asian American"),
    ("black", "Black/African American"),
    ("hispanic", "Hispanic/Latino/Latina/Latinx"),
    ("mena", "Middle Eastern/North African"),
    ("native", "Native American/American Indian/Alaska Native/Indigenous"),
    ("pacific", "Native Hawaiian/Pacific Islander"),
    ("white", "White/Caucasian/European"),
    ("another", "Another race or identity"),
]

SINGLE = {
    "gender": ("How do you describe your gender?", [
        ("female", "Female"), ("male", "Male"),
        ("nonbinary", "Non-binary/Genderqueer/Gender non-conforming")]),
    "transgender": ("Do you identify as transgender?", [("yes", "Yes"), ("no", "No")]),
    "orientation": ("How do you describe your sexual orientation?", [
        ("straight", "Heterosexual or straight"), ("lgbtqia", "LGBTQIA+")]),
    "disability": ("Do you identify as a person with a disability?", [("yes", "Yes"), ("no", "No")]),
    "age": ("What is your age?", [
        ("18_24", "18–24"), ("25_54", "25–54"), ("55_64", "55–64"), ("65_plus", "65+")]),
    "household_size": ("How many people live in your household, including you?", [
        (str(n), str(n)) for n in range(1, 8)] + [("8_plus", "8 or more")]),
    "income_band": ("What is your household's yearly income?", [
        ("lt_15k", "Under $15,000"), ("15_25k", "$15,000–$24,999"), ("25_35k", "$25,000–$34,999"),
        ("35_50k", "$35,000–$49,999"), ("50_75k", "$50,000–$74,999"),
        ("75_100k", "$75,000–$99,999"), ("100k_plus", "$100,000 or more")]),
}


def parse(form) -> dict:
    """Turn submitted form data into clean values. Anything unexpected becomes 'skip'."""
    race_keys = {k for k, _ in RACE}
    race = sorted({v for v in form.getlist("race") if v in race_keys})
    out: dict = {"race": race}
    for field, (_, options) in SINGLE.items():
        value = form.get(field, SKIP)
        out[field] = value if value in {k for k, _ in options} else SKIP
    return out
