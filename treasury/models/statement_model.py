# Банковские выписки

from django.db import models
from .ba_model import BankAccount

class Statement(models.Model):
    sid = models.CharField(
        max_length=36,
        unique=True,
        verbose_name="SID",
    )

    source_file = models.CharField(
        max_length=250,
        verbose_name="Файл",
        null=True,
        blank=True,
    )

    # Номер счёта непосредственно из выписки
    ba_number = models.CharField(
        max_length=56,
        verbose_name="Номер счёта в выписке",
        null=True,
        blank=True,
    )
    
    ba_account = models.ForeignKey(
        BankAccount,
        verbose_name="Счёт",
        related_name="statements",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    date_from = models.DateField(
        verbose_name="Начало периода",
    )

    date_to = models.DateField(
        verbose_name="Конец периода",
    )

    bb = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        verbose_name="Начальный баланс",
    )

    eb = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        verbose_name="Конечный баланс",
    )

    dt = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        verbose_name="Поступления",
    )

    cr = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        verbose_name="Списания",
    )

    class Meta:
        verbose_name = "Выписка"
        verbose_name_plural = "Выписки"
        ordering = ["-date_to", "ba_number"]

    def __str__(self) -> str:
        ba = (
            f"...{self.ba_number[-6:]}"
            if self.ba_number
            else "Без счёта"
        )

        return (
            f"{ba} "
            f"{self.date_from:%d.%m.%Y} - "
            f"{self.date_to:%d.%m.%Y}"
        )
    
    