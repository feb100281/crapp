"""
Журнал проводок: шапка (JournalEntry) + строки (JournalLine).

Суммы строк — в рублях (dt / cr), остаток по счёту = sum(dt − cr).
Для валютных счетов дополнительно сумма в валюте и курс.
Проводка сохраняется, только если сумма Дт = сумме Кт
(проверяется в админке при сохранении строк).
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from cp.models.cp_model import CP
from treasury.models.cf_item_model import CFItem

from .gl_account_model import GLAccount


class EntryKind(models.TextChoices):
    OPENING = "OPENING", "Ввод остатков"
    MANUAL = "MANUAL", "Ручная"
    AUTO = "AUTO", "Автомат"


class JournalEntry(models.Model):
    date = models.DateField(db_index=True, verbose_name="Дата")
    kind = models.CharField(
        max_length=10, choices=EntryKind.choices, default=EntryKind.MANUAL, verbose_name="Тип"
    )
    description = models.CharField(max_length=500, verbose_name="Содержание")
    note = models.TextField(blank=True, null=True, verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")

    class Meta:
        verbose_name = "Проводка"
        verbose_name_plural = "Журнал проводок"
        ordering = ["-date", "-id"]

    def __str__(self) -> str:
        return f"{self.date} {self.description}"


class JournalLine(models.Model):
    entry = models.ForeignKey(
        JournalEntry, on_delete=models.CASCADE, related_name="lines", verbose_name="Проводка"
    )

    account = models.ForeignKey(
        GLAccount, on_delete=models.PROTECT, related_name="lines", verbose_name="Счёт"
    )

    dt = models.DecimalField(max_digits=18, decimal_places=2, default=0, verbose_name="Дт, ₽")
    cr = models.DecimalField(max_digits=18, decimal_places=2, default=0, verbose_name="Кт, ₽")

    amount_cur = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        verbose_name="Сумма в валюте",
        help_text="Только для валютных счетов, без знака",
    )
    rate = models.DecimalField(
        max_digits=18, decimal_places=8, null=True, blank=True, verbose_name="Курс"
    )

    cf_item = models.ForeignKey(
        CFItem, on_delete=models.PROTECT, null=True, blank=True,
        related_name="journal_lines", verbose_name="Статья ДДС",
        help_text="Для денежных счетов, если это движение денег (не ввод остатка)",
    )

    cp = models.ForeignKey(
        CP, on_delete=models.PROTECT, null=True, blank=True,
        related_name="journal_lines", verbose_name="Контрагент",
    )

    note = models.CharField(max_length=300, blank=True, null=True, verbose_name="Комментарий")

    class Meta:
        verbose_name = "Строка проводки"
        verbose_name_plural = "Строки проводки"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.account.code} Дт {self.dt} Кт {self.cr}"

    def clean(self):
        errors = {}
        dt, cr = self.dt or Decimal(0), self.cr or Decimal(0)

        if dt < 0 or cr < 0:
            errors["dt"] = "Суммы без знака — сторону задаёт колонка Дт или Кт"
        elif bool(dt) == bool(cr):
            errors["dt"] = "Заполните ровно одну колонку: Дт или Кт"

        if self.account_id:
            if self.account.children.exists():
                errors["account"] = "У счёта есть субсчета — выберите субсчёт"
            cur = self.account.currency
            if cur and cur.code != "RUB" and not self.amount_cur:
                errors["amount_cur"] = f"Счёт в {cur.code}: укажите сумму в валюте"

        if errors:
            raise ValidationError(errors)
