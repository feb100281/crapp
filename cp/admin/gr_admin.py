"""Админка компаний группы (Gr) — зеркало CpAdmin."""

from __future__ import annotations

import time

from django.contrib import admin, messages
from django.http import Http404, HttpRequest
from django.shortcuts import redirect
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe

from unfold.contrib.filters.admin import ChoicesDropdownFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from core.admins.badges import Badge, TimeBadge
from core.admins.base_admin import AppModelAdmin
from core.admins.fields import FirstCol, Stack

from .filters import (
    FRESH_DAYS,
    STALE_DAYS,
    CpFreshnessFilter,
    CpInnFilter,
    CpManagerFilter,
    CpNameFilter,
    GrCountryFilter,
    GrRegionFilter,
)
from ..models.cp_model import CPStatus
from ..models.gr_model import Gr
from treasury.admin.bank_account_admin import BankAccountInline
from .panels import dadata_panel
from .services.dadata import (
    REQUEST_DELAY,
    DaDataError,
    build_session,
    update_cp_from_dadata,
)

# Максимум записей за один массовый прогон — чтобы не подвесить воркер
BULK_LIMIT = 300

DEFAULT_AVATAR = "img/defaull_company_avatar.svg"

# статус -> (иконка, стиль бэйджа)
STATUS_BADGES = {
    CPStatus.ACTIVE: ("verified", "success"),
    CPStatus.LIQUIDATING: ("hourglass_bottom", "warning"),
    CPStatus.LIQUIDATED: ("cancel", "danger"),
    CPStatus.BANKRUPT: ("gavel", "danger"),
    CPStatus.REORGANIZING: ("cached", "info"),
    CPStatus.NA: ("help", "gray"),
}


