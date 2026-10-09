"""
Казначейские отчёты в parquet (data/parquet/treasury/):

    cash_reval.parquet       — счёт × день: курс, остатки, обороты, курсовая
    bs_line_rub.parquet      — строки выписок в рублях
    reval_check.parquet      — выписки, где посчитанный остаток ≠ остатку банка
    cash_flow.parquet        — ДДС в рублях: остаток на начало, разноска, неразнесённое,
                               ручные проводки, курсовые
    cash_flow_check.parquet  — сверка по счетам: начало + ДДС = конец

Те же данные кладутся витринами в SQLite (dashboard_cash_flow, _balance,
_balance_day, _check) — их читает приложение dashboard. Плюс витрина
dashboard_cp_audit (sql/bs/cp_audit_mart.sql, без DuckDB).

Пересчитывается целиком: после импорта выписок (bs.py), командой
`python manage.py cashflow` и кнопкой в резолверах.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
from django.conf import settings
from django.db import connection

from core.management.misc.helpers import SQLHelpers

TABLES = ("cash_reval", "bs_line_rub", "reval_check", "cash_flow", "cash_flow_check")


def build(log=print) -> dict:
    out_dir = Path(settings.PARQUET_FILES_PATH) / "treasury"
    out_dir.mkdir(parents=True, exist_ok=True)
    fx_dir = Path(settings.PARQUET_FILES_PATH) / "fx"

    if not any(fx_dir.glob("*.parquet")):
        log("     ПРОПУЩЕНО: нет ни одного файла курсов в data/parquet/fx")
        return {"skipped": True}

    sql = SQLHelpers()
    with duckdb.connect() as con:
        con.execute(f"ATTACH '{settings.SQLITE_CON}' AS target_db (TYPE SQLITE);")
        con.execute(
            sql.read("bs", "cash_reval.sql").replace("__FX_GLOB__", (fx_dir / "*.parquet").as_posix())
        )
        con.execute(sql.read("bs", "cash_flow.sql"))

        for table in TABLES:
            target = (out_dir / f"{table}.parquet").as_posix()
            con.execute(f"COPY {table} TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)")

        # витрины дашборда — целиком заменяются в SQLite
        con.execute(sql.read("bs", "dashboard_load.sql"))

        # главная сверка — с банком: остаток последней выписки и пропуски выписок
        bank_off = con.execute(
            "SELECT ba_number, stmt_to, bank_eb, calc_eb, diff_cur, gaps "
            "FROM target_db.dashboard_cash_check "
            "WHERE abs(COALESCE(diff_cur, 0)) >= 0.01 OR gap_count > 0 ORDER BY id"
        ).fetchall()
        log(f"     сверка с банком (последняя выписка): "
            f"{'всё сходится' if not bank_off else f'не сходится счетов: {len(bank_off)}'}")
        for number, stmt_to, bank_eb, calc_eb, diff, gaps in bank_off:
            log(f"       {number} на {stmt_to}: банк {bank_eb:,.2f}, расчёт {calc_eb:,.2f}, "
                f"разница {diff:+,.2f}".replace(",", " ") + (f"; нет выписок: {gaps}" if gaps else ""))
        log("     витрины дашборда обновлены (dashboard_cash_flow / _balance / _balance_day / _check)")

        days, no_rate, bad = con.execute(
            "SELECT (SELECT count(*) FROM cash_reval), "
            "(SELECT count(*) FROM cash_reval WHERE rate IS NULL), "
            "(SELECT count(*) FROM reval_check)"
        ).fetchone()
        log(f"     счёт×дней: {days}, без курса: {no_rate}")
        log(f"     выписок, где остаток не сошёлся с банком: {bad}")

        stale = con.execute(
            "SELECT number, stmt_to, base_eb, currency FROM cash_reval "
            "WHERE date = (SELECT max(date) FROM cash_reval) AND stale ORDER BY stmt_to"
        ).fetchall()
        if stale:
            log(f"     ВНИМАНИЕ: выписки устарели по {len(stale)} счетам "
                "(остаток взят по последней выписке):")
            for number, stmt_to, eb, cur in stale:
                log(f"       {number}  выписка по {stmt_to:%d.%m.%Y}, остаток {eb:,.2f} {cur}".replace(",", " "))
        for row in con.execute(
            "SELECT number, date_to, statement_eb, calc_eb, diff FROM reval_check"
        ).fetchall():
            log(f"       {row}")

        no_opening = con.execute(
            "SELECT count(*) FROM reval_accounts WHERE NOT has_gl_opening AND opening_stmt <> 0"
        ).fetchone()[0]
        if no_opening:
            log(f"     ВНИМАНИЕ: нет ввода остатков в журнале по {no_opening} счетам — "
                "остаток на начало взят из первой выписки")

        rows, unalloc, unalloc_rub, fx_rub = con.execute(
            "SELECT count(*), "
            "count(*) FILTER (WHERE source = 'UNALLOC'), "
            "COALESCE(sum(abs(amount_rub)) FILTER (WHERE source = 'UNALLOC'), 0), "
            "COALESCE(sum(amount_rub) FILTER (WHERE source = 'FX'), 0) "
            "FROM cash_flow"
        ).fetchone()
        log(f"     ДДС: строк {rows}, не разнесено {unalloc} на {unalloc_rub:,.0f} ₽, "
            f"курсовые на остаток {fx_rub:,.0f} ₽".replace(",", " "))

        off = con.execute(
            "SELECT number, opening_rub, flows_rub, closing_rub, diff "
            "FROM cash_flow_check WHERE abs(diff) > 1"
        ).fetchall()
        log(f"     арифметика ДДС (начало + ДДС = конец): {'ок' if not off else f'не сходится счетов: {len(off)}'}")
        for row in off:
            log(f"       {row}")

    # DuckDB индексы в SQLite не создаёт — раскрытие дня ищет счета по дате
    with connection.cursor() as cur:
        cur.execute("CREATE INDEX IF NOT EXISTS dashboard_cash_balance_date "
                    "ON dashboard_cash_balance(date)")
        cur.execute("CREATE INDEX IF NOT EXISTS dashboard_cash_flow_ba_date "
                    "ON dashboard_cash_flow(ba_id, date)")

    # витрина «Контрагенты и статьи» — собирается в самой SQLite
    from dashboard.services.marts import refresh_cp_audit

    refresh_cp_audit()
    log("     витрина dashboard_cp_audit обновлена")

    log(f"     → {out_dir}")
    return {"days": days, "no_rate": no_rate, "bad": bad, "rows": rows,
            "unalloc": unalloc, "unalloc_rub": unalloc_rub, "off": len(off)}
