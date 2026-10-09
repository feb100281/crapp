"""
Резолверы КБК.

Если для контрагента заведён свой резолвер и его правило подошло —
строка разносится им, правила КБК её не трогают.
"""

from __future__ import annotations

from django.contrib import admin

from ..models.resolver_model import KbkResolver, ResolverKind
from .resolver_admin_base import ResolverAdminBase


@admin.register(KbkResolver)
class KbkResolverAdmin(ResolverAdminBase):
    kind = ResolverKind.KBK
    kind_label = "КБК"
    changelist_url_name = "admin:treasury_kbkresolver_changelist"

    list_display = [
        "key",
        "direction_display",
        "payee_display",
        "lines_count",
        "amount_display",
        "open_display",
        "items_display",
    ]

    search_fields = ["key", "payee", "rules__cf_item__name", "rules__text_regex"]
    search_help_text = "КБК, получатель, статья — или назначение из строк"

    readonly_fields = ["key", "direction", "payee", "lines_count", "amount_display",
                       "open_display", "open_patterns_display", "alloc_summary_display"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    ("key", "direction"),
                    "payee",
                    ("lines_count", "amount_display", "open_display"),
                    "open_patterns_display",
                    "alloc_summary_display",
                    "note",
                ),
            },
        ),
    )
