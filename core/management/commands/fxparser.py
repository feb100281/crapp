from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET

import duckdb
import requests

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from macro.models.fx_model import Fx
from ...models.jobs import Jobs, JobStatus


LINE = "============================================================"
THIN = "------------------------------------------------------------"

CBR_URL = "https://www.cbr.ru/scripts/XML_dynamic.asp"


class Command(BaseCommand):
    help = "Грузим курсы валют с сайта ЦБ"

    def add_arguments(self, parser):
        parser.add_argument(
            "job_id",
            type=int,
        )

    def handle(self, *args, **options):
        job_id = options["job_id"]

        job = Jobs.objects.get(pk=job_id)

        params = job.param or {}

        # ---------------------------------------------------------
        # Дата начала загрузки
        #
        # Можно передать:
        #
        # {
        #     "date_from": "2024-01-01"
        # }
        #
        # Если не передано — грузим с 01.01.2023
        # ---------------------------------------------------------

        raw_date_from = params.get("date_from") or "2023-01-01"

        if isinstance(raw_date_from, date):
            date_from = raw_date_from
        else:
            date_from = date.fromisoformat(str(raw_date_from))

        date_to = timezone.localdate()
        loaded_at = timezone.now()

        # ---------------------------------------------------------
        # Папка parquet
        # ---------------------------------------------------------

        output_dir = (
            Path(settings.PARQUET_FILES_PATH)
            / "fx"
        )

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        log_file = job.logfile.path

        def writelog(text=""):
            log.write(str(text) + "\n")
            log.flush()

        with open(log_file, "w", encoding="utf-8") as log:

            writelog("")
            writelog(
                f"{timezone.now()}: Start {job.command} ({job.name})"
            )
            writelog(LINE)
            writelog("Parameters:")
            writelog(str(job.param))
            writelog(LINE)

            writelog(
                f"Date range: {date_from} -> {date_to}"
            )

            writelog(
                f"Output: {output_dir}"
            )

            writelog(LINE)
            writelog("Running...")

            job.status = JobStatus.RUNNING
            job.save(update_fields=["status"])

            try:

                # =================================================
                # DuckDB используем только для формирования parquet
                # =================================================

                with duckdb.connect(":memory:") as con:

                    for fx in Fx.objects.all().order_by("code"):

                        writelog("")
                        writelog(THIN)

                        writelog(
                            f"{fx.code} | "
                            f"{fx.sid} | "
                            f"{fx.name}"
                        )

                        # -----------------------------------------
                        # Запрос в ЦБ
                        # -----------------------------------------

                        response = requests.get(
                            CBR_URL,
                            params={
                                "date_req1": date_from.strftime(
                                    "%d/%m/%Y"
                                ),
                                "date_req2": date_to.strftime(
                                    "%d/%m/%Y"
                                ),
                                "VAL_NM_RQ": fx.sid,
                            },
                            timeout=60,
                            headers={
                                "User-Agent": "Mozilla/5.0",
                            },
                        )

                        response.raise_for_status()

                        # -----------------------------------------
                        # XML
                        # -----------------------------------------

                        root = ET.fromstring(
                            response.content
                        )

                        rows = []

                        for record in root.findall("Record"):

                            dt = datetime.strptime(
                                record.attrib["Date"],
                                "%d.%m.%Y",
                            ).date()

                            nominal = int(
                                record.findtext("Nominal")
                            )

                            value = Decimal(
                                record
                                .findtext("Value")
                                .replace(",", ".")
                            )

                            # Курс именно за 1 единицу валюты
                            rate = value / Decimal(nominal)

                            rows.append(
                                (
                                    fx.id,
                                    dt,
                                    fx.code,
                                    nominal,
                                    value,
                                    rate,
                                    loaded_at,
                                )
                            )

                        writelog(
                            f"Received: {len(rows)} rows"
                        )

                        # -----------------------------------------
                        # Временная таблица
                        # -----------------------------------------

                        con.execute("""
                            CREATE OR REPLACE TEMP TABLE fx_rates (
                                fx_id       BIGINT,
                                dt          DATE,
                                code        VARCHAR,
                                nominal     INTEGER,
                                value       DECIMAL(18, 8),
                                rate        DECIMAL(18, 10),
                                loaded_at   TIMESTAMPTZ
                            )
                        """)

                        if rows:

                            con.executemany(
                                """
                                INSERT INTO fx_rates
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                rows,
                            )

                        # -----------------------------------------
                        # Полностью перезаписываем parquet
                        # -----------------------------------------

                        output_path = (
                            output_dir
                            / f"{fx.code.lower()}.parquet"
                        )

                        # Для SQL-строки DuckDB
                        parquet_path = (
                            output_path
                            .resolve()
                            .as_posix()
                            .replace("'", "''")
                        )

                        con.execute(
                            f"""
                            COPY (
                                SELECT
                                    fx_id,
                                    dt,
                                    code,
                                    nominal,
                                    value,
                                    rate,
                                    loaded_at

                                FROM fx_rates

                                ORDER BY dt
                            )
                            TO '{parquet_path}'
                            (
                                FORMAT PARQUET,
                                COMPRESSION ZSTD
                            )
                            """
                        )

                        writelog(
                            f"Saved: {output_path}"
                        )

                # =================================================
                # DONE
                # =================================================

                job.status = JobStatus.DONE
                job.save(update_fields=["status"])

                writelog("")
                writelog(LINE)
                writelog(
                    f"{timezone.now()}: DONE"
                )
                writelog(LINE)

            except Exception as e:

                job.status = JobStatus.FAILED
                job.save(update_fields=["status"])

                writelog("")
                writelog(LINE)
                writelog("FAILED")
                writelog(str(e))
                writelog(LINE)

                raise