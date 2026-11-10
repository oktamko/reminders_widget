import threading
import time

from reminders_widget.app.scheduler import Scheduler
from reminders_widget.sync import SyncResult


class Runs:
    def __init__(self, results=None, sleep=0.0, fail=False):
        self.count, self.results, self.sleep, self.fail = 0, list(results or []), sleep, fail
        self.event = threading.Event()

    def __call__(self):
        self.count += 1
        self.event.set()
        if self.sleep:
            time.sleep(self.sleep)
        if self.fail:
            raise RuntimeError("boom")
        return self.results.pop(0) if self.results else SyncResult("ok")


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_runs_once_at_start():
    runs, got = Runs(), []
    s = Scheduler(runs, got.append, period=60, debounce=0.05)
    s.start()
    try:
        assert wait_for(lambda: runs.count == 1)
        assert got and got[0].status == "ok"
    finally:
        s.stop()


def test_five_quick_pokes_give_one_run():   # Review Focus 4
    runs = Runs()
    s = Scheduler(runs, lambda r: None, period=60, debounce=0.15)
    s.start()
    try:
        assert wait_for(lambda: runs.count == 1)
        for _ in range(5):
            s.poke()
            time.sleep(0.02)
        time.sleep(0.4)
        assert runs.count == 2
    finally:
        s.stop()


def test_now_runs_immediately():
    runs = Runs()
    s = Scheduler(runs, lambda r: None, period=60, debounce=5)
    s.start()
    try:
        assert wait_for(lambda: runs.count == 1)
        s.now()
        assert wait_for(lambda: runs.count == 2, timeout=0.5)
    finally:
        s.stop()


def test_backoff_sequence_and_retry_after():
    s = Scheduler(Runs(), lambda r: None)
    assert s.next_wait(SyncResult("offline")) == 15
    assert s.next_wait(SyncResult("offline")) == 30
    assert s.next_wait(SyncResult("error")) == 60
    assert s.next_wait(SyncResult("ok")) == 15          # back to the period
    assert s.next_wait(SyncResult("transient", retry_after=120)) == 120


def test_wall_clock_jump_triggers_run():
    runs = Runs()
    wall = {"t": 1000.0}
    s = Scheduler(runs, lambda r: None, period=30, debounce=1, wall=lambda: wall["t"])
    s.start()
    try:
        assert wait_for(lambda: runs.count == 1)
        wall["t"] += 3600          # the PC slept for an hour
        assert wait_for(lambda: runs.count == 2, timeout=1.5)
    finally:
        s.stop()


def test_stop_during_slow_run_returns_quickly():   # Review Focus 5
    runs = Runs(sleep=0.6)
    s = Scheduler(runs, lambda r: None, period=60, debounce=0.05)
    s.start()
    assert runs.event.wait(1)
    started = time.monotonic()
    s.stop(timeout=2.0)
    assert time.monotonic() - started < 2.0


def test_exception_in_run_keeps_loop_alive():
    runs = Runs(fail=True)
    s = Scheduler(runs, lambda r: None, period=60, debounce=0.05)
    s.start()
    try:
        assert wait_for(lambda: runs.count == 1)
        s.now()
        assert wait_for(lambda: runs.count == 2)
    finally:
        s.stop()


def test_exception_in_on_result_keeps_loop_alive():
    runs = Runs()

    def bad(_):
        raise ValueError("ui gone")

    s = Scheduler(runs, bad, period=60, debounce=0.05)
    s.start()
    try:
        assert wait_for(lambda: runs.count == 1)
        s.now()
        assert wait_for(lambda: runs.count == 2)
    finally:
        s.stop()


def test_stopped_status_ends_loop_until_start():
    runs = Runs(results=[SyncResult("stopped")])
    s = Scheduler(runs, lambda r: None, period=0.05, debounce=0.01)
    s.start()
    try:
        assert wait_for(lambda: runs.count == 1)
        time.sleep(0.3)
        assert runs.count == 1
        s.start()
        assert wait_for(lambda: runs.count == 2)
    finally:
        s.stop()


def test_on_start_runs_before_each_run():
    order = []
    s = Scheduler(lambda: order.append("run") or SyncResult("ok"), lambda r: order.append("result"),
                  period=60, debounce=0.05, on_start=lambda: order.append("start"))
    s.start()
    try:
        assert wait_for(lambda: len(order) >= 3)
        assert order[:3] == ["start", "run", "result"]
    finally:
        s.stop()
