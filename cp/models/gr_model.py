"""Модель компаний группы (holding) — зеркало CP по набору полей."""

from __future__ import annotations

from django.db import models

from .cp_model import CPStatus, avatar_field


class Gr(models.Model):
    inn = models.CharField(
        max_length=12,
        unique=True,
        db_index=True,
        verbose_name="ИНН",
    )

    ogrn = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        db_index=True,
        verbose_name="ОГРН / ОГРНИП",
    )

    name = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        db_index=True,
        verbose_name="Наименование",
    )

    address = models.TextField(
        blank=True,
        null=True,
        verbose_name="Адрес",
    )

    registration_date = models.DateField(
        blank=True,
        null=True,
        verbose_name="Дата регистрации",
    )

    manager_name = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        verbose_name="Руководитель",
    )

    country = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Страна",
    )

    region = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Регион",
    )

    avatar = avatar_field()

    status = models.CharField(
        max_length=30,
        choices=CPStatus.choices,
        default=CPStatus.NA,
        db_index=True,
        verbose_name="Статус",
    )

    groups = models.ManyToManyField(
        "cp.CPGroup",
        blank=True,
        related_name="grs",
        verbose_name="Группы",
    )

    updated_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Обновлено из источника",
    )

    payload = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        verbose_name="Данные",
    )

    class Meta:
        verbose_name = "Компания"
        verbose_name_plural = "Компании"
        ordering = ["name"]

    def __str__(self):
        return self.name or self.inn
