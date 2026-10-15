"""Админка банковских выписок (полный список по всем счетам)."""

from __future__ import annotations

from django.contrib import admin
from django.utils.safestring import mark_safe

from unfold.contrib.filters.admin import DropdownFilter, RangeDateFilter
from unfold.decorators import display

from core.admins.base_admin import AppModelAdmin

from ..models.statement_model import Statement


def _short(number: str | None) -> str:
    return f"...{number[-6:]}" if number else "—"


class BankFilter(DropdownFilter):
    title = "Банк"
    parameter_name = "bank"

    def lookups(self, request, model_admin):
        rows = (Statement.objects.filter(ba_account__bank__isnull=False)
                .values_list("ba_account__bank_id", "ba_account__bank__name").distinct())
        return sorted({(str(pk), name or "—") for pk, name in rows}, key=lambda r: r[1])

    def queryset(self, request, queryset):
        return queryset.filter(ba_account__bank_id=self.value()) if self.value() else queryset


class AccountFilter(DropdownFilter):
    title = "Счёт"
    parameter_name = "acc"

    def lookups(self, request, model_admin):
        rows = (Statement.objects.filter(ba_account__isnull=False)
                .values_list("ba_account_id", "ba_account__number", "ba_account__bank__name").distinct())
        items = {(str(pk), f"{_short(num)} · {bank or '—'}") for pk, num, bank in rows}
        return sorted(items, key=lambda r: r[1])

    def queryset(self, request, queryset):
        return queryset.filter(ba_account_id=self.value()) if self.value() else queryset


class CurrencyFilter(DropdownFilter):
    title = "Валюта"
    parameter_name = "cur"

    def lookups(self, request, model_admin):
        from macro.models import Fx

        ids = (Statement.objects.filter(ba_account__currency__isnull=False)
               .values_list("ba_account__currency_id", flat=True).distinct())
        return sorted(((str(fx.pk), str(fx)) for fx in Fx.objects.filter(pk__in=list(ids))),
                      key=lambda r: r[1])

    def queryset(self, request, queryset):
        return queryset.filter(ba_account__currency_id=self.value()) if self.value() else queryset


@admin.register(Statement)
class SatatemnetAdmin(AppModelAdmin):
    list_display = [
        "ba_account_display",
        "bank_display",
        "currency_display",
        "date_from",
        "date_to",
        "bb",
        "eb",
        "dt",
        "cr",
    ]

    ordering = ["-date_to"]

    list_filter = [BankFilter, AccountFilter, CurrencyFilter, ("date_to", RangeDateFilter)]
    list_filter_submit = True

    search_fields = [
        "ba_number",
        "ba_account__number",
        "ba_account__gr__name",
        "ba_account__bank__name",
    ]

    search_help_text = "Номер счёта, владелец или банк"

    autocomplete_fields = ["ba_account"]

    @display(description="Счёт", ordering="ba_account__number")
    def ba_account_display(self, obj: Statement):
        if not obj.ba_account:
            return obj.ba_number or mark_safe(
                '<span class="pk-field-sub">—</span>'
            )

        owner = obj.ba_account.gr.name if obj.ba_account.gr else None
        number = f"...{obj.ba_account.number[-6:]}"

        if owner:
            return f"{number} · {owner}"

        return number

    @display(description="Банк", ordering="ba_account__bank__name")
    def bank_display(self, obj: Statement):
        bank = obj.ba_account.bank if obj.ba_account else None

        if not bank:
            return mark_safe('<span class="pk-field-sub">—</span>')

        return bank.name or bank.inn

    @display(description="Валюта", ordering="ba_account__currency__code")
    def currency_display(self, obj: Statement):
        currency = obj.ba_account.currency if obj.ba_account else None

        if not currency:
            return mark_safe('<span class="pk-field-sub">—</span>')

        return str(currency)

    def get_deleted_objects(self, objs, request):
        """
        Строки выписки и разноска в админке только для чтения (удалять по одной
        нельзя), но вместе с выпиской удалять их можно — не требуем на них прав.
        """
        from ..models.bs_line_alloc_model import BSLineAlloc
        from ..models.bs_line_model import BSLine

        deleted, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        skip = {BSLine._meta.verbose_name, BSLineAlloc._meta.verbose_name}
        return deleted, model_count, perms_needed - skip, protected
