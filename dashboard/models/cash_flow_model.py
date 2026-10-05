"""
Витрина ДДС в рублях (таблица dashboard_cash_flow).

managed = False: таблицу создаёт и целиком заменяет DuckDB
(sql/bs/dashboard_load.sql) при пересчёте «ДДС и переоценка».
Витрина «жирная»: всё для отчёта лежит в строке, связей нет.
Знак amount_rub: + поступление, − выплата.
"""

from django.db import models


class CashFlow(models.Model):
    source = models.CharField(max_length=10, verbose_name="Источник")
    date = models.DateField(verbose_name="Дата")
    year = models.IntegerField(verbose_name="Год")
    month = models.IntegerField(verbose_name="Месяц")
    period = models.CharField(max_length=7, verbose_name="Период")

    ba_id = models.IntegerField(null=True, verbose_name="ID счёта")
    ba_number = models.CharField(max_length=32, null=True, verbose_name="Номер счёта")
    account_name = models.CharField(max_length=200, null=True, verbose_name="Счёт")
    bank_name = models.CharField(max_length=500, null=True, verbose_name="Банк")
    currency = models.CharField(max_length=5, null=True, verbose_name="Валюта")

    line_id = models.IntegerField(null=True, verbose_name="ID строки выписки")
    journal_line_id = models.IntegerField(null=True, verbose_name="ID строки проводки")

    amount_cur = models.FloatField(null=True, verbose_name="Сумма в валюте")
    rate = models.FloatField(null=True, verbose_name="Курс")
    amount_rub = models.FloatField(null=True, verbose_name="Сумма, ₽")

    activity = models.IntegerField(null=True, verbose_name="Код деятельности")
    activity_name = models.CharField(max_length=50, null=True, verbose_name="Деятельность")
    direction = models.IntegerField(null=True, verbose_name="Код направления")
    direction_name = models.CharField(max_length=20, null=True, verbose_name="Направление")
    article_code = models.CharField(max_length=6, null=True, verbose_name="Код статьи")
    article_name = models.CharField(max_length=200, null=True, verbose_name="Статья")
    cf_code = models.CharField(max_length=6, null=True, verbose_name="Код подстатьи")
    cf_name = models.CharField(max_length=200, null=True, verbose_name="Подстатья")
    is_conversion = models.BooleanField(null=True, verbose_name="Конвертация")

    cp_name = models.TextField(null=True, verbose_name="Контрагент")
    inn = models.CharField(max_length=20, null=True, verbose_name="ИНН")
    description = models.TextField(null=True, verbose_name="Назначение")

    class Meta:
        managed = False
        db_table = "dashboard_cash_flow"
        verbose_name = "Строка ДДС"
        verbose_name_plural = "ДДС — строки"
        ordering = ["-date", "id"]

    def __str__(self):
        return f"{self.date} {self.cf_name} {self.amount_rub}"
