"""
Сверка по счетам (таблица dashboard_cash_check): остаток на начало + ДДС
должно равняться остатку на конец. managed = False — заменяет DuckDB.
"""

from django.db import models


class CashCheck(models.Model):
    ba_id = models.IntegerField(verbose_name="ID счёта")
    ba_number = models.CharField(max_length=32, verbose_name="Номер счёта")
    account_name = models.CharField(max_length=200, null=True, verbose_name="Счёт")
    bank_name = models.CharField(max_length=500, null=True, verbose_name="Банк")
    opening_rub = models.FloatField(null=True, verbose_name="Остаток на начало, ₽")
    flows_rub = models.FloatField(null=True, verbose_name="Движение, ₽")
    closing_rub = models.FloatField(null=True, verbose_name="Остаток на конец, ₽")
    diff = models.FloatField(null=True, verbose_name="Расхождение, ₽")

    class Meta:
        managed = False
        db_table = "dashboard_cash_check"
        verbose_name = "Сверка счёта"
        verbose_name_plural = "Сверка ДДС с остатками"
        ordering = ["-diff"]

    def __str__(self):
        return self.account_name or self.ba_number
