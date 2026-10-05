"""
Первичная загрузка контрагентов.

Два этапа:

    1. DaData  — по всем ИНН из банковских выписок запрашиваем карточки
                 организаций и складываем сырые ответы в parquet.
    2. Модель  — parquet + выписки прогоняем через sql/bs/cp_init.sql
                 и обновляем модель cp.CP.

Параметры задачи (Jobs.param), все необязательные:

    {
        "dadata_api_key": "...",   # по умолчанию берётся из .env
        "skip_fetch": true,        # не ходить в DaData, взять готовый parquet
        "skip_load": true,         # не трогать модель, только спарсить
        "limit": 100               # ограничить число ИНН (для проверки)
    }
"""

import json
import time

import duckdb

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from cp.admin.services.dadata import REQUEST_DELAY, build_session, fetch_party
from cp.admin.services.loader import load_counterparties

from ...models.jobs import Jobs, JobStatus

LINE = "============================================================"
THIN = "------------------------------------------------------------"


class Command(BaseCommand):
    help = "Первичная загрузка контрагентов из DaData"

    def add_arguments(self, parser):
        parser.add_argument(
            "job_id",
            type=int,
        )

    def handle(self, *args, **options):
        job_id = options["job_id"]

        job = Jobs.objects.get(pk=job_id)

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
            writelog("Running...")

            try:
                params = job.param or {}

                api_key = (
                    params.get("dadata_api_key")
                    or settings.DADATA_API_KEY
                )

                skip_fetch = bool(params.get("skip_fetch"))
                skip_load = bool(params.get("skip_load"))
                limit = params.get("limit")

                target_parquet = (
                    settings.PARQUET_FILES_PATH
                    / "cps"
                    / "init_cp.parquet"
                )

                target_parquet.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                bs_parquet = (
                    settings.PARQUET_FILES_PATH
                    / "bs"
                    / "bs.parquet"
                )

                # =====================================================
                # Этап 1. DaData -> parquet
                # =====================================================

                if skip_fetch:
                    writelog(LINE)
                    writelog(
                        "DADATA: пропущено (skip_fetch), "
                        "используем готовый снапшот"
                    )
                    writelog(f"Файл: {target_parquet}")

                    if not target_parquet.exists():
                        raise FileNotFoundError(
                            f"Не найден снапшот DaData: {target_parquet}"
                        )

                else:
                    self.fetch_stage(
                        writelog=writelog,
                        api_key=api_key,
                        bs_parquet=bs_parquet,
                        target_parquet=target_parquet,
                        limit=limit,
                    )

                # =====================================================
                # Этап 2. parquet -> модель CP
                # =====================================================

                if skip_load:
                    writelog(LINE)
                    writelog("МОДЕЛЬ: пропущено (skip_load)")

                else:
                    writelog(LINE)
                    writelog("ОБНОВЛЕНИЕ МОДЕЛИ КОНТРАГЕНТОВ")
                    writelog(LINE)

                    stats = load_counterparties(log=writelog)

                    writelog(THIN)
                    writelog(f"Всего в снапшоте: {stats['total']}")
                    writelog(f"Создано: {stats['created']}")
                    writelog(f"Обновлено: {stats['updated']}")
                    writelog(f"Пропущено (без ИНН): {stats['skipped']}")
                    writelog(LINE)

                job.status = JobStatus.DONE
                job.lastrun = timezone.now()

                job.save(
                    update_fields=[
                        "status",
                        "lastrun",
                    ]
                )

            except Exception as exc:
                writelog(LINE)
                writelog("FAILED")
                writelog(repr(exc))
                writelog(LINE)

                job.status = JobStatus.FAILED
                job.lastrun = timezone.now()

                job.save(
                    update_fields=[
                        "status",
                        "lastrun",
                    ]
                )

                raise

    # ==================================================================
    # Этап 1
    # ==================================================================

    def fetch_stage(
        self,
        writelog,
        api_key,
        bs_parquet,
        target_parquet,
        limit=None,
    ):
        writelog(LINE)
        writelog("DADATA INITIAL COUNTERPARTIES")
        writelog(LINE)
        writelog(f"Source: {bs_parquet}")
        writelog(f"Target: {target_parquet}")

        if not api_key:
            raise RuntimeError(
                "Не задан DADATA_API_KEY (.env) и не передан "
                "dadata_api_key в параметрах задачи"
            )

        if not bs_parquet.exists():
            raise FileNotFoundError(
                f"Не найдены банковские выписки: {bs_parquet}"
            )

        session = build_session(api_key)

        with duckdb.connect(":memory:") as con:

            # -------------------------------------------------
            # Собираем уникальные ИНН
            # -------------------------------------------------

            con.execute(
                f"""
                CREATE TEMP TABLE inns AS

                WITH source AS (

                    SELECT DISTINCT
                        inn_payer AS inn
                    FROM read_parquet('{bs_parquet}')

                    UNION

                    SELECT DISTINCT
                        inn_receiver AS inn
                    FROM read_parquet('{bs_parquet}')
                )

                SELECT DISTINCT
                    trim(inn::VARCHAR) AS inn

                FROM source

                WHERE inn IS NOT NULL
                  AND trim(inn::VARCHAR) <> ''
                  AND regexp_matches(
                        trim(inn::VARCHAR),
                        '^[0-9]{{10}}$|^[0-9]{{12}}$'
                      )
                ;
                """
            )

            inns = [
                row[0]
                for row in con.execute(
                    """
                    SELECT inn
                    FROM inns
                    ORDER BY inn
                    """
                ).fetchall()
            ]

            if limit:
                inns = inns[: int(limit)]

                writelog(f"Ограничение limit: {len(inns)}")

            total = len(inns)

            writelog(
                f"Найдено уникальных корректных ИНН: {total}"
            )

            if total == 0:
                raise RuntimeError(
                    "В выписках не найдено ни одного корректного ИНН"
                )

            # -------------------------------------------------
            # Таблица результатов
            # -------------------------------------------------

            con.execute(
                """
                CREATE TABLE counterparties_raw (
                    inn         VARCHAR PRIMARY KEY,
                    payload     JSON,
                    fetched_at  TIMESTAMP,
                    found       BOOLEAN,
                    http_status INTEGER,
                    error       VARCHAR
                );
                """
            )

            # -------------------------------------------------
            # Парсим
            # -------------------------------------------------

            success = 0
            not_found = 0
            failed = 0

            started_at = time.monotonic()

            try:
                for index, inn in enumerate(inns, start=1):

                    result = fetch_party(
                        inn,
                        session=session,
                        log=writelog,
                    )

                    payload = result["payload"]
                    http_status = result["status"]
                    error = result["error"]

                    suggestions = []

                    if payload:
                        suggestions = payload.get("suggestions", [])

                    found = bool(suggestions)

                    payload_json = (
                        json.dumps(payload, ensure_ascii=False)
                        if payload is not None
                        else None
                    )

                    con.execute(
                        """
                        INSERT INTO counterparties_raw (
                            inn,
                            payload,
                            fetched_at,
                            found,
                            http_status,
                            error
                        )
                        VALUES (?, ?, current_timestamp, ?, ?, ?)
                        """,
                        [
                            inn,
                            payload_json,
                            found,
                            http_status,
                            error,
                        ],
                    )

                    if http_status == 200:

                        if found:
                            success += 1

                            company_name = (
                                suggestions[0]
                                .get("data", {})
                                .get("name", {})
                                .get("short")
                            )

                            writelog(
                                f"[{index}/{total}] {inn} -> "
                                f"{company_name or 'FOUND'}"
                            )

                        else:
                            not_found += 1

                            writelog(
                                f"[{index}/{total}] {inn} -> NOT FOUND"
                            )

                    else:
                        failed += 1

                        writelog(
                            f"[{index}/{total}] {inn} -> ERROR "
                            f"{http_status}: {error}"
                        )

                    # Progress
                    if index % 100 == 0:
                        elapsed = time.monotonic() - started_at

                        speed = index / elapsed if elapsed > 0 else 0

                        writelog(THIN)
                        writelog(
                            f"Progress: {index}/{total} "
                            f"({index / total:.1%})"
                        )
                        writelog(
                            f"FOUND={success}, "
                            f"NOT_FOUND={not_found}, "
                            f"FAILED={failed}, "
                            f"SPEED={speed:.2f} req/sec"
                        )
                        writelog(THIN)

                    # Throttling
                    time.sleep(REQUEST_DELAY)

            finally:
                session.close()

            # -------------------------------------------------
            # Сохраняем Parquet
            # -------------------------------------------------

            writelog(LINE)
            writelog("Запросы завершены. Сохраняем Parquet...")

            con.execute(
                f"""
                COPY (
                    SELECT *
                    FROM counterparties_raw
                    ORDER BY inn
                )
                TO '{target_parquet}'
                (
                    FORMAT PARQUET,
                    COMPRESSION ZSTD
                )
                """
            )

            result_count = con.execute(
                f"""
                SELECT count(*)
                FROM read_parquet('{target_parquet}')
                """
            ).fetchone()[0]

            writelog(f"Строк записано в parquet: {result_count}")
            writelog(f"FOUND: {success}")
            writelog(f"NOT FOUND: {not_found}")
            writelog(f"FAILED: {failed}")
            writelog(f"File: {target_parquet}")
            writelog(LINE)
