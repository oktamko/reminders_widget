from datetime import datetime, timezone

from reminders_widget.icloud.records import Reminder, ReminderList
from reminders_widget.view import ensure_selection, title_for, visible

LISTS = [ReminderList("w", "Práca", None), ReminderList("s", "Škola", None), ReminderList("h", "Home", None)]


def rem(name, list_id="w", priority=0, created=1, completed=False, deleted=False):
    return Reminder(
        record_name=name, list_id=list_id, title=name, notes=None, completed=completed,
        priority=priority, due=None, deleted=deleted, change_tag="t", readonly=False,
        created=None if created is None else datetime.fromtimestamp(created, timezone.utc),
    )


def names(items):
    return [r.record_name for r in items]


def test_two_lists_merge_sorted_by_priority():
    rs = [rem("a", "w", 0), rem("b", "s", 9), rem("c", "w", 1), rem("d", "s", 5), rem("e", "s", 1)]
    out = visible(rs, {"w", "s"}, "priority")
    assert [r.priority for r in out] == [1, 1, 5, 9, 0]


def test_unselected_lists_excluded():
    rs = [rem("a", "w"), rem("b", "h")]
    assert names(visible(rs, {"w"}, "priority")) == ["a"]


def test_priority_ties_oldest_first_and_none_created_last():
    rs = [rem("new", priority=1, created=50), rem("old", priority=1, created=10), rem("nil", priority=1, created=None)]
    assert names(visible(rs, {"w"}, "priority")) == ["old", "new", "nil"]


def test_icloud_order_by_created_then_record_name():
    rs = [rem("z", priority=1, created=5), rem("b", priority=9, created=5), rem("a", priority=0, created=9),
          rem("n", created=None)]
    assert names(visible(rs, {"w"}, "icloud")) == ["b", "z", "a", "n"]


def test_completed_and_deleted_excluded_readonly_kept():
    rs = [rem("done", completed=True), rem("gone", deleted=True), rem("ok")]
    assert names(visible(rs, {"w"}, "icloud")) == ["ok"]


def test_title_for():
    assert title_for(LISTS, {"w", "s"}) == "Práca + Škola"
    assert title_for(LISTS, {"w", "s", "h"}) == "All"
    assert title_for(LISTS, {"s", "w"}) == "Práca + Škola"


def test_ensure_selection_drops_deleted_and_empty_means_all():
    assert ensure_selection(LISTS, {"w", "gone"}) == {"w"}
    assert ensure_selection(LISTS, {"gone"}) == {"w", "s", "h"}
    assert ensure_selection(LISTS, set()) == {"w", "s", "h"}
