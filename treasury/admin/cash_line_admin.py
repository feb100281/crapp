"""Операции по кассе: строки карточки 50 только на просмотр, разноска — инлайном (как у выписок)."""

from __future__ import annotations

from django.contrib import admin
from django.db import DatabaseError
from django.db.models import Count, Prefetch
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    DropdownFilter,
    RangeDateFilter,
    RangeNumericFilter,
)
from unfold.decorators import display

from core.admins.badges import Badge, ChoiceBadge
from core.admins.base_admin import AppModelAdmin, AppTabularInline
from core.admins.fields import EMPTY, Stack

from ..models.cash_line_alloc_model import CashLineAlloc
from ..models.cash_line_model import CashLine
from ..models.cf_item_model import Direction

DIRECTION_BADGES = {
    Direction.INFLOW: ("south_west", "success"),
    Direction.OUTFLOW: ("north_east", "danger"),
}


class CorrAccountFilter(DropdownFilter):
    title = "Корр. счёт"
    parameter_name = "corr"

    def lookups(self, request, model_admin):
        try:
            values = (CashLine.objects.order_by("corr_account")
                      .values_list("corr_account", flat=True).distinct())
            return [(v, v) for v in values]
        except DatabaseError:
            return []

    def queryset(self, request, queryset):
        return queryset.filter(corr_account=self.value()) if self.value() else queryset


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


class CashLineAllocInline(AppTabularInline):
    model = CashLineAlloc
    fields = ["direction", "amount", "cf_item", "resolver_rule", "manual"]
    readonly_fields = ["resolver_rule"]
    autocomplete_fields = ["cf_item"]
    verbose_name = "Разноска"
    verbose_name_plural = "Разноска"


def _diff_html(diffs) -> str:
    return format_html_join("; ", "{}: «{}» → «{}»", diffs)


def not_in_1c_hint(request) -> None:
    """Предупреждение на экранах: что исправили или удалили в 1С с прошлой выгрузки — было → стало."""
    from django.contrib import messages

    from ..services.cash_lines import changes_in_1c

    if request.GET.get("in_1c__exact") == "0":
        return
    changes = changes_in_1c()
    if not changes:
        return
    edited = [c for c in changes if c["new"]]
    deleted = [c for c in changes if not c["new"]]
    url = reverse("admin:treasury_cashline_changelist") + "?in_1c__exact=0"
    head = []
    if edited:
        head.append(f"исправлено {len(edited)}")
    if deleted:
        head.append(f"удалено {len(deleted)}")
    items = format_html_join("", "<li><b>{} от {}</b>, {} ₽ — {}</li>", (
        (c["old"].doc_number, f"{c['old'].date:%d.%m.%Y}", f"{c['old'].amount:,.0f}".replace(",", "\u00a0"),
         _diff_html(c["diffs"]) if c["new"] and c["diffs"] else
         ("без изменений в полях карточки" if c["new"] else "удалён в 1С"))
        for c in changes[:5]))
    more = format_html(" … и ещё {}", len(changes) - 5) if len(changes) > 5 else ""
    # свёрнуто по умолчанию: строка-заголовок, список по клику
    messages.warning(request, format_html(
        '<details class="cx-changes"><summary style="cursor:pointer">'
        "Касса: с прошлой выгрузки 1С {}. Старые версии в ДДС не входят. "
        '<span style="text-decoration:underline">Показать</span></summary>'
        '<ul style="margin:6px 0 0 18px;list-style:disc">{}</ul>{} '
        '<a href="{}" class="pk-cf-link">Все изменения →</a></details>',
        ", ".join(head), items, more, url))


