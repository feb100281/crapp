"""
Поиск платежей по витрине dashboard_cash_flow: текст в контрагенте или назначении,
ИНН (10 или 12 цифр) или сумма («45000», «45 000,50»).
"""

from __future__ import annotations

import re

from django.db.models import Q

from core.reports.xlsx import Book, Col, Row, period_text

from ..models import CashFlow

SKIP = Q(source__in=("OPENING", "FX"))
INN = re.compile(r"^\d{10}$|^\d{12}$")
AMOUNT = re.compile(r"^-?\d[\d\s ]*([.,]\d{1,2})?$")
LIMIT = 5000
FIELDS = ("date", "account_name", "bank_name", "currency", "amount_cur", "amount_rub",
          "article_name", "cf_name", "cp_name", "inn", "description")


def _near(field: str, value: float) -> Q:
    return (Q(**{f"{field}__gte": value - 0.5, f"{field}__lte": value + 0.5})
            | Q(**{f"{field}__gte": -value - 0.5, f"{field}__lte": -value + 0.5}))


def _text(field: str, q: str) -> Q:
    # SQLite не сравнивает кириллицу без учёта регистра — ищем основные варианты написания
    cond = Q()
    for v in {q, q.lower(), q.upper(), q.capitalize()}:
        cond |= Q(**{f"{field}__contains": v})
    return cond


def condition(q: str) -> tuple[Q, str] | None:
    q = (q or "").strip()
    if len(q) < 2:
        return None
    if INN.match(q):
        return Q(inn=q), f"ИНН {q}"
    if AMOUNT.match(q):
        value = abs(float(re.sub(r"[\s ]", "", q).replace(",", ".")))
        return _near("amount_rub", value) | _near("amount_cur", value), f"сумма {q}"
    if len(q) < 3:
        return None
    return _text("cp_name", q) | _text("description", q), f"«{q}»"


def search(q: str) -> dict | None:
    found = condition(q)
    if found is None:
        return None
    cond, label = found
    ops = list(CashFlow.objects.exclude(SKIP).filter(cond)
               .order_by("-date", "amount_rub").values(*FIELDS)[:LIMIT])
    inflow = sum(o["amount_rub"] or 0 for o in ops if (o["amount_rub"] or 0) > 0)
    outflow = sum(o["amount_rub"] or 0 for o in ops if (o["amount_rub"] or 0) < 0)
    return {
        "query": q, "label": label, "ops": ops, "count": len(ops), "capped": len(ops) >= LIMIT,
        "inflow": inflow, "outflow": outflow,
        "first": ops[-1]["date"] if ops else None, "last": ops[0]["date"] if ops else None,
    }


def book(r: dict) -> Book:
    b = Book(f"Поиск платежей: {r['label']}", f"Найдено операций: {r['count']}",
             f"Российский рубль (RUB) · {period_text(r['first'], r['last'])}")
    b.kpi("Получили", r["inflow"], "поступления")
    b.kpi("Заплатили", -r["outflow"], "выплаты")
    b.kpi("Итого", r["inflow"] + r["outflow"], "поступления − выплаты")
    b.sheet("Операции", "Найденные операции", subtitle=r["label"],
            description="Каждая операция: дата, счёт, контрагент, статья, назначение, сумма").table(
        [Col("Дата", kind="date", width=12), Col("Счёт", width=30), Col("Контрагент", width=36),
         Col("ИНН", kind="code", width=14), Col("Статья", width=30), Col("Подстатья", width=28),
         Col("Назначение платежа", width=60), Col("Валюта", width=8),
         Col("Сумма, вал.", kind="money_dec", width=16), Col("Сумма, ₽", kind="money_dec", width=17, total=True)],
        [Row([o["date"], o["account_name"] or "", o["cp_name"] or "", o["inn"] or "", o["article_name"] or "",
              o["cf_name"] or "", (o["description"] or "")[:400], o["currency"] or "",
              o["amount_cur"], o["amount_rub"]]) for o in r["ops"]],
        autofilter=True,
    )
    return b
