"""
Сравнение месяцев по статьям ДДС: месяц к предыдущему.
Только витрина dashboard_cash_flow. Без остатков, курсовых разниц и переводов между своими.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from django.db.models import Max, Q, Sum

from ..models import CashFlow

SKIP = Q(source__in=("OPENING", "FX")) | Q(activity=4)
MONTHS = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
          "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
MONTHS_WITH = ["", "январём", "февралём", "мартом", "апрелем", "маем", "июнем",
               "июлем", "августом", "сентябрём", "октябрём", "ноябрём", "декабрём"]
SHORT = ["", "янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def last_date() -> date | None:
    return CashFlow.objects.exclude(SKIP).aggregate(d=Max("date"))["d"]


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _prev(y: int, m: int) -> tuple[int, int]:
    return (y - 1, 12) if m == 1 else (y, m - 1)


def _next(y: int, m: int) -> tuple[int, int]:
    return (y + 1, 1) if m == 12 else (y, m + 1)


def default_period(last: date) -> str:
    """Последний полный месяц; если данные по конец месяца — этот месяц."""
    if last == _month_end(last.year, last.month):
        return f"{last.year}-{last.month:02d}"
    y, m = _prev(last.year, last.month)
    return f"{y}-{m:02d}"


def _articles(d1: date, d2: date) -> dict[str, dict]:
    out: dict[str, dict] = {}
    rows = (CashFlow.objects.exclude(SKIP).filter(date__gte=d1, date__lte=d2)
            .values("article_code", "article_name", "activity").annotate(s=Sum("amount_rub")))
    for r in rows:
        name = r["article_name"] or ("Не разнесено" if r["activity"] == 9 else "Без статьи")
        a = out.setdefault(name, {"code": r["article_code"] or "", "name": name, "sum": 0.0})
        a["sum"] += r["s"] or 0.0
    return out


def _totals(d1: date, d2: date) -> dict:
    agg = CashFlow.objects.exclude(SKIP).filter(date__gte=d1, date__lte=d2).aggregate(
        inflow=Sum("amount_rub", filter=Q(amount_rub__gt=0), default=0),
        outflow=Sum("amount_rub", filter=Q(amount_rub__lt=0), default=0))
    return {"inflow": agg["inflow"] or 0.0, "outflow": agg["outflow"] or 0.0,
            "net": (agg["inflow"] or 0.0) + (agg["outflow"] or 0.0)}


def pct(cur: float, prev: float) -> float | None:
    return None if abs(prev) < 1 else (abs(cur) - abs(prev)) / abs(prev) * 100


def build(period: str, mtd: bool = False, top: int = 8) -> dict | None:
    """period — «ГГГГ-ММ». mtd — с 1-го числа по последний день данных, к тем же дням прошлого месяца."""
    last = last_date()
    if last is None:
        return None
    y, m = map(int, period.split("-"))
    if date(y, m, 1) > last:
        y, m = last.year, last.month
    py, pm = _prev(y, m)
    d1, d2 = date(y, m, 1), min(_month_end(y, m), last)
    partial = d2 < _month_end(y, m)
    if mtd or partial:
        p1 = date(py, pm, 1)
        p2 = min(date(py, pm, min(d2.day, calendar.monthrange(py, pm)[1])), _month_end(py, pm))
    else:
        p1, p2 = date(py, pm, 1), _month_end(py, pm)

    cur, prev = _articles(d1, d2), _articles(p1, p2)
    changes = []
    for name in set(cur) | set(prev):
        c = cur.get(name, {}).get("sum", 0.0)
        p = prev.get(name, {}).get("sum", 0.0)
        if abs(c - p) < 1:
            continue
        changes.append({"name": name, "cur": c, "prev": p, "delta": abs(c) - abs(p),
                        "pct": pct(c, p), "out": (c or p) < 0})
    changes.sort(key=lambda x: abs(x["delta"]), reverse=True)

    first = CashFlow.objects.exclude(SKIP).order_by("date").values_list("date", flat=True).first()
    ny, nm = _next(y, m)
    return {
        "period": f"{y}-{m:02d}", "title": f"{MONTHS[m]} {y}", "prev_title": f"{MONTHS[pm]} {py}",
        "with_prev": MONTHS_WITH[pm] + (f" {py}" if py != y else ""),
        "short": SHORT[m], "prev_short": SHORT[pm],
        "d1": d1, "d2": d2, "p1": p1, "p2": p2, "partial": partial or mtd,
        "cur": _totals(d1, d2), "prev": _totals(p1, p2),
        "changes": changes[:top], "changes_all": changes,
        "has_prev": bool(first and first <= _month_end(py, pm)),
        "prev_period": f"{py}-{pm:02d}",
        "next_period": f"{ny}-{nm:02d}" if date(ny, nm, 1) <= last else None,
        "last": last,
    }
