"""
Резолверы кассы: шаблон = «название операции» из 1С + направление.

Название операции — статья ДДС в 1С на стороне кассы («Оплата покупателя»,
«Возврат денежных средств покупателю», «Залог за товар», «Заработная плата»…),
а где её нет («<...>», отчёты о розничных продажах) — вид документа.

Правила внутри шаблона — те же, что у банка: «содержит» → статья, пустое
«содержит» = всё остальное. Текст для правил: вид и номер документа, статья 1С
(как «Назначение» в отчёте ДДС), комментарий, контрагент, субконто и корр. счёт
(«корр. 71.01»).

Сдача в банк (корр. 57.*) сюда не попадает — она разносится сама и
сверяется на экране «Касса → банк». Ручная разноска не перезаписывается.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Case, CharField, Count, F, Q, Sum, Value, When

from ..models.cash_line_alloc_model import CashLineAlloc
from ..models.cash_line_model import CashLine
from ..models.resolver_model import Resolver, ResolverKind
from ..models.resolver_rule_model import ResolverRule
from .resolver import _RuleText, norm_text

NO_NAME = ("", "<...>")


def op_name_expr():
    """Название операции в запросе: статья 1С, а если её нет — вид документа."""
    return Case(
        When(Q(cf_item_1c__in=NO_NAME), then=F("doc_type")),
        default=F("cf_item_1c"),
        output_field=CharField(),
    )


def op_name(line: CashLine) -> str:
    return line.doc_type if line.cf_item_1c in NO_NAME else line.cf_item_1c


def key_for(name: str) -> str:
    return "C" + hashlib.md5(" ".join(name.split()).upper().encode("utf-8")).hexdigest()[:16]


def base_lines():
    """Строки кассы для резолверов: всё, кроме сдачи в банк / получения из банка."""
    return CashLine.objects.exclude(corr_account__startswith="57").annotate(op=op_name_expr())


def lines(resolver: Resolver):
    return base_lines().filter(direction=resolver.direction, op=resolver.name)


def line_text(ln: CashLine) -> str:
    # начало — как «Назначение» кассы в отчёте ДДС: вид и номер документа, статья 1С
    parts = [f"{ln.doc_type} {ln.doc_number}".strip(), ln.cf_item_1c, ln.doc_comment, ln.counterparty,
             ln.subconto2, ln.subconto3, f"корр. {ln.corr_account}"]
    return norm_text(" ".join(p for p in parts if p and p != "<...>"))


def sync() -> int:
    """Шаблоны для всех названий операций в строках кассы. Возвращает число новых."""
    existing = set(Resolver.objects.filter(kind=ResolverKind.CASH).values_list("key", "direction"))
    created = 0
    pairs = base_lines().order_by().values_list("op", "direction").distinct()
    for name, direction in pairs:
        name = (name or "").strip()[:200] or "Без названия"
        key = key_for(name)
        if (key, direction) in existing:
            continue
        corr = Counter(base_lines().filter(op=name, direction=direction)
                       .values_list("corr_account", flat=True))
        Resolver.objects.create(
            kind=ResolverKind.CASH, key=key, direction=direction, name=name,
            payee="корр. " + ", ".join(c for c, _ in corr.most_common(5)),
        )
        existing.add((key, direction))
        created += 1
    return created


def _rules(resolver_id: int):
    rules = list(ResolverRule.objects.filter(resolver_id=resolver_id).select_related("cf_item"))
    rules.sort(key=lambda r: (not r.text_regex.strip(), r.order, r.id))
    return [(r, _RuleText(r.text_regex)) for r in rules]


def _apply(resolver: Resolver) -> None:
    rules = _rules(resolver.pk)
    qs = lines(resolver)
    manual = set(CashLineAlloc.objects.filter(line__in=qs, manual=True).values_list("line_id", flat=True))
    CashLineAlloc.objects.filter(line__in=qs, manual=False).delete()
    new = []
    for ln in qs:
        if ln.pk in manual:
            continue
        text = line_text(ln)
        folded = text.casefold()
        for rule, check in rules:
            if check.matches(text, folded):
                new.append(CashLineAlloc(line=ln, direction=ln.direction, amount=ln.amount,
                                         cf_item=rule.cf_item, resolver_rule=rule))
                break
    CashLineAlloc.objects.bulk_create(new, batch_size=1000)


def _recount(resolver: Resolver) -> None:
    qs = lines(resolver)
    total = qs.aggregate(n=Count("id"), s=Sum("amount"))
    still = qs.filter(allocs__isnull=True).aggregate(n=Count("id"), s=Sum("amount"))
    Resolver.objects.filter(pk=resolver.pk).update(
        lines_count=total["n"] or 0, lines_amount=total["s"] or 0,
        open_count=still["n"] or 0, open_amount=still["s"] or 0,
    )


def _recount_rules() -> None:
    fact = {
        row["resolver_rule"]: (row["n"], row["s"] or Decimal(0))
        for row in CashLineAlloc.objects.filter(resolver_rule__isnull=False)
        .order_by().values("resolver_rule").annotate(n=Count("id"), s=Sum("amount"))
    }
    changed = []
    for rule in ResolverRule.objects.filter(resolver__kind=ResolverKind.CASH):
        n, s = fact.get(rule.id, (0, Decimal(0)))
        if rule.hits != n or rule.hits_amount != s:
            rule.hits, rule.hits_amount = n, s
            changed.append(rule)
    ResolverRule.objects.bulk_update(changed, ["hits", "hits_amount"])


@transaction.atomic
def resolve(resolver: Resolver | None = None) -> dict:
    """Переразнести один шаблон или все. Ручная разноска остаётся."""
    resolvers = [resolver] if resolver else list(Resolver.objects.filter(kind=ResolverKind.CASH))
    for r in resolvers:
        _apply(r)
        _recount(r)
    _recount_rules()
    qs = Resolver.objects.filter(kind=ResolverKind.CASH)
    agg = qs.aggregate(n=Sum("lines_count"), o=Sum("open_count"))
    return {"resolvers": qs.count(), "lines": agg["n"] or 0, "open": agg["o"] or 0}


_DIGITS = re.compile(r"\d+")


def open_patterns(resolver: Resolver, limit: int = 50) -> list[dict]:
    """Неразнесённые строки шаблона, сгруппированные по тексту (цифры → #)."""
    groups: dict[str, dict] = {}
    for ln in lines(resolver).filter(allocs__isnull=True).order_by("-date"):
        text = line_text(ln)
        pattern = _DIGITS.sub("#", text)[:160] or "(пусто)"
        g = groups.setdefault(pattern, {"pattern": pattern, "count": 0, "amount": Decimal(0),
                                        "example": text[:200], "first": ln.date, "last": ln.date,
                                        "cps": Counter()})
        g["count"] += 1
        g["amount"] += ln.amount
        g["first"] = min(g["first"], ln.date)
        if ln.counterparty and ln.counterparty != "<...>":
            g["cps"][ln.counterparty] += 1
    out = sorted(groups.values(), key=lambda g: -g["amount"])
    for g in out:
        g["top_cps"] = ", ".join(n for n, _ in g["cps"].most_common(3))
    return out[:limit]


def summary(resolver: Resolver) -> list[dict]:
    """Разнесено сейчас — по статьям."""
    rows = (CashLineAlloc.objects.filter(line__in=lines(resolver))
            .values("cf_item__code", "cf_item__name", "manual")
            .annotate(n=Count("id"), s=Sum("amount")).order_by("-s"))
    return list(rows)
