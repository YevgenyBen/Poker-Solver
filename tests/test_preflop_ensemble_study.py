"""The R10 study's rule, as code (M290)."""
import pytest

from bench.studies import preflop_ensemble as study


def _nodes(single, ensemble, n=40):
    return [{"count": 10, "facing": k % 4 < 2,
             "single_move": single if k % 4 < 2 else 0.05,
             "ensemble_move": ensemble if k % 4 < 2 else 0.03} for k in range(n)]


def _out(delta_sigma):
    return {"facing": {"sigma": delta_sigma}, "all": {"sigma": delta_sigma}}


def test_a_steadier_ensemble_that_is_not_worse_outside_is_adopted():
    stab = study.stability(_nodes(0.15, 0.08))
    assert stab["all"]["ratio"] == pytest.approx(0.08 / 0.15)
    assert study.verdict(stab, _out(0.5), 0.97)[0]


def test_a_small_steadying_is_refused():
    assert not study.verdict(study.stability(_nodes(0.15, 0.12)), _out(0.5), 0.97)[0]


def test_steadier_but_further_from_strong_play_is_refused():
    """M162's warning: more stable is not more correct."""
    adopt, reasons = study.verdict(study.stability(_nodes(0.15, 0.08)), _out(-2.5), 0.97)
    assert not adopt and any("OUTSIDE" in r for r in reasons)


def test_too_little_warm_coverage_is_refused():
    adopt, reasons = study.verdict(study.stability(_nodes(0.15, 0.08)), _out(0.5), 0.90)
    assert not adopt and any("COVERAGE" in r for r in reasons)


def test_both_halves_must_steady():
    nodes = _nodes(0.15, 0.08)
    for k, n in enumerate(nodes):
        if k % 4 == 0:                  # half0's facing nodes get no steadier
            n["ensemble_move"] = 0.15
    assert not study.verdict(study.stability(nodes), _out(0.5), 0.97)[0]


def test_the_note_is_rejudged_on_the_ensemble_arm():
    assert study.note_after(study.stability(_nodes(0.15, 0.12))) == pytest.approx(0.12)
    assert study.note_after(study.stability(_nodes(0.15, 0.06))) is None


def test_paired_and_outside():
    out = study.outside([{"facing": True, "single_agree": 0.5, "ensemble_agree": 0.6},
                         {"facing": True, "single_agree": 0.5, "ensemble_agree": 0.7},
                         {"facing": False, "single_agree": 0.4, "ensemble_agree": 0.4}])
    assert out["facing"]["delta"] == pytest.approx(0.15) and out["facing"]["n"] == 2
    assert out["all"]["n"] == 3


def test_warm_share_floors_to_the_bucket():
    """F13/M124: the bucket FLOORS, so 99bb is the 95 bucket, not 100."""
    assert study.warm_share([100.0, 104.9, 99.0], {100.0}, 5.0) == pytest.approx(2 / 3)


def test_the_seeds_are_fresh_and_disjoint():
    from bench.studies import node_spread, preflop_fold_seeds
    ens = {s + i for s in study.ENSEMBLE_SEEDS for i in range(study.K)}
    used = set(node_spread.SEEDS) | set(node_spread.FOLD_SEEDS) | set(preflop_fold_seeds.SEEDS)
    fresh = (set(study.SINGLE_SEEDS[1:]) | ens) - set(range(1, study.K + 1))
    assert not fresh & used
    groups = [set(range(s, s + study.K)) for s in study.ENSEMBLE_SEEDS]
    assert all(not a & b for i, a in enumerate(groups) for b in groups[i + 1:])


# -- M290: the recorded run -------------------------------------------------

import json
import pathlib

from api import config as cfg

FIXTURE = pathlib.Path(__file__).parent / "data" / "preflop_ensemble_m290.json"


def _recorded():
    return json.loads(FIXTURE.read_text())


def _coverage(data, include_disk):
    counts = {float(k): v for k, v in data["bucket_counts"].items()}
    warmed = {float(d) for d in cfg.MULTIWAY_PREWARM_STACK_DEPTHS}
    warmed |= {float(d) for p, d in cfg.MULTIWAY_BACKGROUND_WARM if p == study.SIZE}
    if include_disk:
        warmed |= {float(d) for p, d in cfg.MULTIWAY_DISK_WARM if p == study.SIZE}
    return sum(v for k, v in counts.items() if k in warmed) / sum(counts.values())


def test_the_recorded_run_refused_on_coverage_alone():
    """The verdict as it came back: steadier (0.58x) and no worse outside,
    and REFUSED because 91.6% of real six-handed hands sat at warmed
    depths against a 95% bar. Recorded, not rewritten."""
    data = _recorded()
    stab, out = study.stability(data["nodes"]), study.outside(data["decisions"])
    adopt, reasons = study.verdict(stab, out, data["warm_share"])
    assert not adopt and len(reasons) == 1 and reasons[0].startswith("COVERAGE")
    assert stab["all"]["ratio"] < 0.6
    assert _coverage(data, include_disk=False) == pytest.approx(data["warm_share"])


def test_the_warm_list_now_meets_the_coverage_bar_and_the_rule_adopts():
    """Coverage is a property of the warm lists, not a noisy measurement,
    so meeting it by warming more buckets is legitimate - and it is
    re-derived here from the recorded stacks and the SHIPPED lists."""
    data = _recorded()
    share = _coverage(data, include_disk=True)
    assert share >= study.MIN_WARM_SHARE
    stab, out = study.stability(data["nodes"]), study.outside(data["decisions"])
    assert study.verdict(stab, out, share)[0]


def test_on_the_ensemble_arm_the_six_handed_fold_note_stops_firing():
    assert study.note_after(study.stability(_recorded()["nodes"])) is None
    assert 6 not in cfg.PREFLOP_FOLD_SEED_REASONS
