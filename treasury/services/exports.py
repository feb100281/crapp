"""Выгрузки казначейства в Excel: справочник статей ДДС, правила разноски."""

from __future__ import annotations

import html
from collections import defaultdict
from decimal import Decimal

from django.utils import timezone
from django.utils.html import strip_tags

from core.reports.xlsx import FMT_QTY, Book, Col, Row

from ..models.cf_item_model import Activity, CFItem, Direction
from ..models.resolver_model import Resolver, ResolverKind
from ..models.resolver_rule_model import ResolverRule
from .cf_items import usage

KIND_SHEETS = {
    ResolverKind.CP: "Контрагенты",
    ResolverKind.KBK: "КБК",
    ResolverKind.BA: "Счета",
    ResolverKind.IC: "Между своими",
}


def plain(text) -> str:
    """Описание из визуального редактора → обычный текст."""
    return " ".join(html.unescape(strip_tags(text or "")).split())


def _today() -> str:
    return f"дата: {timezone.localdate():%d.%m.%Y}"


def _resolver_label(r: Resolver) -> str:
    if r.kind == ResolverKind.CP:
        return f"ИНН {r.key}" if not r.match_name else "по названию"
    return r.key


def _rules_sheet(book: Book, kinds, name: str = "Правила разноски") -> int:
    rules = (
        ResolverRule.objects.filter(resolver__kind__in=kinds)
        .select_related("resolver", "cf_item")
        .order_by("resolver__kind", "resolver__key", "resolver__direction", "order", "id")
    )
    order = {k: i for i, k in enumerate(KIND_SHEETS)}
    rows = [
        Row([
            r.resolver.get_kind_display(),
            _resolver_label(r.resolver),
            r.resolver.name or r.resolver.payee or "",
            r.resolver.get_direction_display(),
            r.order,
            r.text_regex or "всё остальное",
            r.cf_item.code,
            r.cf_item.name,
            r.hits,
            r.hits_amount,
        ])
        for r in sorted(rules, key=lambda r: order.get(r.resolver.kind, 9))
    ]
    book.sheet(
        name, "Правила разноски",
        subtitle="Какое назначение платежа на какую статью разносится. "
                 "Резолвер контрагента проверяется раньше резолвера счёта",
        description="Все правила «назначение содержит → статья» с числом разнесённых строк",
    ).table(
        [
            Col("Резолвер", width=20),
            Col("Ключ", kind="code", width=18),
            Col("Что это / контрагент", width=40),
            Col("Направление", width=14),
            Col("№", kind="int", width=6),
            Col("Назначение содержит", width=46, wrap=True),
            Col("Код", kind="code", width=10),
            Col("Статья", width=38),
            Col("Строк", kind="int", width=10),
            Col("Сумма, ₽", kind="money", width=16, total=True),
        ],
        rows,
        freeze_cols=2,
        autofilter=True,
    )
    return len(rows)


