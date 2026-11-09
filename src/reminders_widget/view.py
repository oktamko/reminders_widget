"""Pure list-view logic: which reminders are shown, in which order, under which title."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from .icloud.records import Reminder, ReminderList

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
) -> list[Reminder]:
    """Open reminders of the selected lists, merged and sorted."""
    items = [r for r in reminders if r.list_id in selected and not r.completed and not r.deleted]
    if sort == "priority":
        items.sort(key=lambda r: (_PRIORITY_RANK.get(r.priority, _NO_PRIORITY), *_created_key(r), r.record_name))
    else:  # "icloud": stored order not decodable, so creation order
        items.sort(key=lambda r: (*_created_key(r), r.record_name))
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
