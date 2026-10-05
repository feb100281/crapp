"""
Ввод начальных остатков по нашим банковским счетам.

Для каждого счёта: дата и входящий остаток ПЕРВОЙ выписки.
    остаток > 0:  Дт «банковский субсчёт» / Кт «Ввод начальных остатков»
    остаток < 0:  наоборот
    остаток = 0:  проводка не нужна

Валюта пересчитывается по курсу ЦБ на эту дату (последний опубликованный
не позже даты, из data/parquet/fx). Уже введённые остатки не трогаются.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import duckdb
from django.conf import settings
from django.db import transaction

from treasury.models.statement_model import Statement

from ..models.gl_account_model import GLAccount
from ..models.journal_model import EntryKind, JournalEntry, JournalLine
from .chart import OPENING_ACCOUNT, sync_bank_accounts


def fx_rate(code: str, on_date) -> Decimal | None:
    if not code or code == "RUB":
        return Decimal(1)
    fx_glob = (Path(settings.PARQUET_FILES_PATH) / "fx" / "*.parquet").as_posix()
    with duckdb.connect() as con:
        row = con.execute(
            f"SELECT rate FROM read_parquet('{fx_glob}') "
            "WHERE code = ? AND dt <= ? ORDER BY dt DESC LIMIT 1",
            [code, on_date],
        ).fetchone()
    return Decimal(str(row[0])) if row else None


@transaction.atomic
def create_opening_entries() -> dict:
    sync_bank_accounts()
    section, number = OPENING_ACCOUNT
    opening = GLAccount.objects.get(section=section, number=number, parent=None)

    done = skipped_zero = already = no_rate = 0

    for acc in GLAccount.objects.filter(bank_account__isnull=False).select_related("currency", "bank_account"):
        if JournalLine.objects.filter(account=acc, entry__kind=EntryKind.OPENING).exists():
            already += 1
            continue

        first = (
            Statement.objects.filter(ba_account=acc.bank_account)
            .order_by("date_from", "id").first()
        )
        if not first or not first.bb:
            skipped_zero += 1
            continue

        code = acc.currency.code if acc.currency else "RUB"
        rate = fx_rate(code, first.date_from)
        if rate is None:
            no_rate += 1
            continue

        amount_cur = abs(Decimal(first.bb))
        rub = (amount_cur * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        is_rub = code == "RUB"
        positive = first.bb > 0

        entry = JournalEntry.objects.create(
            date=first.date_from,
            kind=EntryKind.OPENING,
            description=f"Ввод остатка: {acc.name}",
        )
        JournalLine.objects.create(
            entry=entry, account=acc,
            dt=rub if positive else 0, cr=0 if positive else rub,
            amount_cur=None if is_rub else amount_cur,
            rate=None if is_rub else rate,
        )
        JournalLine.objects.create(
            entry=entry, account=opening,
            dt=0 if positive else rub, cr=rub if positive else 0,
        )
        done += 1

    return {"created": done, "zero": skipped_zero, "already": already, "no_rate": no_rate}
