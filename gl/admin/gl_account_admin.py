"""План счетов: список с остатками, субсчета — в карточке счёта."""

from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from unfold.contrib.filters.admin import ChoicesDropdownFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from core.admins.badges import ChoiceBadge
from core.admins.base_admin import AppModelAdmin, AppTabularInline
from core.admins.fields import EMPTY
from core.admins.sections import ChildrenSection

from ..models.gl_account_model import GLAccount, Nature, Section
from ..services.chart import seed_chart, sync_bank_accounts
from ..services.opening import create_opening_entries

ZERO = Value(0, output_field=DecimalField(max_digits=18, decimal_places=2))

SECTION_BADGES = {
    Section.ASSETS: ("account_balance_wallet", "primary"),
    Section.LIABILITIES: ("credit_card", "warning"),
    Section.EQUITY: ("savings", "info"),
}


def money(value) -> str:
    return f"{value or 0:,.0f}".replace(",", " ")


class SubAccountsSection(ChildrenSection):
    """Субсчета — раскрываются прямо в плане счетов, с сальдо."""

    verbose_name = "Субсчета"
    empty_text = "Субсчетов нет — проводки идут на сам счёт"
    fields = ["code", "name", "nature", "currency", "bank", "balance"]

    def rows(self, instance):
        return (
            instance.children.select_related("currency", "bank_account")
            .annotate(balance=Coalesce(Sum("lines__dt"), ZERO) - Coalesce(Sum("lines__cr"), ZERO))
            .order_by("code")
        )

    @display(description="Код")
    def code(self, obj):
        return self.change_link(obj, obj.code)

    @display(description="Субсчёт")
    def name(self, obj):
        return self.change_link(obj, obj.name)

    @display(description="Характер")
    def nature(self, obj):
        return obj.get_nature_display()

    @display(description="Валюта")
    def currency(self, obj):
        return obj.currency.code if obj.currency_id else ""

    @display(description="Банковский счёт")
    def bank(self, obj):
        return obj.bank_account.number if obj.bank_account_id else ""

    @display(description="Сальдо, ₽")
    def balance(self, obj):
        if not obj.balance:
            return EMPTY
        return format_html('<span class="pk-money">{}</span>', money(obj.balance))


class SubAccountInline(AppTabularInline):
    model = GLAccount
    fk_name = "parent"
    fields = ["number", "name", "code", "nature", "currency", "bank_account", "is_active"]
    readonly_fields = ["code"]
    verbose_name = "Субсчёт"
    verbose_name_plural = "Субсчета"


@admin.register(GLAccount)
class GLAccountAdmin(AppModelAdmin):
    list_display = [
        "code",
        "name_display",
        "section_display",
        "nature",
        "currency",
        "balance_display",
        "is_active",
    ]
    list_display_links = ["code", "name_display"]
    ordering = ["code"]
    list_per_page = 200

    # в списке — только счета; субсчета раскрываются строкой (list_sections),
    # поиск находит счёт и по его субсчетам
    list_sections = [SubAccountsSection]
    search_fields = ["code", "name", "children__code", "children__name", "children__bank_account__number"]
    search_help_text = "Код, название счёта / субсчёта или номер банковского счёта"

    list_filter = [
        ("section", ChoicesDropdownFilter),
        "is_active",
    ]

    readonly_fields = ["code"]
    autocomplete_fields = ["bank_account"]

    fieldsets = (
        (
            "Счёт",
            {
                "fields": (
                    ("section", "parent"),
                    ("number", "code"),
                    "name",
                    ("nature", "currency"),
                    "bank_account",
                    "is_active",
                ),
            },
        ),
        ("Описание", {"classes": ["tab"], "fields": ("description",)}),
    )

    actions_list = ["sync_banks", "make_opening"]

    def get_inlines(self, request, obj):
        if obj is None or obj.parent_id is None:
            return [SubAccountInline]
        return []

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "parent":
            kwargs["queryset"] = GLAccount.objects.filter(parent__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        match = getattr(request, "resolver_match", None)
        if match and match.url_name == "gl_glaccount_changelist":
            qs = qs.filter(parent__isnull=True)
        return (
            qs
            .select_related("parent", "currency")
            .annotate(
                balance=Coalesce(Sum("lines__dt"), ZERO) - Coalesce(Sum("lines__cr"), ZERO),
                children_balance=Coalesce(Sum("children__lines__dt"), ZERO)
                - Coalesce(Sum("children__lines__cr"), ZERO),
            )
        )

    # ------------------------------------------------------------------

    @display(description="Название", ordering="code")
    def name_display(self, obj):
        if obj.is_sub:
            return format_html('<span class="pk-field-sub">↳</span> {}', obj.name)
        return format_html("<strong>{}</strong>", obj.name)

    @display(description="Раздел", ordering="section")
    def section_display(self, obj):
        return ChoiceBadge(obj, "section", SECTION_BADGES).badge

    @display(description="Сальдо, ₽ (Дт − Кт)")
    def balance_display(self, obj):
        value = obj.balance if obj.is_sub or not obj.children_balance else obj.children_balance
        if not value:
            return EMPTY
        return money(value)

    # ------------------------------------------------------------------

    @action(
        description="Счета под банковские счета",
        url_path="sync-banks",
        icon="account_balance",
        variant=ActionVariant.DEFAULT,
    )
    def sync_banks(self, request: HttpRequest):
        new_chart = seed_chart()
        new = sync_bank_accounts()
        messages.success(request, f"Новых счетов плана: {new_chart}, новых банковских субсчетов: {new}")
        return redirect(reverse("admin:gl_glaccount_changelist"))

    @action(
        description="Ввести остатки по первым выпискам",
        url_path="make-opening",
        icon="input",
        variant=ActionVariant.PRIMARY,
    )
    def make_opening(self, request: HttpRequest):
        s = create_opening_entries()
        msg = (
            f"Введено остатков: {s['created']}. Уже были: {s['already']}. "
            f"Нулевой остаток: {s['zero']}."
        )
        if s["no_rate"]:
            messages.warning(request, msg + f" Нет курса ЦБ на дату: {s['no_rate']} — сначала «Импорт курсов».")
        else:
            messages.success(request, msg)
        return redirect(reverse("admin:gl_glaccount_changelist"))