@admin.register(CashLine)
class CashLineAdmin(AppModelAdmin):
    list_display = [
        "date",
        "doc_display",
        "direction_display",
        "amount",
        "corr_account",
        "article_display",
        "how_display",
        "counterparty_display",
        "cf_1c_display",
        "in_1c_display",
    ]
    list_display_links = ["date", "amount"]
    date_hierarchy = "date"

    search_fields = ["doc_comment", "counterparty", "cf_item_1c", "doc_number", "subconto3"]
    search_help_text = "Комментарий, контрагент, статья 1С, номер документа"

    list_filter = [
        ("date", RangeDateFilter),
        ("amount", RangeNumericFilter),
        ("direction", ChoicesDropdownFilter),
        CorrAccountFilter,
        AllocStatusFilter,
        "in_1c",
    ]

    inlines = [CashLineAllocInline]

    def changelist_view(self, request, extra_context=None):
        not_in_1c_hint(request)
        return super().changelist_view(request, extra_context)

    def get_list_display(self, request):
        cols = list(super().get_list_display(request))
        if request.GET.get("in_1c__exact") == "0":          # отбор «нет в 1С» — что изменилось
            cols.insert(cols.index("amount") + 1, "change_display")
        return cols

    @display(description="Что изменилось в 1С")
    def change_display(self, obj):
        from ..services.cash_lines import line_changes

        ch = line_changes(obj)
        if not ch["new"]:
            return Badge("удалён в 1С", None, "danger").badge
        if not ch["diffs"]:
            return "без изменений в полях карточки"
        return format_html_join("", '<div class="pk-mini-note">{}: <s>{}</s> → <b>{}</b></div>', ch["diffs"])

    fieldsets = (
        ("Операция", {"fields": (
            ("date", "direction", "amount"),
            ("doc_type", "doc_number", "doc_datetime"),
            "doc_comment",
            ("corr_account", "division"),
        )}),
        ("Аналитика 1С", {"classes": ["tab"], "fields": (
            "cf_item_1c", "counterparty", "subconto2", "subconto3",
        )}),
        ("Служебное", {"classes": ["tab"], "fields": (
            ("cash_account", "balance_1c"), ("source_file", "in_1c"), ("rr_id", "imported_at"),
        )}),
    )

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in CashLine._meta.fields]

    def save_formset(self, request, form, formset, change):
        # правка разноски руками фиксирует её: правила её больше не трогают
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for obj in instances:
            obj.manual = True
            obj.resolver_rule = None
            obj.save()
        formset.save_m2m()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .annotate(alloc_count=Count("allocs"))
            .prefetch_related(Prefetch(
                "allocs", queryset=CashLineAlloc.objects.select_related(
                    "cf_item", "cf_item__parent", "resolver_rule__resolver")))
        )

    # ------------------------------------------------------------------

    @display(description="Документ", ordering="doc_datetime")
    def doc_display(self, obj: CashLine):
        sub = obj.doc_comment[:70] + ("…" if len(obj.doc_comment) > 70 else "") if obj.doc_comment else None
        return Stack(f"{obj.doc_type} {obj.doc_number}".strip() or None, sub).html

    @display(description="Направление", ordering="direction")
    def direction_display(self, obj: CashLine):
        return ChoiceBadge(obj, "direction", DIRECTION_BADGES).badge

    @display(description="Контрагент", ordering="counterparty")
    def counterparty_display(self, obj: CashLine):
        name = "" if obj.counterparty == "<...>" else obj.counterparty
        return Stack(name[:60] or None, obj.subconto2[:40] or None).html if name else EMPTY

    @display(description="Статья в 1С", ordering="cf_item_1c")
    def cf_1c_display(self, obj: CashLine):
        return Stack(obj.cf_item_1c[:60]).html if obj.cf_item_1c and obj.cf_item_1c != "<...>" else EMPTY

    @display(description="Статья ДДС", ordering="alloc_count")
    def article_display(self, obj: CashLine):
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
    def how_display(self, obj: CashLine):
        parts = []
        for a in obj.allocs.all():
            res = a.resolver_rule.resolver if a.resolver_rule_id and a.resolver_rule else None
            if res:
                url = reverse("admin:treasury_cashresolver_change", args=[res.pk])
                parts.append(format_html('<a href="{}" target="_blank" rel="noopener" class="pk-cf-link">{} ↗</a>',
                                         url, res.name))
            elif a.manual:
                parts.append("руками")
            else:
                parts.append("авто: сдача в банк" if obj.is_transfer else "авто")
        if not parts:
            return EMPTY
        return format_html_join("", '<div class="pk-mini-note">{}</div>', ((p,) for p in parts))

    @display(description="В 1С", ordering="in_1c")
    def in_1c_display(self, obj: CashLine):
        if obj.in_1c:
            return ""
        return Badge("нет в 1С", "error", "danger",
                     title="Строки нет в последней выгрузке за этот период").badge
