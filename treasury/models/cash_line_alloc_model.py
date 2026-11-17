"""
Разноска строки кассы (1:N) — как BSLineAlloc у банка.

Сдача в банк (корр. 57.*) разносится сама: 420500 / 410600.
Остальное — резолверами кассы (следующий шаг) или руками.
"""

from __future__ import annotations

from django.db import models

from .cash_line_model import CashLine
from .cf_item_model import CFItem, Direction
from .resolver_rule_model import ResolverRule


class CashLineAlloc(models.Model):
    line = models.ForeignKey(CashLine, on_delete=models.CASCADE, related_name="allocs",
                             verbose_name="Операция по кассе")
    direction = models.PositiveSmallIntegerField(choices=Direction.choices, verbose_name="Направление")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Сумма")
    cf_item = models.ForeignKey(CFItem, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="cash_allocs", verbose_name="Статья ДДС")
    resolver_rule = models.ForeignKey(ResolverRule, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="cash_allocs", verbose_name="Правило",
                                      help_text="Каким правилом разнесено. Пусто — руками или автоматически")
    manual = models.BooleanField(default=False, verbose_name="Руками",
                                 help_text="Правила такую разноску не перезаписывают")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Изменено")

    class Meta:
        verbose_name = "Разноска"
        verbose_name_plural = "Разноска"

    def __str__(self) -> str:
        return f"{self.get_direction_display()} {self.amount}"
