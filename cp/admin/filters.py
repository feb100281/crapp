"""Фильтры списков контрагентов (CP) и компаний группы (Gr)."""

from __future__ import annotations

from datetime import timedelta

from django.contrib import admin
from django.db.models import Q
from django.utils import timezone

from unfold.contrib.filters.admin import (
    DropdownFilter,
    RadioFilter,
    TextFilter,
)

from ..models import CP, Gr

# Границы «свежести» данных, дней
FRESH_DAYS = 60
STALE_DAYS = 180


class CpNameFilter(TextFilter):
    title = "Наименованию"
    parameter_name = "cp_name"

    def queryset(self, request, queryset):
        value = self.value()

        if not value:
            return queryset

        return queryset.filter(name__icontains=value.strip())


class CpInnFilter(TextFilter):
    title = "ИНН / ОГРН"
    parameter_name = "cp_inn"

    def queryset(self, request, queryset):
        value = self.value()

        if not value:
            return queryset

        value = value.strip()

        return queryset.filter(
            Q(inn__startswith=value)
            | Q(ogrn__startswith=value)
        )


class CpManagerFilter(TextFilter):
    title = "Руководителю"
    parameter_name = "cp_manager"

    def queryset(self, request, queryset):
        value = self.value()

        if not value:
            return queryset

        return queryset.filter(
            manager_name__icontains=value.strip()
        )


class _DistinctValueFilter(DropdownFilter):
    """Выпадающий список из значений, которые реально есть в базе."""

    field_name = None
    model = CP

    def lookups(self, request, model_admin):
        values = (
            self.model.objects.exclude(
                **{f"{self.field_name}__isnull": True}
            )
            .exclude(**{self.field_name: ""})
            .order_by(self.field_name)
            .values_list(self.field_name, flat=True)
            .distinct()
        )

        return [(value, value) for value in values]

    def queryset(self, request, queryset):
        value = self.value()

        if not value:
            return queryset

        return queryset.filter(**{self.field_name: value})


class CpCountryFilter(_DistinctValueFilter):
    title = "Страна"
    parameter_name = "cp_country"
    field_name = "country"
    model = CP


class CpRegionFilter(_DistinctValueFilter):
    title = "Регион"
    parameter_name = "cp_region"
    field_name = "region"
    model = CP


class CpFreshnessFilter(RadioFilter):
    title = "Актуальность данных"
    parameter_name = "cp_freshness"

    def lookups(self, request, model_admin):
        return [
            ("fresh", f"Свежие (до {FRESH_DAYS} дн.)"),
            ("stale", f"Требуют обновления ({FRESH_DAYS}–{STALE_DAYS} дн.)"),
            ("old", f"Устаревшие (более {STALE_DAYS} дн.)"),
            ("never", "Никогда не обновлялись"),
        ]

    def queryset(self, request, queryset):
        value = self.value()

        if not value:
            return queryset

        now = timezone.now()

        fresh_edge = now - timedelta(days=FRESH_DAYS)
        stale_edge = now - timedelta(days=STALE_DAYS)

        if value == "fresh":
            return queryset.filter(updated_at__gte=fresh_edge)

        if value == "stale":
            return queryset.filter(
                updated_at__lt=fresh_edge,
                updated_at__gte=stale_edge,
            )

        if value == "old":
            return queryset.filter(updated_at__lt=stale_edge)

        if value == "never":
            return queryset.filter(updated_at__isnull=True)

        return queryset


# ==========================================================================
# То же самое, но для Gr (компании группы)
# ==========================================================================


class GrCountryFilter(_DistinctValueFilter):
    title = "Страна"
    parameter_name = "gr_country"
    field_name = "country"
    model = Gr


class GrRegionFilter(_DistinctValueFilter):
    title = "Регион"
    parameter_name = "gr_region"
    field_name = "region"
    model = Gr
