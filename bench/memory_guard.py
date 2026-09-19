"""Run a long job under a memory watchdog, and stop it BEFORE the machine
crashes.

This machine has 31.7 GB. A three-round reference solve (the audit's R1)
held 23.8 GB while serialising and died writing a 0-byte file (M285), and
an earlier session was lost outright. A study that dies that way loses
its partial output and can take the session with it; one stopped
cleanly keeps what it wrote and says why.

    python -m bench.memory_guard --floor-gb 4 --cap-gb 22 --log run.guard.json -- \\
        python -m bench.studies.something out.json

Stops the job's whole process tree when EITHER
- the system's available memory falls below `--floor-gb` (other work on
  the machine counts, which is the point: it is the machine that
  crashes, not the job), or
- the job's own tree (it plus every child) holds more than `--cap-gb`.

The summary it writes - peak tree memory, lowest available memory, and
how the job ended - is what a report quotes about a run's footprint.
Instrument code: nothing under poker_solver/ or api/ may import it.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

GB = 1024 ** 3
DEFAULT_FLOOR_GB = 4.0
DEFAULT_CAP_GB = 22.0


def should_stop(available: int, tree_bytes: int, floor: int, cap: int | None):
    """The reason to stop now, or None. Pure."""
    if available < floor:
        return (f"system available memory {available / GB:.2f} GB is below the "
                f"{floor / GB:.2f} GB floor")
    if cap is not None and tree_bytes > cap:
        return f"job holds {tree_bytes / GB:.2f} GB, over the {cap / GB:.2f} GB cap"
    return None


def tree_bytes(proc) -> int:
    """Resident memory of a psutil process and every descendant."""
    import psutil
    total = 0
    try:
        members = [proc] + proc.children(recursive=True)
    except psutil.Error:
        return 0
    for p in members:
        try:
            total += p.memory_info().rss
        except psutil.Error:
            pass
    return total


def kill_tree(proc) -> None:
    import psutil
    try:
        members = proc.children(recursive=True) + [proc]
    except psutil.Error:
        return
    for p in members:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(members, timeout=10)


def watch(popen, floor: int, cap: int | None, poll: float = 1.0,
          available=None, measure=None, sleep=time.sleep) -> dict:
    """Poll `popen` until it exits or must be stopped. `available` and
    `measure` are injectable so the stop rule is testable without
    exhausting a machine's memory."""
    import psutil
    try:
        handle = psutil.Process(popen.pid)
    except psutil.Error:
        handle = None                    # already gone: nothing to measure or kill
    available = available or (lambda: psutil.virtual_memory().available)
    measure = measure or (lambda: tree_bytes(handle) if handle else 0)
    peak, lowest, started = 0, None, time.time()
    while True:
        code = popen.poll()
        avail, held = available(), measure()
        peak = max(peak, held)
        lowest = avail if lowest is None else min(lowest, avail)
        if code is not None:
            return {"outcome": "exited", "exit_code": code, "peak_gb": round(peak / GB, 2),
                    "lowest_available_gb": round(lowest / GB, 2),
                    "seconds": round(time.time() - started, 1)}
        reason = should_stop(avail, held, floor, cap)
        if reason:
            if handle is not None:
                kill_tree(handle)
            else:
                popen.kill()
            return {"outcome": "stopped", "reason": reason, "peak_gb": round(peak / GB, 2),
                    "lowest_available_gb": round(lowest / GB, 2),
                    "seconds": round(time.time() - started, 1)}
        sleep(poll)


def main(argv=None) -> int:                              # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--floor-gb", type=float, default=DEFAULT_FLOOR_GB)
    parser.add_argument("--cap-gb", type=float, default=DEFAULT_CAP_GB)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--log", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    popen = subprocess.Popen(command)
    summary = watch(popen, int(args.floor_gb * GB), int(args.cap_gb * GB), args.poll)
    summary["command"] = command
    with open(args.log, "w") as fh:
        json.dump(summary, fh, indent=1)
    print("memory_guard:", json.dumps(summary), file=sys.stderr)
    if summary["outcome"] == "stopped":
        return 3
    return summary["exit_code"]


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
