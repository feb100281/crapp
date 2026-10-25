"""
Касса: карточки счёта 50 из 1С (Excel) → data/parquet/cash/.

    cash_desk.parquet        — строки карточек (приход/расход, корр. счёт, аналитика)
    cash_desk_files.parquet  — итоги каждого файла: сальдо и обороты для сверки

Параметры задачи (Jobs.param):
    {"source": "/Users/.../07 КАССА"}   # папка с выгрузками, подпапки допускаются
"""

from pathlib import Path

import duckdb
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from treasury.services.cash_desk import COLUMNS, FILE_COLUMNS, parse_folder

from ...models.jobs import Jobs, JobStatus

LINE = "=" * 60


def write_parquet(rows: list[dict], columns: dict[str, str], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE t (" + ", ".join(f'"{k}" {v}' for k, v in columns.items()) + ")")
        if rows:
            names = list(columns)
            con.executemany(f"INSERT INTO t VALUES ({', '.join('?' * len(names))})",
                            [[r.get(n) for n in names] for r in rows])
        con.execute(f"COPY t TO '{target.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()


class Command(BaseCommand):
    help = "Касса: карточки счёта 50 (Excel из 1С) → parquet"

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
                source = (job.param or {}).get("source")
                if not source:
                    raise ValueError("не задан параметр source — папка с карточками счёта 50")
                root = Path(source).expanduser().resolve()
                writelog(f"Папка: {root}")
                rows, files = parse_folder(root, log=writelog)

                writelog(LINE)
                for f in sorted(files, key=lambda x: str(x["date_from"])):
                    got = [r for r in rows if r["source_file"] == f["source_file"]]
                    dt = round(sum(r["amount"] for r in got if r["direction"] == 1), 2)
                    kt = round(sum(r["amount"] for r in got if r["direction"] == 2), 2)
                    ok = None not in (f["opening"], f["closing"]) and \
                        abs(f["opening"] + dt - kt - f["closing"]) < 0.01
                    writelog(f"  {f['source_file']}: начало {f['opening']} + приход {dt} − расход {kt} "
                             f"= конец {f['closing']} · {'сходится' if ok else 'НЕ СХОДИТСЯ'}")

                out = Path(settings.PARQUET_FILES_PATH) / "cash"
                write_parquet(rows, COLUMNS, out / "cash_desk.parquet")
                write_parquet(files, FILE_COLUMNS, out / "cash_desk_files.parquet")
                writelog(LINE)
                writelog(f"Записано: {out / 'cash_desk.parquet'} ({len(rows)} строк), "
                         f"{out / 'cash_desk_files.parquet'} ({len(files)} файлов)")
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
