"""
Резолверы кассы: шаблон = название операции из 1С («Оплата покупателя»,
«Возврат денежных средств покупателю», «Залог за товар», «Заработная плата»…)
+ направление. Внутри — правила «содержит» → статья, как у банка.

Шаблоны создаются сами (импорт кассы или кнопка «Обновить список»).
Сдачи в банк (корр. 57.*) здесь нет — она на экране «Касса → банк».
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpRequest
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from unfold.contrib.filters.admin import ChoicesDropdownFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from core.admins.badges import Badge, ChoiceBadge
from core.admins.base_admin import AppModelAdmin
from core.admins.fields import Stack

from ..models.cf_item_model import Direction
from ..models.resolver_model import CashResolver
from ..services import cash_resolver
from .resolver_admin_base import OpenFilter, ResolverRuleInline

DIRECTION_BADGES = {
    Direction.INFLOW: ("south_west", "success"),
    Direction.OUTFLOW: ("north_east", "danger"),
}
PATTERNS_LIMIT = 50


def money(value) -> str:
    return f"{value or 0:,.0f}".replace(",", " ")


@admin.register(CashResolver)
class CashResolverAdmin(AppModelAdmin):
    list_display = ["name_display", "direction_display", "payee_display", "lines_count",
                    "amount_display", "open_display", "items_display"]
    list_display_links = ["name_display"]
    ordering = ["-open_amount", "-lines_amount"]
    search_fields = ["name", "payee", "rules__cf_item__name", "rules__text_regex"]
    search_help_text = "Название операции, корр. счёт, статья или текст правила"
    list_filter = [OpenFilter, ("direction", ChoicesDropdownFilter)]
    inlines = [ResolverRuleInline]
    actions_list = ["refresh_all", "rebuild_cash_flow"]

    readonly_fields = ["name", "direction", "payee", "lines_count", "amount_display",
                       "open_display", "open_patterns_display", "alloc_summary_display"]
    fieldsets = (
        (None, {"fields": (
            ("name", "direction"),
            "payee",
            ("lines_count", "amount_display", "open_display"),
            "open_patterns_display",
            "alloc_summary_display",
            "note",
        )}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("rules__cf_item")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        cash_resolver.resolve(obj)
        obj.refresh_from_db()
        if obj.open_count:
            messages.info(request, f"«{obj.name}»: не разнесено {obj.open_count} строк на "
                                   f"{money(obj.open_amount)} ₽")
        else:
            messages.success(request, f"«{obj.name}»: всё разнесено")

    # ------------------------------------------------------------------

    @display(description="Операция (как в 1С)", ordering="name")
    def name_display(self, obj):
        return Stack(obj.name or obj.key).html

    @display(description="Направление", ordering="direction")
    def direction_display(self, obj):
        return ChoiceBadge(obj, "direction", DIRECTION_BADGES).badge

    @display(description="Корр. счета", ordering="payee")
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
        names = list(dict.fromkeys(str(r.cf_item) for r in obj.rules.all()))
        if not names:
            return Badge("нет правил", None, "gray").badge
        return Stack(*names[:4]).html

    @display(description="Не разнесено — тексты (цифры заменены на #)")
    def open_patterns_display(self, obj):
        if not obj.pk:
            return "Появится после сохранения"
        groups = cash_resolver.open_patterns(obj, limit=PATTERNS_LIMIT + 1)
        if not groups:
            return Badge("Всё разнесено", "check", "success").badge
        for g in groups:
            g["amount_text"] = money(g["amount"])
            g["search"] = f"{g['pattern']} {g['top_cps']}".lower()
        return render_to_string("treasury/cash_open_patterns.html", {
            "groups": groups[:PATTERNS_LIMIT], "limited": len(groups) > PATTERNS_LIMIT,
            "limit": PATTERNS_LIMIT,
        })

    @display(description="Разнесено сейчас — по статьям")
    def alloc_summary_display(self, obj):
        if not obj.pk:
            return "Появится после сохранения"
        rows = cash_resolver.summary(obj)
        if not rows:
            return Badge("Пока ничего не разнесено", None, "gray").badge
        for r in rows:
            r["amount_text"] = money(r["s"])
            r["badge"] = Badge("руками", None, "primary").badge if r["manual"] else \
                Badge("правила этого шаблона", None, "success").badge
        return render_to_string("treasury/alloc_summary.html", {"rows": rows})

    @action(description="Обновить список и переразнести", url_path="refresh-all", icon="refresh",
            variant=ActionVariant.PRIMARY)
    def refresh_all(self, request: HttpRequest):
        new = cash_resolver.sync()
        s = cash_resolver.resolve()
        messages.success(request, f"Новых шаблонов: {new}. Всего: {s['resolvers']}, строк: {s['lines']}, "
                                  f"не разнесено: {s['open']}.")
        return redirect(reverse("admin:treasury_cashresolver_changelist"))

    @action(
        description="Пересчитать ДДС (parquet)",
        url_path="rebuild-cash-flow",
        icon="table_chart",
        variant=ActionVariant.DEFAULT,
    )
    def rebuild_cash_flow(self, request: HttpRequest):
        from ..services.cash_reports import build as build_cash_reports

        s = build_cash_reports(log=lambda *_: None)
        if s.get("skipped"):
            messages.warning(request, "ДДС не пересчитан: нет курсов ЦБ (запустите «Импорт курсов»)")
        else:
            level = messages.success if not s["off"] else messages.warning
            level(request,
                  f"ДДС пересчитан: строк {s['rows']}, не разнесено {s['unalloc']} на {money(s['unalloc_rub'])} ₽. "
                  + ("Остатки сходятся." if not s["off"] else f"Не сходится счетов: {s['off']}."))
        return redirect(reverse("admin:treasury_cashresolver_changelist"))
