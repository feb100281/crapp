"""
Курсы ЦБ: один загрузчик — задача fxparser («Список команд») → data/parquet/fx/*.parquet.

    refresh_mart()  parquet → витрина dashboard_fx_rate (fxparser вызывает её в конце)
    update()        запустить fxparser; нет задачи — только пересобрать витрину
    maybe_update()  то же, но не чаще раза в FEED_HOURS — для цикла бота

Те же паркеты читает «Пересчитать ДДС» (переоценка), поэтому курсы в боте и в ДДС одни.
ЦБ устанавливает курс на завтра в рабочие дни по рынку на 15:30 МСК и публикует к вечеру
(обычно до 18:00 МСК); в эти часы проверяем чаще. В выходные курс не меняется.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

log = logging.getLogger("fx_feed")

TABLE = "dashboard_fx_rate"
FEED_HOURS = 2
EVENING_MINUTES = 20                     # 15:30–19:30 МСК в рабочие дни: ждём курс на завтра
MSK = ZoneInfo("Europe/Moscow")
_last_try = 0.0

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    code VARCHAR(3) NOT NULL,
    nominal INTEGER NOT NULL DEFAULT 1,
    rate REAL NOT NULL,
    loaded_at DATETIME,
    UNIQUE (date, code)
)"""


def ensure_table() -> None:
    with connection.cursor() as cur:
        cur.execute(DDL)


def refresh_mart() -> int:
    """Пересобрать витрину из паркетов fxparser. Возвращает число курсов."""
    import duckdb

    folder = settings.PARQUET_FILES_PATH / "fx"
    files = sorted(p for p in folder.glob("*.parquet") if p.stem.upper() != "RUB")
    ensure_table()
    if not files:
        return 0
    paths = ", ".join("'" + p.resolve().as_posix().replace("'", "''") + "'" for p in files)
    with duckdb.connect(":memory:") as con:
        rows = con.execute(
            "SELECT dt::DATE, upper(code), nominal::INTEGER, rate::DOUBLE, loaded_at::TIMESTAMP "
            f"FROM read_parquet([{paths}], union_by_name = true) WHERE rate IS NOT NULL ORDER BY dt, code"
        ).fetchall()
    data = [(d.isoformat(), c, n or 1, r, la.isoformat() if la else None) for d, c, n, r, la in rows]
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute(f"DELETE FROM {TABLE}")
        cur.executemany(f"INSERT INTO {TABLE} (date, code, nominal, rate, loaded_at) "
                        f"VALUES (%s, %s, %s, %s, %s)", data)
    return len(data)


def update() -> int:
    """Скачать курсы задачей fxparser (она же пересоберёт витрину)."""
    from django.core.management import call_command

    from core.models.jobs import Jobs

    job = Jobs.objects.filter(command="fxparser").first()
    if job is None:
        log.warning("нет задачи fxparser в «Списке команд» — витрина из имеющихся паркетов")
        return refresh_mart()
    call_command("fxparser", job.id)
    with connection.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
        return cur.fetchone()[0]


def msk_now() -> datetime:
    return timezone.now().astimezone(MSK)


def _interval() -> int:
    now = msk_now()
    if now.weekday() < 5 and dtime(15, 30) <= now.time() <= dtime(19, 30):
        return EVENING_MINUTES * 60
    return FEED_HOURS * 3600


def maybe_update() -> None:
    global _last_try
    if _last_try and time.monotonic() - _last_try < _interval():
        return
    _last_try = time.monotonic()
    try:
        log.info("курсы ЦБ обновлены: %s в витрине", update())
    except Exception:                    # бот не падает из-за ЦБ
        log.exception("курсы ЦБ не обновились")


def has_data() -> bool:
    return TABLE in connection.introspection.table_names()
