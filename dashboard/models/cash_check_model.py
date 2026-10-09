"""
Сверка с банком по счетам (таблица dashboard_cash_check), managed = False — заменяет DuckDB.

    bank_eb   — конечный остаток последней выписки (stmt_to), в валюте счёта
    calc_eb   — наш расчёт на ту же дату: ввод остатков + все строки выписок + проводки ГК
    diff_cur  — calc_eb − bank_eb (≠ 0 → не хватает строк/выписок или лишние)
    gaps      — разрывы в выписках: стыки, где входящий остаток ≠ исходящему предыдущей,
                и выписки, чьи строки не бьются с итогами; gap_amount — сколько diff они объясняют
    check_rub — внутренняя арифметика отчёта ДДС (начало + ДДС − конец), должна быть 0
"""

from django.db import models


class CashCheck(models.Model):
    ba_id = models.IntegerField(verbose_name="ID счёта")
    ba_number = models.CharField(max_length=32, verbose_name="Номер счёта")
    account_name = models.CharField(max_length=200, null=True, verbose_name="Счёт")
    bank_name = models.CharField(max_length=500, null=True, verbose_name="Банк")
    currency = models.CharField(max_length=5, null=True, verbose_name="Валюта")

    stmt_to = models.DateField(null=True, verbose_name="Последняя выписка")
    statements = models.IntegerField(null=True, verbose_name="Выписок")
    bank_eb = models.FloatField(null=True, verbose_name="Остаток по банку")
    calc_eb = models.FloatField(null=True, verbose_name="Остаток по расчёту")
    diff_cur = models.FloatField(null=True, verbose_name="Расхождение (вал.)")
    diff_rub = models.FloatField(null=True, verbose_name="Расхождение, ₽")

    gap_count = models.IntegerField(default=0, verbose_name="Пропусков")
    gaps = models.TextField(null=True, verbose_name="Пропущенные периоды")
    gap_amount = models.FloatField(null=True, verbose_name="Движение в пропусках")

    opening_rub = models.FloatField(null=True, verbose_name="Остаток на начало, ₽")
    flows_rub = models.FloatField(null=True, verbose_name="Движение, ₽")
    closing_rub = models.FloatField(null=True, verbose_name="Остаток на конец, ₽")
    check_rub = models.FloatField(null=True, verbose_name="Арифметика ДДС, ₽")

    class Meta:
        managed = False
        db_table = "dashboard_cash_check"
        verbose_name = "Сверка счёта"
        verbose_name_plural = "Сверка с банком"
        ordering = ["id"]

    def __str__(self):
        return self.account_name or self.ba_number

    @property
    def ok(self) -> bool:
        return abs(self.diff_cur or 0) < 0.01 and not self.gap_count
