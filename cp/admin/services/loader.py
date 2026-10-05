"""
Загрузка контрагентов из parquet-снапшота DaData в модель CP.

Логика разбора вынесена в SQL (sql/bs/cp_init.sql) и выполняется
duckdb — питон только раскладывает результат по строкам модели.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

import duckdb

from django.conf import settings
from django.utils import timezone

from ...models import CP, CPStatus

# Поля, которые приходят из SQL и пишутся в модель
SQL_FIELDS = (
    "ogrn",
    "name",
    "address",
    "registration_date",
    "manager_name",
    "country",
    "region",
    "status",
    "updated_at",
    "ba",
    "payload",
)

BATCH_SIZE = 500


def sql_path() -> Path:
    return settings.SQL_FILES_PATH / "bs" / "cp_init.sql"


def cp_parquet() -> Path:
    return settings.PARQUET_FILES_PATH / "cps" / "init_cp.parquet"


def bs_parquet() -> Path:
    return settings.PARQUET_FILES_PATH / "bs" / "bs.parquet"


def _literal(path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def render_sql(sql: str, cp_file: Path, bs_file: Path) -> str:
    """
    Подставляет пути вместо $cp / $bs.

    Параметры prepared statement внутри read_parquet() ведут себя
    по-разному в разных версиях duckdb, поэтому подставляем литералы.
    """

    return (
        sql
        .replace("$bs", _literal(bs_file))
        .replace("$cp", _literal(cp_file))
    )


# ======================================================================
# Приведение типов
# ======================================================================


def _json(value, default):
    if value in (None, ""):
        return default

    if isinstance(value, (dict, list)):
        return value

    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _aware(value):
    if not isinstance(value, datetime):
        return None

    if timezone.is_naive(value):
        # duckdb пишет снапшот в UTC
        return value.replace(tzinfo=dt_timezone.utc)

    return value


def clean_row(row: dict) -> dict:
    """Строка результата SQL -> словарь полей модели."""

    status = row.get("status") or CPStatus.NA

    if status not in CPStatus.values:
        status = CPStatus.NA

    return {
        "ogrn": row.get("ogrn"),
        "name": row.get("name"),
        "address": row.get("address"),
        "registration_date": row.get("registration_date"),
        "manager_name": row.get("manager_name"),
        "country": row.get("country"),
        "region": row.get("region"),
        "status": status,
        "updated_at": _aware(row.get("updated_at")),
        "ba": _json(row.get("ba"), []),
        "payload": _json(row.get("payload"), {}),
    }


# ======================================================================
# Загрузка
# ======================================================================


def fetch_rows(log=None) -> list[dict]:
    """Выполняет sql/bs/cp_init.sql и возвращает список словарей."""

    cp_file = cp_parquet()
    bs_file = bs_parquet()

    for file in (cp_file, bs_file):
        if not file.exists():
            raise FileNotFoundError(f"Не найден файл: {file}")

    sql = render_sql(
        sql_path().read_text(encoding="utf-8"),
        cp_file,
        bs_file,
    )

    if log:
        log(f"SQL: {sql_path()}")
        log(f"Источник DaData: {cp_file}")
        log(f"Источник выписок: {bs_file}")

    with duckdb.connect(":memory:") as con:
        cursor = con.execute(sql)

        columns = [column[0] for column in cursor.description]

        return [
            dict(zip(columns, row))
            for row in cursor.fetchall()
        ]


def load_counterparties(log=None) -> dict:
    """
    Обновляет модель CP из parquet-снапшота.

    Пустыми значениями существующие данные не затираем: если DaData
    ничего не вернула по полю, в базе остаётся то, что было.
    """

    def write(text):
        if log:
            log(text)

    rows = fetch_rows(log=log)

    write(f"Строк из SQL: {len(rows)}")

    existing = {
        cp.inn: cp
        for cp in CP.objects.all()
    }

    to_create = []
    to_update = []

    skipped = 0

    for raw in rows:
        inn = (raw.get("inn") or "").strip()

        if not inn:
            skipped += 1
            continue

        values = clean_row(raw)

        cp = existing.get(inn)

        if cp is None:
            to_create.append(
                CP(inn=inn, **values)
            )
            continue

        changed = False

        for field, value in values.items():
            if value in (None, "", [], {}):
                continue

            if getattr(cp, field) != value:
                setattr(cp, field, value)
                changed = True

        if changed:
            to_update.append(cp)

    if to_create:
        CP.objects.bulk_create(
            to_create,
            batch_size=BATCH_SIZE,
        )

    if to_update:
        CP.objects.bulk_update(
            to_update,
            list(SQL_FIELDS),
            batch_size=BATCH_SIZE,
        )

    stats = {
        "total": len(rows),
        "created": len(to_create),
        "updated": len(to_update),
        "skipped": skipped,
    }

    write(
        f"Создано: {stats['created']} · "
        f"обновлено: {stats['updated']} · "
        f"без ИНН: {stats['skipped']}"
    )

    return stats
