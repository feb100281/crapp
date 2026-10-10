"""
Платежи по контрагенту: сколько мы ему заплатили и сколько он нам — за период,
по статьям и списком операций. Только витрина dashboard_cash_flow.

Контрагент — по ИНН (все варианты названия вместе), без ИНН — по названию.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import date, timedelta

from django.db.models import Count, Max, Q, Sum

from ..models import CashFlow

INN_PREFIX = re.compile(r"^(ИНН\s*)?\d{10,12}\s+")
PERIODS = OrderedDict([("m", "30 дней"), ("q", "90 дней"), ("y", "С начала года"), ("a", "Всё время")])
SKIP = Q(source__in=("OPENING", "FX")) | Q(activity=4)       # остатки, курсовые, свои счета


def clean_name(name: str) -> str:
    return INN_PREFIX.sub("", name or "").strip() or "Без названия"


def key_of(inn: str | None, name: str | None) -> str:
    return f"inn:{inn}" if inn else f"name:{name or ''}"


def _scope(key: str):
    kind, _, value = key.partition(":")
    base = CashFlow.objects.exclude(SKIP)
    return base.filter(inn=value) if kind == "inn" else base.filter(inn__isnull=True, cp_name=value)


def search(q: str, limit: int = 8) -> list[dict]:
    """Контрагенты по части названия или ИНН, сначала самые активные."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    cond = Q(inn__startswith=q) if q.isdigit() else Q(cp_name__icontains=q)
    rows = (CashFlow.objects.exclude(SKIP).filter(cond).exclude(cp_name__isnull=True)
            .values("inn", "cp_name").annotate(n=Count("id"), last=Max("date")).order_by("-n"))
    out: OrderedDict[str, dict] = OrderedDict()
    for r in rows:
        k = key_of(r["inn"], r["cp_name"])
        if k in out:
            out[k]["n"] += r["n"]
            out[k]["last"] = max(out[k]["last"], r["last"])
            continue
        out[k] = {"key": k, "name": clean_name(r["cp_name"]), "inn": r["inn"] or "", "n": r["n"], "last": r["last"]}
        if len(out) >= limit:
            break
    return list(out.values())


def period_range(period: str, last: date) -> tuple[date | None, date]:
    if period == "m":
        return last - timedelta(days=29), last
    if period == "q":
        return last - timedelta(days=89), last
    if period == "y":
        return date(last.year, 1, 1), last
    return None, last


def build(key: str, period: str = "y") -> dict:
    scope = _scope(key)
    last = CashFlow.objects.aggregate(d=Max("date"))["d"] or date.today()
    d1, d2 = period_range(period, last)
    qs = scope.filter(date__lte=d2) if d1 is None else scope.filter(date__gte=d1, date__lte=d2)

    names = list(OrderedDict.fromkeys(
        clean_name(n) for n in scope.order_by("-date").values_list("cp_name", flat=True)[:500]))[:5]
    kind, _, value = key.partition(":")
    agg = qs.aggregate(inflow=Sum("amount_rub", filter=Q(amount_rub__gt=0), default=0),
                       outflow=Sum("amount_rub", filter=Q(amount_rub__lt=0), default=0),
                       n=Count("id"), first=Max("date"))
    arts = OrderedDict()
    for r in (qs.values("article_code", "article_name", "cf_name", "activity")
              .annotate(s=Sum("amount_rub"), n=Count("id")).order_by("article_code")):
        name = r["article_name"] or r["cf_name"] or ("Не разнесено" if r["activity"] == 9 else "Без статьи")
        a = arts.setdefault(name, {"code": r["article_code"] or "", "name": name, "sum": 0.0, "n": 0})
        a["sum"] += r["s"] or 0
        a["n"] += r["n"]
    ops = list(qs.order_by("-date", "amount_rub").values(
        "date", "account_name", "bank_name", "currency", "amount_cur", "amount_rub",
        "article_name", "cf_name", "description"))
    return {
        "key": key,
        "name": names[0] if names else (value if kind == "name" else "Контрагент"),
        "inn": value if kind == "inn" else "",
        "aliases": names[1:],
        "period": period, "period_name": PERIODS.get(period, ""),
        "date_from": d1 or (ops[-1]["date"] if ops else None), "date_to": d2,
        "inflow": agg["inflow"] or 0.0, "outflow": agg["outflow"] or 0.0,
        "net": (agg["inflow"] or 0.0) + (agg["outflow"] or 0.0), "count": agg["n"],
        "articles": sorted(arts.values(), key=lambda a: a["sum"]),
        "ops": ops,
        "last": ops[0]["date"] if ops else None,
    }
