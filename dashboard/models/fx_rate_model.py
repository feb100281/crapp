"""
Витрина курсов ЦБ (таблица dashboard_fx_rate).

managed = False: таблицу ведёт dashboard.services.fx_feed — бот обновляет её сам
каждые несколько часов с сайта ЦБ, включая курс на завтра, когда ЦБ его установит.
"""

from django.db import models


class FxRate(models.Model):
    date = models.DateField(verbose_name="Дата")
    code = models.CharField(max_length=3, verbose_name="Валюта")
    nominal = models.IntegerField(default=1, verbose_name="Номинал")
    rate = models.FloatField(verbose_name="Курс за 1 единицу, ₽")
    loaded_at = models.DateTimeField(null=True, verbose_name="Загружено")

    class Meta:
        managed = False
        db_table = "dashboard_fx_rate"
        verbose_name = "Курс ЦБ"
        verbose_name_plural = "Курсы ЦБ"
        ordering = ["-date", "code"]

    def __str__(self):
        return f"{self.date} {self.code} {self.rate}"
