"""
Контрагенты и статьи: на какие статьи разнесены платежи каждого контрагента.

Читает только витрину dashboard_cp_audit (модель CpAudit). Нужен, чтобы
увидеть ошибку разноски: контрагент почти всегда идёт на одну статью,
а пару раз — на другую. Иногда это правильно (несколько услуг), иногда нет.

Контрагент = ИНН + направление; если ИНН нет или он общий (больше 5 разных
названий на ИНН — физлица с ИНН банка) — название, регистр не важен.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from django.db.models import Count, Max, Min, Q, Sum

from ..models import CpAudit

# «Редкая» статья: не больше стольких операций и не больше такой доли операций
RARE_OPS = 2
RARE_SHARE = 0.10
SHARED_INN_NAMES = 5

KIND_LABELS = {"CP": "контрагент", "KBK": "КБК", "BA": "счёт", "IC": "свои"}


def norm_name(value) -> str:
    return " ".join(str(value or "").split()).upper()


@dataclass
class ItemUse:
    item_id: int | None
    code: str
    name: str
    article_code: str = ""
    article_name: str = ""
    count: int = 0
    amount: float = 0.0
    first: date | None = None
    last: date | None = None
    manual: int = 0
    share: float = 0.0
    rare: bool = False
    by: dict = field(default_factory=lambda: defaultdict(int))   # резолвер правила → строк


@dataclass
class CpRow:
    mode: str                 # inn | name
    key: str
    inn: str
    name: str
    direction: int
    items: dict = field(default_factory=dict)
    names: dict = field(default_factory=lambda: defaultdict(int))
    open_count: int = 0
    open_amount: float = 0.0
    open_in: dict = field(default_factory=lambda: defaultdict(int))  # где лежит неразнесённое
    places: dict = field(default_factory=dict)                       # резолверы строк
    count: int = 0
    amount: float = 0.0
    pinned: bool = False
    suspect: bool = False

    @property
    def item_list(self) -> list[ItemUse]:
        return sorted(self.items.values(), key=lambda i: -i.count)

    @property
    def multi(self) -> bool:
        return len(self.items) > 1

    @property
    def total_count(self) -> int:
        return self.count + self.open_count

    @property
    def total_amount(self) -> float:
        return self.amount + self.open_amount


def shared_inns(qs=None) -> set[str]:
    names = defaultdict(set)
    pairs = (qs or CpAudit.objects.all()).exclude(inn__isnull=True).exclude(inn="") \
        .order_by().values_list("inn", "cp_name").distinct()
    for inn, name in pairs:
        names[inn].add(norm_name(name))
    return {inn for inn, found in names.items() if len(found) > SHARED_INN_NAMES}


def cp_key(inn, name, shared: set[str]) -> tuple[str, str]:
    if inn and inn not in shared:
        return "inn", inn
    return "name", norm_name(name)


def scope(*, direction=None, year=None, ba=None, q="") -> Q:
    cond = Q()
    if direction:
        cond &= Q(direction=direction)
    if year:
        cond &= Q(year=year)
    if ba:
        cond &= Q(ba_key=ba)
    if q:
        cond &= Q(cp_name__icontains=q) | Q(inn__startswith=q)
    return cond


def build(*, direction=None, year=None, ba=None, q="", item_id=None) -> list[CpRow]:
    shared = shared_inns()
    rows: dict[tuple, CpRow] = {}

    def row_for(inn, name, d) -> CpRow:
        mode, key = cp_key(inn, name, shared)
        row = rows.get((mode, key, d))
        if row is None:
            row = rows[(mode, key, d)] = CpRow(mode, key, inn or "", name or "", d)
        return row

    groups = (
        CpAudit.objects.filter(scope(direction=direction, year=year, ba=ba, q=q)).order_by()
        .values("inn", "cp_name", "direction", "is_open", "cf_item_id", "cf_code", "cf_name",
                "article_code", "article_name",
                "manual", "rule_resolver_id", "rule_kind", "kbk", "ba_key",
                "kbk_resolver_id", "ba_resolver_id", "cp_resolver_id")
        .annotate(n=Count("id"), s=Sum("amount"), d1=Min("date"), d2=Max("date"))
    )
    for g in groups:
        row = row_for(g["inn"], g["cp_name"], g["direction"])
        row.names[g["cp_name"] or ""] += g["n"]
        if g["cp_resolver_id"] or g["rule_kind"] == "CP":
            row.pinned = True

        # резолверы, которым принадлежат строки: здесь и настраивается разноска
        if g["kbk_resolver_id"]:
            row.places[("KBK", g["kbk_resolver_id"])] = f"КБК {g['kbk']}"
        if g["ba_resolver_id"]:
            row.places[("BA", g["ba_resolver_id"])] = f"счёт {g['ba_key']}"

        if g["is_open"]:
            row.open_count += g["n"]
            row.open_amount += g["s"] or 0
            if g["kbk_resolver_id"]:
                row.open_in[("KBK", g["kbk_resolver_id"])] += g["n"]
            elif g["ba_resolver_id"]:
                row.open_in[("BA", g["ba_resolver_id"])] += g["n"]
            continue

        use = row.items.get(g["cf_item_id"])
        if use is None:
            use = row.items[g["cf_item_id"]] = ItemUse(
                g["cf_item_id"], g["cf_code"] or "", g["cf_name"] or "без статьи",
                # верхний уровень — только если это подстатья
                g["article_code"] if g["article_code"] != g["cf_code"] else "",
                g["article_name"] if g["article_code"] != g["cf_code"] else "")
        use.count += g["n"]
        use.amount += g["s"] or 0
        use.first = min(filter(None, (use.first, g["d1"])), default=None)
        use.last = max(filter(None, (use.last, g["d2"])), default=None)
        if g["manual"] or not g["rule_resolver_id"]:
            use.manual += g["n"]
        else:
            use.by[(g["rule_kind"], g["rule_resolver_id"])] += g["n"]
        row.count += g["n"]
        row.amount += g["s"] or 0

    out = []
    for row in rows.values():
        if item_id and item_id not in row.items:
            continue
        row.name = max(row.names.items(), key=lambda kv: kv[1])[0] or row.name
        if row.count:
            for use in row.items.values():
                use.share = use.count / row.count
                use.rare = len(row.items) > 1 and use.count <= RARE_OPS and use.share <= RARE_SHARE
            row.suspect = any(u.rare for u in row.items.values())
        out.append(row)
    return out


VIEWS = (
    ("multi", "Несколько статей"),
    ("suspect", "Похоже на ошибку"),
    ("open", "Есть неразнесённое"),
    ("pinned", "Закреплённые"),
    ("all", "Все"),
)


def select(rows: list[CpRow], view: str) -> list[CpRow]:
    if view == "multi":
        rows = [r for r in rows if r.multi]
    elif view == "suspect":
        rows = [r for r in rows if r.suspect]
    elif view == "open":
        rows = [r for r in rows if r.open_count]
    elif view == "pinned":
        rows = [r for r in rows if r.pinned]
    if view == "open":
        return sorted(rows, key=lambda r: -r.open_amount)
    return sorted(rows, key=lambda r: (not r.suspect, -r.total_amount))


def stats(rows: list[CpRow]) -> dict:
    return {
        "total": len(rows),
        "multi": sum(1 for r in rows if r.multi),
        "suspect": sum(1 for r in rows if r.suspect),
        "pinned": sum(1 for r in rows if r.pinned),
        "open_count": sum(r.open_count for r in rows),
        "open_amount": sum(r.open_amount for r in rows),
    }


def cp_lines(mode: str, inn: str, name: str, direction: int, *, year=None, ba=None):
    """Строки витрины одного контрагента."""
    qs = CpAudit.objects.filter(scope(direction=direction, year=year, ba=ba))
    if mode == "inn":
        return qs.filter(inn=inn)
    target = norm_name(name)
    names = [n for n in qs.order_by().values_list("cp_name", flat=True).distinct()
             if norm_name(n) == target]
    return qs.filter(cp_name__in=names)
