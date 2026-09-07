# -*- coding: utf-8 -*-
"""Guards the drift instrument in `bench/reference_units.py`.

The property that matters is that a UNIFORM slowdown leaves reference
units unchanged - that is the whole reason to divide by a reference at
all. It is tested against a simulated machine rather than by waiting for
this one to actually slow down, which is neither fast nor reproducible.
"""
import pytest

from bench.reference_units import DriftClock, Measurement, reference_result, reference_seconds


class FakeMachine:
    """A machine whose speed can be changed on demand.

    `speed` multiplies how long everything takes. Virtual time advances
    only when work is done, so nothing here sleeps.
    """

    def __init__(self, speed: float = 1.0, unit_cost: float = 0.25):
        self.speed = speed
        self.unit_cost = unit_cost
        self.now = 1000.0
        self.reference_runs = 0

    def timer(self) -> float:
        return self.now

    def workload(self) -> float:
        self.reference_runs += 1
        cost = self.unit_cost * self.speed
        self.now += cost
        return cost

    def work(self, true_cost: float):
        """Do a fixed amount of real work, which the speed scales."""
        self.now += true_cost * self.speed
        return "done"

    def idle(self, seconds: float):
        self.now += seconds


def _clock(machine, **kwargs):
    return DriftClock(timer=machine.timer, workload=machine.workload, **kwargs)


def test_the_reference_workload_is_the_same_work_every_time():
    # A reference whose cost could change for its own reasons would
    # measure the machine and itself at once.
    first = reference_result()
    second = reference_result()
    rows_a = first.strategy_at(first.root)
    rows_b = second.strategy_at(second.root)
    assert rows_a.keys() == rows_b.keys()
    assert rows_a
    for hand, row in rows_a.items():
        assert row == pytest.approx(rows_b[hand], abs=0.0)


def test_a_reference_unit_costs_measurable_time():
    assert reference_seconds() > 0.0


def test_units_are_seconds_over_the_held_calibration():
    machine = FakeMachine(unit_cost=0.25)
    clock = _clock(machine)
    _value, measurement = clock.measure(machine.work, 1.0)
    assert measurement.seconds == pytest.approx(1.0)
    assert measurement.reference_seconds == pytest.approx(0.25)
    assert measurement.units == pytest.approx(4.0)


def test_a_uniform_slowdown_leaves_units_unchanged():
    # The property the instrument exists for. The same work on a machine
    # running at 1.7x - M70's measured drift - reads 1.7x the seconds and
    # the SAME number of units.
    quick = FakeMachine(speed=1.0, unit_cost=0.25)
    _v, fast = _clock(quick).measure(quick.work, 1.0)
    slow_machine = FakeMachine(speed=1.7, unit_cost=0.25)
    _v, slow = _clock(slow_machine).measure(slow_machine.work, 1.0)

    assert slow.seconds == pytest.approx(fast.seconds * 1.7)
    assert slow.units == pytest.approx(fast.units)


def test_units_still_move_when_the_work_itself_changes():
    # The other half: normalising must not flatten a REAL difference,
    # which would make the instrument useless in the opposite direction.
    machine = FakeMachine(unit_cost=0.25)
    clock = _clock(machine)
    _v, cheap = clock.measure(machine.work, 1.0)
    _v, dear = clock.measure(machine.work, 2.0)
    assert dear.units == pytest.approx(cheap.units * 2.0)


def test_the_clock_recalibrates_when_its_rate_goes_stale():
    # M234's drift arrived inside one run, so a calibration taken once at
    # startup would have normalised the whole run against a state that
    # had already gone.
    machine = FakeMachine(speed=1.0, unit_cost=0.25)
    clock = _clock(machine, recalibrate_after_seconds=10.0)
    _v, before = clock.measure(machine.work, 1.0)

    machine.idle(30.0)
    machine.speed = 2.0
    _v, after = clock.measure(machine.work, 1.0)

    assert machine.reference_runs == 2
    assert after.seconds == pytest.approx(before.seconds * 2.0)
    assert after.units == pytest.approx(before.units)


def test_the_clock_reuses_a_calibration_that_is_still_fresh():
    machine = FakeMachine(unit_cost=0.25)
    clock = _clock(machine, recalibrate_after_seconds=1000.0)
    for _ in range(4):
        clock.measure(machine.work, 0.1)
    assert machine.reference_runs == 1


def test_a_reference_that_takes_no_time_is_refused():
    # Dividing by it would report nonsense rather than fail.
    machine = FakeMachine(unit_cost=0.0)
    with pytest.raises(RuntimeError, match="cannot"):
        _clock(machine).calibrate()


def test_the_report_names_the_drift_it_saw():
    machine = FakeMachine(speed=1.0, unit_cost=0.25)
    clock = _clock(machine, recalibrate_after_seconds=10.0)
    clock.measure(machine.work, 1.0)
    machine.idle(30.0)
    machine.speed = 1.7
    clock.measure(machine.work, 1.0)

    report = clock.report()
    assert report["calibrations"] == 2
    assert report["drift"] == pytest.approx(1.7, abs=1e-3)
    assert report["slowest_seconds"] > report["fastest_seconds"]


def test_an_unused_clock_claims_no_drift_rather_than_one():
    assert DriftClock().report() == {"calibrations": 0}


def test_a_measurement_reports_both_units_and_seconds():
    # A study that printed units alone would be unreadable; one that
    # printed seconds alone is what this module exists to stop.
    fields = Measurement(seconds=2.0, units=8.0, reference_seconds=0.25).as_dict()
    assert set(fields) == {"seconds", "units", "reference_seconds"}


def test_the_default_is_the_setting_the_instrument_was_validated_at():
    # M240 measured the correction with a fresh reference before EVERY
    # measurement. Shipping a different default would mean quoting a
    # validation for a configuration nobody ran.
    assert DriftClock().recalibrate_after_seconds == 0.0


def test_every_measurement_recalibrates_by_default():
    machine = FakeMachine(unit_cost=0.25)
    clock = DriftClock(timer=machine.timer, workload=machine.workload)
    for _ in range(3):
        clock.measure(machine.work, 0.5)
    assert machine.reference_runs == 3
