from django.db import models
from pathlib import Path
from django.conf import settings
from django.utils import timezone


class JobStatus(models.TextChoices):
    INACTIVE = "INACTIVE", "Не запускалась"
    PENDING = "PENDING", "Ожидает"
    RUNNING = "RUNNING", "Выполняется"
    DONE = "DONE", "Выполнено"
    FAILED = "FAILED", "Ошибка"
    CANCELED = "CANCELED", "Отменено"


class ScopeOfWorks(models.TextChoices):
    SERVER = "SERVER", "Сервер"
    LOCAL = "LOCAL", "Локалка"
    LOCSER = "LOCSER", "Локалка / Сервер"


class JobTypes(models.TextChoices):
    DATA = "DATA", "Данные и миграции"
    ETL = "ETL", "Расчеты"
    PUBLISH = "PUBLISH", "Обновления"

class Jobs(models.Model):
    jobtype = models.CharField(
        verbose_name="Тип задачи",
        max_length=20,
        choices=JobTypes.choices,
        default=JobTypes.DATA,
    )

    name = models.CharField(
        verbose_name="Задача",
        max_length=250,
    )

    command = models.CharField(
        verbose_name="Команда",
        max_length=250,
        unique=True,
    )

    description = models.TextField(
        verbose_name="Описание",
        blank=True,
    )

    scope = models.CharField(
        verbose_name="Область",
        max_length=20,
        choices=ScopeOfWorks.choices,
        default=ScopeOfWorks.LOCAL,
    )

    param = models.JSONField(
        verbose_name="Параметры",
        default=dict,
        blank=True,
    )

    status = models.CharField(
        verbose_name="Статус",
        max_length=20,
        choices=JobStatus.choices,
        default=JobStatus.INACTIVE,
    )

    lastrun = models.DateTimeField(
        verbose_name="Последний запуск",
        null=True,
        blank=True,
    )

    logfile = models.FileField(
        verbose_name="Файл лога",
        upload_to="jobs/logs/",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Команда"
        verbose_name_plural = "Список команд"
        ordering = ["jobtype", "name"]

    def save(self, *args, **kwargs):

        super().save(*args, **kwargs)

        relative_path = Path("jobs") / "logs" / f"{self.command}.log"
        absolute_path = Path(settings.MEDIA_ROOT) / relative_path
        if not absolute_path.exists():
            absolute_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            created = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
            absolute_path.write_text(
                "============================================================\n"
                f"JOB      : {self.command}\n"
                f"CREATED  : {created}\n"
                "============================================================\n\n",
                encoding="utf-8",
            )
        if self.logfile.name != str(relative_path):
            self.logfile.name = str(relative_path)
            super().save(update_fields=["logfile"])

    def __str__(self):
        return self.name