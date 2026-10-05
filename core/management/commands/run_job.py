import traceback
from django.core.management import BaseCommand, call_command
from django.utils import timezone
from ...models.jobs import Jobs, JobStatus


class Command(BaseCommand):

    def add_arguments(self, parser):
        parser.add_argument(
            "job_id",
            type=int,
        )

    def handle(self, *args, **options):

        job_id = options["job_id"]

        job = Jobs.objects.get(pk=job_id)

        job.status = JobStatus.RUNNING
        job.lastrun = timezone.now()

        job.save(
            update_fields=[
                "status",
                "lastrun",
            ]
        )

        try:
            params = job.param or {}

            self.stdout.write(
                f"Запуск: {job.name}"
            )

            self.stdout.write(
                f"Команда: {job.command}"
            )

            self.stdout.write(
                f"Параметры: {params}"
            )

            call_command(
                job.command,
                job_id,
            )

            job.status = JobStatus.DONE

            self.stdout.write(
                self.style.SUCCESS("DONE")
            )

        except Exception:

            job.status = JobStatus.FAILED

            self.stderr.write(
                traceback.format_exc()
            )

            raise

        finally:
            job.save(
                update_fields=[
                    "status",
                ]
            )