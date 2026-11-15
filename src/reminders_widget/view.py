"""Pure list-view logic: which reminders are shown, in which order, under which title."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from .icloud.records import Reminder, ReminderList, bare_id

_PRIORITY_RANK = {1: 0, 5: 1, 9: 2}  # anything else (0 / unknown) sorts last
_NO_PRIORITY = 3


def _created_key(r: Reminder) -> tuple[int, float]:
    created: datetime | None = r.created
    if created is None:
        return (1, 0.0)  # missing creation time sorts after any datetime
    return (0, created.timestamp())


GROUPS = ("overdue", "today", "tomorrow", "week", "later", "none")   # in display order


def _day(due: date | datetime) -> date:
    return due.astimezone().date() if isinstance(due, datetime) else due


def day_group(due: date | datetime | None, today: date) -> str:
    """Which heading a due date falls under: overdue, today, tomorrow, week (2-6 days), later, none."""
    if due is None:
        return "none"
    days = (_day(due) - today).days
    if days < 0:
        return "overdue"
    return {0: "today", 1: "tomorrow"}.get(days, "week" if days < 7 else "later")


def _date_key(r: Reminder, today: date) -> tuple:
    """By day heading, then all-day before timed, then time, then priority, then age."""
    group = GROUPS.index(day_group(r.due, today))
    if r.due is None:
        when = (date.max, 0, 0.0)
    elif isinstance(r.due, datetime):
        when = (_day(r.due), 1, r.due.timestamp())
    else:
        when = (r.due, 0, 0.0)
    return (group, *when, _PRIORITY_RANK.get(r.priority, _NO_PRIORITY), *_created_key(r), r.record_name)


def visible(
    reminders: list[Reminder],
    selected: set[str],
    sort: Literal["priority", "icloud", "date"],
    lists: list[ReminderList] | None = None,
    *,
    view: Literal["lists", "today", "scheduled"] = "lists",
    today: date | None = None,
) -> list[Reminder]:
    """Open reminders to show, merged and sorted.

    view "lists": the selected lists, in `sort` order. "today": due today or earlier, and
    "scheduled": everything with a due date — both across all lists, by day.
    "icloud": lists in their own order, each in its ReminderIDs order; reminders the
    stored order does not name yet (just created, or iCloud's list lags) follow, oldest first.
    """
    today = today or date.today()
    items = [r for r in reminders if not r.completed and not r.deleted]
    if view == "today":
        items = [r for r in items if day_group(r.due, today) in ("overdue", "today")]
    elif view == "scheduled":
        items = [r for r in items if r.due is not None]
    else:
        items = [r for r in items if r.list_id in selected]
    if view != "lists" or sort == "date":
        items.sort(key=lambda r: _date_key(r, today))
    elif sort == "priority":
        items.sort(key=lambda r: (_PRIORITY_RANK.get(r.priority, _NO_PRIORITY), *_created_key(r), r.record_name))
    else:
        known = lists or []
        list_rank = {lst.record_name: i for i, lst in enumerate(known)}
        position = {lst.record_name: {rid: i for i, rid in enumerate(lst.order)} for lst in known}

        def icloud_key(r: Reminder):
            pos = position.get(r.list_id, {}).get(bare_id(r.record_name))
            return (list_rank.get(r.list_id, len(known)), pos is None, pos or 0, *_created_key(r), r.record_name)
        items.sort(key=icloud_key)
    return items


def title_for(lists: list[ReminderList], selected: set[str]) -> str:
    chosen = [lst.name for lst in lists if lst.record_name in selected]
    if len(chosen) == len(lists):
        return "All"
    return " + ".join(chosen)


def ensure_selection(lists: list[ReminderList], selected: set[str]) -> set[str]:
    """Drop ids of lists that no longer exist; an empty result means all lists."""
    existing = {lst.record_name for lst in lists}
    kept = selected & existing
    return kept or existing
