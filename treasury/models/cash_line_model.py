"""
Строки кассы — карточка счёта 50 из 1С, как есть (аналог строк выписок).

Заполняется командой cash_desk (core/management/commands/cash_desk.py):
импорт только ДОБАВЛЯЕТ новые строки (ключ rr_id) и не меняет загруженные.

Касса в 1С, в отличие от банка, правится задним числом. Поэтому строка,
которая есть в базе, но пропала из свежей выгрузки за тот же период,
не удаляется, а получает in_1c = False — её видно и можно разобраться.

Разноска (статья ДДС) — отдельно, в CashLineAlloc (1:N).
Сдача в банк (корр. 57.*) разносится сама и сверяется с банковскими
взносами на экране «Касса → банк» (CashTransfer).
"""

from __future__ import annotations

from django.db import models

from .cf_item_model import Direction


class CashLine(models.Model):
    rr_id = models.CharField(max_length=32, unique=True, verbose_name="ID строки",
                             help_text="md5 реквизитов строки карточки 50")

    cash_account = models.CharField(max_length=10, verbose_name="Счёт кассы", help_text="50.01")
    date = models.DateField(verbose_name="Дата", db_index=True)

    doc_type = models.CharField(max_length=200, blank=True, default="", verbose_name="Документ")
    doc_number = models.CharField(max_length=50, blank=True, default="", verbose_name="Номер")
    doc_datetime = models.DateTimeField(null=True, blank=True, verbose_name="Дата и время документа")
    doc_comment = models.TextField(blank=True, default="", verbose_name="Комментарий / основание")
    division = models.CharField(max_length=200, blank=True, default="", verbose_name="Подразделение")

    direction = models.PositiveSmallIntegerField(choices=Direction.choices, verbose_name="Направление")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Сумма")

    corr_account = models.CharField(max_length=20, db_index=True, verbose_name="Корр. счёт")
    cf_item_1c = models.CharField(max_length=500, blank=True, default="", verbose_name="Статья ДДС в 1С")
    counterparty = models.CharField(max_length=500, blank=True, default="", verbose_name="Контрагент / субконто")
    subconto2 = models.CharField(max_length=500, blank=True, default="", verbose_name="Субконто 2")
    subconto3 = models.TextField(blank=True, default="", verbose_name="Субконто 3")

    balance_1c = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True,
                                     verbose_name="Сальдо 1С после строки")
    source_file = models.CharField(max_length=300, blank=True, default="", verbose_name="Файл")

    in_1c = models.BooleanField(default=True, verbose_name="Есть в 1С",
                                help_text="Нет в последней выгрузке за этот период — строку в 1С удалили или изменили")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="Загружено")

    class Meta:
        verbose_name = "Операция по кассе"
        verbose_name_plural = "Операции по кассе"
        ordering = ["-date", "-doc_datetime", "-id"]

    def __str__(self) -> str:
        return f"{self.date} {self.doc_type} {self.doc_number} {self.amount}"

    @property
    def is_transfer(self) -> bool:
        """Сдача в банк / получение из банка — корр. счёт 57 (переводы в пути)."""
        return self.corr_account.startswith("57")


class CashTransfer(CashLine):
    """Экран «Касса → банк»: сверка сдачи наличных с взносами по выпискам."""

    class Meta:
        proxy = True
        verbose_name = "Касса → банк"
        verbose_name_plural = "Касса → банк"
