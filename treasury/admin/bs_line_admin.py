"""Админка операций по выпискам: строки только на просмотр, разноска — инлайном."""

from __future__ import annotations

import threading
from urllib.parse import urlencode

from django.contrib import admin
from django.db.models import Count, Prefetch
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    DropdownFilter,
    FieldTextFilter,
    RangeDateFilter,
    RangeNumericFilter,
    RelatedDropdownFilter,
)
from django.shortcuts import get_object_or_404, redirect
from unfold.decorators import action, display

from core.admins.badges import Badge, ChoiceBadge
from core.admins.base_admin import AppModelAdmin, AppTabularInline
from core.admins.fields import EMPTY, Stack

from ..models.bs_line_alloc_model import BSLineAlloc
from ..models.bs_line_model import BSLine
from ..models.cf_item_model import CFItem, Direction


DIRECTION_BADGES = {
    Direction.INFLOW: ("south_west", "success"),
    Direction.OUTFLOW: ("north_east", "danger"),
}


class AllocStatusFilter(admin.SimpleListFilter):
    title = "Разноска"
    parameter_name = "alloc"

    def lookups(self, request, model_admin):
        return [("no", "Не разнесено"), ("yes", "Разнесено")]

    def queryset(self, request, queryset):
        if self.value() == "no":
            return queryset.filter(allocs__isnull=True)
        if self.value() == "yes":
            return queryset.filter(allocs__isnull=False).distinct()
        return queryset


class CfItemFilter(DropdownFilter):
    """Разнесено на статью ДДС (у статьи — вместе с её подстатьями)."""

    title = "Статья ДДС"
    parameter_name = "cf"

    def lookups(self, request, model_admin):
        return [(str(i.pk), str(i)) for i in CFItem.objects.order_by("code")]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        ids = [int(self.value())] + list(
            CFItem.objects.filter(parent_id=self.value()).values_list("pk", flat=True))
        return queryset.filter(allocs__cf_item_id__in=ids).distinct()


RESOLVER_ADMIN = {"KBK": "kbkresolver", "BA": "baresolver", "IC": "icresolver", "CP": "cpresolver"}


class BSLineAllocInline(AppTabularInline):
    model = BSLineAlloc
    fields = ["direction", "amount", "cf_item", "resolver_rule", "manual"]
    readonly_fields = ["resolver_rule"]
    autocomplete_fields = ["cf_item"]
    verbose_name = "Разноска"
    verbose_name_plural = "Разноска"


