import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone
import duckdb

from ...models.jobs import Jobs, JobStatus
from ..misc.helpers import SQLHelpers

from treasury.models.statement_model import Statement

from macro.models.fx_model import Fx


# ============================================================
# Хелперы
# ============================================================

def norm(value) -> str:
    """Нормализация части ключа: без лишних пробелов."""

    if value is None:
        return ""

    return " ".join(str(value).split())


def sha(*parts, length: int = 16) -> str:
    return hashlib.sha1(
        "|".join(norm(part) for part in parts).encode("utf-8")
    ).hexdigest()[:length]


def make_statement_id(relative_path: str) -> str:
    """
    Детерминированный id выписки = хеш имени файла.

    Один файл = одна выписка. Сколько файлов в папке, столько
    и разных statement_id — даже если банк нарезал файл на
    несколько секций СекцияРасчСчет (Газпромбанк — по блоку
    на календарный день). Все блоки одного файла и все его
    операции получают один и тот же id.
    """

    return sha(relative_path)


def write_jsonl(file_obj, row: dict):
    file_obj.write(
        json.dumps(row, ensure_ascii=False) + "\n"
    )


def count_lines(file: Path) -> int:
    with file.open("r", encoding="utf-8") as f:
        return sum(1 for _ in f)


# ============================================================
# Парсер одного файла
# ============================================================

def parse_bank_file(file: Path, bs_path: Path, statements_out, documents_out):
    """
    Разбирает один файл 1CClientBankExchange.

    Всё плоско и в лоб:

      * statement_id = хеш имени файла, один на весь файл;
      * каждая секция СекцияРасчСчет пишется как есть,
        с этим statement_id;
      * каждая операция получает тот же statement_id
        и счёт последней открытой секции. Имя файла в
        операциях не дублируем — оно лежит в выписке,
        джойнится по statement_id.

    Ничего не агрегируем, не дедуплицируем и не выкидываем.
    """

    relative_path = file.relative_to(bs_path).as_posix()

    statement_id = make_statement_id(relative_path)

    statement = None          # секция, которую читаем прямо сейчас
    current_account = None    # счёт последней закрытой секции
    document = None

    counts = {"statements": 0, "documents": 0}

    with file.open("r", encoding="cp1251", errors="ignore") as f:

        for raw_line in f:

            line = raw_line.strip()

            if not line:
                continue

            # ==================================================
            # Секция расчётного счёта
            # ==================================================

            if line == "СекцияРасчСчет":
                statement = {}
                continue

            if line == "КонецРасчСчет":

                if statement is not None:

                    current_account = norm(statement.get("РасчСчет")) or None

                    statement["statement_id"] = statement_id
                    statement["source_file"] = relative_path

                    write_jsonl(statements_out, statement)

                    counts["statements"] += 1

                    statement = None

                continue

            # ==================================================
            # Секция документа
            # ==================================================

            if line.startswith("СекцияДокумент="):

                _, document_type = line.split("=", 1)

                document = {"СекцияДокумент": document_type}

                continue

            if line == "КонецДокумента":

                if document is not None:

                    document["statement_id"] = statement_id
                    document["ba_number"] = current_account

                    write_jsonl(documents_out, document)

                    counts["documents"] += 1

                    document = None

                continue

            # ==================================================
            # key=value
            # ==================================================

            if "=" not in line:
                continue

            key, value = line.split("=", 1)

            if statement is not None:
                statement[key] = value
                continue

            if document is not None:
                document[key] = value
                continue

    # ==========================================================
    # Незакрытая секция РасчСчет (битый файл) — тоже сохраняем
    # ==========================================================

    if statement is not None:

        statement["statement_id"] = statement_id
        statement["source_file"] = relative_path

        write_jsonl(statements_out, statement)

        counts["statements"] += 1

    return counts


# ============================================================
# Парсер папки
# ============================================================

def parse_all(bs_path: Path, statements: Path, documents: Path, log=print):

    txt_files = sorted(
        file
        for file in bs_path.rglob("*.txt")
        if file.is_file()
    )

    log(f"Найдено TXT файлов: {len(txt_files)}")

    total_statements = 0
    total_documents = 0
    failed = 0

    with (
        statements.open("w", encoding="utf-8") as statements_out,
        documents.open("w", encoding="utf-8") as documents_out,
    ):

        for i, file in enumerate(txt_files, start=1):

            relative_path = file.relative_to(bs_path).as_posix()

            log(f"[{i}/{len(txt_files)}] {relative_path}")

            try:

                counts = parse_bank_file(
                    file=file,
                    bs_path=bs_path,
                    statements_out=statements_out,
                    documents_out=documents_out,
                )

                total_statements += counts["statements"]
                total_documents += counts["documents"]

                log(
                    f"    выписок: {counts['statements']}, "
                    f"операций: {counts['documents']}"
                )

            except Exception as e:

                failed += 1

                log(f"ERROR: {relative_path}\n{type(e).__name__}: {e}")

    return "\n".join([
        "Готово:",
        f"    Файлов:     {len(txt_files)} (ошибок: {failed})",
        f"    Выписок:    {total_statements}",
        f"    Операций:   {total_documents}",
        f"    Statements: {statements}",
        f"    Operations: {documents}",
    ])


