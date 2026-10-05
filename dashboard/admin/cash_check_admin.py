"""Сверка ДДС с остатками — витрина dashboard_cash_check (только чтение)."""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Count, Q, Sum
from unfold.decorators import display

from core.admins.badges import Badge

from ..models import CashCheck
from .common import DashboardAdmin, kpi, money, money_cell, value_filter

TOLERANCE = 1  # ₽ — расхождение меньше рубля считаем округлением


@admin.register(CashCheck)
class CashCheckAdmin(DashboardAdmin):
    list_display = [
        "account_name",
        "ba_number",
        "bank_name",
        "opening_display",
        "flows_display",
        "closing_display",
        "diff_display",
        "status_display",
    ]
    list_display_links = None
    ordering = ["-diff"]
    list_per_page = 100

    list_filter = [value_filter("bank_name", "Банк")]

    @display(description="Начало, ₽", ordering="opening_rub")
    def opening_display(self, obj):
        return money_cell(obj.opening_rub)

    @display(description="Движение, ₽", ordering="flows_rub")
    def flows_display(self, obj):
        return money_cell(obj.flows_rub, signed=True)

    @display(description="Конец, ₽", ordering="closing_rub")
    def closing_display(self, obj):
        return money_cell(obj.closing_rub)

    @display(description="Расхождение, ₽", ordering="diff")
    def diff_display(self, obj):
        return money_cell(obj.diff, signed=True)

    @display(description="Статус")
    def status_display(self, obj):
        if abs(obj.diff or 0) < TOLERANCE:
            return Badge("Сходится", "check_circle", "success").badge
        return Badge("Расхождение", "error", "danger").badge

    def header(self, request, queryset):
        agg = queryset.aggregate(
            n=Count("id"),
            off=Count("id", filter=Q(diff__gte=TOLERANCE) | Q(diff__lte=-TOLERANCE)),
            opening=Sum("opening_rub"),
            flows=Sum("flows_rub"),
            closing=Sum("closing_rub"),
            diff=Sum("diff"),
        )
        if not agg["n"]:
            return {}

        ok = agg["off"] == 0
        return {
            "hero": {
                "kicker": "Контроль",
                "title": "Сверка ДДС с остатками",
                "sub": "Остаток на начало + движение по ДДС = остаток на конец, по каждому счёту",
            },
            "status": {
                "tone": "ok" if ok else "bad",
                "icon": "verified" if ok else "report",
                "title": "Всё сходится" if ok else f"Не сходится счетов: {agg['off']} из {agg['n']}",
                "sub": f"Проверено счетов: {agg['n']}" if ok
                else f"Суммарное расхождение {money(agg['diff'], signed=True)} ₽",
            },
            "kpis": [
                kpi("Остаток на начало", agg["opening"], tone="plain"),
                kpi("Движение по ДДС", agg["flows"], signed=True),
                kpi("Остаток на конец", agg["closing"], tone="plain"),
                kpi("Расхождение", agg["diff"], signed=True),
            ],
        }
