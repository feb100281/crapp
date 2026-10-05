"""
План счетов управленческого учёта.

    уровень 1   Баланс / P&L              — первая цифра раздела (1 / 2)
    уровень 2   раздел (Активы, Выручка…) — поле section
    уровень 3   счёт                      — number, parent = пусто
    уровень 4   субсчёт                   — parent = счёт 3 уровня

Код — 7 цифр, собирается сам:  РР НН ССС
    РР   раздел        11 Активы … 27 Налог на прибыль
    НН   счёт          01–99
    ССС  субсчёт       000 у самого счёта, 001–999 у субсчетов

    1101000  Расчётные счета (руб.)
    1101001    ОТП · RUB · …4366       ← привязан к нашему банковскому счёту

Проводки делаются только на «листья» — счета без субсчетов.
Каждому нашему банковскому счёту соответствует свой субсчёт
(создаются сами: services.chart.sync_bank_accounts).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from macro.models.fx_model import Fx
from treasury.models.ba_model import BankAccount


class Section(models.IntegerChoices):
    ASSETS = 11, "Активы"
    LIABILITIES = 12, "Обязательства"
    EQUITY = 13, "Капитал"
    REVENUE = 21, "Выручка"
    DIRECT = 22, "Прямые расходы"
    OVERHEADS = 23, "Overheads"
    SGA = 24, "SG&A"
    OTHER = 25, "Прочие доходы и расходы"
    FINANCE = 26, "Финансовые доходы и расходы"
    TAX = 27, "Налог на прибыль"


class Nature(models.TextChoices):
    ACTIVE = "A", "Активный"
    PASSIVE = "P", "Пассивный"
    BOTH = "AP", "Активно-пассивный"


class GLAccount(models.Model):
    section = models.PositiveSmallIntegerField(
        choices=Section.choices,
        verbose_name="Раздел",
        help_text="У субсчёта берётся из счёта",
    )

    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="Счёт",
        help_text="Заполняется только у субсчёта",
    )

    number = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(999)],
        verbose_name="Номер",
        help_text="Счёт: 1–99, субсчёт: 1–999",
    )

    name = models.CharField(max_length=200, verbose_name="Название")

    code = models.CharField(max_length=7, unique=True, editable=False, verbose_name="Код")

    nature = models.CharField(
        max_length=2,
        choices=Nature.choices,
        default=Nature.BOTH,
        verbose_name="Характер",
    )

    currency = models.ForeignKey(
        Fx,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="gl_accounts",
        verbose_name="Валюта",
        help_text="Пусто — рубли",
    )

    bank_account = models.OneToOneField(
        BankAccount,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="gl_account",
        verbose_name="Банковский счёт",
    )

    description = models.TextField(blank=True, null=True, verbose_name="Описание")
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        verbose_name = "Счёт"
        verbose_name_plural = "План счетов"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"

    # ------------------------------------------------------------------

    @property
    def is_sub(self) -> bool:
        return self.parent_id is not None

    @property
    def statement(self) -> str:
        return "Баланс" if self.section < 20 else "P&L"

    def build_code(self) -> str:
        if self.parent:
            return f"{self.parent.code[:4]}{self.number:03}"
        return f"{self.section}{self.number:02}000"

    def clean(self):
        if self.parent:
            if self.parent.parent_id:
                raise ValidationError({"parent": "Только два уровня: счёт → субсчёт"})
            if self.pk and self.children.exists():
                raise ValidationError({"parent": "У счёта есть субсчета — он не может стать субсчётом"})
            self.section = self.parent.section
        elif self.number and self.number > 99:
            raise ValidationError({"number": "Номер счёта 1–99 (до 999 — только у субсчетов)"})

        if self.section is None or self.number is None:
            return

        self.code = self.build_code()
        clash = GLAccount.objects.filter(code=self.code).exclude(pk=self.pk).first()
        if clash:
            raise ValidationError({"number": f"Код {self.code} уже занят: {clash.name}"})

    def save(self, *args, **kwargs):
        if self.parent:
            self.section = self.parent.section

        old_code = None
        if self.pk:
            old_code = GLAccount.objects.filter(pk=self.pk).values_list("code", flat=True).first()

        self.code = self.build_code()
        super().save(*args, **kwargs)

        if old_code and old_code != self.code and not self.parent_id:
            for child in self.children.all():
                child.save()
