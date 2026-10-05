"""Модель банковских счетов — привязаны к собственнику (Gr), банк — это CP."""

from __future__ import annotations

from django.db import models

from cp.models.cp_model import CP
from cp.models.gr_model import Gr
from macro.models.fx_model import Fx

class BAType(models.TextChoices):
    MAIN = "MAIN", "Основной"
    TRANSIT = "TRANSIT", "Транзитный"
    


class BankAccount(models.Model):
    number = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        verbose_name="Номер счёта",
    )

    gr = models.ForeignKey(
        Gr,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="bank_accounts",
        verbose_name="Владелец",
    )

    bank = models.ForeignKey(
        CP,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="accounts",
        verbose_name="Банк",
    )

    currency = models.ForeignKey(
        Fx,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="fx_ba",
        verbose_name="Валюта",
    )
    
    ba_type = models.CharField(
        max_length=20,
        choices=BAType,
        default=BAType.MAIN,
        verbose_name='Тип Счета'
    )

    class Meta:
        verbose_name = "Банковский счёт"
        verbose_name_plural = "Банковские счета"
        ordering = ["number"]

    def __str__(self) -> str:
        currency = self.currency or "—"
        bank_name = str(self.bank) if self.bank else "—"

        return f"{bank_name} ...{self.number[-6:]} {currency}"
