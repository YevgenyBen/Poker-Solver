"""Driving the independent solver, with the failures it actually has.

The wrapper this replaces lived in a scratchpad script and had one
guard: a run that printed no exploitability line did not solve. That is
necessary and it is not sufficient, and the gap put three rows of
fabricated data into a study's index before anything noticed.

**What the solver does when it fails.** It prints an exploitability line
carrying a nonsense figure and writes no dump. Measured, on real runs:

    exploitability   318.0%   296.5%   118.9%      dump 0 bytes

A caller checking only that a figure exists records those as successes.
A study then opens a file that is not there - or, with slightly worse
luck, one that is and is garbage. This project's recurring failure in a
new place: **a run that did not happen, recorded as a row of data.**

**And the trigger is load, not the spot.** `Kd7c2h_three_bet_c12`
returned 118.9% with no dump while other work shared the machine, and
**0.392% with a 23 MB dump when run alone** - same parameters, same
board, same ranges. So the reference's own convergence is not
reproducible under contention, which is M240's drift rule applying to
the instrument rather than to a stopwatch.

Two consequences for anyone using this: solve on a quiet machine, and do
not believe a figure this module did not return.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import time

#: A converged run reaches 0.32-0.50% of pot (M221, M242, M256). Anything
#: above a few percent did not solve, whatever it printed.
MAX_PLAUSIBLE_EXPLOITABILITY_PCT = 5.0

_EXPLOITABILITY = re.compile(r"Total exploitability ([\d.eE+-]+) precent")


class SolveFailed(RuntimeError):
    """The run did not produce usable output. Never a silent None."""


def solve(console, params_path, *, resource_dir, dump_path=None,
          timeout=900, min_dump_bytes=1, kill_first=True):
    """Run the solver and return `(exploitability_pct, seconds, tail)`.

    Raises `SolveFailed` rather than returning anything a caller could
    mistake for data. Three distinct failures are checked, because each
    has been seen:

      * no exploitability line at all - the binary loaded and did
        nothing, which is what a relative params path produces;
      * an exploitability figure that cannot be real;
      * a requested dump that was not written.
    """
    params_path = pathlib.Path(params_path)
    if not params_path.is_absolute():
        raise ValueError(
            "params_path must be ABSOLUTE (%s): the solver runs with cwd set "
            "to its own directory, so a relative path silently resolves to a "
            "file that does not exist and the run does nothing" % params_path)
    if not params_path.exists():
        raise FileNotFoundError("params file does not exist: %s" % params_path)

    if kill_first:
        # A crashed run leaves the process holding its whole tree; two of
        # them held 7.74 GB and 9.1 GB, and every subsequent spot then
        # died during tree build with an access violation - a failure
        # that reads as "the tree is too big" and means "the machine has
        # no memory".
        try:
            subprocess.run(["taskkill", "/F", "/IM", "console_solver.exe"],
                           capture_output=True, timeout=30)
        except Exception:                                          # noqa: BLE001
            pass

    if dump_path is not None:
        dump_path = pathlib.Path(dump_path)
        if dump_path.exists():
            dump_path.unlink()

    started = time.perf_counter()
    proc = subprocess.run(
        [str(console), "--input_file", str(params_path),
         "--resource_dir", str(resource_dir)],
        cwd=str(pathlib.Path(console).parent), capture_output=True, text=True,
        timeout=timeout)
    seconds = time.perf_counter() - started
    output = (proc.stdout or "") + (proc.stderr or "")
    tail = output[-600:]

    found = _EXPLOITABILITY.findall(output)
    if not found:
        raise SolveFailed(
            "no exploitability line for %s (%.1fs, exit %s) - it did not "
            "solve. Tail: %s" % (params_path.name, seconds, proc.returncode,
                                 tail[-300:]))
    exploitability = float(found[-1])

    if exploitability > MAX_PLAUSIBLE_EXPLOITABILITY_PCT:
        raise SolveFailed(
            "%s reported %.1f%% exploitability (%.1fs), which is not a solve - "
            "a converged run reaches 0.32-0.50%%. Seen at 318%%, 296%% and "
            "118.9%%, every time while the machine was shared."
            % (params_path.name, exploitability, seconds))

    if dump_path is not None:
        size = dump_path.stat().st_size if dump_path.exists() else 0
        if size < min_dump_bytes:
            raise SolveFailed(
                "%s reported %.3f%% exploitability but wrote %d bytes to %s - "
                "a dump that is not there is not data"
                % (params_path.name, exploitability, size, dump_path.name))

    return exploitability, seconds, tail
