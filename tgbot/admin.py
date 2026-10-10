"""Пользователи бота и приглашения."""

from __future__ import annotations

from datetime import timedelta

from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html
from unfold.decorators import display

from core.admins.badges import Badge
from core.admins.base_admin import AppModelAdmin

from . import conf
from .models import TgInvite, TgUser


@admin.register(TgUser)
class TgUserAdmin(AppModelAdmin):
    list_display = ["tg_id", "note", "first_name", "username", "access_display", "is_active", "created_at",
                    "last_seen"]
    list_editable = ["note", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["tg_id", "first_name", "username", "note"]
    readonly_fields = ["tg_id", "first_name", "username", "created_at", "last_seen"]
    actions = ["block", "unblock"]

    @display(description="Доступ", ordering="is_active")
    def access_display(self, obj):
        if obj.tg_id in conf.admin_ids():
            return Badge("Администратор", "shield_person", "primary").badge
        return Badge("Есть", "check", "success").badge if obj.is_active else Badge("Отключён", "block", "danger").badge

    @admin.action(description="Отключить доступ")
    def block(self, request, queryset):
        n = queryset.update(is_active=False)
        messages.success(request, f"Отключено: {n}")

    @admin.action(description="Вернуть доступ")
    def unblock(self, request, queryset):
        n = queryset.update(is_active=True)
        messages.success(request, f"Доступ возвращён: {n}")


@admin.register(TgInvite)
class TgInviteAdmin(AppModelAdmin):
    list_display = ["note", "state_display", "link_display", "expires_at", "used_by", "used_at"]
    readonly_fields = ["link_display", "code", "used_at", "used_by", "created_at"]
    fields = ["note", "expires_at", "link_display", "used_by", "used_at", "created_at"]

    def response_add(self, request, obj, post_url_continue=None):
        # после создания — сразу на карточку, где видна ссылка
        messages.success(request, "Приглашение создано. Скопируйте ссылку и перешлите её человеку в Telegram.")
        from django.shortcuts import redirect
        from django.urls import reverse
        return redirect(reverse("admin:tgbot_tginvite_change", args=[obj.pk]))

    @display(description="Ссылка-приглашение")
    def link_display(self, obj):
        if not obj.pk:
            return "Появится после сохранения"
        if not obj.link:
            return format_html('<span class="pk-field-sub">Запустите бота один раз — тогда здесь появится ссылка '
                               '(код {})</span>', obj.code)
        if not obj.is_valid:
            return format_html('<span class="pk-field-sub">{}</span>', obj.link)
        return format_html(
            '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
            '<code style="user-select:all">{0}</code>'
            '<button type="button" class="pk-btn" onclick="navigator.clipboard.writeText(\'{0}\');'
            'this.textContent=\'Скопировано ✓\'">Скопировать</button></div>'
            '<div class="pk-mini-note">Одноразовая: человек открывает её и жмёт «Старт» — доступ появится. '
            'Бот сам написать первым не может.</div>', obj.link)

    def get_changeform_initial_data(self, request):
        return {"expires_at": timezone.now() + timedelta(hours=conf.INVITE_HOURS)}

    @display(description="Состояние")
    def state_display(self, obj):
        if obj.used_at:
            return Badge("Использовано", "check", "gray").badge
        if obj.expires_at <= timezone.now():
            return Badge("Истекло", "schedule", "danger").badge
        return Badge("Ждёт", "hourglass_top", "warning").badge
