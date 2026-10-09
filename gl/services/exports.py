"""Выгрузка плана счетов в Excel / CSV: Баланс / P&L → раздел → счёт → субсчёт."""

from __future__ import annotations

import html
from collections import defaultdict
from decimal import Decimal

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.html import strip_tags

from core.reports.xlsx import FMT_QTY, Book, Col, Row

from ..models.gl_account_model import GLAccount, Section

ZERO = Value(0, output_field=DecimalField(max_digits=18, decimal_places=2))

STATEMENTS = (("1", "Баланс"), ("2", "P&L"))


def _plain(text) -> str:
    return " ".join(html.unescape(strip_tags(text or "")).split())


def _accounts():
    """Все счета с оборотами по проводкам (свои, без субсчетов)."""
    return list(
        GLAccount.objects.select_related("parent", "currency", "bank_account")
        .annotate(dt=Coalesce(Sum("lines__dt"), ZERO), cr=Coalesce(Sum("lines__cr"), ZERO))
        .order_by("code")
    )


def chart_rows() -> list[dict]:
    """Строки плана счетов по уровням (для Excel и CSV)."""
    accounts = _accounts()
    children = defaultdict(list)
    for a in accounts:
        if a.parent_id:
            children[a.parent_id].append(a)
    tops = [a for a in accounts if not a.parent_id]

    def turnover(acc):
        group = [acc] + children[acc.pk]
        return sum((g.dt for g in group), Decimal(0)), sum((g.cr for g in group), Decimal(0))

    def account(acc, level):
        dt, cr = turnover(acc) if level == 3 else (acc.dt, acc.cr)
        return {
            "level": level, "code": acc.code, "name": acc.name,
            "section": acc.get_section_display(), "nature": acc.get_nature_display(),
            "currency": acc.currency.code if acc.currency_id else "",
            "bank": acc.bank_account.number if acc.bank_account_id else "",
            "dt": dt, "cr": cr, "saldo": dt - cr,
            "active": acc.is_active, "description": _plain(acc.description),
        }

    rows = []
    for prefix, statement in STATEMENTS:
        sections = [(v, label) for v, label in Section.choices if str(v).startswith(prefix)]
        block = [a for a in tops if str(a.section).startswith(prefix)]
        if not block:
            continue
        dt = sum((turnover(a)[0] for a in block), Decimal(0))
        cr = sum((turnover(a)[1] for a in block), Decimal(0))
        rows.append({"level": 1, "code": "", "name": statement, "dt": dt, "cr": cr, "saldo": dt - cr})
        for value, label in sections:
            part = [a for a in block if a.section == value]
            if not part:
                continue
            dt = sum((turnover(a)[0] for a in part), Decimal(0))
            cr = sum((turnover(a)[1] for a in part), Decimal(0))
            rows.append({"level": 2, "code": f"{value}00000", "name": label, "dt": dt, "cr": cr,
                         "saldo": dt - cr})
            for acc in part:
                rows.append(account(acc, 3))
                rows += [account(sub, 4) for sub in children[acc.pk]]
    return rows


def chart_workbook() -> Book:
    rows = chart_rows()
    accounts = [r for r in rows if r["level"] == 3]
    subs = [r for r in rows if r["level"] == 4]
    book = Book(
        "План счетов",
        subtitle="Управленческий план счетов: Баланс / P&L → раздел → счёт → субсчёт",
        params=f"Российский рубль (RUB) · дата: {timezone.localdate():%d.%m.%Y} · "
               f"счетов: {len(accounts)} · субсчетов: {len(subs)}",
    )
    book.kpi("Счетов", len(accounts), "верхний уровень", FMT_QTY)
    book.kpi("Субсчетов", len(subs), "проводки идут на них", FMT_QTY)
    book.kpi("Банковских субсчетов", sum(1 for r in subs if r["bank"]), "привязаны к счетам", FMT_QTY)

    levels = {1: 1, 2: 2, 3: 3, 4: None}
    book.sheet(
        "План счетов", "План счетов",
        subtitle="Код: раздел · счёт · субсчёт. Обороты и сальдо — по всем проводкам журнала, ₽",
        description="Дерево счетов с характером, валютой, банковским счётом, оборотами и сальдо",
    ).table(
        [
            Col("Код", kind="code", width=11),
            Col("Счёт", width=50, indent=True),
            Col("Характер", width=18),
            Col("Валюта", width=8),
            Col("Банковский счёт", kind="code", width=24),
            Col("Оборот Дт, ₽", kind="money", width=17),
            Col("Оборот Кт, ₽", kind="money", width=17),
            Col("Сальдо, ₽ (Дт − Кт)", kind="money", width=19, total=True),
            Col("Активен", width=9),
            Col("Описание", width=50, wrap=True),
        ],
        [
            Row([
                r["code"], r["name"], r.get("nature", ""), r.get("currency", ""), r.get("bank", ""),
                r["dt"], r["cr"], r["saldo"],
                ("да" if r["active"] else "нет") if "active" in r else "",
                r.get("description", ""),
            ], level=levels[r["level"]], outline=r["level"] - 1, indent=2 * (r["level"] - 1))
            for r in rows
        ],
        freeze_cols=2,
    )
    return book


def chart_csv():
    header = ["Уровень", "Код", "Счёт", "Субсчёт", "Раздел", "Характер", "Валюта",
              "Банковский счёт", "Оборот Дт, ₽", "Оборот Кт, ₽", "Сальдо, ₽", "Активен", "Описание"]
    out, parent = [], ""
    for r in chart_rows():
        if r["level"] < 3:
            continue
        if r["level"] == 3:
            parent = r["name"]
        out.append([
            "счёт" if r["level"] == 3 else "субсчёт", r["code"],
            parent, r["name"] if r["level"] == 4 else "", r["section"], r["nature"], r["currency"],
            r["bank"], r["dt"], r["cr"], r["saldo"], r["active"], r["description"],
        ])
    return header, out
