"""
Резолверы — группы строк выписки, которые разбираются вместе.

    КБК  : ключ = КБК,                    + направление
    Счёт : ключ = первые 5 цифр счёта,    + направление
    Свои : ключ = валюта нашего счёта / валюта второго нашего счёта
           (цифровые коды: 810/810 — перевод, 810/156 — конвертация) + направление

Строка с КБК относится ТОЛЬКО к резолверу КБК и в резолвер счетов
не попадает. Переводы между своими счетами — только в резолвер «Свои».

Записи создаются сами (services.resolver.sync) из загруженных строк.
Руками заводятся только правила (ResolverRule) внутри резолвера.
Счётчики ниже пересчитываются при каждой разноске.
"""

from __future__ import annotations

from django.db import models

from .cf_item_model import Direction


class ResolverKind(models.TextChoices):
    KBK = "KBK", "КБК"
    BA = "BA", "Счёт контрагента"
    IC = "IC", "Между своими счетами"


class Resolver(models.Model):
    kind = models.CharField(max_length=5, choices=ResolverKind.choices, verbose_name="Тип")
    key = models.CharField(max_length=20, verbose_name="Ключ", help_text="КБК или 5 цифр счёта")
    direction = models.PositiveSmallIntegerField(choices=Direction.choices, verbose_name="Направление")

    name = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        verbose_name="Что за счёт",
        help_text="Для счетов — подсказка по плану счетов ЦБ, можно поправить",
    )

    payee = models.CharField(
        max_length=300,
        blank=True,
        null=True,
        verbose_name="Получатель / плательщик",
        help_text="Самый частый контрагент в строках",
    )

    note = models.TextField(blank=True, null=True, verbose_name="Комментарий")

    lines_count = models.PositiveIntegerField(default=0, verbose_name="Строк")
    lines_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, verbose_name="Сумма")
    open_count = models.PositiveIntegerField(default=0, verbose_name="Не разнесено, строк")
    open_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, verbose_name="Не разнесено, сумма")

    class Meta:
        verbose_name = "Резолвер"
        verbose_name_plural = "Резолверы"
        ordering = ["-open_amount", "-lines_amount"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "key", "direction"], name="resolver_kind_key_dir"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.key} · {self.get_direction_display()}"


class KbkResolverManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(kind=ResolverKind.KBK)


class KbkResolver(Resolver):
    """Резолверы КБК — отдельный пункт меню."""

    objects = KbkResolverManager()

    class Meta:
        proxy = True
        verbose_name = "Резолвер КБК"
        verbose_name_plural = "Резолверы КБК"


class BaResolverManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(kind=ResolverKind.BA)


class BaResolver(Resolver):
    """Резолверы по 5 цифрам счёта контрагента — отдельный пункт меню."""

    objects = BaResolverManager()

    class Meta:
        proxy = True
        verbose_name = "Резолвер счёта"
        verbose_name_plural = "Резолверы счетов"


class IcResolverManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(kind=ResolverKind.IC)


class IcResolver(Resolver):
    """Переводы между своими счетами — отдельный пункт меню."""

    objects = IcResolverManager()

    class Meta:
        proxy = True
        verbose_name = "Резолвер переводов между своими"
        verbose_name_plural = "Резолверы: между своими"
