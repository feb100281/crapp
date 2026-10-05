# management/commands/load_bik.py

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import duckdb
import requests

from django.conf import settings
from django.core.management.base import BaseCommand

from cp.models.bank_model import Bank
from ...models.jobs import Jobs, JobStatus


CBR_BIK_URL = "https://www.cbr.ru/s/newbik"


class Command(BaseCommand):
    help = "Загружаем справочник БИК Банка России (ED807)"
    
    def add_arguments(self, parser):
            parser.add_argument(
                "job_id",
                type=int,
            )
            

    def handle(self, *args, **options):
        
        job_id = options["job_id"]
        
        job = Jobs.objects.get(pk=job_id)

        params = job.param or {}
        
        log_file = job.logfile.path
        
        def writelog(text=""):
            log.write(str(text) + "\n")
            log.flush()
        
        with open(log_file, "w", encoding="utf-8") as log:
        
                    writelog("RUNNING --- ALL GOOD")

        # ============================================================
        # Пути
        # ============================================================

        output_dir = Path(settings.PARQUET_FILES_PATH) / "banks"
        output_dir.mkdir(parents=True, exist_ok=True)

        bik_file = output_dir / "bik.parquet"
        accounts_file = output_dir / "bik_accounts.parquet"

        # ============================================================
        # Скачиваем ZIP ED807 с сайта ЦБ
        # ============================================================

        response = requests.get(
            CBR_BIK_URL,
            timeout=60,
            headers={
                "User-Agent": "Mozilla/5.0",
            },
        )

        response.raise_for_status()

        # ============================================================
        # Достаём XML из ZIP прямо в память
        # ============================================================

        with ZipFile(BytesIO(response.content)) as zf:

            xml_name = next(
                name
                for name in zf.namelist()
                if name.lower().endswith(".xml")
            )

            xml_data = zf.read(xml_name)

        root = ET.fromstring(xml_data)

        # ============================================================
        # Namespace ED807
        #
        # Не хардкодим namespace — вытаскиваем его из root.
        # ============================================================

        namespace = root.tag.split("}")[0].strip("{")

        ns = {
            "ed": namespace,
        }

        # ============================================================
        # Основной справочник БИК
        # ============================================================

        bik_rows = []

        # ============================================================
        # Счета участников
        # ============================================================

        account_rows = []

        for entry in root.findall("ed:BICDirectoryEntry", ns):

            bic = entry.attrib.get("BIC")

            participant = entry.find("ed:ParticipantInfo", ns)

            if participant is None:
                continue

            # ========================================================
            # ParticipantInfo
            # ========================================================

            row = {
                "bic": bic,

                "name": participant.attrib.get("NameP"),

                "eng_name": participant.attrib.get("EnglName"),

                "reg_number": participant.attrib.get("RegN"),

                "country_code": participant.attrib.get("CntrCd"),

                "region": participant.attrib.get("Rgn"),

                "postal_code": participant.attrib.get("Ind"),

                "tnp": participant.attrib.get("Tnp"),

                "city": participant.attrib.get("Nnp"),

                "address": participant.attrib.get("Adr"),

                "parent_bic": participant.attrib.get("PrntBIC"),

                "participant_type": participant.attrib.get("PtType"),

                "service": participant.attrib.get("Srvcs"),

                "xch_type": participant.attrib.get("XchType"),

                "uid": participant.attrib.get("UID"),

                "date_in": participant.attrib.get("DateIn"),

                "date_out": participant.attrib.get("DateOut"),

                "status": participant.attrib.get("ParticipantStatus"),
            }

            bik_rows.append(row)

            # ========================================================
            # Accounts
            # ========================================================

            for account in entry.findall("ed:Accounts", ns):

                account_rows.append(
                    {
                        "bic": bic,

                        "account": account.attrib.get("Account"),

                        "regulation_account_type":
                            account.attrib.get("RegulationAccountType"),

                        "account_cbr_bic":
                            account.attrib.get("AccountCBRBIC"),

                        "ck":
                            account.attrib.get("CK"),

                        "account_type":
                            account.attrib.get("AccountType"),

                        "date_in":
                            account.attrib.get("DateIn"),

                        "date_out":
                            account.attrib.get("DateOut"),
                    }
                )

        # ============================================================
        # DuckDB
        # ============================================================

        with duckdb.connect() as con:

            # ========================================================
            # Python list[dict] -> DuckDB
            #
            # DuckDB напрямую list[dict] как relation не регистрирует,
            # поэтому используем VALUES через executemany.
            # ========================================================

            con.execute("""
                CREATE TABLE bik (
                    bic VARCHAR,
                    name VARCHAR,
                    eng_name VARCHAR,
                    reg_number VARCHAR,
                    country_code VARCHAR,
                    region VARCHAR,
                    postal_code VARCHAR,
                    tnp VARCHAR,
                    city VARCHAR,
                    address VARCHAR,
                    parent_bic VARCHAR,
                    participant_type VARCHAR,
                    service VARCHAR,
                    xch_type VARCHAR,
                    uid VARCHAR,
                    date_in DATE,
                    date_out DATE,
                    status VARCHAR
                )
            """)

            con.executemany(
                """
                INSERT INTO bik VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    tuple(row.values())
                    for row in bik_rows
                ],
            )
            
            rows = con.execute(
                """
                SELECT
                    bic::TEXT AS bic,
                    name AS bank_name,

                    {
                        'eng_name': eng_name,
                        'address': address,
                        'city': city,
                        'country_code': country_code,
                        'date_in': date_in::TEXT,
                        'date_out': date_out::TEXT,
                        'parent_bic': parent_bic,
                        'participant_type': participant_type,
                        'postal_code': postal_code,
                        'reg_number': reg_number,
                        'region': region,
                        'service': service,
                        'status': status,
                        'tnp': tnp,
                        'uid': uid,
                        'xch_type': xch_type
                    } AS details

                FROM read_parquet('data/parquet/banks/bik.parquet')
                """
            ).fetchall()


            banks = [
                Bank(
                    bic=bic,
                    name=name,
                    details=details,
                )
                for bic, name, details in rows
            ]


            Bank.objects.bulk_create(
                banks,
                update_conflicts=True,
                unique_fields=["bic"],
                update_fields=["name", "details"],
            )
            

            # ========================================================
            # Accounts
            # ========================================================

            con.execute("""
                CREATE TABLE bik_accounts (
                    bic VARCHAR,
                    account VARCHAR,
                    regulation_account_type VARCHAR,
                    account_cbr_bic VARCHAR,
                    ck VARCHAR,
                    account_type VARCHAR,
                    date_in DATE,
                    date_out DATE
                )
            """)

            if account_rows:

                con.executemany(
                    """
                    INSERT INTO bik_accounts VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    [
                        tuple(row.values())
                        for row in account_rows
                    ],
                )

            # ========================================================
            # Сохраняем Parquet
            # ========================================================

            con.execute(
                f"""
                COPY (
                    SELECT *
                    FROM bik
                    ORDER BY bic
                )
                TO '{bik_file}'
                (
                    FORMAT PARQUET,
                    COMPRESSION ZSTD
                )
                """
            )

            con.execute(
                f"""
                COPY (
                    SELECT *
                    FROM bik_accounts
                    ORDER BY bic, account
                )
                TO '{accounts_file}'
                (
                    FORMAT PARQUET,
                    COMPRESSION ZSTD
                )
                """
            )

        # ============================================================
        # Результат
        # ============================================================

        self.stdout.write(
            self.style.SUCCESS(
                f"""
                Готово.

                БИК:      {len(bik_rows):,}
                Счета:    {len(account_rows):,}

                {bik_file}
                {accounts_file}
                """
                            )
                        )