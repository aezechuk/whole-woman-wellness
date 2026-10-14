from datetime import datetime, timedelta, timezone

import pytest

from wellness.scoring import (
    IncompleteResponses,
    compare,
    is_late_baseline,
    load_instrument,
    score,
)
from wellness.scoring.engine import display

INST = load_instrument("v1")
DOMAIN_KEYS = [d.key for d in INST.domains]


def answers(per_domain: dict[str, list[int]] | None = None, default: int = 3, connection=(3, 3, 3)):
    """Build a full response set. per_domain maps domain key -> its 4 answers."""
    per_domain = per_domain or {}
    out = {}
    for d in INST.domains:
        vals = per_domain.get(d.key, [default] * len(d.items))
        for item, v in zip(d.items, vals, strict=True):
            out[item.key] = v
    for item, v in zip(INST.indices[0].items, connection, strict=True):
        out[item.key] = v
    return out


# --- instrument ---------------------------------------------------------------

def test_instrument_shape():
    assert len(INST.domains) == 6
    assert all(len(d.items) == 4 for d in INST.domains)
    assert len(INST.indices) == 1 and len(INST.indices[0].items) == 3
    assert len(INST.item_keys) == 27
    assert DOMAIN_KEYS == ["reset", "embodiment", "nourishment", "play", "dream", "reclamation"]


def test_no_trend_or_double_barreled_wording_left():
    texts = " ".join(i.text.lower() for s in INST.all_scales for i in s.items)
    assert "increasingly" not in texts


@pytest.mark.parametrize("value,label", [
    (0, "Asking for Care"), (39, "Asking for Care"), (40, "Finding Your Rhythm"),
    (59, "Finding Your Rhythm"), (60, "On Steady Ground"), (79, "On Steady Ground"),
    (80, "Flowing Freely"), (100, "Flowing Freely"),
])
def test_band_boundaries(value, label):
    assert INST.band_for(value) == label


# --- scoring ------------------------------------------------------------------

def test_all_ones_and_all_fives():
    low = score(INST, answers(default=1, connection=(1, 1, 1)))
    high = score(INST, answers(default=5, connection=(5, 5, 5)))
    assert all(d.display == 0 for d in low.domains) and low.overall_display == 0
    assert all(d.display == 100 for d in high.domains) and high.overall_display == 100
    assert low.indices[0].display == 0 and high.indices[0].display == 100


@pytest.mark.parametrize("vals,expected", [
    ([1, 1, 1, 1], 0), ([2, 2, 2, 2], 25), ([3, 3, 3, 3], 50),
    ([4, 4, 4, 4], 75), ([5, 5, 5, 5], 100), ([3, 3, 3, 4], 56),  # 56.25
    ([2, 3, 3, 3], 44),  # 43.75 -> 44 (half-up)
])
def test_domain_formula(vals, expected):
    r = score(INST, answers({"play": vals}))
    assert r.domain("play").display == expected


def test_half_up_rounding_not_bankers():
    assert display(12.5) == 13 and display(37.5) == 38 and round(12.5) == 12


def test_connection_excluded_from_overall():
    a = score(INST, answers(connection=(1, 1, 1)))
    b = score(INST, answers(connection=(5, 5, 5)))
    assert a.overall == b.overall == 50


def test_overall_is_equal_weight_mean_of_unrounded():
    r = score(INST, answers({"reset": [3, 3, 3, 4], "play": [2, 3, 3, 3]}))
    expected = (56.25 + 43.75 + 50 * 4) / 6
    assert r.overall == pytest.approx(expected)


def test_incomplete_and_invalid_raise():
    resp = answers()
    del resp["DREAM_02"]
    resp["PLAY_01"] = 6
    resp["RESET_01"] = True  # bools are not answers
    with pytest.raises(IncompleteResponses) as exc:
        score(INST, resp)
    assert exc.value.missing == ["DREAM_02"]
    assert set(exc.value.invalid) == {"PLAY_01", "RESET_01"}


def test_deterministic():
    resp = answers({"play": [1, 2, 1, 2], "dream": [5, 4, 5, 4]})
    assert score(INST, resp).to_dict() == score(INST, resp).to_dict()


