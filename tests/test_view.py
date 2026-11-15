from datetime import date, datetime, timedelta, timezone

from reminders_widget.icloud.records import Reminder, ReminderList
from reminders_widget.view import day_group, ensure_selection, title_for, visible

LISTS = [ReminderList("w", "Práca", None), ReminderList("s", "Škola", None), ReminderList("h", "Home", None)]


def rem(name, list_id="w", priority=0, created=1, completed=False, deleted=False, due=None):
    return Reminder(
        record_name=name, list_id=list_id, title=name, notes=None, completed=completed,
        priority=priority, due=due, deleted=deleted, change_tag="t", readonly=False,
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


def test_icloud_order_follows_reminder_ids_and_puts_unknown_ones_last():   # probe round 4
    lists = [ReminderList("w", "Work", None, order=("c", "a")), ReminderList("s", "School", None, order=("y",))]
    rs = [rem("Reminder/a", "w", created=1), rem("Reminder/b", "w", created=2), rem("Reminder/c", "w", created=3),
          rem("Reminder/y", "s", created=0), rem("Reminder/x", "s", created=9)]
    # lists in their own order, each by ReminderIDs; ids missing from it (new ones) after, oldest first
    assert names(visible(rs, {"w", "s"}, "icloud", lists)) == [
        "Reminder/c", "Reminder/a", "Reminder/b", "Reminder/y", "Reminder/x"]


TODAY = date(2026, 11, 15)


def at(days, hour=None):
    d = TODAY + timedelta(days=days)
    return d if hour is None else datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc).astimezone()


def test_day_groups():   # UI feedback 6
    assert [day_group(at(n), TODAY) for n in (-3, 0, 1, 2, 6, 7, 30)] == [
        "overdue", "today", "tomorrow", "week", "week", "later", "later"]
    assert day_group(None, TODAY) == "none" and day_group(at(0, 9), TODAY) == "today"


def test_date_sort_groups_by_day_then_time_then_priority():
    rs = [rem("none"), rem("later", due=at(9)), rem("today9", due=at(0, 9)), rem("todayAll", due=at(0)),
          rem("late", due=at(-1)), rem("today9hi", priority=1, due=at(0, 9))]
    assert names(visible(rs, {"w"}, "date", today=TODAY)) == ["late", "todayAll", "today9hi", "today9", "later", "none"]


def test_today_view_spans_all_lists_and_keeps_overdue():
    rs = [rem("a", "w", due=at(0)), rem("b", "h", due=at(-2)), rem("c", "s", due=at(1)), rem("d", "w")]
    assert names(visible(rs, {"w"}, "priority", view="today", today=TODAY)) == ["b", "a"]


def test_scheduled_view_is_everything_with_a_date_by_day():
    rs = [rem("a", "w", due=at(3)), rem("b", "h", due=at(-2)), rem("c", "s"), rem("d", "s", due=at(0))]
    assert names(visible(rs, {"w"}, "icloud", view="scheduled", today=TODAY)) == ["b", "d", "a"]
