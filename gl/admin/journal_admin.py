"""Журнал проводок: шапка + строки. Сохраняется только сбалансированная проводка."""

from __future__ import annotations

from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.forms.models import BaseInlineFormSet

from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateFilter
from unfold.decorators import display

from core.admins.badges import Badge, ChoiceBadge
from core.admins.base_admin import AppModelAdmin, AppTabularInline

from ..models.gl_account_model import GLAccount
from ..models.journal_model import EntryKind, JournalEntry, JournalLine
from ..services.chart import OPENING_ACCOUNT

ZERO = Value(0, output_field=DecimalField(max_digits=18, decimal_places=2))

KIND_BADGES = {
    EntryKind.OPENING: ("input", "info"),
    EntryKind.MANUAL: ("edit", "warning"),
    EntryKind.AUTO: ("bolt", "gray"),
}


def money(value) -> str:
    return f"{value or 0:,.2f}".replace(",", " ")


class BalancedFormSet(BaseInlineFormSet):
    """
    Сумма Дт = сумме Кт, иначе проводку не сохранить.
    Ввод остатков — исключение: достаточно строк по счетам с остатками,
    вторую сторону («Ввод начальных остатков») система добавит сама.
    """

    def clean(self):
        super().clean()
        if getattr(self.instance, "kind", None) == EntryKind.OPENING:
            return
        dt = cr = Decimal(0)
        lines = 0
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            dt += form.cleaned_data.get("dt") or 0
            cr += form.cleaned_data.get("cr") or 0
            lines += 1
        if lines < 2:
            raise ValidationError("В проводке должно быть минимум две строки")
        if dt != cr:
            raise ValidationError(f"Проводка не сбалансирована: Дт {money(dt)} ≠ Кт {money(cr)}")


class JournalLineInline(AppTabularInline):
    model = JournalLine
    formset = BalancedFormSet
    fields = ["account", "dt", "cr", "amount_cur", "rate", "cf_item", "cp", "note"]
    autocomplete_fields = ["account", "cf_item", "cp"]
    extra = 1
    tab = False
    show_change_link = False


@admin.register(JournalEntry)
class JournalEntryAdmin(AppModelAdmin):
    list_display = ["date", "kind_display", "description", "total_display", "accounts_display"]
    list_display_links = ["date", "description"]
    search_fields = ["description", "note", "lines__account__name", "lines__account__code"]
    search_help_text = "Содержание, счёт или его код"

    list_filter = [
        ("date", RangeDateFilter),
        ("kind", ChoicesDropdownFilter),
    ]

    inlines = [JournalLineInline]

    fieldsets = (
        (None, {"fields": (("date", "kind"), "description", "note")}),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        entry = form.instance
        if entry.kind != EntryKind.OPENING:
            return
        # вторая сторона ввода остатков — одной строкой на «Ввод начальных остатков»
        section, number = OPENING_ACCOUNT
        opening = GLAccount.objects.get(section=section, number=number, parent=None)
        entry.lines.filter(account=opening).delete()
        totals = entry.lines.aggregate(dt=Coalesce(Sum("dt"), ZERO), cr=Coalesce(Sum("cr"), ZERO))
        diff = totals["dt"] - totals["cr"]
        if diff:
            JournalLine.objects.create(
                entry=entry,
                account=opening,
                dt=0 if diff > 0 else -diff,
                cr=diff if diff > 0 else 0,
                note="Добавлено автоматически",
            )

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .annotate(total=Coalesce(Sum("lines__dt"), ZERO))
            .prefetch_related("lines__account")
        )

    @display(description="Тип", ordering="kind")
    def kind_display(self, obj):
        return ChoiceBadge(obj, "kind", KIND_BADGES).badge

    @display(description="Сумма, ₽", ordering="total")
    def total_display(self, obj):
        return money(obj.total)

    @display(description="Счета")
    def accounts_display(self, obj):
        dt = sorted({l.account.code for l in obj.lines.all() if l.dt})
        cr = sorted({l.account.code for l in obj.lines.all() if l.cr})
        return f"Дт {', '.join(dt)} · Кт {', '.join(cr)}" if dt or cr else Badge("нет строк", None, "gray").badge
