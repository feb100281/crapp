"""
Сверка с банком — витрина dashboard_cash_check (только чтение).

Главное: конечный остаток ПОСЛЕДНЕЙ выписки по счёту против нашего расчёта
на ту же дату + пропуски между выписками. Арифметика отчёта ДДС
(начало + ДДС = конец) — справочно, в шапке.
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Count, Q, Sum
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.decorators import display

from core.admins.badges import Badge

from ..models import CashCheck
from .common import DashboardAdmin, kpi, money, money_cell, value_filter

OFF = Q(diff_cur__gte=0.01) | Q(diff_cur__lte=-0.01) | Q(gap_count__gt=0)


class StatusFilter(admin.SimpleListFilter):
    title = "Статус"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return [("off", "Расхождения"), ("ok", "Сходится")]

    def queryset(self, request, queryset):
        if self.value() == "off":
            return queryset.filter(OFF)
        if self.value() == "ok":
            return queryset.exclude(OFF)
        return queryset


@admin.register(CashCheck)
class CashCheckAdmin(DashboardAdmin):
    list_display = [
        "account_name",
        "stmt_display",
        "bank_display",
        "calc_display",
        "diff_display",
        "gaps_display",
        "status_display",
    ]
    list_display_links = None
    ordering = ["id"]
    list_per_page = 100

    list_filter = [StatusFilter, value_filter("bank_name", "Банк"), value_filter("currency", "Валюта")]

    @display(description="Последняя выписка", ordering="stmt_to")
    def stmt_display(self, obj):
        if not obj.stmt_to:
            return "—"
        return format_html('{}<div class="pk-mini-note">выписок: {}</div>',
                           obj.stmt_to.strftime("%d.%m.%Y"), obj.statements or 0)

    @display(description="Остаток по банку", ordering="bank_eb")
    def bank_display(self, obj):
        return format_html('{}<div class="pk-mini-note pk-num">{}</div>',
                           money_cell(obj.bank_eb), obj.currency or "")

    @display(description="Наш расчёт", ordering="calc_eb")
    def calc_display(self, obj):
        return money_cell(obj.calc_eb)

    @display(description="Расхождение", ordering="diff_rub")
    def diff_display(self, obj):
        if abs(obj.diff_cur or 0) < 0.01:
            return mark_safe('<span class="pk-money">0</span>')
        rub = "" if obj.currency == "RUB" else format_html(
            '<div class="pk-mini-note pk-num">{} ₽</div>', money(obj.diff_rub, signed=True))
        return format_html("{}{}", money_cell(obj.diff_cur, signed=True), rub)

    @display(description="Пропуски выписок")
    def gaps_display(self, obj):
        if not obj.gap_count:
            return ""
        return format_html('<span class="pk-chip pk-chip-bad">{}</span>'
                           '<div class="pk-mini-note">движение {} {}</div>',
                           obj.gaps, money(obj.gap_amount, signed=True), obj.currency or "")

    @display(description="Статус")
    def status_display(self, obj):
        if obj.ok:
            return Badge("Сходится с банком", "check_circle", "success").badge
        if obj.gap_count:
            return Badge("Нет выписок за период", "event_busy", "danger").badge
        return Badge("Расхождение", "error", "danger").badge

    def header(self, request, queryset):
        agg = queryset.aggregate(
            n=Count("id"),
            off=Count("id", filter=OFF),
            gaps=Count("id", filter=Q(gap_count__gt=0)),
            diff=Sum("diff_rub"),
            arithmetic=Sum("check_rub"),
        )
        if not agg["n"]:
            return {}

        ok = agg["off"] == 0
        dates = sorted({d for d in queryset.values_list("stmt_to", flat=True) if d})
        span = (f"{dates[0]:%d.%m.%Y} — {dates[-1]:%d.%m.%Y}" if len(dates) > 1
                else (f"{dates[0]:%d.%m.%Y}" if dates else "—"))
        arithmetic_ok = abs(agg["arithmetic"] or 0) < 1

        return {
            "hero": {
                "kicker": "Контроль",
                "title": "Сверка с банком",
                "sub": "Конечный остаток последней выписки по каждому счёту против нашего расчёта "
                       "на ту же дату, плюс пропуски между выписками",
            },
            "status": {
                "tone": "ok" if ok else "bad",
                "icon": "verified" if ok else "report",
                "title": (f"Все {agg['n']} счетов сходятся с банком" if ok
                          else f"Не сходятся с банком {agg['off']} из {agg['n']} счетов"),
                "sub": (f"Пропуски выписок по {agg['gaps']} счетам — запросите выписки за эти периоды."
                        if agg["gaps"] else
                        "Пропусков между выписками нет." if ok else
                        "Пропусков нет — значит, расхождение внутри выписок: проверьте строки и проводки."),
            },
            "kpis": [
                {"label": "Счетов", "value": str(agg["n"]), "sub": f"выписки по {span}", "tone": "plain"},
                {"label": "Расходятся", "value": str(agg["off"]), "sub": "с остатком банка",
                 "tone": "neg" if agg["off"] else "pos"},
                {"label": "С пропусками", "value": str(agg["gaps"]), "sub": "нет выписок за период",
                 "tone": "neg" if agg["gaps"] else "pos"},
                kpi("Расхождение, ₽", agg["diff"], "расчёт − банк", signed=True),
                {"label": "Арифметика ДДС", "value": "ок" if arithmetic_ok else money(agg["arithmetic"]),
                 "sub": "начало + ДДС = конец", "tone": "pos" if arithmetic_ok else "neg"},
            ],
        }
