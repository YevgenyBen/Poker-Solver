"""The R6 follow-up's rule and its recorded run (M291)."""
import json
import pathlib

import pytest

from bench.studies import three_live_budget as study

FIXTURE = pathlib.Path(__file__).parent / "data" / "three_live_budget_m291.json"


def _rows(shipped, half, n=200, live=3, street="flop"):
    """Deltas must VARY, or their sigma is undefined and every verdict
    reads "not worse" - the first draft of this fixture did exactly that
    and passed the refusal test for the wrong reason."""
    return [{"street": street, "live": live,
             "shipped": shipped + (0.01 if k % 2 else -0.01),
             "half": half + (0.01 if k % 2 else -0.01) + (0.02 if k % 3 == 0 else -0.01)}
            for k in range(n)]


def test_a_clearly_worse_half_budget_is_refused():
    adopt, refusing = study.verdict(study.summarise(_rows(0.58, 0.50)))
    assert not adopt and refusing == ["all", "live3"]


def test_an_equal_half_budget_is_adopted():
    """Same answers at half the work: the rule takes the cheaper arm."""
    adopt, _ = study.verdict(study.summarise(_rows(0.58, 0.58)))
    assert adopt


def test_the_rule_reads_every_decision_not_only_the_three_live_ones():
    """As pre-registered: "not worse over all decisions AND over the
    three-live subset". The arms differ only below four live, so a large
    four-live move would be noise - and the rule would still refuse. That
    is the conservative direction, and it is recorded rather than quietly
    narrowed after the fact."""
    rows = _rows(0.58, 0.58) + _rows(0.58, 0.30, n=80, live=4)
    adopt, refusing = study.verdict(study.summarise(rows))
    assert not adopt and refusing == ["all"]
    assert study.verdict(study.summarise(_rows(0.58, 0.58)))[0]


def test_paired_reads_the_delta_not_the_levels():
    out = study.paired([{"shipped": 0.5, "half": 0.4}, {"shipped": 0.7, "half": 0.6}])
    assert out["delta"] == pytest.approx(-0.1) and out["n"] == 2


def test_the_recorded_run_refuses_the_half_budget():
    """579 real multiway decisions, scored on the action the outside player
    took. Halving is separably worse and about twice as fast."""
    rows = json.loads(FIXTURE.read_text())
    summary = study.summarise(rows)
    adopt, refusing = study.verdict(summary)
    assert not adopt and refusing == ["all", "live3"]
    assert summary["all"]["sigma"] < -2.5
    assert summary["flop3"]["delta"] < 0 and summary["turn3"]["delta"] < 0
    faster = [r for r in rows if r["live"] == 3 and r["street"] in ("flop", "turn")]
    assert (sum(r["half_seconds"] for r in faster)
            < 0.7 * sum(r["shipped_seconds"] for r in faster))


def test_the_recorded_run_covers_the_cells_the_rule_reads():
    rows = json.loads(FIXTURE.read_text())
    assert len(rows) == 579
    assert {r["live"] for r in rows} >= {3, 4}
    assert {r["street"] for r in rows} == {"flop", "turn", "river"}
