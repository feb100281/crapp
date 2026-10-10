"""
Отчёт «Движение денег за день»: остаток на начало → поступления и выплаты
по статьям и контрагентам → внутригрупповые → курсовая → остаток на конец.

Только витрины (dashboard_cash_flow, dashboard_cash_balance). Одни данные
для страницы печати, PDF (бот, сайт) и Excel.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date as date_type

from django.db.models import Count, Sum

from ..models import CashBalance, CashFlow

INTRAGROUP, FX, UNALLOC = 4, 5, 9
INN_PREFIX = re.compile(r"^ИНН\s*\d+\s+")

ACCOUNT_FILTERS = ("bank_name", "account_name", "currency")


@dataclass
class Article:
    code: str
    name: str
    total: float = 0.0
    count: int = 0
    rows: list = field(default_factory=list)   # [{name, inn, sub, amount, count}]


@dataclass
class Section:
    key: str
    title: str
    total: float = 0.0
    articles: list = field(default_factory=list)


def _article_key(o: dict) -> tuple[str, str]:
    if o["activity"] == UNALLOC:
        return "—", "Не разнесено по статьям"
    return (o["article_code"] or o["cf_code"] or "—",
            o["article_name"] or o["cf_name"] or "Без статьи")


def _section(key: str, title: str, flows: list[dict]) -> Section:
    sec = Section(key, title)
    arts: OrderedDict[tuple, Article] = OrderedDict()
    cps: dict[tuple, dict] = {}
    for o in flows:
        code, name = _article_key(o)
        art = arts.setdefault((code, name), Article(code, name))
        sub = o["cf_name"] if o["cf_name"] and o["cf_name"] != name and o["activity"] != UNALLOC else ""
        k = (code, name, o["inn"] or "", o["cp_name"] or "", sub)
        row = cps.get(k)
        if row is None:
            name_cp = INN_PREFIX.sub("", o["cp_name"] or "").strip() or "Без контрагента"
            row = cps[k] = {"name": name_cp, "inn": o["inn"] or "",
                            "sub": sub, "amount": 0.0, "count": 0}
            art.rows.append(row)
        row["amount"] += o["s"] or 0
        row["count"] += o["n"]
        art.total += o["s"] or 0
        art.count += o["n"]
    for art in arts.values():
        art.rows.sort(key=lambda r: -abs(r["amount"]))
    sec.articles = sorted(arts.values(), key=lambda a: (a.code == "—", a.code))
    sec.total = sum(a.total for a in sec.articles)
    return sec


def build(on: date_type, flt: dict | None = None) -> dict:
    flt = {k: v for k, v in (flt or {}).items() if k in ACCOUNT_FILTERS and v}
    bal = CashBalance.objects.filter(date=on, **flt).aggregate(bb=Sum("bb_rub"), eb=Sum("eb_rub"), n=Count("id"))
    flows = list(
        CashFlow.objects.filter(date=on, **flt)
        .values("source", "activity", "direction", "article_code", "article_name", "cf_code", "cf_name",
                "cp_name", "inn")
        .annotate(s=Sum("amount_rub"), n=Count("id"))
        .order_by()
    )

    regular = [o for o in flows if o["source"] != "OPENING" and o["activity"] not in (INTRAGROUP, FX)]
    inflow = _section("in", "Поступления", [o for o in regular if o["direction"] == 1])
    outflow = _section("out", "Выплаты", [o for o in regular if o["direction"] != 1])
    intra = _section("intra", "Внутригрупповые операции",
                     [o for o in flows if o["source"] != "OPENING" and o["activity"] == INTRAGROUP])
    fx = sum(o["s"] or 0 for o in flows if o["activity"] == FX)
    openings = sum(o["s"] or 0 for o in flows if o["source"] == "OPENING")

    opening, closing = bal["bb"] or 0.0, bal["eb"] or 0.0
    moved = inflow.total + outflow.total + intra.total + fx + openings
    return {
        "date": on,
        "scope": " · ".join(flt.values()) or "все счета",
        "filters": flt,
        "accounts": bal["n"] or 0,
        "opening": opening,
        "closing": closing,
        "sections": [s for s in (inflow, outflow) if s.articles],
        "inflow": inflow,
        "outflow": outflow,
        "intra": intra,
        "fx": fx,
        "openings": openings,
        "net": inflow.total + outflow.total,
        "check": round(opening + moved - closing, 2),
        "empty": not flows and not opening and not closing,
    }


def operations(on: date_type, flt: dict | None = None):
    """Плоский список операций дня — второй лист Excel."""
    flt = {k: v for k, v in (flt or {}).items() if k in ACCOUNT_FILTERS and v}
    return (CashFlow.objects.filter(date=on, **flt).exclude(source="OPENING")
            .order_by("activity", "direction", "article_code", "-amount_rub")
            .values("account_name", "bank_name", "currency", "activity_name", "direction_name",
                    "article_code", "article_name", "cf_name", "cp_name", "inn", "description",
                    "amount_cur", "amount_rub"))
