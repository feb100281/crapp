"""Админка банковских счетов собственника (Gr) с табличной секцией выписок."""

from __future__ import annotations

from django.contrib import admin
from django.templatetags.static import static
from django.utils.safestring import mark_safe

from unfold.contrib.filters.admin import ChoicesDropdownFilter
from unfold.decorators import display
from unfold.sections import TableSection

from core.admins.base_admin import AppModelAdmin, AppTabularInline
from core.admins.fields import FirstCol

from ..models.ba_model import BankAccount

# Используем ту же заглушку, что и у компаний — для счёта без
# привязанного банка.
DEFAULT_AVATAR = "img/defaull_company_avatar.svg"


class BankAccountInline(AppTabularInline):
    """Счета компании — редактируются прямо со страницы Gr."""

    model = BankAccount
    fk_name = "gr"
    fields = ["number", "currency", "bank", "ba_type"]
    autocomplete_fields = ["bank"]


class StatementsSection(TableSection):
    """Раскрывающаяся таблица выписок прямо в строке счёта."""

    verbose_name = "Выписки"
    related_name = "statements"
    height = 280
    fields = [
        "date_from",
        "date_to",
        "bb",
        "eb",
        "dt",
        "cr",
    ]


@admin.register(BankAccount)
class BankAccountAdmin(AppModelAdmin):
    list_display = [
        "bank_display",
        "currency",
        "statements_count",
        "ba_type",
        "owner_display",
    ]

    list_display_links = ["bank_display"]

    list_sections = [StatementsSection]

    ordering = ["number"]

    search_fields = [
        "number",
        "bank__name",
        "bank__inn",
        "gr__name",
        "gr__inn",
    ]

    search_help_text = "Номер счёта, банк или владелец"

    list_filter = [("ba_type", ChoicesDropdownFilter)]

    autocomplete_fields = ["gr", "bank"]

    fieldsets = (
        (
            "Счёт",
            {
                "fields": (
                    "number",
                    "gr",
                    ("bank", "currency"),
                    "ba_type",
                ),
            },
        ),
    )

    @display(description="Банк", ordering="bank__name")
    def bank_display(self, obj: BankAccount):
        avatar_url = (
            obj.bank.avatar.url
            if obj.bank and obj.bank.avatar
            else static(DEFAULT_AVATAR)
        )

        bank_name = obj.bank.name or obj.bank.inn if obj.bank else "—"

        return FirstCol(
            bank_name,
            obj.number,
            avatar_url,
        ).avatar_name_subtext

    @display(description="Владелец", ordering="gr__name")
    def owner_display(self, obj: BankAccount):
        if not obj.gr:
            return mark_safe('<span class="pk-field-sub">—</span>')

        return obj.gr.name or obj.gr.inn

    @admin.display(description="Выписок")
    def statements_count(self, obj: BankAccount) -> int:
        return obj.statements.count()
