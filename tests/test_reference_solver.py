"""The three ways the reference solver fails, and the guard for each.

M257. The wrapper this replaces checked only that an exploitability line
existed, and three real runs printed one carrying **318%, 296% and
118.9%** while writing no dump. All three were recorded as successful
spots in a study's index.

The trigger was load rather than the spot: `Kd7c2h_three_bet_c12`
returned 118.9% with no dump while other work shared the machine, and
**0.392% with a 23 MB dump when run alone** — same parameters, same
board, same ranges.

The solver is never invoked here. Each failure is reproduced by faking
the subprocess, which is the only way to test what happens when it goes
wrong without waiting for it to go wrong.
"""
import pathlib
import subprocess
from unittest import mock

import pytest

from bench.reference_solver import (
    MAX_PLAUSIBLE_EXPLOITABILITY_PCT,
    SolveFailed,
    solve,
)


class _Result:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


def _params(tmp_path):
    path = tmp_path / "spot.txt"
    path.write_text("set_pot 10\n", encoding="utf-8")
    return path


def _run(tmp_path, stdout, dump=None, writes=None, **kwargs):
    """Fake one solver run.

    `writes` is what the solver puts on disk WHILE it runs. Stale output
    is deleted before the run, so a fixture that pre-writes the dump is
    exercising deletion and calling it success — which is how the first
    version of this file passed while testing nothing.
    """
    def fake(*_args, **_kwargs):
        if writes is not None and dump is not None:
            pathlib.Path(dump).write_bytes(writes)
        return _Result(stdout)

    with mock.patch.object(subprocess, "run", side_effect=fake):
        return solve("console.exe", _params(tmp_path),
                     resource_dir=tmp_path, dump_path=dump,
                     kill_first=False, **kwargs)


def test_a_converged_run_is_returned(tmp_path):
    dump = tmp_path / "dump.json"
    expl, seconds, _tail = _run(
        tmp_path, "Total exploitability 0.484000 precent",
        dump=dump, writes=b"{}" * 100)
    assert expl == pytest.approx(0.484)
    assert seconds >= 0


def test_no_exploitability_line_means_it_did_not_run(tmp_path):
    """The binary loads its dictionary and exits 0 having done nothing —
    what a relative params path produces, and indistinguishable from a
    solve that produced no output."""
    with pytest.raises(SolveFailed, match="did not solve"):
        _run(tmp_path, "EXEC FROM FILE\nloading...\n")


def test_an_impossible_exploitability_is_refused(tmp_path):
    """318%, 296% and 118.9% were each printed by a real run, and each
    was recorded as data. A converged run reaches 0.32-0.50%."""
    for nonsense in (318.047, 296.515, 118.93661):
        with pytest.raises(SolveFailed, match="not a solve"):
            _run(tmp_path, "Total exploitability %f precent" % nonsense)
    assert MAX_PLAUSIBLE_EXPLOITABILITY_PCT < 118.93661


def test_a_missing_dump_is_refused_even_when_the_figure_looks_fine(tmp_path):
    """The combination that actually happened: a plausible line and no
    file. A study then opens something that is not there."""
    with pytest.raises(SolveFailed, match="is not data"):
        _run(tmp_path, "Total exploitability 0.400000 precent",
             dump=tmp_path / "absent.json")


def test_an_empty_dump_is_refused_too(tmp_path):
    """Zero bytes is the exact shape observed — the file is created and
    nothing is written to it."""
    with pytest.raises(SolveFailed, match="is not data"):
        _run(tmp_path, "Total exploitability 0.400000 precent",
             dump=tmp_path / "empty.json", writes=b"", min_dump_bytes=1)


def test_stale_output_is_deleted_before_the_run(tmp_path):
    """A previous run's dump left in place turns a failed solve into a
    successful-looking one, carrying a different spot's data."""
    dump = tmp_path / "stale.json"
    dump.write_bytes(b"x" * 5000)
    with pytest.raises(SolveFailed, match="is not data"):
        _run(tmp_path, "Total exploitability 0.400000 precent", dump=dump)
    assert not dump.exists(), "the stale dump survived and could be read"


def test_a_relative_params_path_is_refused_up_front(tmp_path):
    """The solver runs with cwd set to its own directory, so a relative
    path resolves somewhere else entirely and the run does nothing."""
    with pytest.raises(ValueError, match="ABSOLUTE"):
        solve("console.exe", pathlib.Path("spot.txt"), resource_dir=tmp_path)


def test_the_last_exploitability_line_is_the_one_that_counts(tmp_path):
    """The binary prints progress; the final figure describes the
    strategy actually dumped."""
    expl, _seconds, _tail = _run(
        tmp_path,
        "Total exploitability 12.000000 precent\n"
        "Total exploitability 3.000000 precent\n"
        "Total exploitability 0.470000 precent\n")
    assert expl == pytest.approx(0.47)
