"""
Витрины, которые собираются прямо в SQLite (без DuckDB).

    refresh_cp_audit()   — пересобрать dashboard_cp_audit сейчас
    schedule_cp_audit()  — пересобрать после текущей транзакции (из сохранений)
"""

from __future__ import annotations

from django.db import connection, transaction

from core.management.misc.helpers import SQLHelpers

CP_AUDIT_TABLE = "dashboard_cp_audit"


def refresh_cp_audit() -> None:
    sql = SQLHelpers().read("bs", "cp_audit_mart.sql")
    statements = [s.strip() for s in sql.split(";")]
    with transaction.atomic(), connection.cursor() as cur:
        for statement in statements:
            # комментарии в начале куска не мешают, пустые куски пропускаем
            body = "\n".join(line for line in statement.splitlines() if not line.strip().startswith("--"))
            if body.strip():
                cur.execute(body)


def schedule_cp_audit() -> None:
    transaction.on_commit(refresh_cp_audit)


def ensure_cp_audit() -> None:
    """Витрины ещё нет (первый запуск) или она старой структуры — собрать."""
    from ..models import CpAudit

    if CP_AUDIT_TABLE not in connection.introspection.table_names():
        refresh_cp_audit()
        return
    with connection.cursor() as cur:
        have = {c.name for c in connection.introspection.get_table_description(cur, CP_AUDIT_TABLE)}
    if {f.column for f in CpAudit._meta.fields} - have:
        refresh_cp_audit()