# --- highlights ---------------------------------------------------------------

def test_single_clear_high_and_low():
    r = score(INST, answers({"nourishment": [5, 5, 5, 5], "play": [1, 1, 1, 1]}))
    assert r.flowing_most_easily == ("nourishment",)
    assert r.asking_for_more_care == ("play",)
    assert not r.all_even


def test_second_shown_only_within_five_points():
    # dream 75, reset 69 (6 apart) -> only dream; play 25, reclamation 31 -> only play
    r = score(INST, answers({"dream": [4, 4, 4, 4], "reset": [4, 4, 4, 3],
                              "play": [2, 2, 2, 2], "reclamation": [2, 2, 2, 3]},
                             default=3))
    assert r.flowing_most_easily == ("dream",)
    assert r.asking_for_more_care == ("play",)

    # nourishment 75, embodiment 75 (tie) -> both, in session order
    r = score(INST, answers({"nourishment": [4, 4, 4, 4], "embodiment": [4, 4, 4, 4]}))
    assert r.flowing_most_easily == ("embodiment", "nourishment")


def test_ties_break_by_session_order():
    r = score(INST, answers({"play": [1, 1, 1, 1], "reset": [1, 1, 1, 1], "dream": [1, 1, 1, 1],
                              "embodiment": [5, 5, 5, 5]}))
    assert r.asking_for_more_care == ("reset", "play")


def test_all_even_profile_has_no_highlights():
    r = score(INST, answers(default=4))
    assert r.all_even
    assert r.flowing_most_easily == () and r.asking_for_more_care == ()


def test_one_outlier_never_lists_a_domain_on_both_cards():
    r = score(INST, answers({"dream": [4, 3, 3, 3]}))  # dream 56, rest 50
    assert r.flowing_most_easily == ("dream",)
    assert r.asking_for_more_care == ("reset", "embodiment")
    assert not set(r.flowing_most_easily) & set(r.asking_for_more_care)


def test_no_overlap_across_many_profiles():
    import random
    rng = random.Random(42)
    for _ in range(2000):
        per = {k: [rng.randint(1, 5) for _ in range(4)] for k in DOMAIN_KEYS}
        r = score(INST, answers(per))
        assert not set(r.flowing_most_easily) & set(r.asking_for_more_care)
        assert len(r.flowing_most_easily) <= 2 and len(r.asking_for_more_care) <= 2
        if not r.all_even:
            assert r.flowing_most_easily and r.asking_for_more_care


# --- comparison ---------------------------------------------------------------

def test_comparison_uses_displayed_scores():
    pre = score(INST, answers({"play": [2, 3, 3, 3]}))      # 44 (43.75)
    post = score(INST, answers({"play": [3, 3, 3, 4]}))     # 56 (56.25)
    c = compare(pre, post, focus=("play", "dream"))
    play = next(d for d in c.domains if d.key == "play")
    assert (play.before, play.after, play.change) == (44, 56, 12)
    assert c.greatest_change == ("play",)
    assert [f.key for f in c.focus_domains] == ["play", "dream"]


def test_decrease_is_flagged_not_hidden():
    pre = score(INST, answers({"nourishment": [4, 4, 4, 4]}))
    post = score(INST, answers({"nourishment": [3, 3, 3, 3]}))
    nour = next(d for d in compare(pre, post).domains if d.key == "nourishment")
    assert nour.change == -25 and nour.decreased


def test_no_greatest_change_when_nothing_rose():
    r = score(INST, answers())
    assert compare(r, r).greatest_change == ()


# --- late baseline ------------------------------------------------------------

def test_late_baseline_flag():
    edt = timezone(timedelta(hours=-4))
    assert not is_late_baseline(INST, datetime(2026, 10, 21, 19, 0, tzinfo=edt))
    assert not is_late_baseline(INST, datetime(2026, 10, 28, 23, 59, tzinfo=edt))
    assert is_late_baseline(INST, datetime(2026, 10, 29, 0, 0, tzinfo=edt))
    with pytest.raises(ValueError):
        is_late_baseline(INST, datetime(2026, 10, 30))
