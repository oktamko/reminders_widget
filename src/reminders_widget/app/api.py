"""The object the window's JavaScript calls (window.pywebview.api).

Every method returns plain JSON-able values and never raises to JS. It touches only
the Store and pokes the scheduler; the scheduler alone talks to iCloud.
"""
import logging
import threading
from datetime import date, datetime
from typing import Callable

from reminders_widget.icloud.records import Reminder, new_reminder_name
from reminders_widget.store import NOTICE_RECURRING, Store
from reminders_widget.sync import SyncResult
from reminders_widget.view import ensure_selection, title_for, visible

log = logging.getLogger("reminders_widget.api")

MSG_READONLY = "This reminder can't be changed on PC yet."
MSG_FAILED = "That change couldn't be saved."
_SORTS = ("priority", "icloud")


# States that need the user, then plain information. "transient" and "error" clear up on
# their own retries and the status dot already shows them, so they get no bar.
_NEEDS_USER = {
    "session_expired": {"text": "Sign in again to keep syncing.", "action": "sign_in", "tone": "warning"},
    "stopped": {"text": "iCloud changed something. Check for an update.", "action": None, "tone": "error"},
}
_OFFLINE = {"text": "Offline. Your changes will sync when you're back online.", "action": None, "tone": "info"}


def banner_for(status: str, notices: list[str]) -> dict | None:
    """What the bar at the top of the window says, or None for no bar.

    Returns {"text": str, "action": "sign_in" | None, "tone": "info" | "warning" | "error"}.
    Order: a state the user must act on, then sync notices, then offline.
    """
    if status in _NEEDS_USER:
        return dict(_NEEDS_USER[status])
    if notices:
        return {"text": " ".join(notices), "action": None, "tone": "info"}
    if status == "offline":
        return dict(_OFFLINE)
    return None


def parse_due(value: str | None, all_day: bool) -> date | datetime | None:
    """A value from <input type=date|datetime-local>; naive times are the PC's local time."""
    if not value:
        return None
    if all_day:
        return date.fromisoformat(value[:10])
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.astimezone()


def _due_view(due: date | datetime | None) -> tuple[str | None, str, bool, bool]:
    """(iso value for the inputs, label, overdue, all_day)."""
    if due is None:
        return None, "", False, False
    today = date.today()
    if isinstance(due, datetime):
        local = due.astimezone()
        day, time_part, iso = local.date(), local.strftime(" %H:%M"), local.strftime("%Y-%m-%dT%H:%M")
        overdue = local < datetime.now().astimezone()
        all_day = False
    else:
        day, time_part, iso = due, "", due.isoformat()
        overdue = due < today
        all_day = True
    delta = (day - today).days
    name = {0: "Today", 1: "Tomorrow", -1: "Yesterday"}.get(delta)
    if name is None:
        name = day.strftime("%a %d %b") if abs(delta) < 300 else day.strftime("%d %b %Y")
    return iso, name + time_part, overdue, all_day


