"""
Касса в базе: строки карточки 50 → CashLine, и сверка сдачи в банк.

    sync_cash_lines(rows, files)  — после разбора Excel (services.cash_desk.parse_folder)
    bank_cash_in()                — взносы наличных на наши счета по банковским выпискам
    cash_bank_days()              — по дням: сдано по 1С против зачислено банком
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Q

from ..models.bs_line_model import BSLine
from ..models.cash_line_alloc_model import CashLineAlloc
from ..models.cash_line_model import CashLine
from ..models.cf_item_model import CFItem
from ..models.statement_model import Statement

# сдача в банк / получение из банка разносятся сами
TRANSFER_ITEMS = {2: "420500", 1: "410600"}   # направление в кассе → статья
TRANSIT_DAYS = 3                               # «в пути» нормально до стольких дней


def _key(r: dict) -> str:
    parts = [
        r["cash_account"], str(r["date"]), r["doc_type"], r["doc_number"],
        str(r["doc_datetime"] or ""), str(r["direction"]), r["corr_account"],
        f"{r['amount'] or 0:.2f}", r["corr_subconto1"], r["corr_subconto2"], r["corr_subconto3"],
        r["cf_item_1c"],
    ]
    return "|".join(str(p or "") for p in parts)


def _rr_ids(rows: list[dict]) -> list[str]:
    """md5 реквизитов; одинаковые строки внутри выгрузки нумеруются (#2, #3…)."""
    seen: dict[str, int] = defaultdict(int)
    out = []
    for r in rows:
        k = _key(r)
        seen[k] += 1
        if seen[k] > 1:
            k += f"#{seen[k]}"
        out.append(hashlib.md5(k.encode("utf-8")).hexdigest())
    return out


def ensure_cash_accounts(rows: list[dict], log=print) -> int:
    """Касса как счёт: BankAccount с типом CASH под каждый счёт 1С (50.01) + субсчёт 1103 в плане."""
    from cp.models.gr_model import Gr
    from macro.models.fx_model import Fx

    from ..models.ba_model import BankAccount, BAType

    owner = Gr.objects.filter(inn="7731432601").first() or Gr.objects.first()
    rub = Fx.objects.filter(code="RUB").first()
    created = 0
    for number in sorted({r["cash_account"] for r in rows}):
        _, new = BankAccount.objects.get_or_create(
            number=number,
            defaults={"ba_type": BAType.CASH, "currency": rub, "gr": owner},
        )
        if new:
            created += 1
            log(f"Новый счёт кассы: {number}")
    from gl.services.chart import sync_bank_accounts

    gl_new = sync_bank_accounts()
    if gl_new:
        log(f"Новых субсчетов в плане счетов: {gl_new}")
    return created


@transaction.atomic
def sync_cash_lines(rows: list[dict], files: list[dict], log=print) -> dict:
    ensure_cash_accounts(rows, log=log)
    ids = _rr_ids(rows)
    existing = set(CashLine.objects.values_list("rr_id", flat=True))

    new = []
    for rr, r in zip(ids, rows):
        if rr in existing:
            continue
        new.append(CashLine(
            rr_id=rr,
            cash_account=r["cash_account"],
            date=r["date"],
            doc_type=(r["doc_type"] or "")[:200],
            doc_number=(r["doc_number"] or "")[:50],
            doc_datetime=r["doc_datetime"],
            doc_comment=r["doc_comment"] or "",
            division=(r["division"] or "")[:200],
            direction=r["direction"],
            amount=Decimal(f"{r['amount'] or 0:.2f}"),
            corr_account=r["corr_account"] or "",
            cf_item_1c=(r["cf_item_1c"] or "")[:500],
            counterparty=(r["corr_subconto1"] or "")[:500],
            subconto2=(r["corr_subconto2"] or "")[:500],
            subconto3=r["corr_subconto3"] or "",
            balance_1c=None if r["balance"] is None else Decimal(f"{r['balance']:.2f}"),
            source_file=(r["source_file"] or "")[:300],
        ))
    CashLine.objects.bulk_create(new, batch_size=1000)

    # «есть в 1С»: строки базы за периоды выгрузки, которых в выгрузке нет
    current = set(ids)
    gone = 0
    for f in files:
        if not (f.get("date_from") and f.get("date_to")):
            continue
        qs = CashLine.objects.filter(cash_account=f["cash_account"],
                                     date__range=(f["date_from"], f["date_to"]))
        gone_ids = [pk for pk, rr in qs.values_list("pk", "rr_id") if rr not in current]
        gone += CashLine.objects.filter(pk__in=gone_ids, in_1c=True).update(in_1c=False)
        CashLine.objects.filter(pk__in=qs.values("pk"), rr_id__in=current, in_1c=False).update(in_1c=True)

    auto = _auto_transfers()
    stats = {"rows": len(rows), "new": len(new), "gone": gone, "auto": auto,
             "total": CashLine.objects.count(),
             "not_in_1c": CashLine.objects.filter(in_1c=False).count()}
    log(f"Строк в выгрузке: {stats['rows']}, новых в базе: {stats['new']}, всего в базе: {stats['total']}")
    if stats["not_in_1c"]:
        log(f"ВНИМАНИЕ: строк нет в последней выгрузке 1С (удалены или изменены): {stats['not_in_1c']}"
            f" (новых таких: {gone}) — Казначейство → Операции по кассе, фильтр «Есть в 1С»")
    log(f"Сдача в банк разнесена автоматически: {auto}")
    return stats


def _auto_transfers() -> int:
    """Строки с корр. 57.* без разноски → 420500 (сдали в банк) / 410600 (получили из банка)."""
    items = {d: CFItem.objects.filter(code=code).first() for d, code in TRANSFER_ITEMS.items()}
    allocs = [
        CashLineAlloc(line=ln, direction=ln.direction, amount=ln.amount, cf_item=items.get(ln.direction))
        for ln in CashLine.objects.filter(corr_account__startswith="57", allocs__isnull=True)
    ]
    CashLineAlloc.objects.bulk_create(allocs, batch_size=1000)
    return len(allocs)


# ----------------------------------------------------------------------
# Сверка «касса → банк»
# ----------------------------------------------------------------------

def bank_cash_in():
    """
    Взносы наличных на наши счета по выпискам:
      20208                     — Альфа: «Внес.ср. ООО "КОСМО" ч-з ТУ …» (касса банка)
      «Внес.ср» в назначении    — Альфа, те же взносы через транзитный 30233
      30232 «внесенной…выручки» — ГПБ: «зачисление внесенной на карту выручки через Устройства Банка»
    """
    return (
        BSLine.objects.filter(direction=1)
        .filter(
            Q(cp_ba__startswith="20208")
            | Q(description__contains="Внес.ср")
            | Q(cp_ba__startswith="30232", description__regex=r"(?i)внесенн.*выручк")
        )
        .select_related("ba_account", "ba_account__bank")
    )


_BANK_HINT = re.compile(r"(альфа|гпб|газпром|втб|отп|финам|сбер|санкт)", re.I)


def cash_bank_days(year: int | None = None, problems_only: bool = False) -> dict:
    onec = defaultdict(lambda: {"sum": Decimal(0), "n": 0, "hints": set()})
    for ln in CashLine.objects.filter(corr_account__startswith="57", direction=2, in_1c=True):
        d = onec[ln.date]
        d["sum"] += ln.amount
        d["n"] += 1
        m = _BANK_HINT.search(ln.doc_comment or "")
        if m:
            d["hints"].add(m.group(1).capitalize())

    bank = defaultdict(lambda: defaultdict(lambda: [Decimal(0), 0]))
    accounts = {}
    for ln in bank_cash_in():
        acc = ln.ba_account
        key = acc.number if acc else "?"
        if acc and key not in accounts:
            accounts[key] = f"{(acc.bank.name if acc.bank else '').split()[0].title()} …{key[-4:]}"
        cell = bank[ln.op_date][key]
        cell[0] += ln.amount
        cell[1] += 1

    # до какой даты есть выписки по счетам, куда сдают наличные
    ends = (Statement.objects.filter(ba_account__number__in=list(accounts))
            .values("ba_account__number").annotate(d=Max("date_to")))
    stmt_end = {e["ba_account__number"]: e["d"] for e in ends}
    coverage_end = min(stmt_end.values()) if stmt_end else None

    rows, cum, since = [], Decimal(0), None
    for d in sorted(set(onec) | set(bank)):
        o = onec[d]["sum"] if d in onec else Decimal(0)
        b = sum((v[0] for v in bank[d].values()), Decimal(0)) if d in bank else Decimal(0)
        before = cum
        cum += o - b
        if abs(cum) < Decimal("0.01"):
            cum, since = Decimal(0), None
        elif abs(before) < Decimal("0.01"):
            since = d
        if cum == 0:
            status = "ok"
        elif coverage_end and d > coverage_end:
            status = "nostmt"
        elif since and (d - since).days <= TRANSIT_DAYS:
            status = "transit"
        else:
            status = "bad"
        rows.append({
            "date": d,
            "onec": o, "onec_n": onec[d]["n"] if d in onec else 0,
            "hints": ", ".join(sorted(onec[d]["hints"])) if d in onec else "",
            "bank": {k: bank[d][k] if d in bank and k in bank[d] else None for k in accounts},
            "diff": o - b, "cum": cum, "status": status,
            "age": (d - since).days if since else 0,
        })

    years = sorted({r["date"].year for r in rows})
    if year:
        rows = [r for r in rows if r["date"].year == year]
    totals = {
        "onec": sum((r["onec"] for r in rows), Decimal(0)),
        "bank": sum((r["onec"] - r["diff"] for r in rows), Decimal(0)),
        "bad_days": sum(r["status"] == "bad" for r in rows),
        "transit_days": sum(r["status"] == "transit" for r in rows),
        "days": len(rows),
    }
    if problems_only:
        rows = [r for r in rows if r["status"] != "ok"]
    return {
        "rows": rows, "accounts": accounts, "years": years, "coverage_end": coverage_end,
        "stmt_end": stmt_end, "totals": totals, "in_transit": cum,
    }
