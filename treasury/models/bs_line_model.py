"""
Строки банковских выписок — сырые операции.

Заполняется командой bs (core/management/commands/bs.py) из
sql/bs/bs_gl_adj.sql. Импорт только ДОБАВЛЯЕТ новые строки
(ключ rr_id) и никогда не меняет уже загруженные.

Решения по разноске (статья ДДС и т.п.) здесь НЕ хранятся —
они в BSLineAlloc (1:N). Всё, что ниже, — либо поля выписки
как есть, либо то, что однозначно выводится из самой строки.
"""

from __future__ import annotations

from django.db import models

from .ba_model import BankAccount
from .cf_item_model import Direction
from .statement_model import Statement


def money(verbose_name: str, null: bool = False) -> models.DecimalField:
    return models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=null,
        blank=null,
        verbose_name=verbose_name,
    )


class BSLine(models.Model):
    rr_id = models.CharField(
        max_length=32,
        unique=True,
        verbose_name="ID операции",
        help_text="md5 реквизитов операции",
    )

    # удалили выписку в админке → её строки (и разноска) уходят вместе с ней;
    # повторный импорт файла вернёт строки, ручная разноска при этом теряется
    statement = models.ForeignKey(
        Statement,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="lines",
        verbose_name="Выписка",
    )

    ba_account = models.ForeignKey(
        BankAccount,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lines",
        verbose_name="Наш счёт",
    )

    # ------------------------------------------------------------------
    # Документ
    # ------------------------------------------------------------------

    doc_type = models.CharField(max_length=100, null=True, blank=True, verbose_name="Вид документа")
    doc_number = models.CharField(max_length=50, null=True, blank=True, verbose_name="Номер")
    doc_date = models.DateField(null=True, blank=True, verbose_name="Дата документа")
    op_date = models.DateField(null=True, blank=True, db_index=True, verbose_name="Дата операции")

    # ------------------------------------------------------------------
    # Суммы
    # ------------------------------------------------------------------

    direction = models.PositiveSmallIntegerField(
        choices=Direction.choices,
        verbose_name="Направление",
    )

    amount = money("Сумма")
    dt = money("Поступление")
    cr = money("Списание")

    intercompany = models.BooleanField(default=False, verbose_name="Между своими счетами")

    # ------------------------------------------------------------------
    # Контрагент — как в выписке
    # ------------------------------------------------------------------

    inn = models.CharField(max_length=20, null=True, blank=True, verbose_name="ИНН (выписка)")
    inn_adjust = models.CharField(max_length=20, null=True, blank=True, db_index=True, verbose_name="ИНН")
    cp_name = models.TextField(null=True, blank=True, verbose_name="Контрагент (выписка)")
    cp_ba = models.CharField(max_length=40, null=True, blank=True, db_index=True, verbose_name="Счёт контрагента")
    cp_bic = models.CharField(max_length=20, null=True, blank=True, verbose_name="БИК контрагента")

    # ------------------------------------------------------------------
    # Признаки для разноски
    # ------------------------------------------------------------------

    ba_resolver = models.CharField(
        max_length=5,
        null=True,
        blank=True,
        verbose_name="Резолвер",
        help_text="Первые 5 цифр счёта контрагента",
    )

    kbk = models.CharField(max_length=20, null=True, blank=True, db_index=True, verbose_name="КБК")
    vo_code = models.CharField(max_length=10, null=True, blank=True, verbose_name="Код валютной операции")

    description = models.TextField(null=True, blank=True, verbose_name="Назначение платежа")
    desc_pattern = models.TextField(
        null=True,
        blank=True,
        verbose_name="Шаблон назначения",
        help_text="Назначение, где номера, суммы и даты заменены на #",
    )

    # ------------------------------------------------------------------
    # Комиссия и НДС из назначения
    # ------------------------------------------------------------------

    fee_cr = money("Комиссия банка", null=True)
    fee_withheld = models.BooleanField(default=False, verbose_name="Комиссия удержана")
    debt_cr = money("Удержано в счёт долга", null=True)
    gross_dt = money("Оплата покупателя (gross)", null=True)

    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Ставка НДС")
    vat_amount = money("Сумма НДС", null=True)
    vat_free = models.BooleanField(default=False, verbose_name="Без НДС")
    vat_check = models.BooleanField(
        null=True,
        blank=True,
        verbose_name="НДС сходится",
        help_text="Пусто — проверять нечем",
    )

    imported_at = models.DateTimeField(null=True, blank=True, verbose_name="Загружено")

    class Meta:
        verbose_name = "Операция по выписке"
        verbose_name_plural = "Операции по выпискам"
        ordering = ["-op_date", "id"]
        indexes = [
            models.Index(fields=["ba_resolver", "direction"], name="bsline_resolver_dir"),
        ]

    def __str__(self) -> str:
        return f"{self.op_date} {self.amount} {self.cp_name or ''}"[:120]
