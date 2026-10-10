"""
Вывод отчёта за день и остатков на дату: HTML (печать), PDF, Excel.

PDF печатает headless Chromium (Playwright) из того же HTML, что и
страница печати, — выглядит один в один. Нужно один раз:
    pip install playwright && playwright install chromium
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.staticfiles import finders
from django.template.loader import render_to_string

from core.reports.xlsx import Book, Col, Row

from ..admin.common import money2, print_context
from . import day_report

FONT_CSS = "unfold/fonts/inter/styles.css"


class PdfUnavailable(RuntimeError):
    """Playwright/Chromium не установлен."""


# ---------------------------------------------------------------- HTML

def day_context(report: dict) -> dict:
    """Готовые к выводу строки: суммы текстом, знаки, счётчики."""
    def fmt(v, signed=False):
        return money2(v, signed=signed) if round(v or 0, 2) else "–"

    blocks = []
    for sec in [report["inflow"], report["outflow"], report["intra"]]:
        if not sec.articles:
            continue
        blocks.append({
            "title": sec.title.upper(),
            "total_t": fmt(sec.total, signed=sec.key == "intra"),
            "neg": sec.total < 0,
            "count": sum(a.count for a in sec.articles),
            "articles": [{
                "code": a.code if a.code != "—" else "",
                "name": a.name, "count": a.count, "total_t": fmt(a.total), "neg": a.total < 0,
                "rows": [dict(c, amount_t=fmt(c["amount"]), neg=c["amount"] < 0) for c in a.rows],
            } for a in sec.articles],
        })
    return {
        **report,
        "blocks": blocks,
        "opening_t": fmt(report["opening"]),
        "closing_t": fmt(report["closing"]),
        "inflow_t": fmt(report["inflow"].total),
        "outflow_t": fmt(report["outflow"].total),
        "fx_t": fmt(report["fx"], signed=True) if round(report["fx"], 2) else "",
        "fx_neg": report["fx"] < 0,
        "openings_t": fmt(report["openings"]) if round(report["openings"], 2) else "",
        "check_ok": abs(report["check"]) < 1,
        "check_t": money2(report["check"], signed=True),
    }


def day_html(report: dict, request=None, **extra) -> str:
    ctx = print_context(request, "Движение денег", f"Движение денег за {report['date']:%d.%m.%Y}",
                        f"{report['scope']} · поступления и выплаты по статьям и контрагентам")
    ctx.update({"r": day_context(report), **extra})
    return render_to_string("dashboard/print_day.html", ctx, request=request)


# ---------------------------------------------------------------- PDF

def html_to_pdf(html: str) -> bytes:
    """HTML печатной формы → PDF A4 через Chromium."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PdfUnavailable("pip install playwright && playwright install chromium") from exc

    css = finders.find(FONT_CSS)
    if css:
        root = Path(css).resolve().parents[3]          # …/static/
        html = html.replace('href="/static/', f'href="{root.as_uri()}/')

    with tempfile.TemporaryDirectory() as tmp:
        page_file = Path(tmp) / "report.html"
        page_file.write_text(html, encoding="utf-8")
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:              # пакет есть, а браузер не скачан
                if "Executable doesn't exist" in str(exc):
                    raise PdfUnavailable("выполните в окружении проекта: playwright install chromium") from exc
                raise
            try:
                page = browser.new_page()
                page.goto(page_file.as_uri(), wait_until="networkidle")
                return page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
            finally:
                browser.close()


def day_pdf(report: dict) -> bytes:
    return html_to_pdf(day_html(report, pdf=True))


# ---------------------------------------------------------------- Excel

