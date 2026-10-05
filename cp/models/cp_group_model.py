"""Справочник групп/ролей контрагентов (учредители, related parties и т.п.)."""

from __future__ import annotations

from django.db import models

from .cp_model import avatar_field

class CPRole(models.TextChoices):
    GROUP = "GROUP", "Группа"
    SHAREHOLDER = "SHAREHOLDER", "Акционер"
    RELATED = "RELATED", "Аффилированное лицо"
    BANK = "BANK", "Банк"
    STATE = "STATE", "Государственный орган"
    CONTRACTOR = "CONTRACTOR", "Подрядчик"
    CUSTOMER = "CUSTOMER", "Покупатель"
    LENDER = "LENDER", "Займодавец"
    BORROWER = "BORROWER", "Заёмщик"
    EMPLOYEE = "EMPLOYEE", "Сотрудник"


class CPGroup(models.Model):

    name = models.CharField(
        max_length=20,
        choices=CPRole.choices,
        unique=True,
        verbose_name="Группа",
    )

    avatar = avatar_field(
        verbose_name="Иконка группы",
    )

    description = models.TextField(
        blank=True,
        null=True,
        verbose_name="Описание",
    )

    class Meta:
        db_table = "cp_groups"
        verbose_name = "Группа контрагентов"
        verbose_name_plural = "Группы контрагентов"
        ordering = ["name"]

    def __str__(self):
        return self.get_name_display()