@admin.register(BSLine)
class BSLineAdmin(AppModelAdmin):
    _req = threading.local()
    list_display = [
        "op_date",
        "account_display",
        "direction_display",
        "amount",
        "article_display",
        "resolver_display",
        "cp_display",
        "ba_resolver",
        "description_display",
    ]

    list_display_links = ["op_date", "amount"]

    search_fields = ["description", "cp_name", "inn_adjust", "cp_ba", "kbk", "allocs__cf_item__name"]
    search_help_text = "Назначение, контрагент, ИНН, счёт, КБК или статья ДДС. Сумма и даты — в фильтрах"

    actions_detail = ["cp_resolver_detail"]
    date_hierarchy = "op_date"          # год → месяц → день над таблицей

    list_filter = [
        ("op_date", RangeDateFilter),
        ("amount", RangeNumericFilter),
        ("direction", ChoicesDropdownFilter),
        ("ba_resolver", FieldTextFilter),
        ("ba_account", RelatedDropdownFilter),
        AllocStatusFilter,
        CfItemFilter,
        "intercompany",
        "fee_withheld",
        "vat_check",
    ]

    inlines = [BSLineAllocInline]

    fieldsets = (
        (
            "Операция",
            {
                "fields": (
                    ("op_date", "direction"),
                    ("amount", "dt", "cr"),
                    ("ba_account", "statement"),
                    ("doc_type", "doc_number", "doc_date"),
                    "intercompany",
                    "description",
                ),
            },
        ),
        (
            "Контрагент",
            {
                "classes": ["tab"],
                "fields": (
                    "cp_name",
                    ("inn", "inn_adjust"),
                    ("cp_ba", "cp_bic"),
                ),
            },
        ),
        (
            "Признаки",
            {
                "classes": ["tab"],
                "fields": (
                    ("ba_resolver", "kbk", "vo_code"),
                    "desc_pattern",
                ),
            },
        ),
        (
            "Комиссия и НДС",
            {
                "classes": ["tab"],
                "fields": (
                    ("gross_dt", "fee_cr", "fee_withheld", "debt_cr"),
                    ("vat_rate", "vat_amount", "vat_free", "vat_check"),
                ),
            },
        ),
        (
            "Служебное",
            {
                "classes": ["tab"],
                "fields": ("rr_id", "imported_at"),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        # строка выписки не редактируется — меняется только разноска
        return [f.name for f in BSLine._meta.fields]

    def save_formset(self, request, form, formset, change):
        # любая правка разноски руками фиксирует её: правила её больше не трогают
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for obj in instances:
            obj.manual = True
            obj.resolver_rule = None
            obj.save()
        formset.save_m2m()

        from dashboard.services.marts import schedule_cp_audit

        schedule_cp_audit()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("ba_account", "ba_account__bank")
            .annotate(alloc_count=Count("allocs"))
            .prefetch_related(Prefetch(
                "allocs",
                queryset=BSLineAlloc.objects.select_related("cf_item", "cf_item__parent", "resolver_rule__resolver"),
            ))
        )

    # ------------------------------------------------------------------

    @display(description="Счёт", ordering="ba_account__number")
    def account_display(self, obj: BSLine):
        if not obj.ba_account:
            return EMPTY
        bank = obj.ba_account.bank
        return Stack(
            f"…{obj.ba_account.number[-6:]}",
            (bank.name if bank else None),
        ).html

    @display(description="Направление", ordering="direction")
    def direction_display(self, obj: BSLine):
        return ChoiceBadge(obj, "direction", DIRECTION_BADGES).badge

    @display(description="Контрагент", ordering="cp_name")
    def cp_display(self, obj: BSLine):
        return Stack(
            (obj.cp_name or "")[:60] or None,
            obj.inn_adjust,
        ).html

    @display(description="Назначение")
    def description_display(self, obj: BSLine):
        text = obj.description or ""
        return Stack(text[:70] + ("…" if len(text) > 70 else "")).html if text else EMPTY

    @display(description="Статья ДДС", ordering="alloc_count")
    def article_display(self, obj: BSLine):
        """Статья → подстатья, куда разнесена строка (по каждой разноске)."""
        if not obj.alloc_count:
            return Badge("Не разнесено", "help", "warning").badge
        rows = []
        for a in obj.allocs.all():
            item = a.cf_item
            if item is None:
                rows.append(("", "без статьи"))
                continue
            parent = item.parent.name if item.parent_id and item.parent else ""
            rows.append((parent, f"{item.code} {item.name}"))
        return format_html_join("", '<div class="pk-alloc-row" style="min-width:210px">'
                                    '<div class="pk-mini-note">{}</div><div>{}</div></div>', rows)

    @display(description="Как разнесено")
    def resolver_display(self, obj: BSLine):
        parts = []
        for a in obj.allocs.all():
            res = a.resolver_rule.resolver if a.resolver_rule_id and a.resolver_rule else None
            if res:
                url = reverse(f"admin:treasury_{RESOLVER_ADMIN.get(res.kind, 'resolver')}_change",
                              args=[res.pk])
                parts.append(format_html('<a href="{}" target="_blank" rel="noopener" class="pk-cf-link" '
                                         'title="Открыть резолвер в новой вкладке">{} ↗</a>', url, res.name or res.key))
            else:
                parts.append("руками" if a.manual else "—")
        has_cp = any(a.resolver_rule_id and a.resolver_rule and a.resolver_rule.resolver.kind == "CP"
                     for a in obj.allocs.all())
        if not obj.intercompany and not has_cp and (obj.inn_adjust or obj.cp_name):
            # резолвер контрагента по этой строке: откроет существующий или форму нового
            url = self._pin_url(obj, getattr(self._req, "path", ""))
            parts.append(format_html('<a href="{}" class="pk-cf-link" title="Резолвер контрагента '
                                     '{} — {}">＋ резолвер</a>', url, obj.inn_adjust or "без ИНН",
                                     obj.get_direction_display().lower()))
        if not parts:
            return EMPTY
        return format_html_join("", '<div class="pk-mini-note">{}</div>', ((p,) for p in parts))

    @staticmethod
    def _pin_url(obj: BSLine, back: str = "") -> str:
        """Резолвер контрагента по строке: существующий или форма нового (ИНН, название, направление)."""
        query = {"inn": obj.inn_adjust or "", "name": (obj.cp_name or "")[:200], "dir": obj.direction}
        if back:
            query["next"] = back
        return reverse("admin:treasury_cpresolver_pin") + "?" + urlencode(query)

    @action(description="Резолвер контрагента", url_path="cp-resolver", icon="rule")
    def cp_resolver_detail(self, request, object_id):
        obj = get_object_or_404(BSLine, pk=object_id)
        if obj.intercompany or not (obj.inn_adjust or obj.cp_name):
            self.message_user(request, "У перевода между своими счетами или строки без контрагента "
                                       "резолвера контрагента нет", level="warning")
            return redirect(reverse("admin:treasury_bsline_change", args=[object_id]))
        return redirect(self._pin_url(obj, reverse("admin:treasury_bsline_change", args=[object_id])))

    def changelist_view(self, request, extra_context=None):
        self._req.path = request.get_full_path()      # куда вернуться после резолвера
        return super().changelist_view(request, extra_context)

    @display(description="Разноска · резолвер", ordering="alloc_count")
    def alloc_display(self, obj: BSLine):
        if not obj.alloc_count:
            return Badge("Нет", "help", "gray").badge
        parts = []
        for a in obj.allocs.all():
            item = f"{a.cf_item.code} {a.cf_item.name}" if a.cf_item else "без статьи"
            res = a.resolver_rule.resolver if a.resolver_rule_id and a.resolver_rule else None
            if res:
                url = reverse(f"admin:treasury_{RESOLVER_ADMIN.get(res.kind, 'resolver')}_change",
                              args=[res.pk])
                how = format_html('<a href="{}" target="_blank" rel="noopener" class="pk-cf-link" '
                                  'title="Открыть резолвер в новой вкладке">{} ↗</a>',
                                  url, res.name or res.key)
            else:
                how = "руками" if a.manual else "—"
            parts.append((item, how))
        return format_html_join("", '<div class="pk-alloc-row"><div>{}</div>'
                                    '<div class="pk-mini-note">{}</div></div>', parts)
