"""
Резолверы счетов: первые 5 цифр счёта контрагента + направление.

Строки с КБК и переводы между своими счетами сюда не попадают.
Удержанная комиссия / долг раскладываются автоматически в отчёте ДДС.
"""

from __future__ import annotations

from django.contrib import admin

from unfold.decorators import display

from core.admins.fields import Stack

from ..models.resolver_model import BaResolver, ResolverKind
from .resolver_admin_base import ResolverAdminBase


@admin.register(BaResolver)
class BaResolverAdmin(ResolverAdminBase):
    kind = ResolverKind.BA
    kind_label = "Счёт"
    changelist_url_name = "admin:treasury_baresolver_changelist"

    list_display = [
        "key",
        "direction_display",
        "name_display",
        "payee_display",
        "lines_count",
        "amount_display",
        "open_display",
        "items_display",
    ]

    search_fields = ["key", "name", "payee", "rules__cf_item__name"]
    search_help_text = "Цифры счёта, описание, контрагент или статья"

    readonly_fields = ["key", "direction", "payee", "lines_count", "amount_display",
                       "open_display", "open_patterns_display"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    ("key", "direction"),
                    "name",
                    "payee",
                    ("lines_count", "amount_display", "open_display"),
                    "open_patterns_display",
                    "note",
                ),
            },
        ),
    )

    @display(description="Что за счёт", ordering="name")
    def name_display(self, obj):
        return Stack(obj.name or None).html
