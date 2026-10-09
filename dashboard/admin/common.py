"""
Общее для админок дашборда: фильтр-выпадашка по уникальным значениям поля,
формат денег без копеек (отрицательные — в скобках), базовый read-only класс
с шапкой над списком и выгрузкой списка в CSV / Excel.
"""

from __future__ import annotations

import json

from django.db import DatabaseError, connection
from django.template.response import TemplateResponse
from django.utils.html import format_html
from unfold.contrib.filters.admin import DropdownFilter

from core.admins.base_admin import AppModelAdmin
from core.reports.http import csv_response, xlsx_response

EXPORT_PARAM = "export"


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
    """1234567.89 → «1 234 568» (без копеек); отрицательное — в скобках: «(1 234 568)»."""
    if value is None:
        return "—"
    text = f"{abs(value):,.0f}".replace(",", "\u00a0")
    if round(value) < 0:
        return f"({text})"
    if signed and round(value) > 0:
        return "+" + text
    return text


def money2(value, signed: bool = False) -> str:
    """С копейками — для печатных форм: «1 234 567,89», отрицательное в скобках."""
    if value is None:
        return "—"
    text = f"{abs(value):,.2f}".replace(",", "\u00a0").replace(".", ",")
    if round(value, 2) < 0:
        return f"({text})"
    if signed and round(value, 2) > 0:
        return "+" + text
    return text


def print_context(request, kicker: str, title: str, sub: str = "") -> dict:
    """Общая шапка печатных форм (dashboard/print_base.html)."""
    from django.conf import settings
    from django.utils import timezone

    return {
        "site_title": getattr(settings, "UNFOLD", {}).get("SITE_TITLE", ""),
        "kicker": kicker,
        "doc_title": title,
        "doc_sub": sub,
        "now": timezone.localtime(),
    }


def money_cell(value, signed: bool = False):
    """Сумма в ячейке: вправо, без копеек, отрицательная — в скобках и красным."""
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

    def export_book(self, request, queryset):
        """Книга Excel по отфильтрованному списку (core.reports.xlsx.Book). Переопределяется."""
        return None

    def export_csv(self, request, queryset):
        """(заголовки, строки) для CSV по отфильтрованному списку. Переопределяется."""
        return None

    def print_links(self, request) -> list[tuple[str, str]]:
        """Кнопки печатных форм в шапке: [(подпись, адрес)]. Открываются в новой вкладке."""
        return []

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

        # ?export=csv|xlsx — тот же список с теми же фильтрами, но файлом
        export = request.GET.get(EXPORT_PARAM)
        if export:
            request.GET = request.GET.copy()
            request.GET.pop(EXPORT_PARAM)

        response = super().changelist_view(request, extra_context)
        ctx = getattr(response, "context_data", None)
        if not ctx or "cl" not in ctx:
            return response

        name = str(self.model._meta.verbose_name_plural)
        if export == "csv":
            data = self.export_csv(request, ctx["cl"].queryset)
            if data:
                return csv_response(data[0], data[1], name)
        elif export == "xlsx":
            book = self.export_book(request, ctx["cl"].queryset)
            if book:
                return xlsx_response(book, name)

        dash = self.header(request, ctx["cl"].queryset)
        if dash:
            query = request.GET.copy()
            query.pop("p", None)
            prefix = "?" + query.urlencode() + ("&" if query else "")
            exports = []
            if type(self).export_book is not DashboardAdmin.export_book:
                exports.append(("Excel", f"{prefix}{EXPORT_PARAM}=xlsx"))
            if type(self).export_csv is not DashboardAdmin.export_csv:
                exports.append(("CSV", f"{prefix}{EXPORT_PARAM}=csv"))
            dash["exports"] = exports
            dash["prints"] = self.print_links(request)
        ctx["dash"] = dash
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
