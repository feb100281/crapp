"""
Правило внутри резолвера: «назначение содержит …» → статья.

Проверяются сверху вниз по полю «№». Правило с пустым «содержит»
означает «всё остальное» и всегда проверяется последним.
Статья должна совпадать по направлению с резолвером.

У резолвера контрагента правила те же. Если ни одно не подошло
(нет правила «всё остальное»), строку разбирает резолвер КБК или счёта.

Удержанную банком комиссию / долг правило не трогает: они раскладываются
автоматически в отчёте ДДС (sql/bs/cash_flow.sql).
"""

from __future__ import annotations


from django.core.exceptions import ValidationError
from django.db import models

from .cf_item_model import CFItem
from .resolver_model import Resolver


class ResolverRule(models.Model):
    resolver = models.ForeignKey(
        Resolver,
        on_delete=models.CASCADE,
        related_name="rules",
        verbose_name="Резолвер",
    )

    order = models.PositiveSmallIntegerField(default=10, verbose_name="№")

    text_regex = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Назначение содержит",
        help_text="Регулярка, регистр не важен. Пусто — всё остальное",
    )

    cf_item = models.ForeignKey(
        CFItem,
        on_delete=models.PROTECT,
        related_name="resolver_rules",
        verbose_name="Статья",
    )

    hits = models.PositiveIntegerField(default=0, verbose_name="Строк")
    hits_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, verbose_name="Сумма")

    class Meta:
        verbose_name = "Правило"
        verbose_name_plural = "Правила"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"«{self.text_regex or 'всё остальное'}» → {self.cf_item}" if self.cf_item_id else "правило"

    def clean(self):
        errors = {}

        # «содержит» — обычный текст (скобки, точки можно) или регулярка;
        # неправильная регулярка просто ищется как текст, ошибкой не считается

        if self.cf_item_id:
            if self.cf_item.children.exists():
                errors["cf_item"] = "У статьи есть подстатьи — выберите подстатью"
            elif self.resolver_id and not self.cf_item.accepts(self.resolver.direction):
                errors["cf_item"] = (
                    f"Нужна статья «{self.resolver.get_direction_display()}», "
                    f"а выбрана «{self.cf_item.get_direction_display()}». "
                    f"Если это возврат — включите у статьи «Принимает возвраты»"
                )

        if errors:
            raise ValidationError(errors)
