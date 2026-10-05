"""
Общая основа админки резолверов (КБК и счета).

Список: по строке на ключ + направление, неразобранное — наверху.
Карточка: что ещё не разнесено (шаблоны назначений) + правила
«содержит → статья». Сохранили правила — строки резолвера сразу
переразнеслись, разнесённое исчезает из списка «Не разнесено».
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from unfold.contrib.filters.admin import ChoicesDropdownFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from core.admins.badges import Badge, ChoiceBadge
from core.admins.base_admin import AppModelAdmin, AppTabularInline
from core.admins.fields import Stack

from ..models.cf_item_model import Direction
from ..models.resolver_rule_model import ResolverRule
from ..services.cash_reports import build as build_cash_reports
from ..services.resolver import open_patterns, resolve, resolve_all, sync


DIRECTION_BADGES = {
    Direction.INFLOW: ("south_west", "success"),
    Direction.OUTFLOW: ("north_east", "danger"),
}


def money(value) -> str:
    """Рубли без копеек, тысячи через пробел."""
    return f"{value or 0:,.0f}".replace(",", " ")


class OpenFilter(admin.SimpleListFilter):
    title = "Разнесено"
    parameter_name = "open"

    def lookups(self, request, model_admin):
        return [("yes", "Есть неразнесённое"), ("no", "Всё разнесено")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(open_count__gt=0)
        if self.value() == "no":
            return queryset.filter(open_count=0)
        return queryset


class ResolverRuleInline(AppTabularInline):
    model = ResolverRule
    fk_name = "resolver"
    fields = ["order", "text_regex", "cf_item", "hits", "hits_amount"]
    readonly_fields = ["hits", "hits_amount"]
    autocomplete_fields = ["cf_item"]
    verbose_name = "Правило"
    verbose_name_plural = "Правила: «содержит» → статья (пустое «содержит» = всё остальное)"
    tab = False
    show_change_link = False


class ResolverAdminBase(AppModelAdmin):
    """Наследники задают kind_label, changelist_url_name и при необходимости inline."""

    kind = None
    kind_label = "Резолвер"
    changelist_url_name = ""

    list_display_links = ["key"]

    list_filter = [
        OpenFilter,
        ("direction", ChoicesDropdownFilter),
    ]

    inlines = [ResolverRuleInline]

    actions_list = ["refresh_all", "rebuild_cash_flow"]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("rules__cf_item")

    def has_add_permission(self, request):
        return False

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        resolve(obj)
        obj.refresh_from_db()
        if obj.open_count:
            messages.info(
                request,
                f"{self.kind_label} {obj.key}: не разнесено {obj.open_count} строк "
                f"на {money(obj.open_amount)} ₽",
            )
        else:
            messages.success(request, f"{self.kind_label} {obj.key}: всё разнесено")

    # ------------------------------------------------------------------

    @display(description="Направление", ordering="direction")
    def direction_display(self, obj):
        return ChoiceBadge(obj, "direction", DIRECTION_BADGES).badge

    @display(description="Контрагенты", ordering="payee")
    def payee_display(self, obj):
        return Stack((obj.payee or "")[:80] or None).html

    @display(description="Сумма", ordering="lines_amount")
    def amount_display(self, obj):
        return money(obj.lines_amount)

    @display(description="Не разнесено", ordering="open_amount")
    def open_display(self, obj):
        if not obj.open_count:
            return Badge("Всё", "check", "success").badge
        return Badge(f"{obj.open_count} стр · {money(obj.open_amount)}", "pending", "warning").badge

    @display(description="Статьи")
    def items_display(self, obj):
        names = [str(r.cf_item) for r in obj.rules.all()]
        return Stack(*names).html if names else Badge("нет правил", None, "gray").badge

    @display(description="Не разнесено — шаблоны назначений")
    def open_patterns_display(self, obj):
        limit = 500
        groups = open_patterns(obj, limit=limit + 1)
        if not groups:
            return Badge("Всё разнесено", "check", "success").badge
        rows = format_html_join(
            "",
            "<tr><td class='pk-num'>{}</td><td class='pk-num'>{}</td><td title='{}'>{}</td></tr>",
            ((g["count"], money(g["amount"]), g["example"], g["pattern"]) for g in groups[:limit]),
        )
        note = (
            format_html("<div class='pk-mini-note'>Показаны первые {} шаблонов по сумме</div>", limit)
            if len(groups) > limit else ""
        )
        return format_html(
            "<div class='pk-scroll'><table class='pk-mini-table'>"
            "<thead><tr><th class='pk-num'>Строк</th><th class='pk-num'>Сумма</th>"
            "<th>Шаблон (наведите — пример)</th></tr></thead>"
            "<tbody>{}</tbody></table></div>{}",
            rows, note,
        )

    # ------------------------------------------------------------------

    @action(
        description="Обновить список и переразнести",
        url_path="refresh-all",
        icon="refresh",
        variant=ActionVariant.PRIMARY,
    )
    def refresh_all(self, request: HttpRequest):
        new = sync()
        s = resolve_all(self.kind)
        messages.success(
            request,
            f"Новых резолверов: {new}. Всего: {s['resolvers']}, строк: {s['lines']}, "
            f"не разнесено: {s['open']}.",
        )
        return redirect(reverse(self.changelist_url_name))

    @action(
        description="Пересчитать ДДС (parquet)",
        url_path="rebuild-cash-flow",
        icon="table_chart",
        variant=ActionVariant.DEFAULT,
    )
    def rebuild_cash_flow(self, request: HttpRequest):
        log = []
        s = build_cash_reports(log=log.append)
        if s.get("skipped"):
            messages.warning(request, "ДДС не пересчитан: нет курсов ЦБ (запустите «Импорт курсов»)")
        else:
            level = messages.success if not s["off"] else messages.warning
            level(
                request,
                f"ДДС пересчитан: строк {s['rows']}, не разнесено {s['unalloc']} "
                f"на {money(s['unalloc_rub'])} ₽. "
                + ("Остатки сходятся." if not s["off"] else f"Не сходится счетов: {s['off']} — см. лог / cash_flow_check."),
            )
        return redirect(reverse(self.changelist_url_name))
