"""Админка справочника статей ДДС."""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from unfold.contrib.filters.admin import ChoicesDropdownFilter
from unfold.decorators import display

from core.admins.badges import ChoiceBadge
from core.admins.base_admin import AppModelAdmin, AppTabularInline
from core.admins.sections import ChildrenSection

from ..models.cf_item_model import Activity, CFItem, Direction


ACTIVITY_BADGES = {
    Activity.OPERATING: ("storefront", "primary"),
    Activity.INVESTING: ("domain", "info"),
    Activity.FINANCING: ("account_balance", "warning"),
    Activity.INTRAGROUP: ("sync_alt", "gray"),
}

DIRECTION_BADGES = {
    Direction.INFLOW: ("south_west", "success"),
    Direction.OUTFLOW: ("north_east", "danger"),
}


class SubItemsSection(ChildrenSection):
    """Подстатьи — раскрываются прямо в списке статей."""

    verbose_name = "Подстатьи"
    empty_text = "Подстатей нет — разносится на саму статью"
    fields = ["code", "name", "description", "is_active"]

    @display(description="Код")
    def code(self, obj):
        return self.change_link(obj, obj.code)

    @display(description="Подстатья")
    def name(self, obj):
        return self.change_link(obj, obj.name)

    @display(description="Описание")
    def description(self, obj):
        return obj.description or ""

    @display(description="Активна")
    def is_active(self, obj):
        return "да" if obj.is_active else "нет"


class CFSubItemInline(AppTabularInline):
    """Подстатьи — заводятся прямо в карточке статьи."""

    model = CFItem
    fk_name = "parent"
    fields = ["number", "name", "code", "is_active"]
    readonly_fields = ["code"]
    verbose_name = "Подстатья"
    verbose_name_plural = "Подстатьи"


@admin.register(CFItem)
class CFItemAdmin(AppModelAdmin):
    list_display = [
        "code",
        "name_display",
        "activity_display",
        "direction_display",
        "children_count",
        "is_active",
    ]

    list_display_links = ["code", "name_display"]

    ordering = ["code"]

    # в списке — только статьи; подстатьи раскрываются строкой (list_sections),
    # поиск находит статью и по её подстатьям
    list_sections = [SubItemsSection]
    search_fields = ["code", "name", "children__code", "children__name"]
    search_help_text = "Код или название статьи / подстатьи"

    list_filter = [
        ("activity", ChoicesDropdownFilter),
        ("direction", ChoicesDropdownFilter),
        "is_active",
    ]

    readonly_fields = ["code"]

    fieldsets = (
        (
            "Статья",
            {
                "fields": (
                    ("activity", "direction"),
                    "parent",
                    ("number", "code"),
                    "name",
                    "is_active",
                ),
            },
        ),
        (
            "Описание",
            {
                "classes": ["tab"],
                "fields": ("description",),
            },
        ),
    )

    def get_inlines(self, request, obj):
        # подстатьи показываем только у статьи верхнего уровня
        if obj is None or obj.parent_id is None:
            return [CFSubItemInline]
        return []

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # родителем может быть только статья верхнего уровня
        if db_field.name == "parent":
            kwargs["queryset"] = CFItem.objects.filter(parent__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("parent")
        match = getattr(request, "resolver_match", None)
        if match and match.url_name == "treasury_cfitem_changelist":
            qs = qs.filter(parent__isnull=True)
        return qs

    # ------------------------------------------------------------------

    @display(description="Название", ordering="code")
    def name_display(self, obj: CFItem):
        if obj.is_sub:
            return format_html('<span class="pk-field-sub">↳</span> {}', obj.name)
        return format_html("<strong>{}</strong>", obj.name)

    @display(description="Деятельность", ordering="activity")
    def activity_display(self, obj: CFItem):
        return ChoiceBadge(obj, "activity", ACTIVITY_BADGES).badge

    @display(description="Направление", ordering="direction")
    def direction_display(self, obj: CFItem):
        return ChoiceBadge(obj, "direction", DIRECTION_BADGES).badge

    @display(description="Подстатей")
    def children_count(self, obj: CFItem) -> int | str:
        if obj.is_sub:
            return "—"
        return obj.children.count()
