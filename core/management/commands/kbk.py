# Парсер kbk

from pathlib import Path

import duckdb

from django.conf import settings

from django.core.management.base import BaseCommand

class Command(BaseCommand):

    help = "Собирает исторический справочник КБК"

    def add_arguments(self, parser):

        parser.add_argument(

            "years",

            nargs="*",

            type=int,

            default=[2023, 2024, 2025, 2026],

        )

    def handle(self, *args, **options):

        years = options["years"]

        source_dir = (

            Path(settings.BASE_DIR)

            / "data"

            / "raw"

            / "kbk"

        )

        output_file = (

            Path(settings.PARQUET_FILES_PATH)

            / "macro"

            / "kbk.parquet"

        )

        output_file.parent.mkdir(

            parents=True,

            exist_ok=True

        )

        queries = []

        for year in years:

            file = source_dir / f"{year}.xlsx"

            if not file.exists():

                self.stdout.write(

                    self.style.WARNING(

                        f"{year}: файл не найден — {file}"

                    )

                )

                continue

            queries.append(

                f"""

                SELECT

                    {year}::INTEGER AS year,

                    *

                FROM read_xlsx(

                    '{file}',

                    header = true

                )

                """

            )

        if not queries:

            self.stdout.write(

                self.style.ERROR("Нет файлов для загрузки")

            )

            return

        union_sql = "\nUNION ALL\n".join(queries)

        with duckdb.connect() as con:

            con.execute(

                f"""

                COPY (

                    {union_sql}

                )

                TO '{output_file}'

                (

                    FORMAT PARQUET,

                    COMPRESSION ZSTD

                )

                """

            )

        self.stdout.write(

            self.style.SUCCESS(

                f"KBK → {output_file}"

            )

        )