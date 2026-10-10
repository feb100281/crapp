"""Кому можно пользоваться ботом: приглашения и пользователи Telegram."""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone


class TgUser(models.Model):
    tg_id = models.BigIntegerField(unique=True, verbose_name="Telegram ID")
    first_name = models.CharField(max_length=200, blank=True, verbose_name="Имя")
    username = models.CharField(max_length=200, blank=True, verbose_name="Логин")
    is_active = models.BooleanField(default=True, verbose_name="Доступ")
    note = models.CharField(max_length=200, blank=True, verbose_name="Кто это")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Подключён")
    last_seen = models.DateTimeField(null=True, blank=True, verbose_name="Последний раз")

    class Meta:
        verbose_name = "Пользователь бота"
        verbose_name_plural = "Пользователи бота"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        name = self.note or self.first_name or self.username or str(self.tg_id)
        return f"{name} ({self.tg_id})"


def _code() -> str:
    return secrets.token_urlsafe(12)


class TgInvite(models.Model):
    code = models.CharField(max_length=40, unique=True, default=_code, verbose_name="Код")
    note = models.CharField(max_length=200, blank=True, verbose_name="Для кого")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    expires_at = models.DateTimeField(verbose_name="Действует до")
    used_at = models.DateTimeField(null=True, blank=True, verbose_name="Использовано")
    used_by = models.ForeignKey(TgUser, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="invites", verbose_name="Кто пришёл")

    class Meta:
        verbose_name = "Приглашение в бот"
        verbose_name_plural = "Приглашения в бот"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.note or 'приглашение'} · {self.code}"

    @classmethod
    def issue(cls, note: str = "", hours: int = 24) -> "TgInvite":
        return cls.objects.create(note=note[:200], expires_at=timezone.now() + timedelta(hours=hours))

    @property
    def link(self) -> str:
        from .conf import bot_username

        name = bot_username()
        return f"https://t.me/{name}?start={self.code}" if name else ""

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()
