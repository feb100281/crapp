"""
Новые ИНН из выписок → справочник контрагентов (cp_cp).

ИНН берём из inn_adjust строк выписок. В DaData ходим только за теми,
которых в справочнике ещё нет. Если DaData организацию не нашла,
название — самое частое написание из выписок (строки со счетами
физлиц не учитываем: там стоит ИНН банка, а имя — человека).
"""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict

from django.db import IntegrityError
from django.utils import timezone

from ...models.cp_model import CP
from .titles import auto_title, clean_title
from .dadata import (
    REQUEST_DELAY,
    DaDataError,
    build_session,
    extract_fields,
    fetch_party,
    first_suggestion,
)

PERSONAL_PREFIXES = ("40817", "40820", "423")   # счета физлиц
INN_RE = re.compile(r"^(\d{10}|\d{12})$")


def is_personal(account: str | None) -> bool:
    return (account or "").startswith(PERSONAL_PREFIXES)


def new_inns() -> list[str]:
    """ИНН из выписок, которых ещё нет в справочнике."""
    from treasury.models.bs_line_model import BSLine

    seen = {
        (inn or "").strip()
        for inn in BSLine.objects.values_list("inn_adjust", flat=True).distinct()
    }
    known = set(CP.objects.values_list("inn", flat=True))
    return sorted(inn for inn in seen if INN_RE.match(inn) and inn not in known)


def statement_names(inns: list[str]) -> dict[str, str]:
    """Самое частое название по каждому ИНН; счета физлиц — только если других нет."""
    from treasury.models.bs_line_model import BSLine

    main: dict[str, Counter] = defaultdict(Counter)
    personal: dict[str, Counter] = defaultdict(Counter)
    rows = BSLine.objects.filter(inn_adjust__in=inns).values_list("inn_adjust", "cp_name", "cp_ba")
    for inn, name, account in rows.iterator():
        name = clean_title(name)
        if name:
            (personal if is_personal(account) else main)[inn][name] += 1
    return {
        inn: (main.get(inn) or personal.get(inn)).most_common(1)[0][0]
        for inn in inns
        if main.get(inn) or personal.get(inn)
    }


def sync_new(log=print, limit: int | None = None) -> dict:
    """Добавляет в справочник новые ИНН. Ошибка сети по ИНН — пропуск до следующего запуска."""
    inns = new_inns()
    if limit:
        inns = inns[: int(limit)]
    stats = {"new": len(inns), "found": 0, "not_found": 0, "errors": 0}
    if not inns:
        log("     новых ИНН нет")
        return stats

    log(f"     новых ИНН: {len(inns)} — запрос в DaData (~{len(inns) * REQUEST_DELAY / 60:.0f} мин)")
    names = statement_names(inns)
    try:
        session = build_session()
    except DaDataError as exc:
        log(f"     ВНИМАНИЕ: {exc} — справочник не пополнен")
        stats["errors"] = len(inns)
        return stats

    try:
        for index, inn in enumerate(inns, start=1):
            try:
                result = fetch_party(inn, session=session, log=log)
            except DaDataError as exc:                     # неверный ключ — дальше смысла нет
                log(f"     ВНИМАНИЕ: {exc}")
                stats["errors"] += len(inns) - index + 1
                break
            if result["status"] != 200:
                stats["errors"] += 1
                continue
            payload = result["payload"] or {}
            found = bool(first_suggestion(payload))
            fields = extract_fields(payload, fallback_name=names.get(inn))
            cp = CP(inn=inn, payload=payload, updated_at=timezone.now() if found else None, **fields)
            cp.title = auto_title(cp)[:255]
            try:                                           # ИНН мог добавить параллельный запуск
                cp.save()
            except IntegrityError:
                continue
            stats["found" if found else "not_found"] += 1
            if index % 200 == 0:
                log(f"     … {index} из {len(inns)}")
            time.sleep(REQUEST_DELAY)
    finally:
        session.close()

    log(f"     DaData нашла: {stats['found']}, название из выписки: {stats['not_found']}, "
        f"ошибок (повторим в следующий раз): {stats['errors']}")
    return stats