@admin.register(Gr)
class GrAdmin(AppModelAdmin):
    # ==================================================================
    # Список
    # ==================================================================

    list_display = [
        "gr_display",
        "status_display",
        "geo_display",
        "manager_display",
        "ba_display",
        "updated_display",
    ]

    list_display_links = ["gr_display"]

    ordering = ["name"]

    inlines = [BankAccountInline]

    search_fields = [
        "name",
        "inn",
        "ogrn",
        "manager_name",
    ]

    search_help_text = "Наименование, ИНН, ОГРН или руководитель"

    list_filter = [
        CpNameFilter,
        CpInnFilter,
        CpManagerFilter,
        ("status", ChoicesDropdownFilter),
        GrCountryFilter,
        GrRegionFilter,
        CpFreshnessFilter,
    ]

    list_filter_submit = True
    list_fullwidth = True

    # Счётчики фильтров требуют get_facet_counts у кастомных фильтров
    show_facets = admin.ShowFacets.NEVER

    # ==================================================================
    # Форма
    # ==================================================================

    fieldsets = (
        (
            "Компания",
            {
                "fields": (
                    ("name", "avatar"),
                    ("inn", "ogrn"),
                    ("status", "registration_date"),
                ),
            },
        ),
        (
            "Реквизиты",
            {
                "classes": ["tab"],
                "fields": (
                    "manager_name",
                    "address",
                    ("country", "region"),
                    "groups",
                ),
            },
        ),
        (
            "DaData",
            {
                "classes": ["tab"],
                "fields": (
                    "dadata_display",
                    "updated_at",
                    "payload",
                ),
            },
        ),
    )

    readonly_fields = [
        "dadata_display",
        "updated_at",
    ]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))

        # ИНН — ключ связи с источниками, менять его у существующей
        # записи нельзя: иначе загрузка перестанет находить компанию.
        if obj and "inn" not in fields:
            fields.append("inn")

        return fields

    # ==================================================================
    # Колонки
    # ==================================================================

    @display(description="Компания", ordering="name")
    def gr_display(self, obj: Gr):
        if obj.avatar:
            avatar_url = obj.avatar.url
        else:
            avatar_url = static(DEFAULT_AVATAR)

        subtext = f"ИНН {obj.inn}"

        if obj.ogrn:
            subtext = f"{subtext} · ОГРН {obj.ogrn}"

        return FirstCol(
            obj.name or obj.inn,
            subtext,
            avatar_url,
        ).avatar_name_subtext

    @display(description="Статус", ordering="status")
    def status_display(self, obj: Gr):
        icon, style = STATUS_BADGES.get(
            obj.status,
            ("help", "gray"),
        )

        badge = Badge(
            obj.get_status_display(),
            icon,
            style,
        ).badge

        if not obj.registration_date:
            return badge

        return Stack(
            badge,
            f"с {obj.registration_date.strftime('%d.%m.%Y')}",
        ).html

    @display(description="География", ordering="region")
    def geo_display(self, obj: Gr):
        if not obj.region and not obj.country:
            return mark_safe('<span class="pk-field-sub">—</span>')

        return Stack(
            obj.region or obj.country,
            obj.country if obj.region else None,
        ).html

    @display(description="Руководитель", ordering="manager_name")
    def manager_display(self, obj: Gr):
        if not obj.manager_name:
            return mark_safe('<span class="pk-field-sub">—</span>')

        post = None

        suggestions = (obj.payload or {}).get("suggestions") or []

        if suggestions:
            management = (
                (suggestions[0] or {}).get("data", {}) or {}
            ).get("management") or {}

            post = management.get("post")

        return Stack(
            obj.manager_name,
            post,
        ).html

    @display(description="Счета", dropdown=True)
    def ba_display(self, obj: Gr):
        accounts = list(obj.bank_accounts.all())

        if not accounts:
            return {"title": "—", "items": []}

        return {
            "title": f"{len(accounts)} шт.",
            "striped": True,
            "width": 320,
            "items": [
                {
                    "title": (
                        f"{account.number} · {account.bank.name}"
                        if account.bank and account.bank.name
                        else account.number
                    ),
                }
                for account in accounts
            ],
        }

    @display(description="Обновлено", ordering="updated_at")
    def updated_display(self, obj: Gr):
        updated = obj.updated_at

        if not updated:
            return Badge(
                "Не обновлялся",
                "error",
                "danger",
            ).badge

        badge = TimeBadge(updated)

        days = badge.days

        if days is not None and days < FRESH_DAYS:
            badge.icon = "check_circle"
            badge.style = "success"

        elif days is not None and days < STALE_DAYS:
            badge.icon = "warning"
            badge.style = "warning"

        else:
            badge.icon = "alarm"
            badge.style = "danger"

        return badge.related_time_badge

    @display(description="Данные источника")
    def dadata_display(self, obj: Gr):
        return dadata_panel(obj.payload)

    # ==================================================================
    # Действия
    # ==================================================================

    actions_row = ["update_dadata_row"]
    actions_detail = ["update_dadata_detail"]
    actions = ["update_dadata_bulk"]

    def _update_one(self, request: HttpRequest, gr: Gr) -> None:
        """Обновление одной записи + сообщение пользователю."""

        try:
            found = update_cp_from_dadata(gr)

        except DaDataError as exc:
            messages.error(request, f"DaData: {exc}")
            return

        except Exception as exc:  # noqa: BLE001
            messages.error(
                request,
                f"Ошибка обновления {gr.inn}: {exc}",
            )
            return

        if found:
            messages.success(
                request,
                f"Обновлено из DaData: {gr.name or gr.inn}",
            )

        else:
            messages.warning(
                request,
                f"DaData не нашла организацию по ИНН {gr.inn}",
            )

    @action(
        description="Обновить",
        permissions=["update_dadata"],
        url_path="update-dadata-row",
        icon="update",
        variant=ActionVariant.PRIMARY,
    )
    def update_dadata_row(self, request: HttpRequest, object_id: int):
        gr = self.get_object(request, object_id)

        if not gr:
            raise Http404("Компания не найдена")

        self._update_one(request, gr)

        return redirect(
            reverse("admin:cp_gr_changelist")
        )

    @action(
        description="Обновить из DaData",
        permissions=["update_dadata"],
        url_path="update-dadata-detail",
        icon="cloud_download",
        variant=ActionVariant.PRIMARY,
    )
    def update_dadata_detail(self, request: HttpRequest, object_id: int):
        gr = self.get_object(request, object_id)

        if not gr:
            raise Http404("Компания не найдена")

        self._update_one(request, gr)

        return redirect(
            reverse(
                "admin:cp_gr_change",
                args=[object_id],
            )
        )

    def has_update_dadata_permission(
        self,
        request: HttpRequest,
        object_id=None,
    ) -> bool:
        return request.user.has_perm("cp.change_gr")

    @admin.action(description="Обновить данные из DaData")
    def update_dadata_bulk(self, request: HttpRequest, queryset):
        if not self.has_update_dadata_permission(request):
            messages.error(request, "Недостаточно прав")
            return

        total = queryset.count()

        if total > BULK_LIMIT:
            messages.error(
                request,
                f"Выбрано {total} записей. За один раз можно обновить "
                f"не более {BULK_LIMIT} — для полной загрузки "
                f"используйте команду init_cp.",
            )
            return

        updated = 0
        missing = 0
        failed = 0

        started = timezone.now()

        session = build_session()

        try:
            for gr in queryset:
                try:
                    if update_cp_from_dadata(gr, session=session):
                        updated += 1
                    else:
                        missing += 1

                except Exception:  # noqa: BLE001
                    failed += 1

                time.sleep(REQUEST_DELAY)

        finally:
            session.close()

        seconds = (timezone.now() - started).total_seconds()

        messages.success(
            request,
            f"Обновлено: {updated} · не найдено: {missing} · "
            f"ошибок: {failed} · за {seconds:.0f} сек.",
        )
