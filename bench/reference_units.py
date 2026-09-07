# -*- coding: utf-8 -*-
"""Latency measured in REFERENCE UNITS, so machine drift is visible.

CLAUDE.md's standing rule (M70) is that absolute wall-clock numbers from
different sessions are not comparable on this machine - identical work
was measured running 1.7x slower between two milestones. M234 then found
the same drift happening WITHIN one benchmark run (1.13s -> 1.78s on the
same workload), and a cost study produced a negative marginal cost
before it was caught and discarded.

The rule has been "run an interleaved A/B", which works when there are
two arms to interleave and does nothing for a single-arm measurement -
a cold-request cost, a per-street breakdown, a cache-fill rate. Those
are the numbers that have gone wrong.

**A reference unit is one run of a fixed engine workload.** Time the
thing you care about, time the reference near it, and report the ratio.
A slowdown that scales both cancels out of the ratio; one that does not
is a real change in the work, which is what you were trying to measure.

Two properties make the reference trustworthy, and both are asserted by
tests rather than assumed:

- **It is the same work every time.** A fixed board, a fixed 90-combo
  pool, a fixed seed. `reference_result()` returns byte-identical
  strategies run to run, so a change in its cost is a change in the
  machine and cannot be a change in what was asked of it.
- **It is made of the work being normalised.** It is a real
  `solve_flop`, so it pays for an equity table AND a CFR solve - the two
  cost centres whose ratio M217 measured inverting three times. A
  reference built from only one of them would mis-normalise the other.

It costs ~0.28s, so calibrating between measurements is affordable
enough to do before every one, which is the default: drift arriving
mid-run is then caught rather than smeared across the results.

**Validated by inducing drift rather than waiting for it** (M240). The
same measured workload, run quiet / under full CPU contention / quiet
again:

| comparison | in seconds | in reference units |
|---|---|---|
| quiet -> loaded | **4.82x slower** | **0.944x** |
| quiet -> quiet, same run | **1.32x slower** | **0.986x** |

The second row is the one that matters: two phases that a study would
call identical differ by 32% in seconds and by 1.4% in units, with no
load applied - that is M70's problem happening live. Across the whole
run the instrument removes 98.3% of the induced slowdown.

**What it costs.** On a machine that is holding still, units are NOISIER
than seconds (4% against 0.6% in the quiet phase) because a ratio
carries the reference's own variation, and under heavy contention a
single reading is noisier still. Normalising buys a correct LEVEL at the
price of some per-measurement precision, so quote a median over several
measurements and never a lone unit reading.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from poker_solver.cards import Card
from poker_solver.combos import HandCombo
from poker_solver.solver import solve_flop

#: A fixed, deliberately dull spot. Nothing about it is meant to be
#: representative of good play - only of the COST of a flop solve.
REFERENCE_BOARD = ("Ah", "7d", "2c")
REFERENCE_COMBOS = 90
REFERENCE_POT = 6.0
REFERENCE_STACK_BB = 40.0
REFERENCE_ITERATIONS = 250
REFERENCE_EQUITY_SAMPLES = 30
REFERENCE_EQUITY_SEED = 42

#: Measured at adoption on an otherwise idle machine, for scale only:
#: one unit was ~0.28s. Never compare a stored second against a fresh
#: one - that is the mistake this module exists to prevent.


def _reference_inputs():
    board = tuple(Card.from_str(c) for c in REFERENCE_BOARD)
    deck = [Card.from_str(rank + suit)
            for rank in "KQJT98765432" for suit in "shdc"]
    combos = [HandCombo(a, b) for a, b in itertools.combinations(deck, 2)]
    combos = combos[:REFERENCE_COMBOS]
    weights = {combo: 1.0 for combo in combos}
    return board, weights


def reference_result():
    """Run the reference workload and return its StrategyResult.

    Exposed so a test can assert the work is IDENTICAL run to run. If it
    ever stops being identical, every unit measured with it is measuring
    two things at once.
    """
    board, weights = _reference_inputs()
    return solve_flop(
        board=board, hero_range=weights, villain_range=weights,
        pot=REFERENCE_POT, effective_stack_bb=REFERENCE_STACK_BB,
        positions=("BTN", "BB"), raise_sizes=(0.75, 2.0), max_raises=3,
        iterations=REFERENCE_ITERATIONS,
        equity_samples=REFERENCE_EQUITY_SAMPLES,
        equity_seed=REFERENCE_EQUITY_SEED)


def reference_seconds(timer: Callable[[], float] = time.perf_counter) -> float:
    """Seconds this machine currently takes for one reference unit."""
    start = timer()
    reference_result()
    return timer() - start


@dataclass(frozen=True)
class Measurement:
    """One timed thing, in seconds AND in reference units."""

    seconds: float
    units: float
    reference_seconds: float

    def as_dict(self) -> dict:
        return {"seconds": round(self.seconds, 4),
                "units": round(self.units, 4),
                "reference_seconds": round(self.reference_seconds, 4)}


@dataclass
class DriftClock:
    """Times work against a reference workload re-measured as it goes.

    `recalibrate_after_seconds` is the age at which the held calibration
    is considered stale. **It defaults to 0.0 - a fresh reference before
    every measurement - because that is the setting the instrument was
    validated at** (M240), and a calibration held across a window is
    uncorrected for whatever happens inside it. During that validation
    the reference itself ranged from 0.27s to 2.63s, a 9.7x spread, so a
    rate held even briefly can be badly wrong.

    The cost is one reference unit per measurement: ~0.28s against work
    that is normally seconds, so under 10% for a typical study. Raise it
    to trade correction for speed, knowing that is the axis being traded.

    `timer` and `workload` are injectable so the invariance property -
    that a uniform slowdown leaves units unchanged - can be tested
    without waiting for the machine to actually slow down.
    """

    recalibrate_after_seconds: float = 0.0
    timer: Callable[[], float] = time.perf_counter
    workload: Optional[Callable[[], float]] = None
    calibrations: List[float] = field(default_factory=list)
    _held: Optional[float] = None
    _held_at: Optional[float] = None

    def _run_reference(self) -> float:
        if self.workload is not None:
            return self.workload()
        return reference_seconds(self.timer)

    def calibrate(self) -> float:
        """Measure one reference unit and hold it as the current rate."""
        secs = self._run_reference()
        if secs <= 0:
            raise RuntimeError(
                "the reference workload measured %r seconds, which cannot "
                "be a rate - a clock with no resolution would divide every "
                "measurement by zero and silently report nonsense" % secs)
        self.calibrations.append(secs)
        self._held = secs
        self._held_at = self.timer()
        return secs

    def _current_rate(self) -> float:
        if self._held is None:
            return self.calibrate()
        age = self.timer() - self._held_at
        if age >= self.recalibrate_after_seconds:
            return self.calibrate()
        return self._held

    def measure(self, fn: Callable, *args, **kwargs):
        """Run `fn`, returning (its return value, a Measurement)."""
        rate = self._current_rate()
        start = self.timer()
        value = fn(*args, **kwargs)
        secs = self.timer() - start
        return value, Measurement(seconds=secs, units=secs / rate,
                                  reference_seconds=rate)

    def report(self) -> dict:
        """What the machine did during the run.

        `drift` is the slowest calibration over the fastest. It is the
        number a study should print beside its results: at 1.0 the
        machine held still and seconds mean what they say, and at 1.7 -
        the figure M70 recorded between two milestones - they do not.
        """
        if not self.calibrations:
            return {"calibrations": 0}
        fastest = min(self.calibrations)
        slowest = max(self.calibrations)
        return {"calibrations": len(self.calibrations),
                "first_seconds": round(self.calibrations[0], 4),
                "last_seconds": round(self.calibrations[-1], 4),
                "fastest_seconds": round(fastest, 4),
                "slowest_seconds": round(slowest, 4),
                "drift": round(slowest / fastest, 3)}
