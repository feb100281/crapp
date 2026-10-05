from django.db import models


class Fx(models.Model):
    sid = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Код ЦБ",
        null=True,
        blank=True,
    )

    code = models.CharField(
        max_length=3,
        verbose_name="Код валюты",
        null=True,
        blank=True,
    )

    numeric_code = models.CharField(
        max_length=3,
        unique=True,
        verbose_name="Цифровой код",
    )

    name = models.CharField(
        max_length=100,
        verbose_name="Наименование",
        null=True,
        blank=True,
    )

    badge = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name="Бейдж",
    )

    class Meta:
        verbose_name = "Валюта"
        verbose_name_plural = "Валюты"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.badge}"