def day_book(report: dict) -> Book:
    on = report["date"]
    book = Book("Движение денег за день",
                "Остаток на начало, поступления и выплаты по статьям и контрагентам, остаток на конец",
                f"Российский рубль (RUB) · {on:%d.%m.%Y} · {report['scope']}")
    book.kpi("Остаток на начало", report["opening"], f"{on:%d.%m.%Y}")
    book.kpi("Поступления", report["inflow"].total, "за день")
    book.kpi("Выплаты", report["outflow"].total, "за день")
    book.kpi("Остаток на конец", report["closing"], f"{on:%d.%m.%Y}")

    rows = [Row(["", "ОСТАТОК НА НАЧАЛО ДНЯ", "", None, report["opening"]], level="band")]
    for sec in (report["inflow"], report["outflow"], report["intra"]):
        if not sec.articles:
            continue
        rows.append(Row(["", sec.title.upper(), "", sum(a.count for a in sec.articles), sec.total], level=1))
        for a in sec.articles:
            rows.append(Row([a.code if a.code != "—" else "", a.name, "", a.count, a.total],
                            level=3, outline=1, indent=2))
            rows += [Row(["", c["name"] + (f" · {c['sub']}" if c["sub"] else ""), c["inn"], c["count"],
                          c["amount"]], outline=2, indent=4) for c in a.rows]
    if round(report["fx"], 2):
        rows.append(Row(["", "Курсовая разница", "", None, report["fx"]], level=2))
    if round(report["openings"], 2):
        rows.append(Row(["", "Ввод остатков", "", None, report["openings"]], level=2))

    book.sheet(
        "За день", f"Движение денег за {on:%d.%m.%Y}",
        subtitle="Статья → контрагенты. Плюсики слева сворачивают контрагентов и статьи",
        description="Остаток на начало, поступления и выплаты по статьям и контрагентам, "
                    "внутригрупповые отдельно, остаток на конец",
    ).table(
        [Col("Код", kind="code", width=10), Col("Статья / контрагент", width=62, indent=True),
         Col("ИНН", kind="code", width=14), Col("Операций", kind="int", width=11),
         Col("Сумма, ₽", kind="money_dec", width=18, total=True)],
        rows,
        total=Row(["", "ОСТАТОК НА КОНЕЦ ДНЯ", "", None, report["closing"]], level="band"),
        freeze_cols=2,
        note=("Начало + движение = конец." if abs(report["check"]) < 1
              else f"Внимание: начало + движение ≠ конец на {money2(report['check'], signed=True)} ₽."),
    )

    book.sheet(
        "Операции", f"Операции за {on:%d.%m.%Y}",
        subtitle="Каждая операция дня: счёт, статья, контрагент, назначение",
        description="Плоская таблица операций дня — для фильтров",
    ).table(
        [Col("Счёт", width=30), Col("Банк", width=24), Col("Валюта", width=8), Col("Деятельность", width=18),
         Col("Направление", width=13), Col("Код", kind="code", width=9), Col("Статья", width=30),
         Col("Подстатья", width=30), Col("Контрагент", width=36), Col("ИНН", kind="code", width=13),
         Col("Назначение платежа", width=60), Col("Сумма, вал.", kind="money_dec", width=16),
         Col("Сумма, ₽", kind="money_dec", width=17, total=True)],
        (Row([o["account_name"], o["bank_name"] or "", o["currency"], o["activity_name"], o["direction_name"],
              o["article_code"] or "", o["article_name"] or "", o["cf_name"] or "", o["cp_name"] or "",
              o["inn"] or "", (o["description"] or "")[:400], o["amount_cur"], o["amount_rub"]])
         for o in day_report.operations(on, report["filters"])),
        autofilter=True,
    )
    return book


# ---------------------------------------------------------------- остатки на дату

def balances_data(on=None, flt: dict | None = None) -> dict:
    """Остатки по счетам на дату (нулевые не берём), сгруппированы по банкам."""
    from collections import OrderedDict

    from django.db.models import Max, Min

    from ..models import CashBalance

    flt = {k: v for k, v in (flt or {}).items() if k in day_report.ACCOUNT_FILTERS and v}
    span = CashBalance.objects.aggregate(lo=Min("date"), hi=Max("date"))
    if not span["hi"]:
        return {"on": None, "groups": [], "total": 0.0, "by_currency": [], "count": 0, "flt": flt}
    on = min(max(on or span["hi"], span["lo"]), span["hi"])

    groups, by_cur, total = OrderedDict(), OrderedDict(), 0.0
    for r in (CashBalance.objects.filter(date=on, **flt).exclude(base_eb__range=(-0.005, 0.005))
              .order_by("bank_name", "currency", "ba_number")):
        g = groups.setdefault(r.bank_name or "Банк не указан", {"bank": r.bank_name or "Банк не указан",
                                                                "rows": [], "total": 0.0})
        g["rows"].append(r)
        g["total"] += r.eb_rub or 0
        total += r.eb_rub or 0
        by_cur[r.currency or "RUB"] = by_cur.get(r.currency or "RUB", 0.0) + (r.base_eb or 0)
    return {"on": on, "lo": span["lo"], "hi": span["hi"], "groups": list(groups.values()), "total": total,
            "by_currency": list(by_cur.items()), "count": sum(len(g["rows"]) for g in groups.values()),
            "flt": flt}


