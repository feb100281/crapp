"""
Витрина «Контрагенты и статьи» (таблица dashboard_cp_audit).

managed = False: таблицу пересобирает dashboard.services.marts.refresh_cp_audit
(sql/bs/cp_audit_mart.sql) после разноски. Строка — одна разноска строки
выписки или строка без разноски (is_open = 1). Связей нет, всё в строке.
"""

from django.db import models


class CpAudit(models.Model):
    line_id = models.IntegerField(verbose_name="ID строки выписки")
    date = models.DateField(null=True, verbose_name="Дата")
    year = models.IntegerField(null=True, verbose_name="Год")
    direction = models.IntegerField(verbose_name="Направление")

    inn = models.CharField(max_length=20, null=True, verbose_name="ИНН")
    cp_name = models.TextField(null=True, verbose_name="Контрагент")
    kbk = models.CharField(max_length=20, null=True, verbose_name="КБК")
    ba_key = models.CharField(max_length=5, null=True, verbose_name="Счёт контрагента, 5 цифр")
    ba_number = models.CharField(max_length=32, null=True, verbose_name="Наш счёт")
    description = models.TextField(null=True, verbose_name="Назначение")

    amount = models.FloatField(null=True, verbose_name="Сумма")
    is_open = models.BooleanField(default=False, verbose_name="Не разнесено")

    cf_item_id = models.IntegerField(null=True, verbose_name="ID статьи")
    cf_code = models.CharField(max_length=6, null=True, verbose_name="Код статьи")
    cf_name = models.CharField(max_length=200, null=True, verbose_name="Статья")
    article_code = models.CharField(max_length=6, null=True, verbose_name="Код статьи верхнего уровня")
    article_name = models.CharField(max_length=200, null=True, verbose_name="Статья верхнего уровня")
    manual = models.BooleanField(default=False, verbose_name="Руками")

    rule_resolver_id = models.IntegerField(null=True, verbose_name="Резолвер правила")
    rule_kind = models.CharField(max_length=5, null=True, verbose_name="Тип резолвера правила")
    kbk_resolver_id = models.IntegerField(null=True, verbose_name="Резолвер КБК")
    ba_resolver_id = models.IntegerField(null=True, verbose_name="Резолвер счёта")
    cp_resolver_id = models.IntegerField(null=True, verbose_name="Резолвер контрагента (по ИНН)")

    class Meta:
        managed = False
        db_table = "dashboard_cp_audit"
        verbose_name = "Контрагент и статья"
        verbose_name_plural = "Контрагенты и статьи"
        ordering = ["-date", "id"]

    def __str__(self):
        return f"{self.date} {self.cp_name} {self.amount}"
