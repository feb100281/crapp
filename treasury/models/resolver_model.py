"""
Резолверы — группы строк выписки, которые разбираются вместе.

    КБК  : ключ = КБК,                    + направление
    Счёт : ключ = первые 5 цифр счёта,    + направление
    Свои : ключ = валюта нашего счёта / валюта второго нашего счёта
           (цифровые коды: 810/810 — перевод, 810/156 — конвертация) + направление
    Контрагент : ключ = ИНН (или название, если ИНН общий / его нет) + направление

Строка с КБК относится ТОЛЬКО к резолверу КБК и в резолвер счетов
не попадает. Переводы между своими счетами — только в резолвер «Свои».

Резолвер контрагента — приоритетный: строка сначала проверяется по его
правилам, и только если ни одно не подошло — по правилам резолвера КБК
или счёта. На переводы между своими он не действует.

Резолверы КБК / счетов / «своих» создаются сами (services.resolver.sync).
Резолверы контрагентов заводит человек — кнопкой «Закрепить» или вручную.
Счётчики ниже пересчитываются при каждой разноске.
"""

from __future__ import annotations

import hashlib

from django.core.exceptions import ValidationError
from django.db import models

from .cf_item_model import Direction


NAME_KEY_PREFIX = "~"


def norm_name(value) -> str:
    """Название контрагента для сравнения: регистр и пробелы не важны."""
    return " ".join(str(value or "").split()).upper()


def name_key(value) -> str:
    """Ключ резолвера контрагента, когда он ищется по названию."""
    return NAME_KEY_PREFIX + hashlib.md5(norm_name(value).encode("utf-8")).hexdigest()[:16]


class ResolverKind(models.TextChoices):
    KBK = "KBK", "КБК"
    BA = "BA", "Счёт контрагента"
    IC = "IC", "Между своими счетами"
    CP = "CP", "Контрагент"


class Resolver(models.Model):
    kind = models.CharField(max_length=5, choices=ResolverKind.choices, verbose_name="Тип")
    key = models.CharField(max_length=20, verbose_name="Ключ", help_text="КБК, 5 цифр счёта или ИНН")
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

    match_name = models.CharField(
        max_length=300,
        blank=True,
        null=True,
        verbose_name="Искать по названию",
        help_text="Только для контрагентов: строки ищутся по названию, а не по ИНН",
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
        if self.kind == ResolverKind.CP:
            return f"{self.name or self.key} · {self.get_direction_display()}"
        return f"{self.get_kind_display()} {self.key} · {self.get_direction_display()}"

    @property
    def by_name(self) -> bool:
        return self.kind == ResolverKind.CP and bool(self.match_name)

    @property
    def inn(self) -> str:
        """ИНН контрагента (пусто, если резолвер ищет по названию)."""
        return "" if self.kind != ResolverKind.CP or self.by_name else self.key


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


class CpResolverManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(kind=ResolverKind.CP)


class CpResolver(Resolver):
    """Правила по контрагенту — проверяются раньше резолверов КБК и счетов."""

    objects = CpResolverManager()

    class Meta:
        proxy = True
        verbose_name = "Резолвер контрагента"
        verbose_name_plural = "Резолверы контрагентов"

    def clean(self):
        self.kind = ResolverKind.CP
        self.key = (self.key or "").strip()
        self.match_name = " ".join((self.match_name or "").split()) or None

        if self.match_name:
            self.key = name_key(self.match_name)
            self.name = self.name or self.match_name
        elif not self.key or self.key.startswith(NAME_KEY_PREFIX):
            raise ValidationError("Укажите ИНН или название контрагента")

        clash = Resolver.objects.filter(
            kind=ResolverKind.CP, key=self.key, direction=self.direction
        ).exclude(pk=self.pk)
        if self.direction and clash.exists():
            raise ValidationError("Для этого контрагента и направления резолвер уже есть")

    def save(self, *args, **kwargs):
        self.kind = ResolverKind.CP
        if self.match_name:
            self.key = name_key(self.match_name)
        super().save(*args, **kwargs)
