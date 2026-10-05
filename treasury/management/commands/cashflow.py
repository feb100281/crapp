"""
Переоценка денег и ДДС в рублях → data/parquet/treasury/*.parquet

    python manage.py cashflow            — из консоли
    задача «ДДС и переоценка» в «Списке команд» (run_job передаёт job_id)
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models.jobs import Jobs, JobStatus
from treasury.services.cash_reports import build


class Command(BaseCommand):
    help = "Переоценка денег и ДДС в рублях → data/parquet/treasury/*.parquet"

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=int, nargs="?")

    def handle(self, *args, **options):
        job_id = options.get("job_id")

        if not job_id:
            build(log=self.stdout.write)
            return

        job = Jobs.objects.get(pk=job_id)
        line = "=" * 60

        with open(job.logfile.path, "w", encoding="utf-8") as log:

            def writelog(text=""):
                log.write(str(text) + "\n")
                log.flush()

            writelog(f"{timezone.now()}: Start {job.command} ({job.name})")
            writelog(line)
            try:
                build(log=writelog)
                writelog(line)
                writelog("DONE !!!!")
                job.status = JobStatus.DONE
            except Exception as exc:
                writelog(line)
                writelog("FAILED")
                writelog(str(exc))
                job.status = JobStatus.FAILED
                raise
            finally:
                job.lastrun = timezone.now()
                job.save(update_fields=["status", "lastrun"])
