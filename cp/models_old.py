from django.db import models
from pathlib import Path
# Create your models here.

from django.core.exceptions import ValidationError


def validate_svg(file):
    """
    Разрешает загрузку только SVG.
    """

    if Path(file.name).suffix.lower() != ".svg":
        raise ValidationError("Допускаются только SVG-файлы.")

    file.seek(0)
    data = file.read()

    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="ignore")

    if "<svg" not in data.lower():
        raise ValidationError("Файл не является корректным SVG.")

    file.seek(0)

def name_field(**kwargs):
    defaults = {
        "max_length": 255,
        "null": True,
        "blank": True,
        "verbose_name": "Название",
    }

    defaults.update(kwargs)
    return models.CharField(**defaults)

def avatar_field(**kwargs):
    defaults = {
            "upload_to":"avatar/",
            "validators":[validate_svg],
            "blank":True,
            "null":True,
            "verbose_name": "Аватар",
    }
    defaults.update(kwargs)
    return models.FileField(**defaults)


class CPStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Действующий"
    LIQUIDATING = "LIQUIDATING", "Ликвидируется"
    LIQUIDATED = "LIQUIDATED", "Ликвидирован"
    BANKRUPT = "BANKRUPT", "Банкротство"
    REORGANIZING = "REORGANIZING", "Реорганизация"
    NA = "NA", "Нет данных"


class CP(models.Model):
    inn = models.CharField(
        max_length=12,
        unique=True,
        db_index=True,
        verbose_name="ИНН",
    )

    ogrn = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        db_index=True,
        verbose_name="ОГРН / ОГРНИП",
    )

    name = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        db_index=True,        
        verbose_name="Наименование",
    )

    address = models.TextField(
        blank=True,
        null=True,
        verbose_name="Адрес",
    )

    registration_date = models.DateField(
        blank=True,
        null=True,
        verbose_name="Дата регистрации",
    )

    manager_name = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        verbose_name="Руководитель",
    )

    country = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Страна",
    )

    region = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Регион",
    )
    
    avatar = avatar_field()

    status = models.CharField(
        max_length=30,
        choices=CPStatus.choices,
        default=CPStatus.NA,
        db_index=True,
        verbose_name="Статус",
    )

    ba = models.JSONField(
        default=list,
        blank=True,
        null=True,
        verbose_name="Банковские счета",
    )

    updated_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Обновлено из источника",
    )
    
    payload = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        verbose_name="Данные",
    )

    class Meta:
        verbose_name = "Контрагент"
        verbose_name_plural = "Контрагенты"
        ordering = ["name"]

    def __str__(self):
        return self.name or self.inn