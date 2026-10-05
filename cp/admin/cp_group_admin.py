"""Админка справочника групп контрагентов."""

from __future__ import annotations

from django.contrib import admin
from django.templatetags.static import static

from unfold.decorators import display

from core.admins.base_admin import AppModelAdmin
from core.admins.fields import FirstCol

from ..models.cp_group_model import CPGroup

DEFAULT_AVATAR = "img/defaull_company_avatar.svg"


@admin.register(CPGroup)
class CPGroupAdmin(AppModelAdmin):
    list_display = ["group_display", "cps_count"]

    list_display_links = ["group_display"]

    search_fields = ["name"]

    ordering = ["name"]

    fieldsets = (
        (
            "Группа",
            {
                "fields": (
                    ("name", "avatar"),
                    "description",
                ),
            },
        ),
    )

    @display(description="Группа", ordering="name")
    def group_display(self, obj: CPGroup):
        avatar_url = obj.avatar.url if obj.avatar else static(DEFAULT_AVATAR)

        return FirstCol(
            obj.name,
            obj.description,
            avatar_url,
        ).avatar_name_subtext

    @admin.display(description="Контрагентов")
    def cps_count(self, obj: CPGroup) -> int:
        return obj.cps.count()
