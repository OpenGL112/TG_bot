from datetime import datetime, timedelta

from reminders import plan, upcoming_phrase

START = datetime(2030, 1, 11, 14, 0)          # визит: пт 11.01 в 14:00
EARLY = datetime(2030, 1, 5, 12, 0)           # запись создана за неделю


def kind(result):
    reminder, _ = result
    return reminder.kind if reminder else None


def test_nothing_before_time():
    assert kind(plan(START, EARLY, START - timedelta(hours=25), set())) is None


def test_day_before_with_cancel():
    reminder, mark = plan(START, EARLY, START - timedelta(hours=24), set())
    assert reminder.kind == "day" and reminder.can_cancel and mark == {"day"}


def test_same_day_reminders_without_cancel():
    reminder, _ = plan(START, EARLY, START - timedelta(hours=3), {"day"})
    assert reminder.kind == "3h" and not reminder.can_cancel
    assert kind(plan(START, EARLY, START - timedelta(hours=1), {"day", "3h"})) == "1h"


def test_already_sent_not_repeated():
    assert kind(plan(START, EARLY, START - timedelta(hours=23), {"day"})) is None


def test_booked_inside_window_skips_earlier_reminders():
    created = START - timedelta(hours=2)
    reminder, mark = plan(START, created, created + timedelta(minutes=1), set())
    assert reminder is None and mark == {"day", "3h"}
    assert kind(plan(START, created, START - timedelta(hours=1), mark)) == "1h"


def test_after_downtime_only_latest_is_sent():
    reminder, mark = plan(START, EARLY, START - timedelta(minutes=50), set())
    assert reminder.kind == "1h" and mark == {"day", "3h", "1h"}


def test_cancel_reminder_never_sent_on_visit_day():
    # бот лежал всю ночь, включился в 08:00 в день визита: «за день» уже неуместно
    reminder, mark = plan(START, EARLY, datetime(2030, 1, 11, 8, 0), set())
    assert reminder is None and mark == {"day"}


def test_nothing_after_start():
    assert kind(plan(START, EARLY, START + timedelta(minutes=1), set())) is None


def test_upcoming_phrase():
    assert upcoming_phrase(START, START - timedelta(days=3)) == "за день, за 3 часа и за час"
    assert upcoming_phrase(START, START - timedelta(hours=5)) == "за 3 часа и за час"
    assert upcoming_phrase(START, START - timedelta(minutes=30)) == ""
