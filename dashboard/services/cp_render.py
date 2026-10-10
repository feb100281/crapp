"""Отчёт по контрагенту: текст для Telegram, PDF и Excel."""

from __future__ import annotations

import html

from django.template.loader import render_to_string

from core.reports.xlsx import Book, Col, Row

from ..admin.common import money2, print_context
from .day_render import html_to_pdf


def _period(r: dict) -> str:
    d1 = f"{r['date_from']:%d.%m.%Y}" if r["date_from"] else "начала"
    return f"{d1} — {r['date_to']:%d.%m.%Y}"


def text(r: dict) -> str:
    esc = lambda s: html.escape(str(s or ""), quote=False)  # noqa: E731
    lines = [f"🔎 <b>{esc(r['name'])}</b>"]
    if r["inn"]:
        lines.append(f"ИНН {r['inn']}")
    lines += [f"{r['period_name']}: {_period(r)}", ""]
    if not r["count"]:
        return "\n".join(lines + ["Платежей за период нет."])
    if round(r["outflow"], 2):
        lines.append(f"➖ Мы заплатили: <b>{money2(-r['outflow'])} ₽</b>")
    if round(r["inflow"], 2):
        lines.append(f"➕ Нам заплатили: <b>{money2(r['inflow'])} ₽</b>")
    lines.append(f"Операций: {r['count']} · последняя {r['last']:%d.%m.%Y}")
    if len(r["articles"]) > 1 or (r["articles"] and r["articles"][0]["name"]):
        lines += ["", "<b>По статьям:</b>"]
        lines += [f"• {esc(a['name'])}: {money2(a['sum'], signed=True)}" for a in r["articles"][:8]]
    lines += ["", "<b>Последние операции:</b>"]
    for o in r["ops"][:8]:
        lines.append(f"{o['date']:%d.%m} · {money2(o['amount_rub'], signed=True)} · {esc(o['article_name'] or o['cf_name'] or 'без статьи')}")
    if len(r["ops"]) > 8:
        lines.append(f"…и ещё {len(r['ops']) - 8} — в PDF/Excel")
    return "\n".join(lines)


def page_html(r: dict, request=None, pdf: bool = False) -> str:
    ctx = print_context(request, "Платежи по контрагенту", r["name"],
                        f"{('ИНН ' + r['inn'] + ' · ') if r['inn'] else ''}{r['period_name']}: {_period(r)}")
    ctx.update({
        "r": r, "pdf": pdf,
        "paid_t": money2(-r["outflow"]) if round(r["outflow"], 2) else "–",
        "got_t": money2(r["inflow"]) if round(r["inflow"], 2) else "–",
        "net_t": money2(r["net"], signed=True),
        "arts": [dict(a, sum_t=money2(a["sum"], signed=True), neg=a["sum"] < 0) for a in r["articles"]],
        "ops": [dict(o, rub_t=money2(o["amount_rub"], signed=True), neg=(o["amount_rub"] or 0) < 0,
                     cur_t="" if (o["currency"] or "RUB") == "RUB" else f"{money2(o['amount_cur'])} {o['currency']}")
                for o in r["ops"]],
    })
    return render_to_string("dashboard/print_cp.html", ctx, request=request)


def pdf(r: dict) -> bytes:
    return html_to_pdf(page_html(r, pdf=True))


def book(r: dict) -> Book:
    b = Book(f"Платежи: {r['name']}", f"{('ИНН ' + r['inn'] + ' · ') if r['inn'] else ''}{r['period_name']}",
             f"Российский рубль (RUB) · {_period(r)}")
    b.kpi("Мы заплатили", -r["outflow"], r["period_name"])
    b.kpi("Нам заплатили", r["inflow"], r["period_name"])
    b.kpi("Итого", r["net"], "поступления − выплаты")
    b.sheet("По статьям", "Платежи по статьям", subtitle=r["name"],
            description="Сумма по каждой статье ДДС за период").table(
        [Col("Код", kind="code", width=10), Col("Статья", width=50), Col("Операций", kind="int", width=11),
         Col("Сумма, ₽", kind="money_dec", width=18, total=True)],
        [Row([a["code"], a["name"], a["n"], a["sum"]]) for a in r["articles"]],
        total=Row(["", "ИТОГО", r["count"], r["net"]], level="total"),
    )
    b.sheet("Операции", "Все операции", subtitle=r["name"],
            description="Каждая операция: дата, счёт, статья, назначение, сумма").table(
        [Col("Дата", kind="date", width=12), Col("Счёт", width=30), Col("Банк", width=24), Col("Валюта", width=8),
         Col("Статья", width=30), Col("Подстатья", width=28), Col("Назначение платежа", width=60),
         Col("Сумма, вал.", kind="money_dec", width=16), Col("Сумма, ₽", kind="money_dec", width=17, total=True)],
        [Row([o["date"], o["account_name"], o["bank_name"] or "", o["currency"], o["article_name"] or "",
              o["cf_name"] or "", (o["description"] or "")[:400], o["amount_cur"], o["amount_rub"]]) for o in r["ops"]],
        autofilter=True,
    )
    return b