def balances_html(on=None, flt: dict | None = None, request=None, pdf: bool = False) -> str:
    data = balances_data(on, flt)
    scope = " · ".join(data["flt"].values()) or "все счета"
    if not data["on"]:
        ctx = print_context(request, "Остатки", "Остатки денежных средств")
        ctx["groups"] = []
        return render_to_string("dashboard/print_balances.html", ctx, request=request)

    def row(r):
        return {
            "name": r.account_name if r.account_name and "…" not in r.account_name else "",
            "number": r.ba_number, "currency": r.currency or "", "eb": money2(r.base_eb),
            "rate": "" if (r.currency or "RUB") == "RUB" else f"{r.rate:,.4f}".replace(",", " ").replace(".", ","),
            "eb_rub": money2(r.eb_rub), "neg": (r.base_eb or 0) < 0,
            "stale": r.stale, "stmt_to": f"{r.stmt_to:%d.%m.%Y}" if r.stmt_to else "",
        }

    groups = [{"bank": g["bank"], "rows": [row(r) for r in g["rows"]], "total": money2(g["total"])}
              for g in data["groups"]]
    on = data["on"]
    ctx = print_context(request, "Остатки по счетам", f"Остатки денежных средств на {on:%d.%m.%Y}",
                        f"{scope} · счета с ненулевым остатком")
    ctx.update({
        "groups": groups,
        "total": money2(data["total"]),
        "by_currency": [(c, money2(v)) for c, v in data["by_currency"] if c != "RUB" or len(data["by_currency"]) > 1],
        "count": data["count"],
        "has_names": any(r["name"] or r["stale"] for g in groups for r in g["rows"]),
        "on": f"{on:%d.%m.%Y}", "on_iso": on.isoformat(),
        "min_iso": data["lo"].isoformat(), "max_iso": data["hi"].isoformat(),
        "keep": list(data["flt"].items()),
        "pdf": pdf,
    })
    return render_to_string("dashboard/print_balances.html", ctx, request=request)


def balances_pdf(on=None, flt: dict | None = None) -> bytes:
    return html_to_pdf(balances_html(on, flt, pdf=True))


def balances_text(on=None, flt: dict | None = None) -> str:
    """Короткая сводка для сообщения в Telegram (HTML-разметка Telegram)."""
    data = balances_data(on, flt)
    if not data["on"]:
        return "Остатков пока нет — витрины не построены."
    lines = [f"💰 <b>Остатки на {data['on']:%d.%m.%Y}</b>", "",
             f"Всего: <b>{money2(data['total'])} ₽</b>"]
    if len(data["by_currency"]) > 1 or (data["by_currency"] and data["by_currency"][0][0] != "RUB"):
        lines += [f"  {cur}: {money2(v)}" for cur, v in data["by_currency"]]
    lines.append("")
    for g in data["groups"]:
        lines.append(f"🏦 {g['bank']}: <b>{money2(g['total'])} ₽</b>")
        stale = [r for r in g["rows"] if r.stale]
        if stale:
            lines.append(f"   ⚠️ без свежей выписки: {len(stale)}")
    return "\n".join(lines)


def day_text(report: dict) -> str:
    """Короткая сводка движения за день для Telegram."""
    on = report["date"]
    if report["empty"]:
        return f"📋 <b>{on:%d.%m.%Y}</b>\n\nДвижения за день нет."
    lines = [f"📋 <b>Движение денег за {on:%d.%m.%Y}</b>", "",
             f"Остаток на начало: <b>{money2(report['opening'])} ₽</b>",
             f"➕ Поступления: <b>{money2(report['inflow'].total)}</b>",
             f"➖ Выплаты: <b>{money2(report['outflow'].total)}</b>"]
    if report["intra"].articles:
        lines.append(f"🔁 Внутригрупповые: {money2(report['intra'].total, signed=True)}")
    if round(report["fx"], 2):
        lines.append(f"💱 Курсовая: {money2(report['fx'], signed=True)}")
    lines.append(f"Остаток на конец: <b>{money2(report['closing'])} ₽</b>")
    top = sorted(report["outflow"].articles, key=lambda a: a.total)[:5]
    if top:
        lines += ["", "<b>Крупные выплаты по статьям:</b>"]
        lines += [f"• {a.name}: {money2(a.total)}" for a in top]
    return "\n".join(lines)
