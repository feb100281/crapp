"""Даты для бота: разбор «02.10», «вчера», и календарь из кнопок."""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
          "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEK = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
DATE_RE = re.compile(r"^\s*(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\s*$")


def parse(text: str, today: date | None = None) -> date | None:
    """«сегодня», «вчера», «позавчера», «2.10», «02.10.2026», «2026-10-02»."""
    today = today or date.today()
    t = (text or "").strip().lower()
    if t in ("сегодня", "today"):
        return today
    if t in ("вчера", "yesterday"):
        return today - timedelta(days=1)
    if t == "позавчера":
        return today - timedelta(days=2)
    try:
        return date.fromisoformat(t)
    except ValueError:
        pass
    m = DATE_RE.match(t)
    if not m:
        return None
    d, mo, y = int(m[1]), int(m[2]), m[3]
    year = today.year if not y else (int(y) + 2000 if len(y) == 2 else int(y))
    try:
        found = date(year, mo, d)
    except ValueError:
        return None
    if not y and found > today:          # «31.12» в январе — это прошлый год
        found = date(year - 1, mo, d)
    return found


def calendar_markup(kind: str, year: int, month: int, lo: date | None = None, hi: date | None = None) -> dict:
    """Месяц кнопками. callback: «d:<kind>:YYYY-MM-DD», листание «c:<kind>:YYYY-MM»."""
    rows = [[{"text": f"{MONTHS[month - 1]} {year}", "callback_data": "noop"}],
            [{"text": w, "callback_data": "noop"} for w in WEEK]]
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        line = []
        for day in week:
            if not day:
                line.append({"text": " ", "callback_data": "noop"})
                continue
            d = date(year, month, day)
            if (lo and d < lo) or (hi and d > hi):
                line.append({"text": "·", "callback_data": "noop"})
            else:
                line.append({"text": str(day), "callback_data": f"d:{kind}:{d.isoformat()}"})
        rows.append(line)
    prev_m = date(year, month, 1) - timedelta(days=1)
    next_m = date(year, month, 28) + timedelta(days=4)
    nav = []
    if not lo or prev_m >= lo:
        nav.append({"text": "‹", "callback_data": f"c:{kind}:{prev_m:%Y-%m}"})
    else:
        nav.append({"text": " ", "callback_data": "noop"})
    nav.append({"text": "✕", "callback_data": "x"})
    if not hi or next_m.replace(day=1) <= hi:
        nav.append({"text": "›", "callback_data": f"c:{kind}:{next_m:%Y-%m}"})
    else:
        nav.append({"text": " ", "callback_data": "noop"})
    rows.append(nav)
    return {"inline_keyboard": rows}
