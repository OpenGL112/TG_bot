"""Правила напоминаний о записи (чистая логика, без Telegram и БД)."""
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Reminder:
    kind: str           # совпадает с суффиксом колонки reminded_<kind> в bookings
    before: timedelta   # за сколько до начала отправлять
    can_cancel: bool    # показывать ли кнопки «Приду» / «Отменить»
    label: str          # для текста: «за день», «за 3 часа»...


# Порядок важен: от самого раннего к самому позднему
REMINDERS = (
    Reminder("day", timedelta(hours=24), can_cancel=True, label="за день"),
    Reminder("3h", timedelta(hours=3), can_cancel=False, label="за 3 часа"),
    Reminder("1h", timedelta(hours=1), can_cancel=False, label="за час"),
)


def plan(start: datetime, created_at: datetime | None, now: datetime,
         sent: set[str]) -> tuple[Reminder | None, set[str]]:
    """Решает, что отправить по одной записи прямо сейчас.

    Возвращает (напоминание для отправки или None, виды, которые нужно пометить обработанными).
    - напоминания, момент которых наступил раньше создания записи, пропускаются;
    - если просрочено несколько (бот был выключен), отправляется только самое позднее;
    - напоминание с кнопкой отмены не отправляется в день визита;
    - после начала визита ничего не отправляется.
    """
    if now >= start:
        return None, set()
    to_mark: set[str] = set()
    due: list[Reminder] = []
    for r in REMINDERS:
        if r.kind in sent:
            continue
        moment = start - r.before
        if created_at is not None and moment < created_at:
            to_mark.add(r.kind)          # записались уже после этого момента
        elif moment <= now:
            due.append(r)
    if not due:
        return None, to_mark
    latest = due[-1]
    to_mark |= {r.kind for r in due}
    if latest.can_cancel and now.date() >= start.date():
        return None, to_mark             # в день визита — только поздние напоминания
    return latest, to_mark


def upcoming_phrase(start: datetime, now: datetime) -> str:
    """Какие напоминания ещё придут по только что созданной записи: «за день, за 3 часа и за час»."""
    labels = [r.label for r in REMINDERS if start - r.before > now]
    if not labels:
        return ""
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " и " + labels[-1]
