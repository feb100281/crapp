"""
Справочник статей движения денежных средств (ДДС).

Структура фиксированная, без дерева:

    деятельность → направление → статья → подстатья (необязательно)

Деятельность и направление — поля-выборы, узлы только статья и подстатья.

Код — 6 цифр, собирается автоматически:

    1     деятельность    1 операционная … 4 внутригрупповые
    2     направление     1 поступления, 2 выплаты
    3–4   номер статьи    01–99
    5–6   номер подстатьи 00 — у самой статьи, 01–99 — у подстатей

    110100  Выручка от реализации
    110101    СБП
    110102    Эквайринг
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Activity(models.IntegerChoices):
    OPERATING = 1, "Операционная"
    INVESTING = 2, "Инвестиционная"
    FINANCING = 3, "Финансовая"
    INTRAGROUP = 4, "Внутригрупповые"


class Direction(models.IntegerChoices):
    INFLOW = 1, "Поступления"
    OUTFLOW = 2, "Выплаты"


class CFItem(models.Model):
    activity = models.PositiveSmallIntegerField(
        choices=Activity.choices,
        verbose_name="Деятельность",
        help_text="У подстатьи берётся из статьи",
    )

    direction = models.PositiveSmallIntegerField(
        choices=Direction.choices,
        verbose_name="Направление",
        help_text="У подстатьи берётся из статьи",
    )

    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="Статья",
        help_text="Заполняется только у подстатьи",
    )

    number = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(99)],
        verbose_name="Номер",
        help_text="1–99 внутри своего уровня",
    )

    name = models.CharField(
        max_length=200,
        verbose_name="Название",
    )

    code = models.CharField(
        max_length=6,
        unique=True,
        editable=False,
        verbose_name="Код",
    )

    description = models.TextField(
        blank=True,
        null=True,
        verbose_name="Описание",
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name="Активна",
        help_text="Вместо удаления — выключаем",
    )

    class Meta:
        verbose_name = "Статья ДДС"
        verbose_name_plural = "Статьи ДДС"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"

    # ------------------------------------------------------------------

    @property
    def is_sub(self) -> bool:
        return self.parent_id is not None

    def build_code(self) -> str:
        if self.parent:
            return f"{self.parent.code[:4]}{self.number:02}"
        return f"{self.activity}{self.direction}{self.number:02}00"

    def clean(self):
        if self.parent:
            if self.parent.parent_id:
                raise ValidationError(
                    {"parent": "Только два уровня: статья → подстатья"}
                )
            if self.pk and self.children.exists():
                raise ValidationError(
                    {"parent": "У статьи есть подстатьи — она не может стать подстатьёй"}
                )
            # подстатья наследует деятельность и направление
            self.activity = self.parent.activity
            self.direction = self.parent.direction

        if self.activity is None or self.direction is None or self.number is None:
            return  # обязательность полей проверит сама форма

        self.code = self.build_code()

        clash = CFItem.objects.filter(code=self.code).exclude(pk=self.pk).first()
        if clash:
            raise ValidationError(
                {"number": f"Код {self.code} уже занят: {clash.name}"}
            )

    def save(self, *args, **kwargs):
        if self.parent:
            self.activity = self.parent.activity
            self.direction = self.parent.direction

        old_code = None
        if self.pk:
            old_code = (
                CFItem.objects.filter(pk=self.pk)
                .values_list("code", flat=True)
                .first()
            )

        self.code = self.build_code()
        super().save(*args, **kwargs)

        # статья перенумерована / сменила деятельность — пересобираем подстатьи
        if old_code and old_code != self.code and not self.parent_id:
            for child in self.children.all():
                child.save()