# ============================================================
# Command
# ============================================================

class Command(BaseCommand):
    help = (
        "Разбирает выписки 1CClientBankExchange из папки "
        "в два JSONL: выписки и операции."
    )

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=int)

    def handle(self, *args, **options):

        job = Jobs.objects.get(pk=options["job_id"])

        log_file = job.logfile.path

        def writelog(log, text=""):
            log.write(str(text) + "\n")
            log.flush()

        line = "=" * 60

        with open(log_file, "w", encoding="utf-8") as log:

            writelog(log, "")
            writelog(log, f"{timezone.now()}: Start {job.command} ({job.name})")
            writelog(log, line)
            writelog(log, "Parameters:")
            writelog(log, str(job.param))
            writelog(log, line)
            writelog(log, "Running...")

            STATEMENTS_JSONL = (
                Path(settings.JSON_FILES_PATH) / "bs" / "statements.jsonl"
            )
            OPERATIONS_JSONL = (
                Path(settings.JSON_FILES_PATH) / "bs" / "operations.jsonl"
            )

            STATEMENTS_JSONL.parent.mkdir(parents=True, exist_ok=True)
            OPERATIONS_JSONL.parent.mkdir(parents=True, exist_ok=True)

            STATEMENTS_JSONL = STATEMENTS_JSONL.expanduser().resolve()
            OPERATIONS_JSONL = OPERATIONS_JSONL.expanduser().resolve()

            try:

                params = job.param or {}

                source = Path(params["source"]).expanduser().resolve()

                writelog(log, f"    - Getting data from {source}")
                writelog(log, line)
                writelog(log, "RUNNING PARSER")
                writelog(log, line)

                writelog(
                    log,
                    parse_all(
                        source,
                        STATEMENTS_JSONL,
                        OPERATIONS_JSONL,
                        log=lambda text: writelog(log, text),
                    ),
                )

                writelog(log, line)
                writelog(log, "Строк в файлах:")
                writelog(
                    log,
                    f"    {STATEMENTS_JSONL}: "
                    f"{count_lines(STATEMENTS_JSONL)}"
                )
                writelog(
                    log,
                    f"    {OPERATIONS_JSONL}: "
                    f"{count_lines(OPERATIONS_JSONL)}"
                )
                writelog(log, line)
                
                writelog(log, line)
                
                with duckdb.connect() as con:                   
                    
                    con.execute(
                        f""" 
                        ATTACH '{settings.SQLITE_CON}'
                        AS target_db
                        (TYPE SQLITE);                        
                        """
                    )
                    
                    query = SQLHelpers()
                                   
                    writelog(log, "Импорт банковских выписок ....")
                    writelog(log, "     - создаем временную таблицу выписок:")
                    
                    con.execute(
                        query.create_ststement_table,
                        parameters={'jsonfile':str(STATEMENTS_JSONL)}                        
                    )
                    
                    writelog(log, "     - импортируем валюты если прилетели новые в виписке:")
                    
                    rows = con.execute(
                        """ 
                        select DISTINCT 
                        substr(ba_number, 6, 3)::text AS currency_number
                        from statements
                        """
                    ).fetchall()
                    
                    for (numeric_code,) in rows:
                        Fx.objects.get_or_create(
                            numeric_code=numeric_code,
                        )
                    
                    writelog(log, "     - добавляем счета если нет:")
                    
                    con.execute(
                        """ 
                        INSERT INTO target_db.treasury_bankaccount BY NAME

                        SELECT DISTINCT
                            t.ba_number AS number,
                            f.id AS currency_id,
                            'MAIN' AS ba_type,
                            (SELECT min(id) FROM target_db.cp_gr) AS gr_id

                        FROM statements t

                        LEFT JOIN target_db.macro_fx f
                            ON f.numeric_code = substr(t.ba_number, 6, 3)::TEXT

                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM target_db.treasury_bankaccount s
                            WHERE s.number = t.ba_number
                        );
                        """
                    )                    
                    
                    # Разовая чистка: выписки со старым sid (hash() DuckDB,
                    # не 32 символа md5). Без неё они задвоятся с новыми.
                    # Трогаем только те, на которые ещё не ссылаются операции.
                    con.execute(
                        """
                        DELETE FROM target_db.treasury_statement
                        WHERE length(sid) <> 32
                          AND id NOT IN (
                              SELECT statement_id
                              FROM target_db.treasury_bsline
                              WHERE statement_id IS NOT NULL
                          );
                        """
                    )

                    writelog(log, "     - импортируем выписки:")
                                       

                    
                    con.execute(
                        """ 
                        INSERT INTO target_db.treasury_statement BY NAME

                        SELECT
                            t.sid::TEXT AS sid,
                            t.source_file,
                            t.ba_number,
                            a.id AS ba_account_id,
                            t.date_from,
                            t.date_to,
                            t.bb,
                            t.eb,
                            t.dt,
                            t.cr

                        FROM statements t

                        LEFT JOIN target_db.treasury_bankaccount a
                            ON a.number = t.ba_number

                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM target_db.treasury_statement s
                            WHERE s.sid = t.sid::TEXT
                        );                                           
                        """
                    )
                    
                    # ==================================================
                    # Операции
                    # ==================================================

                    writelog(log, "Импорт операций ....")

                    writelog(log, "     - bs_operations")
                    con.execute(
                        query.read("bs", "bs_operations.sql"),
                        parameters={"operations_json": str(OPERATIONS_JSONL)},
                    )

                    writelog(log, "     - bs_gl")
                    con.execute(query.read("bs", "bs_gl.sql"))

                    writelog(log, "     - bs_gl_adj (ИНН банка, комиссия, НДС)")
                    con.execute(query.read("bs", "bs_gl_adj.sql"))

                    ops, gl = con.execute(
                        "SELECT (SELECT count(*) FROM bs_operations), "
                        "(SELECT count(*) FROM bs_gl_adj)"
                    ).fetchone()
                    writelog(log, f"       операций: {ops}, строк: {gl}")
                    if ops != gl:
                        writelog(
                            log,
                            "       ВНИМАНИЕ: число строк не равно числу операций — "
                            "у части операций наш счёт не совпал ни с плательщиком, "
                            "ни с получателем",
                        )

                    before = con.execute(
                        "SELECT count(*) FROM target_db.treasury_bsline"
                    ).fetchone()[0]

                    writelog(log, "     - добавляем новые строки в базу")
                    con.execute(query.read("bs", "bs_line_insert.sql"))

                    after = con.execute(
                        "SELECT count(*) FROM target_db.treasury_bsline"
                    ).fetchone()[0]
                    writelog(log, f"       добавлено: {after - before}, всего: {after}")

                # ==================================================
                # Новые наши счета: субсчёт в плане счетов + напоминание
                # указать банк (без банка не подставится ИНН банка)
                # ==================================================

                from gl.services.chart import sync_bank_accounts
                from treasury.models.ba_model import BankAccount

                new_gl = sync_bank_accounts()
                if new_gl:
                    writelog(log, f"Новых счетов в плане счетов: {new_gl}")
                no_bank = list(
                    BankAccount.objects.filter(bank__isnull=True).values_list("number", flat=True)
                )
                if no_bank:
                    writelog(
                        log,
                        "ВНИМАНИЕ: у счетов не указан банк (Казначейство → Банковские счета): "
                        + ", ".join(no_bank),
                    )

                # ==================================================
                # Резолверы: новые КБК и счета + переразноска по правилам
                # ==================================================

                from treasury.services.resolver import resolve_all, sync

                writelog(log, "Разноска по резолверам ....")
                new = sync()
                stats = resolve_all()
                writelog(
                    log,
                    f"     новых резолверов: {new}, всего резолверов: {stats['resolvers']}, "
                    f"строк: {stats['lines']}, не разнесено: {stats['open']}",
                )

                # ==================================================
                # Переоценка: всё в рубли, курсовые по дням → parquet
                # Пересчитывается целиком при каждом импорте.
                # ==================================================

                # Сначала свежие курсы ЦБ (задача fxparser из «Списка команд»).
                # Не скачались (нет сети, ЦБ лежит) — считаем по тем, что уже есть.
                writelog(log, "Курсы ЦБ (fxparser) ....")
                fx_job = Jobs.objects.filter(command="fxparser").first()
                if fx_job is None:
                    writelog(log, "     ВНИМАНИЕ: нет задачи fxparser в «Списке команд» — курсы не обновлены")
                else:
                    try:
                        call_command("fxparser", fx_job.id)
                        writelog(log, f"     обновлены (лог: {fx_job.logfile.name})")
                    except Exception as fx_exc:
                        writelog(log, f"     ВНИМАНИЕ: курсы не обновились ({fx_exc}) — считаем по старым")

                writelog(log, "Переоценка денег и ДДС → parquet ....")

                from treasury.services.cash_reports import build

                build(log=lambda text: writelog(log, text))

                writelog(log, line)
                writelog(log, "DONE !!!!")

                job.status = JobStatus.DONE
                job.lastrun = timezone.now()
                job.save(update_fields=["status", "lastrun"])
                
                
                

            except Exception as exc:

                writelog(log, line)
                writelog(log, "FAILED")
                writelog(log, str(exc))
                writelog(log, line)

                job.status = JobStatus.FAILED
                job.lastrun = timezone.now()
                job.save(update_fields=["status", "lastrun"])

                raise