class Api:
    def __init__(self, store: Store, scheduler, push: Callable[[dict], None] = lambda s: None,
                 signed_in: Callable[[], bool] = lambda: True):
        self._store, self._scheduler, self._push = store, scheduler, push
        self._signed_in = signed_in
        self._status = "ok"
        self._notices: list[str] = []
        self._lock = threading.Lock()
        self._version = 0
        self._syncing = False
        self._sign_in_hook: Callable[[], None] | None = None
        self._mode_hook: Callable[[str], None] | None = None

    # --- reading -------------------------------------------------------------
    def _selection(self, lists) -> set[str]:
        saved = self._store.get_setting("selected", None)
        return ensure_selection(lists, set(saved) if isinstance(saved, list) else set())

    def _sort(self) -> str:
        sort = self._store.get_setting("sort", "priority")
        return sort if sort in _SORTS else "priority"

    def _reminder_view(self, r: Reminder) -> dict:
        iso, label, overdue, all_day = _due_view(r.due)
        return {"id": r.record_name, "list": r.list_id, "title": r.title or "", "notes": r.notes or "",
                "priority": r.priority, "due": iso, "due_label": label, "overdue": overdue,
                "all_day": all_day, "readonly": r.readonly, "recurring": r.recurring, "alarm": r.alarmed}

    def state(self) -> dict:
        try:
            return self._state()
        except Exception as exc:  # never raise into JS
            log.warning("state failed: %s", type(exc).__name__)
            return {"ok": False, "message": MSG_FAILED}

    def _state(self) -> dict:
        lists = self._store.lists()
        selected = self._selection(lists)
        sort = self._sort()
        with self._lock:
            status, notices, syncing = self._status, list(self._notices), self._syncing
            self._version += 1
            version = self._version
        return {
            "lists": [{"id": l.record_name, "name": l.name, "color": l.color} for l in lists],
            "selected": sorted(selected),
            "sort": sort,
            "title": title_for(lists, selected),
            "reminders": [self._reminder_view(r)
                          for r in visible(self._store.reminders(), selected, sort, lists)],
            "status": status,
            "banner": banner_for(status, notices),
            "signed_in": bool(self._signed_in()),
            "mode": self._store.get_setting("mode", "desktop"),
            "syncing": syncing,
            "version": version,
        }

    # --- settings ------------------------------------------------------------
    def select(self, list_ids: list[str]) -> dict:
        try:
            lists = self._store.lists()
            chosen = ensure_selection(lists, {i for i in list_ids or [] if isinstance(i, str)})
            self._store.set_setting("selected", sorted(chosen))
            return {"ok": True, "state": self._state()}
        except Exception as exc:  # never raise into JS
            log.warning("select failed: %s", type(exc).__name__)
            return {"ok": False, "message": MSG_FAILED}

    def set_sort(self, sort: str) -> dict:
        if sort not in _SORTS:
            return {"ok": False, "message": MSG_FAILED}
        try:
            self._store.set_setting("sort", sort)
            return {"ok": True, "state": self._state()}
        except Exception as exc:  # never raise into JS
            log.warning("set_sort failed: %s", type(exc).__name__)
            return {"ok": False, "message": MSG_FAILED}

    # --- changes -------------------------------------------------------------
    def _change(self, record_name: str, op: str, changes: dict) -> dict:
        try:
            self._store.apply_local(record_name, op, changes)
        except ValueError as exc:
            text = str(exc)
            if text == NOTICE_RECURRING:
                return {"ok": False, "message": NOTICE_RECURRING}
            if text == "reminder is read-only":
                return {"ok": False, "message": MSG_READONLY}
            log.info("change refused: %s", type(exc).__name__)
            return {"ok": False, "message": MSG_FAILED}
        except Exception as exc:  # never raise into JS
            log.warning("change failed: %s", type(exc).__name__)
            return {"ok": False, "message": MSG_FAILED}
        self._scheduler.poke()
        return {"ok": True, "state": self.state()}

    def add(self, title: str) -> dict:
        title = (title or "").strip() if isinstance(title, str) else ""
        lists = self._store.lists()
        selected = sorted(self._selection(lists))
        if not title or not selected:
            return {"ok": False, "message": MSG_FAILED}
        first = next(l.record_name for l in lists if l.record_name in selected)
        return self._change(new_reminder_name(), "create", {"title": title, "list_id": first})

    def complete(self, record_name: str, done: bool) -> dict:
        return self._change(record_name, "update", {"completed": bool(done)})

    def set_priority(self, record_name: str, priority: int) -> dict:
        return self._change(record_name, "update", {"priority": priority})

    def rename(self, record_name: str, title: str) -> dict:
        if not isinstance(title, str) or not title.strip():
            return {"ok": False, "message": MSG_FAILED}
        return self._change(record_name, "update", {"title": title.strip()})

    def set_notes(self, record_name: str, notes: str) -> dict:
        return self._change(record_name, "update", {"notes": notes if isinstance(notes, str) else ""})

    def set_due(self, record_name: str, value: str | None, all_day: bool) -> dict:
        try:
            due = parse_due(value, bool(all_day))
        except (TypeError, ValueError):
            return {"ok": False, "message": MSG_FAILED}
        return self._change(record_name, "update", {"due": due})

    def set_mode(self, mode: str) -> dict:
        if mode not in ("desktop", "window") or self._mode_hook is None:
            return {"ok": False, "message": MSG_FAILED}
        threading.Thread(target=self._mode_hook, args=(mode,), daemon=True).start()
        return {"ok": True}

    def sign_in(self) -> dict:
        if self._sign_in_hook is None:
            return {"ok": False, "message": MSG_FAILED}
        self._sign_in_hook()
        return {"ok": True}

    # --- from the scheduler --------------------------------------------------
    def _on_sync_start(self) -> None:
        with self._lock:
            self._syncing = True
        self._push(self.state())

    def _on_sync(self, result: SyncResult) -> None:
        with self._lock:
            self._syncing = False
            self._status = result.status
            self._notices = list(result.notices)
        self._push(self.state())
