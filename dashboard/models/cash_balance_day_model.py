"""
Остатки по дням, свёрнутые по всем счетам (таблица dashboard_cash_balance_day).
Счета дня — CashBalance с той же датой: связь по date, без FK.
managed = False — таблицу заменяет DuckDB (sql/bs/dashboard_load.sql).
"""

from django.db import models


class CashBalanceDay(models.Model):
    date = models.DateField(verbose_name="Дата")
    year = models.IntegerField(verbose_name="Год")
    month = models.IntegerField(verbose_name="Месяц")
    period = models.CharField(max_length=7, verbose_name="Период")
    accounts = models.IntegerField(verbose_name="Счетов")

    bb_rub = models.FloatField(null=True, verbose_name="Остаток на начало, ₽")
    dt_rub = models.FloatField(null=True, verbose_name="Приход, ₽")
    cr_rub = models.FloatField(null=True, verbose_name="Расход, ₽")
    fx_diff_rub = models.FloatField(null=True, verbose_name="Курсовая, ₽")
    eb_rub = models.FloatField(null=True, verbose_name="Остаток на конец, ₽")

    # счета, по которым на этот день выписки нет, а остаток есть (перенесён)
    stale_accounts = models.IntegerField(default=0, verbose_name="Без свежей выписки")
    stale_rub = models.FloatField(default=0, verbose_name="Остаток без выписки, ₽")

    class Meta:
        managed = False
        db_table = "dashboard_cash_balance_day"
        verbose_name = "День"
        verbose_name_plural = "Остатки по дням"
        ordering = ["-date"]

    def __str__(self):
        return str(self.date)
