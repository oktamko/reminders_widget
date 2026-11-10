"""Background sync loop: every `period`, `debounce` after a local change, at once on
request, and right after the PC wakes; failures back off with `retry_delay`."""
import logging
import threading
import time
from typing import Callable

from reminders_widget.sync import SyncResult, retry_delay

log = logging.getLogger("reminders_widget.scheduler")
_TICK = 0.25   # how often the wait wakes to notice a wall-clock jump


class Scheduler:
    def __init__(self, run: Callable[[], SyncResult], on_result: Callable[[SyncResult], None],
                 *, period: float = 15.0, debounce: float = 1.0,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):
        self._run, self._on_result = run, on_result
        self.period, self.debounce = period, debounce
        self._clock, self._wall = clock, wall
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._failures = 0
        self._next = 0.0           # monotonic deadline of the next scheduled run
        self._poke_at: float | None = None
        self._now = False

    def next_wait(self, result: SyncResult) -> float:
        """Seconds until the next run after `result`; tracks consecutive failures."""
        if result.status == "ok":
            self._failures = 0
            return self.period
        self._failures += 1
        return retry_delay(result, self._failures - 1)

    def start(self) -> None:
        with self._cond:
            if self._thread and self._thread.is_alive():
                return
            self._stopping = False
            self._next = self._clock()          # first run right away
            self._thread = threading.Thread(target=self._loop, name="rw-sync", daemon=True)
            self._thread.start()

    def poke(self) -> None:
        with self._cond:
            self._poke_at = self._clock() + self.debounce
            self._cond.notify_all()

    def now(self) -> None:
        with self._cond:
            self._now = True
            self._cond.notify_all()

    def stop(self, timeout: float = 2.0) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
            thread = self._thread
        if thread:
            thread.join(timeout)

    def _due(self) -> float:
        due = self._next if self._poke_at is None else min(self._next, self._poke_at)
        return self._clock() if self._now else due

    def _wait_for_turn(self) -> bool:
        """Block until a run is due; False when stopping."""
        with self._cond:
            mono0, wall0 = self._clock(), self._wall()
            while not self._stopping:
                remaining = self._due() - self._clock()
                if remaining <= 0:
                    break
                if (self._wall() - wall0) - (self._clock() - mono0) > 2 * self.period:
                    log.info("wake after sleep")
                    break
                self._cond.wait(min(remaining, _TICK))
            self._now, self._poke_at = False, None
            return not self._stopping

    def _loop(self) -> None:
        while self._wait_for_turn():
            try:
                result = self._run()
            except Exception as exc:  # never let one run kill the loop
                log.warning("sync run raised %s", type(exc).__name__)
                result = SyncResult("error")
            try:
                self._on_result(result)
            except Exception as exc:
                log.warning("on_result raised %s", type(exc).__name__)
            with self._cond:
                self._next = self._clock() + self.next_wait(result)
            if result.status == "stopped":
                log.info("sync stopped")
                return
