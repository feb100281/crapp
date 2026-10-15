"""
Справочник контрагентов: новые ИНН из выписок → DaData → cp_cp.

Только ИНН, которых в справочнике ещё нет, поэтому повторный запуск
занимает секунды. Тот же шаг выполняется в конце команды «Выписки».

Параметры задачи (Jobs.param), необязательные:
    {"limit": 100}   # ограничить число ИНН (для проверки)
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from cp.admin.services.sync import sync_new

from ...models.jobs import Jobs, JobStatus

LINE = "=" * 60


class Command(BaseCommand):
    help = "Новые ИНН из выписок → справочник контрагентов (DaData)"

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=int)

    def handle(self, *args, **options):
        job = Jobs.objects.get(pk=options["job_id"])

        with open(job.logfile.path, "w", encoding="utf-8") as log:
            def writelog(text=""):
                log.write(str(text) + "\n")
                log.flush()

            writelog(f"{timezone.now()}: Start {job.command} ({job.name})")
            writelog(LINE)
            try:
                stats = sync_new(log=writelog, limit=(job.param or {}).get("limit"))
                writelog(LINE)
                writelog(f"Итог: {stats}")
                writelog("DONE")
                job.status = JobStatus.DONE
            except Exception as exc:
                writelog(LINE)
                writelog("FAILED")
                writelog(repr(exc))
                job.status = JobStatus.FAILED
                raise
            finally:
                job.lastrun = timezone.now()
                job.save(update_fields=["status", "lastrun"])
