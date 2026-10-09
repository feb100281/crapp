"""
Главная страница админки (DASHBOARD_CALLBACK Unfold): карточки-ссылки
на дашборды с живыми цифрами из витрин. Пока витрины не построены
(нет запуска «ДДС и переоценка») — карточки без цифр, со ссылками.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.db import DatabaseError
from django.db.models import Q, Sum
from django.db.models.functions import Abs
from django.urls import reverse

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
    }


def dashboard_callback(request, context):
    try:
        cards = _cards()
    except DatabaseError:  # витрины ещё не построены
        cards = {}

    context.update({
        "cards": cards,
        "links": {
            "cash_flow": reverse("admin:dashboard_cashflow_changelist"),
            "balance": reverse("admin:dashboard_cashbalanceday_changelist"),
            "check": reverse("admin:dashboard_cashcheck_changelist"),
        },
        "sections": [
            ("receipt", "Банковские выписки", "Загруженные выписки по счетам",
             reverse("admin:treasury_statement_changelist")),
            ("list_alt", "Операции", "Строки выписок и их разноска",
             reverse("admin:treasury_bsline_changelist")),
            ("person_pin", "Резолверы контрагентов", "Статья по контрагенту — раньше правил КБК и счёта",
             reverse("admin:treasury_cpresolver_changelist")),
            ("manage_search", "Контрагенты и статьи", "Кто разнесён на несколько статей",
             reverse("admin:dashboard_cpaudit_changelist")),
            ("rule", "Резолверы КБК", "Налоги и платежи в бюджет",
             reverse("admin:treasury_kbkresolver_changelist")),
            ("pin", "Резолверы счетов", "Правила по счетам контрагентов",
             reverse("admin:treasury_baresolver_changelist")),
            ("sync_alt", "Между своими", "Переводы, конвертации, депозиты",
             reverse("admin:treasury_icresolver_changelist")),
            ("account_tree", "Статьи ДДС", "Справочник статей и подстатей",
             reverse("admin:treasury_cfitem_tree")),
            ("menu_book", "Главная книга", "План счетов и журнал проводок",
             reverse("admin:gl_glaccount_changelist")),
            ("wand_shine", "Команды", "Импорт выписок, курсы ЦБ, пересчёт ДДС",
             reverse("admin:core_jobs_changelist")),
        ],
    })
    return context
