"""The R2 study's rule, as code (M295)."""
import pytest

from bench.studies import stack_substitution as study


def _rows(seed_tvd, seed_changed, per_distance, n=40):
    rows = []
    for k in range(n):
        row = {"count": 1, "seed_tvd": seed_tvd, "seed_changed": k % 100 < seed_changed * 100}
        for distance, (tvd, changed) in per_distance.items():
            row[f"d{distance:g}_tvd"] = tvd
            row[f"d{distance:g}_changed"] = k % 100 < changed * 100
        rows.append(row)
    return rows


def test_a_substitution_inside_the_noise_is_adopted():
    rows = _rows(0.20, 0.10, {5.0: (0.05, 0.0), 10.0: (0.10, 0.05),
                              20.0: (0.40, 0.30), 40.0: (0.60, 0.50)})
    distance, text = study.verdict(study.summarise(rows))
    assert distance == 10.0 and text.startswith("ADOPT")


def test_nothing_inside_the_noise_is_refused():
    rows = _rows(0.05, 0.02, {5.0: (0.20, 0.10), 10.0: (0.30, 0.20),
                              20.0: (0.40, 0.30), 40.0: (0.60, 0.50)})
    distance, text = study.verdict(study.summarise(rows))
    assert distance is None and text.startswith("REFUSED")


def test_a_distance_that_moves_the_top_action_more_is_refused_even_if_its_tvd_is_small():
    """M124's control is two-sided: a small average move that flips the
    recommendation is the failure a player would notice."""
    rows = _rows(0.20, 0.05, {5.0: (0.05, 0.30), 10.0: (0.9, 0.9),
                              20.0: (0.9, 0.9), 40.0: (0.9, 0.9)})
    assert study.verdict(study.summarise(rows))[0] is None


def test_both_halves_must_hold():
    rows = _rows(0.20, 0.10, {5.0: (0.05, 0.0), 10.0: (0.9, 0.9),
                              20.0: (0.9, 0.9), 40.0: (0.9, 0.9)})
    for k, row in enumerate(rows):
        if k % 2 == 0:
            row["d5_tvd"] = 0.9          # half0 alone breaks it
    assert study.verdict(study.summarise(rows))[0] is None


def test_the_cap_bounds_what_can_be_adopted():
    rows = _rows(0.90, 0.90, {5.0: (0.01, 0.0), 10.0: (0.01, 0.0),
                              20.0: (0.01, 0.0), 40.0: (0.01, 0.0)})
    assert study.verdict(study.summarise(rows))[0] == study.MAX_DISTANCE_BB
    assert max(study.DISTANCES) <= study.MAX_DISTANCE_BB


def test_an_empty_population_is_refused_not_adopted():
    assert study.verdict(study.summarise([]))[0] is None


# -- M295: the recorded run ------------------------------------------------

import json
import pathlib

FIXTURE = pathlib.Path(__file__).parent / "data" / "stack_substitution_m295.json"


def _recorded():
    return json.loads(FIXTURE.read_text())


def test_the_recorded_run_refuses_every_distance():
    """75 real six-handed nodes. Substitution is REFUSED, and the reason
    is worth keeping: it is not that a far bucket is wild, it is that the
    solver's own seed noise is as large as a 40bb change of depth."""
    summary = study.summarise(_recorded())
    distance, text = study.verdict(summary)
    assert distance is None and text.startswith("REFUSED")
    assert summary["all"]["seed"]["n"] == 75


def test_depth_moves_the_strategy_no_more_than_the_seed_does():
    """The finding underneath the refusal: 40bb away is about the same as
    5bb away, and both sit within a whisker of the noise floor."""
    cells = study.summarise(_recorded())["all"]
    noise = cells["seed"]["tvd"]
    for distance in study.DISTANCES:
        moved = cells[str(distance)]["tvd"]
        assert 0.9 * noise <= moved <= 1.15 * noise, (distance, moved, noise)


def test_the_refusal_is_narrow_not_categorical():
    """Every distance is ABOVE the yardstick, which is why the rule says
    no - but by a margin this instrument cannot resolve. Recorded so a
    future attempt knows it needs a sharper yardstick, not a bigger
    sample of the same thing."""
    cells = study.summarise(_recorded())["all"]
    worst = max(cells[str(d)]["tvd"] for d in study.DISTANCES)
    assert worst - cells["seed"]["tvd"] < 0.01
