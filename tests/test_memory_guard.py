"""The memory watchdog: stop rule and watch loop, without exhausting RAM."""
import subprocess
import sys

from bench import memory_guard as mg

GB = mg.GB


def test_the_floor_stops_on_the_machine_not_the_job():
    reason = mg.should_stop(available=3 * GB, tree_bytes=1 * GB, floor=4 * GB, cap=22 * GB)
    assert reason and "floor" in reason


def test_the_cap_stops_on_the_job():
    reason = mg.should_stop(available=10 * GB, tree_bytes=23 * GB, floor=4 * GB, cap=22 * GB)
    assert reason and "cap" in reason


def test_a_healthy_job_is_left_alone():
    assert mg.should_stop(10 * GB, 5 * GB, 4 * GB, 22 * GB) is None
    assert mg.should_stop(10 * GB, 50 * GB, 4 * GB, None) is None


def _sleeper(seconds):
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])


def test_watch_kills_a_job_when_memory_runs_low():
    popen = _sleeper(60)
    readings = iter([20 * GB, 20 * GB, 2 * GB] + [2 * GB] * 50)
    out = mg.watch(popen, floor=4 * GB, cap=None, poll=0.01,
                   available=lambda: next(readings), measure=lambda: 1 * GB,
                   sleep=lambda s: None)
    assert out["outcome"] == "stopped" and "floor" in out["reason"]
    assert popen.wait(timeout=10) is not None, "the job must actually be dead"
    assert out["lowest_available_gb"] == 2.0 and out["peak_gb"] == 1.0


def test_watch_reports_a_normal_exit_and_its_peak():
    popen = subprocess.Popen([sys.executable, "-c", "pass"])
    popen.wait()
    out = mg.watch(popen, floor=4 * GB, cap=22 * GB, poll=0.01,
                   available=lambda: 20 * GB, measure=lambda: 3 * GB)
    assert out["outcome"] == "exited" and out["exit_code"] == 0 and out["peak_gb"] == 3.0


def test_tree_bytes_counts_a_real_process():
    import psutil
    assert mg.tree_bytes(psutil.Process()) > 0
