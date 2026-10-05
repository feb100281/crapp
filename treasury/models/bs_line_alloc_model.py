"""
Разноска строки выписки (1:N).

Обычно у строки одна разноска на всю сумму. Несколько — когда
в одной строке два движения: например эквайринг по картам,
где из оплаты покупателя удержана комиссия:

    + gross_dt → выручка          (поступление)
    − fee_cr   → комиссии банка   (списание)

Суммы хранятся без знака, направление — отдельным полем.
Договор добавится отдельным шагом.
"""

from __future__ import annotations

from django.db import models

from .bs_line_model import BSLine
from .cf_item_model import CFItem, Direction
from .resolver_rule_model import ResolverRule


class BSLineAlloc(models.Model):
    line = models.ForeignKey(
        BSLine,
        on_delete=models.CASCADE,
        related_name="allocs",
        verbose_name="Операция",
    )

    direction = models.PositiveSmallIntegerField(
        choices=Direction.choices,
        verbose_name="Направление",
    )

    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        verbose_name="Сумма",
    )

    cf_item = models.ForeignKey(
        CFItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="allocs",
        verbose_name="Статья ДДС",
    )

    resolver_rule = models.ForeignKey(
        ResolverRule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="allocs",
        verbose_name="Правило",
        help_text="Каким правилом разнесено. Пусто — руками",
    )

    manual = models.BooleanField(
        default=False,
        verbose_name="Руками",
        help_text="Правила такую разноску не перезаписывают",
    )

    updated_at = models.DateTimeField(auto_now=True, verbose_name="Изменено")

    class Meta:
        verbose_name = "Разноска"
        verbose_name_plural = "Разноска"

    def __str__(self) -> str:
        return f"{self.get_direction_display()} {self.amount}"
