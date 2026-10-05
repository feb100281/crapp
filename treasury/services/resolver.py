"""
Разноска строк выписки через резолверы.

    sync()               — создать резолверы для новых КБК и счетов (по строкам в базе)
    resolve(resolver)    — переразнести строки одного резолвера по его правилам
    resolve_all()        — всё сразу (после импорта выписок)
    open_patterns(r)     — шаблоны назначений, которые ещё не разнесены

Какая строка в какой резолвер попадает:
    есть КБК                          → резолвер КБК
    нет КБК, не между своими счетами  → резолвер счёта (5 цифр)
    между своими счетами              → резолвер «Свои» (валюта / валюта)
    счёт контрагента не 20 знаков     → ни в один (валютные, отдельно)

Строки, у которых есть разноска «руками» (manual=True), не трогаются.
У остальных автоматическая разноска стирается и строится заново —
поэтому прогон можно повторять сколько угодно.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.functions import Substr

from macro.models.fx_model import Fx

from ..models.bs_line_alloc_model import BSLineAlloc
from ..models.bs_line_model import BSLine
from ..models.cf_item_model import CFItem, Direction
from ..models.resolver_model import Resolver, ResolverKind
from ..models.resolver_rule_model import ResolverRule


# Подсказки по плану счетов ЦБ (Положение 809-П). Человек может поправить.
BA_NAMES = {
    "03100": "Казначейство (бюджет)",
    "03212": "Казначейство — ФССП, суды",
    "03214": "Бюджетные учреждения",
    "03224": "Бюджетные учреждения",
    "03225": "Бюджетные учреждения",
    "03226": "Бюджетные учреждения",
    "20208": "Касса банка — внесение наличных",
    "30101": "Корсчёт банка в ЦБ",
    "30110": "Корсчета в других банках",
    "30111": "Корсчета банков-нерезидентов (ВЭД)",
    "30220": "Транзит банка по переводам",
    "30232": "Расчёты по картам (эквайринг)",
    "30233": "Незавершённые переводы (карты, СБП, взносы)",
    "30301": "Расчёты между филиалами банка",
    "30302": "Расчёты между филиалами банка",
    "40701": "Финансовые организации",
    "40702": "Коммерческие организации",
    "40703": "Некоммерческие организации",
    "40802": "ИП",
    "40807": "Юрлица-нерезиденты",
    "40817": "Физлица",
    "40820": "Физлица-нерезиденты",
    "42102": "Депозиты юрлиц",
    "42301": "Вклады физлиц",
    "45201": "Кредиты юрлицам — овердрафт",
    "45204": "Кредиты юрлицам",
    "45205": "Кредиты юрлицам",
    "45206": "Кредиты юрлицам",
    "45207": "Кредиты юрлицам",
    "47407": "Конверсионные операции (банк должен)",
    "47408": "Конверсионные операции (банку должны)",
    "47416": "Невыясненные / возвраты платежей",
    "47422": "Прочие обязательства банка (комиссии, ведомости)",
    "47423": "Прочие требования банка (комиссии)",
    "47426": "Проценты по депозитам",
    "47427": "Проценты по кредитам",
    "47443": "Плата за лимит",
    "60309": "НДС банка",
    "60312": "Расчёты банка с поставщиками",
    "70601": "Доходы банка (комиссии, курсовая)",
    "70603": "Переоценка (доход)",
    "70608": "Переоценка (расход)",
}


def _ic_lines():
    """Переводы между своими + валюта нашего счёта и второго счёта (знаки 6–8)."""
    return BSLine.objects.filter(intercompany=True).annotate(
        our_cur=Substr("ba_account__number", 6, 3),
        cp_cur=Substr("cp_ba", 6, 3),
    )


def lines(resolver: Resolver):
    """Все строки резолвера."""
    if resolver.kind == ResolverKind.IC:
        our_cur, _, cp_cur = resolver.key.partition("/")
        return _ic_lines().filter(direction=resolver.direction, our_cur=our_cur, cp_cur=cp_cur)

    qs = BSLine.objects.filter(direction=resolver.direction)
    if resolver.kind == ResolverKind.KBK:
        return qs.filter(kbk=resolver.key)
    return qs.filter(kbk__isnull=True, intercompany=False, ba_resolver=resolver.key)


def _rules(resolver: Resolver):
    rules = list(resolver.rules.select_related("cf_item"))
    # сначала правила с текстом по порядку, «всё остальное» — в конце
    rules.sort(key=lambda r: (r.text_regex == "", r.order, r.id))
    return [
        (r, re.compile(r.text_regex, re.IGNORECASE) if r.text_regex else None)
        for r in rules
    ]


def _allocs(rule: ResolverRule, ln: dict) -> list[BSLineAlloc]:
    """Одна разноска на всю сумму строки. Удержанная комиссия / долг
    раскладываются не здесь, а в отчёте ДДС (sql/bs/cash_flow.sql)."""
    return [BSLineAlloc(
        line_id=ln["id"],
        direction=ln["direction"],
        amount=ln["amount"],
        cf_item_id=rule.cf_item_id,
        resolver_rule_id=rule.id,
    )]


@transaction.atomic
def resolve(resolver: Resolver) -> None:
    rules = _rules(resolver)
    all_lines = lines(resolver)
    free = all_lines.exclude(allocs__manual=True)

    # .values("pk"): у строк «Своих» есть аннотации — в подзапросе нужен только id
    BSLineAlloc.objects.filter(manual=False, line__in=free.values("pk")).delete()

    allocs = []
    hits = defaultdict(lambda: [0, Decimal(0)])

    for ln in free.values("id", "direction", "amount", "description"):
        text = ln["description"] or ""
        for rule, rx in rules:
            if rx is None or rx.search(text):
                allocs.extend(_allocs(rule, ln))
                hits[rule.id][0] += 1
                hits[rule.id][1] += Decimal(str(ln["amount"]))
                break

    BSLineAlloc.objects.bulk_create(allocs, batch_size=2000)

    for rule, _ in rules:
        n, amount = hits.get(rule.id, (0, Decimal(0)))
        ResolverRule.objects.filter(pk=rule.pk).update(hits=n, hits_amount=amount)

    total = all_lines.aggregate(n=Count("id"), s=Sum("amount"))
    still_open = all_lines.filter(allocs__isnull=True).aggregate(n=Count("id"), s=Sum("amount"))

    Resolver.objects.filter(pk=resolver.pk).update(
        lines_count=total["n"] or 0,
        lines_amount=total["s"] or 0,
        open_count=still_open["n"] or 0,
        open_amount=still_open["s"] or 0,
    )


def _payee(qs) -> str | None:
    """Один контрагент — его имя, несколько — их число. Считается в SQL."""
    names = (
        qs.exclude(cp_name__isnull=True).exclude(cp_name="")
        .order_by()
        .values("cp_name")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    total = names.count()
    if not total:
        return None
    top = names[0]["cp_name"]
    if total == 1:
        return top[:300]
    return f"{total} контрагентов, чаще всего: {top}"[:300]


def sync() -> int:
    """Создаёт резолверы для всех КБК и счетов, которые есть в строках. Возвращает число новых."""
    created = 0
    existing = set(Resolver.objects.values_list("kind", "key", "direction"))

    # .order_by() обязателен: иначе сортировка модели (-op_date, id) попадает
    # в SELECT DISTINCT и вместо десятков пар приходят десятки тысяч строк
    kbk_pairs = (
        BSLine.objects.filter(kbk__isnull=False)
        .order_by().values_list("kbk", "direction").distinct()
    )
    for kbk, direction in kbk_pairs:
        if (ResolverKind.KBK, kbk, direction) in existing:
            continue
        Resolver.objects.create(
            kind=ResolverKind.KBK, key=kbk, direction=direction,
            payee=_payee(BSLine.objects.filter(kbk=kbk, direction=direction)),
        )
        created += 1

    ba_pairs = (
        BSLine.objects.filter(kbk__isnull=True, intercompany=False, ba_resolver__isnull=False)
        .order_by().values_list("ba_resolver", "direction").distinct()
    )
    for key, direction in ba_pairs:
        if (ResolverKind.BA, key, direction) in existing:
            continue
        Resolver.objects.create(
            kind=ResolverKind.BA, key=key, direction=direction,
            name=BA_NAMES.get(key),
            payee=_payee(BSLine.objects.filter(
                kbk__isnull=True, intercompany=False, ba_resolver=key, direction=direction,
            )),
        )
        created += 1

    created += _sync_ic(existing)

    return created


# Стартовые правила для «Своих»: конвертация по тексту, остальное — по валютам
IC_CONVERSION_RX = r"купли-продажи|конверс|конвертац"
IC_ITEMS = {
    # (направление, конвертация?) → код статьи
    (Direction.INFLOW, False): "410100",
    (Direction.OUTFLOW, False): "420100",
    (Direction.INFLOW, True): "410200",
    (Direction.OUTFLOW, True): "420200",
}


def _sync_ic(existing: set) -> int:
    created = 0
    fx = dict(Fx.objects.values_list("numeric_code", "code"))
    fx["643"] = fx.get("643") or "RUB"
    items = {code: item for code, item in CFItem.objects.filter(code__in=IC_ITEMS.values())
             .values_list("code", "pk")}

    pairs = _ic_lines().order_by().values_list("our_cur", "cp_cur", "direction").distinct()
    for our_cur, cp_cur, direction in pairs:
        key = f"{our_cur}/{cp_cur}"
        if (ResolverKind.IC, key, direction) in existing:
            continue

        conversion = our_cur != cp_cur
        resolver = Resolver.objects.create(
            kind=ResolverKind.IC, key=key, direction=direction,
            name=f"{fx.get(our_cur, our_cur)} → {fx.get(cp_cur, cp_cur)} · "
                 f"{'конвертация' if conversion else 'перевод'}",
        )
        created += 1

        # стартовые правила — дальше правятся в админке как обычно
        conv_item = items.get(IC_ITEMS[(direction, True)])
        main_item = items.get(IC_ITEMS[(direction, conversion)])
        # «купли-продажи» в паре одной ВАЛЮТЫ (156/156, 356/356) — это приход купленной
        # валюты, вторая нога конвертации 810/156. В рублях (810/810) такого не бывает.
        if conv_item and not conversion and our_cur not in ("810", "643"):
            ResolverRule.objects.create(
                resolver=resolver, order=10, text_regex=IC_CONVERSION_RX, cf_item_id=conv_item,
            )
        if main_item:
            ResolverRule.objects.create(
                resolver=resolver, order=20, text_regex="", cf_item_id=main_item,
            )
    return created


def resolve_all(kind: str | None = None) -> dict:
    qs = Resolver.objects.all()
    if kind:
        qs = qs.filter(kind=kind)
    for resolver in qs:
        resolve(resolver)
    agg = qs.aggregate(n=Sum("lines_count"), o=Sum("open_count"))
    return {"resolvers": qs.count(), "lines": agg["n"] or 0, "open": agg["o"] or 0}


def open_patterns(resolver: Resolver, limit: int = 50) -> list[dict]:
    """Неразнесённые строки, сгруппированные по шаблону назначения."""
    groups: dict[str, dict] = {}
    for pattern, desc, amount in (
        lines(resolver)
        .filter(allocs__isnull=True)
        .values_list("desc_pattern", "description", "amount")
    ):
        g = groups.setdefault(pattern or "", {
            "pattern": pattern or "", "example": desc or "", "count": 0, "amount": Decimal(0),
        })
        g["count"] += 1
        g["amount"] += Decimal(str(amount))
    return sorted(groups.values(), key=lambda g: -g["amount"])[:limit]
