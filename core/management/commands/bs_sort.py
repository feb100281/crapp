"""
Раскладка папки выписок по банкам и счетам (treasury/services/bs_sort.py).

Тот же шаг выполняется в начале «Выписок». Отдельный запуск — чтобы
посмотреть план или разложить без загрузки.

Параметры задачи (Jobs.param), необязательные:
    {"source": "...", "dry_run": true}   # папка (по умолчанию — из «Выписок»), только показать план
"""

from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from treasury.services.bs_sort import known_accounts, sort_folder

from ...models.jobs import Jobs, JobStatus

LINE = "=" * 60


def statements_folder() -> str | None:
    job = Jobs.objects.filter(command="bs").first()
    return (job.param or {}).get("source") if job else None


class Command(BaseCommand):
    help = "Разложить выписки по папкам: БАНК / ВАЛЮТА СЧЁТ / ГОД"

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=int)

    def handle(self, *args, **options):
        job = Jobs.objects.get(pk=options["job_id"])
        params = job.param or {}

        with open(job.logfile.path, "w", encoding="utf-8") as log:
            def writelog(text=""):
                log.write(str(text) + "\n")
                log.flush()

            writelog(f"{timezone.now()}: Start {job.command} ({job.name})")
            writelog(LINE)
            try:
                source = params.get("source") or statements_folder()
                if not source:
                    raise ValueError("не задана папка выписок: параметр source или задача «Выписки»")
                root = Path(source).expanduser().resolve()
                writelog(f"Папка: {root}")
                stats = sort_folder(root, known_accounts(), apply=not params.get("dry_run"), log=writelog)
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
