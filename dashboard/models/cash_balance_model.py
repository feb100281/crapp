"""
Витрина остатков по дням (таблица dashboard_cash_balance): счёт × день,
курс, остатки и обороты в валюте и в рублях, курсовая разница дня.
managed = False — таблицу заменяет DuckDB (sql/bs/dashboard_load.sql).
"""

from django.db import models


class CashBalance(models.Model):
    date = models.DateField(verbose_name="Дата")
    year = models.IntegerField(verbose_name="Год")
    month = models.IntegerField(verbose_name="Месяц")
    period = models.CharField(max_length=7, verbose_name="Период")

    ba_id = models.IntegerField(verbose_name="ID счёта")
    ba_number = models.CharField(max_length=32, verbose_name="Номер счёта")
    account_name = models.CharField(max_length=200, null=True, verbose_name="Счёт")
    bank_name = models.CharField(max_length=500, null=True, verbose_name="Банк")
    currency = models.CharField(max_length=5, null=True, verbose_name="Валюта")

    rate_prev = models.FloatField(null=True, verbose_name="Курс вчера")
    rate = models.FloatField(null=True, verbose_name="Курс")

    base_bb = models.FloatField(null=True, verbose_name="Остаток на начало (вал.)")
    base_dt = models.FloatField(null=True, verbose_name="Приход (вал.)")
    base_cr = models.FloatField(null=True, verbose_name="Расход (вал.)")
    base_eb = models.FloatField(null=True, verbose_name="Остаток на конец (вал.)")

    bb_rub = models.FloatField(null=True, verbose_name="Остаток на начало, ₽")
    dt_rub = models.FloatField(null=True, verbose_name="Приход, ₽")
    cr_rub = models.FloatField(null=True, verbose_name="Расход, ₽")
    fx_diff_rub = models.FloatField(null=True, verbose_name="Курсовая, ₽")
    eb_rub = models.FloatField(null=True, verbose_name="Остаток на конец, ₽")

    stmt_to = models.DateField(null=True, verbose_name="Последняя выписка")
    stale = models.BooleanField(default=False, verbose_name="Выписка устарела")

    class Meta:
        managed = False
        db_table = "dashboard_cash_balance"
        verbose_name = "Остаток за день"
        verbose_name_plural = "Остатки по счетам за день"
        ordering = ["-date", "ba_number"]

    def __str__(self):
        return f"{self.date} {self.account_name}"
