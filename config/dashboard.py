"""
Главная страница админки (DASHBOARD_CALLBACK Unfold): приветствие, остатки по
месяцам, сводка из витрин, последние обновления, кто заходил, рабочие разделы
и разделы в разработке. Пока витрины не построены — карточки без цифр.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.utils import timezone

from django.db import DatabaseError
from django.db.models import Q, Sum
from django.db.models.functions import Abs
from django.urls import reverse

from config.perms import can_view
from dashboard.admin.common import money


def _cards() -> dict:
    from dashboard.models import CashBalanceDay, CashCheck, CashFlow

    last = CashBalanceDay.objects.order_by("-date").first()
    if last is None:
        return {}

    # --- остатки: сумма на последний день + помесячная линия за 12 месяцев
    month_end = {}
    for day, eb in (CashBalanceDay.objects.filter(date__gte=last.date - timedelta(days=365))
                    .order_by("date").values_list("date", "eb_rub")):
        month_end[day.strftime("%m.%y")] = round(eb or 0)
    spark = json.dumps({
        "labels": list(month_end.keys()),
        "datasets": [{
            "data": list(month_end.values()),
            "borderColor": "var(--color-pk-blue)",
            "backgroundColor": "var(--color-pk-blue)",
            "borderWidth": 2,
            "pointRadius": 0,
            "tension": 0.3,
        }],
    })
    spark_options = json.dumps({
        "responsive": True,
        "maintainAspectRatio": False,
        "plugins": {"legend": {"display": False}, "tooltip": {"enabled": True}},
        "scales": {"x": {"display": False}, "y": {"display": False}},
    })

    # --- остаток на конец месяца, 12 месяцев — столбики в приветствии
    by_month = {}
    for day, eb in (CashBalanceDay.objects.filter(date__gt=last.date - timedelta(days=366))
                    .order_by("date").values_list("date", "eb_rub")):
        by_month[(day.year, day.month)] = eb or 0
    top = max(by_month.values(), default=0) or 1
    months = [{"label": MONTHS[m - 1], "value": v, "text": _mln(v), "pct": max(4, round(100 * v / top)),
               "now": (y, m) == (last.date.year, last.date.month)}
              for (y, m), v in sorted(by_month.items())[-12:]]
    peak = max(months, key=lambda x: x["value"]) if months else None
    low = min(months, key=lambda x: x["value"]) if months else None

    # --- ДДС с начала года
    year = last.date.year
    cf = CashFlow.objects.filter(year=year).exclude(source="OPENING")
    flows = cf.exclude(source="FX")
    agg = cf.aggregate(net=Sum("amount_rub"))
    inflow = flows.filter(amount_rub__gt=0).aggregate(s=Sum("amount_rub"))["s"] or 0
    outflow = flows.filter(amount_rub__lt=0).aggregate(s=Sum("amount_rub"))["s"] or 0
    turnover = flows.annotate(a=Abs("amount_rub")).aggregate(s=Sum("a"))["s"] or 0
    unalloc = flows.filter(activity=9).annotate(a=Abs("amount_rub")).aggregate(s=Sum("a"))["s"] or 0

    # --- сверка
    checked = CashCheck.objects.count()
    off = CashCheck.objects.filter(
        Q(diff_cur__gte=0.01) | Q(diff_cur__lte=-0.01) | Q(gap_count__gt=0)
    ).count()
    gaps = CashCheck.objects.filter(gap_count__gt=0).count()

    return {
        "date": last.date,
        "balance": money(last.eb_rub),
        "stale": last.stale_accounts,
        "stale_rub": money(last.stale_rub),
        "spark": spark,
        "spark_options": spark_options,
        "year": year,
        "net": money(agg["net"], signed=True),
        "net_neg": round(agg["net"] or 0) < 0,
        "inflow": money(inflow),
        "outflow": money(outflow),
        "unalloc_pct": f"{100 * unalloc / turnover:.0f}" if turnover else "0",
        "checked": checked,
        "off": off,
        "gaps": gaps,
        "months": months,
        "now_text": _mln(last.eb_rub),
        "peak": peak,
        "low": low,
        "unalloc": money(unalloc),
    }


MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
STATUS_ICON = {"DONE": "task_alt", "FAILED": "error", "RUNNING": "progress_activity", "PENDING": "schedule",
               "CANCELED": "block", "INACTIVE": "radio_button_unchecked"}


def _mln(value) -> str:
    value = float(value or 0)
    if abs(value) >= 1e6:
        return f"{value / 1e6:.1f}".replace(".", ",") + " млн"
    return f"{value / 1e3:.0f} тыс"


def _updates() -> list[dict]:
    """Последние события: запуски команд и правки в админке, по времени."""
    from core.models.jobs import Jobs

    items = []
    for job in Jobs.objects.exclude(lastrun__isnull=True).order_by("-lastrun")[:5]:
        items.append({"icon": STATUS_ICON.get(job.status, "bolt"), "bad": job.status == "FAILED",
                      "text": job.name, "note": job.get_status_display().lower(), "at": job.lastrun})
    for log in LogEntry.objects.select_related("user", "content_type").order_by("-action_time")[:30]:
        who = log.user.get_full_name() or log.user.get_username()
        verb = {1: "добавил(а)", 2: "изменил(а)", 3: "удалил(а)"}.get(log.action_flag, "изменил(а)")
        items.append({"icon": "edit", "bad": False, "text": f"{who} {verb}: {log.object_repr[:70]}",
                      "note": log.content_type.name if log.content_type else "", "at": log.action_time})
    items.sort(key=lambda x: x["at"], reverse=True)
    seen, out = set(), []
    for item in items:                   # одна и та же правка подряд — одной строкой
        if item["text"] not in seen:
            seen.add(item["text"])
            out.append(item)
    return out[:6]


def _visitors() -> list[dict]:
    """Кто заходил: сайт и Telegram-бот."""
    out = []
    for u in get_user_model().objects.exclude(last_login__isnull=True).order_by("-last_login")[:5]:
        out.append({"name": u.get_full_name() or u.get_username(), "where": "сайт", "icon": "person",
                    "at": u.last_login})
    try:
        from tgbot.models import TgUser

        for t in TgUser.objects.exclude(last_seen__isnull=True).order_by("-last_seen")[:5]:
            out.append({"name": t.first_name or t.username or str(t.tg_id), "where": "Telegram", "icon": "send",
                        "at": t.last_seen})
    except DatabaseError:
        pass
    out.sort(key=lambda x: x["at"], reverse=True)
    return out[:6]


# (иконка, название, описание, url-имя, модель для прав)
SECTIONS = [
    ("receipt", "Банковские выписки", "Загруженные выписки по счетам",
     "admin:treasury_statement_changelist", "treasury.statement"),
    ("list_alt", "Операции", "Строки выписок и их разноска",
     "admin:treasury_bsline_changelist", "treasury.bsline"),
    ("account_balance", "Банковские счета", "Счета, банки, валюты, даты закрытия",
     "admin:treasury_bankaccount_changelist", "treasury.bankaccount"),
    ("groups", "Контрагенты", "Справочник: названия, ИНН, группы",
     "admin:cp_cp_changelist", "cp.cp"),
    ("manage_search", "Контрагенты и статьи", "Кто разнесён на несколько статей",
     "admin:dashboard_cpaudit_changelist", "dashboard.cpaudit"),
    ("person_pin", "Резолверы контрагентов", "Статья по контрагенту — раньше правил КБК и счёта",
     "admin:treasury_cpresolver_changelist", "treasury.cpresolver"),
    ("rule", "Резолверы КБК", "Налоги и платежи в бюджет",
     "admin:treasury_kbkresolver_changelist", "treasury.kbkresolver"),
    ("pin", "Резолверы счетов", "Правила по счетам контрагентов",
     "admin:treasury_baresolver_changelist", "treasury.baresolver"),
    ("sync_alt", "Между своими", "Переводы, конвертации, депозиты",
     "admin:treasury_icresolver_changelist", "treasury.icresolver"),
    ("account_tree", "Статьи ДДС", "Справочник статей и подстатей",
     "admin:treasury_cfitem_tree", "treasury.cfitem"),
    ("menu_book", "Главная книга", "План счетов и журнал проводок",
     "admin:gl_journalentry_changelist", "gl.journalentry"),
    ("candlestick_chart", "Курсы валют", "Курсы ЦБ по дням",
     "admin:macro_fx_changelist", "macro.fx"),
    ("wand_shine", "Команды", "Импорт выписок, курсы ЦБ, пересчёт ДДС",
     "admin:core_jobs_changelist", "core.jobs"),
]

LINKS = {
    "cash_flow": ("admin:dashboard_cashflow_changelist", "dashboard.cashflow"),
    "balance": ("admin:dashboard_cashbalanceday_changelist", "dashboard.cashbalanceday"),
    "check": ("admin:dashboard_cashcheck_changelist", "dashboard.cashcheck"),
    "cp_audit": ("admin:dashboard_cpaudit_changelist", "dashboard.cpaudit"),
    "items": ("admin:treasury_cfitem_tree", "treasury.cfitem"),
    "lines": ("admin:treasury_bsline_changelist", "treasury.bsline"),
    "jobs": ("admin:core_jobs_changelist", "core.jobs"),
}


def dashboard_callback(request, context):
    user = request.user
    # нет прав на раздел — ссылки нет, плитка и карточка не показываются
    links = {key: reverse(url) if can_view(user, model) else None for key, (url, model) in LINKS.items()}
    sections = [(icon, title, sub, reverse(url)) for icon, title, sub, url, model in SECTIONS
                if can_view(user, model)]
    kpi_count = sum(bool(links[k]) for k in ("balance", "cash_flow", "lines", "check"))

    cards = {}
    if kpi_count or links["balance"]:
        try:
            cards = _cards()
        except DatabaseError:  # витрины ещё не построены
            cards = {}

    # события и визиты — только администратору
    updates, visitors = [], []
    if user.is_superuser:
        try:
            updates, visitors = _updates(), _visitors()
        except DatabaseError:
            pass

    context.update({
        "cards": cards,
        "updates": updates,
        "visitors": visitors,
        "now": timezone.localtime(),
        "title": "Главная",
        "links": links,
        "kpi_count": kpi_count,
        "show_activity": user.is_superuser,
        # в разработке: (иконка, название, описание, метка)
        "roadmap": [
            ("point_of_sale", "Реализация", "Продажи по салонам, интернет-магазину и маркетплейсам: выручка, маржа, "
             "средний чек", "в разработке"),
            ("grid_view", "Матрица", "Ассортиментная матрица: что держать в наличии, ABC/XYZ по брендам и категориям",
             "в разработке"),
            ("warehouse", "Склады P&L", "Доходы и затраты каждого склада: хранение, логистика, оборачиваемость",
             "скоро"),
            ("donut_small", "Сегментный анализ", "Бренды, категории и каналы: кто приносит деньги, а кто съедает "
             "маржу", "в разработке"),
            ("trending_up", "Прогноз", "Деньги и выручка на 6 месяцев вперёд с учётом сезонности и платежей",
             "в разработке"),
        ],
        "tips": [
            ("ads_click", "Клик по сумме", "в отчёте ДДС открывает операции и контрагентов за период"),
            ("add_box", "Плюсик у месяца", "раскрывает дни — в отчёте и в Excel"),
            ("send", "Telegram-бот", "остатки, движение за день, ДДС и курсы — в телефоне"),
            ("download", "Excel и PDF", "кнопки в шапке каждого отчёта; печать — для бухгалтера"),
        ],
        "sections": sections,
    })
    return context