def cf_items_workbook() -> Book:
    """Справочник статей ДДС: дерево с использованием + правила разноски."""
    items = list(CFItem.objects.select_related("parent").order_by("code"))
    use = usage()
    children = defaultdict(list)
    for item in items:
        if item.parent_id:
            children[item.parent_id].append(item)
    tops = [i for i in items if not i.parent_id]

    def own(item):
        u = use.get(item.pk, {})
        return u.get("rules", 0), u.get("allocs", 0), u.get("amount", Decimal(0))

    def with_children(item):
        totals = [own(item)] + [own(c) for c in children[item.pk]]
        return tuple(sum(t[i] for t in totals) for i in range(3))

    def line(item, level, numbers, outline):
        rules, allocs, amount = numbers
        return Row([
            item.code, item.name, item.get_activity_display(), item.get_direction_display(),
            "да" if item.is_active else "нет", rules, allocs, amount, plain(item.description),
        ], level=level, outline=outline, indent=2 * outline)

    rows = []
    for activity, activity_name in Activity.choices:
        group = [i for i in tops if i.activity == activity]
        if not group:
            continue
        rows.append(Row([f"{activity}00000", f"{activity_name} деятельность"
                         if activity != Activity.INTRAGROUP else "Внутригрупповые операции",
                         "", "", "", None, None, None, ""], level=1))
        for direction, direction_name in Direction.choices:
            part = [i for i in group if i.direction == direction]
            if not part:
                continue
            sums = [with_children(i) for i in part]
            rows.append(Row([
                f"{activity}{direction}0000", direction_name, "", "", "",
                sum(s[0] for s in sums), sum(s[1] for s in sums), sum(s[2] for s in sums), "",
            ], level=2, outline=1, indent=2))
            for item in part:
                rows.append(line(item, 3, with_children(item), 2))
                for child in children[item.pk]:
                    rows.append(line(child, None, own(child), 3))

    subs = len(items) - len(tops)
    rules_total = sum(u["rules"] for u in use.values())
    allocs_total = sum(u["allocs"] for u in use.values())

    book = Book(
        "Статьи движения денежных средств",
        subtitle="Справочник статей ДДС: деятельность → направление → статья → подстатья",
        params=f"{_today()} · статей: {len(tops)} · подстатей: {subs}",
    )
    book.kpi("Статей", len(tops), "верхний уровень", FMT_QTY)
    book.kpi("Подстатей", subs, "на них идёт разноска", FMT_QTY)
    book.kpi("Правил разноски", rules_total, "во всех резолверах", FMT_QTY)
    book.kpi("Разнесено операций", allocs_total, "строк выписок", FMT_QTY)

    book.sheet(
        "Статьи ДДС", "Статьи движения денежных средств",
        subtitle="Код: деятельность · направление · статья · подстатья. "
                 "Суммы — оборот разнесённых строк, без знака",
        description="Дерево статей с кодами, описанием и тем, сколько правил и операций на статье",
    ).table(
        [
            Col("Код", kind="code", width=10),
            Col("Статья", width=50, indent=True),
            Col("Деятельность", width=18),
            Col("Направление", width=14),
            Col("Активна", width=9),
            Col("Правил", kind="int", width=9),
            Col("Операций", kind="int", width=11),
            Col("Оборот, ₽", kind="money", width=18, total=True),
            Col("Описание", width=60, wrap=True),
        ],
        rows,
        freeze_cols=2,
    )
    _rules_sheet(book, list(KIND_SHEETS))
    return book


def rules_workbook(kind: str) -> Book:
    """Резолверы одного типа и их правила."""
    resolvers = list(Resolver.objects.filter(kind=kind).order_by("-lines_amount"))
    label = KIND_SHEETS.get(kind, kind)
    open_n = sum(r.open_count for r in resolvers)
    open_amount = sum((r.open_amount for r in resolvers), Decimal(0))

    book = Book(
        f"Правила разноски — {label.lower()}",
        subtitle="Резолверы и правила «назначение содержит → статья ДДС»",
        params=f"{_today()} · резолверов: {len(resolvers)}",
    )
    book.kpi("Не разнесено, ₽", open_amount, f"строк: {open_n}")
    book.kpi("Резолверов", len(resolvers), label.lower(), FMT_QTY)
    book.kpi("Строк", sum(r.lines_count for r in resolvers), "во всех резолверах", FMT_QTY)

    counts = defaultdict(int)
    for rid in ResolverRule.objects.filter(resolver__kind=kind).values_list("resolver_id", flat=True):
        counts[rid] += 1

    book.sheet(
        "Резолверы", f"Резолверы — {label.lower()}",
        subtitle="Группы строк выписки, которые разбираются вместе",
        description="Список резолверов: сколько строк и денег, сколько ещё не разнесено",
    ).table(
        [
            Col("Ключ", kind="code", width=18),
            Col("Направление", width=14),
            Col("Что это", width=40),
            Col("Контрагенты", width=44),
            Col("Правил", kind="int", width=9),
            Col("Строк", kind="int", width=10),
            Col("Сумма, ₽", kind="money", width=16),
            Col("Не разнесено, строк", kind="int", width=14),
            Col("Не разнесено, ₽", kind="money", width=18, total=True),
        ],
        [
            Row([
                _resolver_label(r), r.get_direction_display(), r.name or "", r.payee or "",
                counts[r.pk], r.lines_count, r.lines_amount, r.open_count, r.open_amount,
            ], level="warn" if r.open_count and not counts[r.pk] else None)
            for r in resolvers
        ],
        total=Row(["", "", "ИТОГО", "", sum(counts.values()), sum(r.lines_count for r in resolvers),
                   sum((r.lines_amount for r in resolvers), Decimal(0)), open_n, open_amount]),
        freeze_cols=2,
        autofilter=True,
        note="Выделены резолверы, где есть неразнесённые строки и нет ни одного правила.",
    )
    _rules_sheet(book, [kind], "Правила")
    return book
