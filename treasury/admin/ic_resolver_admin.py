"""
Резолверы переводов между своими счетами.

Ключ — валюта нашего счёта / валюта второго нашего счёта (цифровые коды):
810/810 — перевод в рублях, 810/156 — рубли ↔ юани (конвертация) и т.д.
При создании резолвер сразу получает стартовые правила:
  «купли-продажи | конверс | конвертац» → конвертация (410200 / 420200)
  всё остальное → перевод между счетами (410100 / 420100) или конвертация,
  если валюты разные. Дальше правится как обычно.
"""

from __future__ import annotations

from django.contrib import admin

from unfold.decorators import display

from core.admins.fields import Stack

from ..models.resolver_model import IcResolver, ResolverKind
from .resolver_admin_base import ResolverAdminBase


@admin.register(IcResolver)
class IcResolverAdmin(ResolverAdminBase):
    kind = ResolverKind.IC
    kind_label = "Свои"
    changelist_url_name = "admin:treasury_icresolver_changelist"

    list_display = [
        "key",
        "direction_display",
        "name_display",
        "lines_count",
        "amount_display",
        "open_display",
        "items_display",
    ]

    search_fields = ["key", "name", "rules__cf_item__name"]
    search_help_text = "Коды валют, описание или статья"

    readonly_fields = ["key", "direction", "lines_count", "amount_display",
                       "open_display", "open_patterns_display", "alloc_summary_display"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    ("key", "direction"),
                    "name",
                    ("lines_count", "amount_display", "open_display"),
                    "open_patterns_display",
                    "alloc_summary_display",
                    "note",
                ),
            },
        ),
    )

    @display(description="Что это", ordering="name")
    def name_display(self, obj):
        return Stack(obj.name or None).html
