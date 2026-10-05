"""
Общее для админок дашборда: фильтр-выпадашка по уникальным значениям поля,
формат денег без копеек, базовый read-only класс с шапкой над списком.
"""

from __future__ import annotations

import json

from django.db import DatabaseError, connection
from django.template.response import TemplateResponse
from django.utils.html import format_html
from unfold.contrib.filters.admin import DropdownFilter

from core.admins.base_admin import AppModelAdmin


def value_filter(field: str, title: str, model=None, apply: bool = True) -> type[DropdownFilter]:
    """
    Выпадашка по distinct-значениям поля витрины (связей ведь нет).

    model — откуда брать значения (по умолчанию модель админки);
    apply=False — фильтр только принимает значение, а применяет его
    сама админка (так в «Остатках по дням» фильтр по банку пересчитывает
    суммы дня, а не отбрасывает дни).
    """

    class _Filter(DropdownFilter):
        parameter_name = field

        def lookups(self, request, model_admin):
            source = model or model_admin.model
            try:
                values = (
                    source.objects.exclude(**{f"{field}__isnull": True})
                    .order_by(field)
                    .values_list(field, flat=True)
                    .distinct()
                )
                return [(str(v), str(v)) for v in values]
            except DatabaseError:  # витрина ещё не построена
                return []

        def queryset(self, request, queryset):
            if apply and self.value():
                return queryset.filter(**{field: self.value()})
            return queryset

    _Filter.title = title
    _Filter.__name__ = f"{field.title().replace('_', '')}Filter"
    return _Filter


def money(value, signed: bool = False) -> str:
    """1234567.89 → «1 234 568» (неразрывные пробелы, без копеек, минус — «−»)."""
    if value is None:
        return "—"
    text = f"{abs(value):,.0f}".replace(",", " ")
    if round(value) < 0:
        return "−" + text
    if signed and round(value) > 0:
        return "+" + text
    return text


def money_cell(value, signed: bool = False):
    """Сумма в ячейке: вправо, без копеек, минус — красным."""
    css = "pk-money pk-money-neg" if round(value or 0) < 0 else "pk-money"
    return format_html('<span class="{}">{}</span>', css, money(value, signed))


def kpi(label: str, value, sub: str = "", signed: bool = False, tone: str | None = None) -> dict:
    """Карточка шапки. tone: None — по знаку, 'plain' — без цвета."""
    if tone is None:
        tone = "neg" if round(value or 0) < 0 else ("pos" if signed and round(value or 0) > 0 else "plain")
    return {"label": label, "value": money(value, signed), "sub": sub, "tone": tone}


def chart(labels: list, datasets: list) -> str:
    """JSON для unfold/components/chart/*.html (Chart.js уже подключён Unfold)."""
    return json.dumps({"labels": labels, "datasets": datasets}, ensure_ascii=False)


class DashboardAdmin(AppModelAdmin):
    """Витрины только читаются: их целиком заменяет DuckDB."""

    list_before_template = "dashboard/header.html"
    show_full_result_count = False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def header(self, request, queryset) -> dict:
        """Контекст шапки: hero, kpis, status, charts. Переопределяется."""
        return {}

    def changelist_view(self, request, extra_context=None):
        # Витрину создаёт job «ДДС и переоценка», не migrate. Пока её нет —
        # понятная заглушка вместо 500.
        table = self.model._meta.db_table
        if table not in connection.introspection.table_names():
            return TemplateResponse(request, "dashboard/not_built.html", {
                **self.admin_site.each_context(request),
                "title": self.model._meta.verbose_name_plural,
                "table": table,
            })

        response = super().changelist_view(request, extra_context)
        ctx = getattr(response, "context_data", None)
        if ctx and "cl" in ctx:
            ctx["dash"] = self.header(request, ctx["cl"].queryset)
        return response


def stale_status(on_date, flt: dict, closing: float | None = None) -> dict:
    """Плашка «выписки устарели / актуальны» на дату (с учётом фильтра счетов)."""
    from ..models import CashBalance

    stale = list(
        CashBalance.objects.filter(date=on_date, stale=True, **flt)
        .order_by("stmt_to")
        .values("account_name", "bank_name", "stmt_to", "eb_rub")
    )
    if not stale:
        return {
            "tone": "ok",
            "icon": "verified",
            "title": f"Выписки актуальны по всем счетам на {on_date:%d.%m.%Y}",
        }

    stale_rub = sum(r["eb_rub"] or 0 for r in stale)
    of_total = f" из {money(closing)} ₽" if closing is not None else ""
    return {
        "tone": "bad",
        "icon": "report",
        "title": f"Выписки устарели по {len(stale)} счетам — "
                 f"{money(stale_rub)} ₽{of_total} взяты по последней выписке",
        "sub": f"На {on_date:%d.%m.%Y}. Загрузите свежие выписки по этим счетам:",
        "rows": [
            (f"{r['account_name']} · {r['bank_name'] or ''}".strip(" ·"),
             f"по {r['stmt_to']:%d.%m.%Y} · {(on_date - r['stmt_to']).days} дн.",
             money(r["eb_rub"]) + " ₽")
            for r in stale
        ],
    }
