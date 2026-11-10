"""Pure list-view logic: which reminders are shown, in which order, under which title."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from .icloud.records import Reminder, ReminderList, bare_id

_PRIORITY_RANK = {1: 0, 5: 1, 9: 2}  # anything else (0 / unknown) sorts last
_NO_PRIORITY = 3


def _created_key(r: Reminder) -> tuple[int, float]:
    created: datetime | None = r.created
    if created is None:
        return (1, 0.0)  # missing creation time sorts after any datetime
    return (0, created.timestamp())


def visible(
    reminders: list[Reminder],
    selected: set[str],
    sort: Literal["priority", "icloud"],
    lists: list[ReminderList] | None = None,
) -> list[Reminder]:
    """Open reminders of the selected lists, merged and sorted.

    "icloud": lists in their own order, each in its ReminderIDs order; reminders the
    stored order does not name yet (just created, or iCloud's list lags) follow, oldest first.
    """
    items = [r for r in reminders if r.list_id in selected and not r.completed and not r.deleted]
    if sort == "priority":
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
