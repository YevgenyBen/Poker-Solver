"""The R8 study's pure half: the spread signal and the pre-registered
rule, tested as code so the rule that decides cannot drift from the one
written down."""
import pytest

from bench.studies import node_spread as study


def test_spread_is_zero_when_every_hand_plays_alike_and_large_when_they_differ():
    same = [[0.5, 0.5]] * 4
    assert study.spread(same, [1, 1, 1, 1]) == 0.0
    split = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]
    assert study.spread(split, [1, 1, 1, 1]) == pytest.approx(0.5)


def test_spread_is_weighted_by_combos():
    """A 12-combo offsuit class must count three times a 4-combo suited
    one, or the node's shape is measured over classes, not hands."""
    rows = [[1.0, 0.0], [0.0, 1.0]]
    assert study.spread(rows, [3, 1]) == pytest.approx(0.375)
    assert study.spread(rows, [1, 1]) == pytest.approx(0.5)


def test_row_tvd():
    assert study.row_tvd([[1.0, 0.0]], [[0.0, 1.0]], [1]) == 1.0
    assert study.row_tvd([[0.5, 0.5]], [[0.5, 0.5]], [1]) == 0.0


def _nodes(low_unstable, high_unstable, n=40):
    """Low-spread nodes unstable with probability `low_unstable`, high ones
    with `high_unstable`, interleaved so both halves see both kinds."""
    out = []
    for k in range(n):
        low = k % 4 < 2                 # low, low, high, high: each half gets both
        rate = low_unstable if low else high_unstable
        out.append({"spread": 0.03 if low else 0.30, "count": 10,
                    "unstable": (k // 2) % 10 < round(rate * 10)})
    return out


def test_a_clean_separation_adopts_the_widest_qualifying_threshold():
    t, text = study.verdict(study.summarise(_nodes(0.9, 0.1)))
    assert t == max(study.THRESHOLDS) and text.startswith("ADOPT")


def test_no_separation_is_null():
    t, text = study.verdict(study.summarise(_nodes(0.5, 0.5)))
    assert t is None and text.startswith("NULL")


def test_high_precision_without_separation_is_null():
    """Everything unstable is not a signal: the gate must beat silence."""
    t, _ = study.verdict(study.summarise(_nodes(0.9, 0.8)))
    assert t is None


def test_a_threshold_must_hold_in_both_halves():
    nodes = _nodes(0.9, 0.1)
    for k, node in enumerate(nodes):
        if k % 4 == 0:                 # half0's low-spread nodes (k even, low), made stable
            node["unstable"] = False
    t, _ = study.verdict(study.summarise(nodes))
    assert t is None


def test_fold_tvd_reads_only_the_fold_column():
    """The follow-up's ground truth: a raise/all-in reshuffle with the fold
    call unchanged is NOT instability on the axis F60 is about."""
    a = [[0.2, 0.8, 0.0]]              # fold, raise, all-in
    b = [[0.2, 0.0, 0.8]]
    assert study.fold_tvd(a, b, 0, [1]) == 0.0
    assert study.row_tvd(a, b, [1]) == pytest.approx(0.8)
    assert study.fold_tvd([[0.5, 0.5]], [[0.2, 0.8]], 0, [1]) == pytest.approx(0.3)


def test_the_follow_up_uses_fresh_seeds():
    assert study.FOLD_SEEDS[0] == 1
    assert not set(study.FOLD_SEEDS[1:]) & set(study.SEEDS)


# -- M288: the recorded runs --------------------------------------------

import json
import pathlib

DATA = pathlib.Path(__file__).parent / "data"


def _load(name):
    return json.loads((DATA / name).read_text())


def test_the_whole_row_rule_recorded_null():
    """Seeds 1-4, whole-row ground truth. 69% of real decisions sit at
    unstable nodes, so nothing can beat silence by 0.30."""
    nodes = _load("node_spread_row_m288.json")
    assert study.verdict(study.summarise(nodes))[0] is None
    weight = sum(n["count"] for n in nodes)
    unstable = sum(n["count"] for n in nodes if n["unstable"]) / weight
    assert unstable > 0.65


def test_the_fold_axis_follow_up_recorded_null_because_a_half_failed():
    """Fresh seeds 5-7. Pooled, spread < 0.15 clears the bar; one split
    half does not, and the rule requires both - M166's lesson."""
    nodes = _load("node_spread_fold_m288.json")
    summary = study.summarise(nodes)
    assert study.verdict(summary)[0] is None
    pooled = summary[0.15]["all"]
    assert pooled["precision"] >= study.MIN_PRECISION
    assert pooled["precision"] - pooled["silent_rate"] >= study.MIN_SEPARATION
    assert not study._qualifies(summary[0.15]["half0"])


def test_the_f60_node_is_unstable_on_the_fold_axis_all_the_same():
    """The node that started this: low spread AND a fold call that moves
    with the seed - it is one case the signal gets right, not a rule."""
    node = next(n for n in _load("node_spread_fold_m288.json")
                if n["path"] == ["raise", "fold", "fold", "fold", "fold"])
    assert node["spread"] < 0.15 and node["unstable"]
