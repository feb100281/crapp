"""
Перестройка справочника статей ДДС без потери разноски.

    make_sub(item, parent)        — статья / подстатья → подстатья другой статьи
    make_top(item, activity)      — подстатья → самостоятельная статья
                                    (или статья → другая деятельность)
    merge(item, target)           — правила, разноска и проводки → на другую статью
    usage()                       — где статья используется (правила, строки, сумма)

Правила, разноска и проводки ссылаются на статью по id, поэтому при переносе
ничего переразносить не нужно — меняются только код и место в отчёте.
После изменений пересобираются витрины ДДС (код и название статьи лежат в них).

Статьи из SYSTEM_CODES трогать нельзя: их коды зашиты в расчёт ДДС
(sql/bs/cash_flow.sql) и в стартовые правила «между своими».
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Sum

from ..models.bs_line_alloc_model import BSLineAlloc
from ..models.cf_item_model import CFItem
from ..models.resolver_rule_model import ResolverRule

SYSTEM_CODES = {
    "121103": "удержанная комиссия эквайринга",
    "121104": "удержания банка из эквайринга",
    "410100": "поступление с нашего счёта",
    "420100": "перевод на наш счёт",
    "410200": "конвертация, поступление",
    "420200": "конвертация, списание",
}

MAX_NUMBER = 99


def _journal_lines():
    from gl.models.journal_model import JournalLine

    return JournalLine.objects


def usage() -> dict[int, dict]:
    """{id статьи: {"rules", "allocs", "amount", "journal"}} — по самой статье, без подстатей."""
    out: dict[int, dict] = defaultdict(
        lambda: {"rules": 0, "allocs": 0, "amount": Decimal(0), "journal": 0}
    )
    for row in ResolverRule.objects.order_by().values("cf_item").annotate(n=Count("id")):
        out[row["cf_item"]]["rules"] = row["n"]
    for row in (BSLineAlloc.objects.filter(cf_item__isnull=False).order_by()
                .values("cf_item").annotate(n=Count("id"), s=Sum("amount"))):
        out[row["cf_item"]]["allocs"] = row["n"]
        out[row["cf_item"]]["amount"] = row["s"] or Decimal(0)
    for row in (_journal_lines().filter(cf_item__isnull=False).order_by()
                .values("cf_item").annotate(n=Count("id"))):
        out[row["cf_item"]]["journal"] = row["n"]
    return out


def is_used(item: CFItem) -> bool:
    return (
        ResolverRule.objects.filter(cf_item=item).exists()
        or BSLineAlloc.objects.filter(cf_item=item).exists()
        or _journal_lines().filter(cf_item=item).exists()
    )


def locked_reason(item: CFItem) -> str:
    """Пусто — статью можно переносить. Иначе — почему нельзя."""
    codes = [item.code] + list(item.children.values_list("code", flat=True))
    for code in codes:
        if code in SYSTEM_CODES:
            return (f"Код {code} ({SYSTEM_CODES[code]}) используется в расчёте ДДС — "
                    "статью нельзя переносить и объединять")
    return ""


def _free_number(siblings, taken: set[int]) -> int:
    used = set(siblings.values_list("number", flat=True)) | taken
    for number in range(1, MAX_NUMBER + 1):
        if number not in used:
            taken.add(number)
            return number
    raise ValidationError("В этой группе заняты все 99 номеров")


def _check(item: CFItem) -> None:
    reason = locked_reason(item)
    if reason:
        raise ValidationError(f"{item}: {reason}")


@transaction.atomic
def make_sub(item: CFItem, parent: CFItem) -> list[str]:
    """Сделать статью подстатьёй parent. У статьи с подстатьями переезжают подстатьи."""
    _check(item)
    if parent.parent_id:
        raise ValidationError(f"«{parent}» — подстатья, вложить в неё нельзя (только два уровня)")
    if parent.pk == item.pk:
        raise ValidationError("Нельзя перенести статью в саму себя")
    if item.parent_id == parent.pk:
        raise ValidationError(f"«{item}» уже в статье «{parent}»")
    if item.direction != parent.direction:
        raise ValidationError(
            f"«{item}» — {item.get_direction_display().lower()}, а «{parent}» — "
            f"{parent.get_direction_display().lower()}: направление должно совпадать"
        )

    log, taken = [], set()
    siblings = CFItem.objects.filter(parent=parent)
    children = list(item.children.order_by("code")) if not item.parent_id else []

    for child in children:
        old = child.code
        child.parent = parent
        child.number = _free_number(siblings, taken)
        child.save()
        log.append(f"{old} {child.name} → {child.code}")

    if children and not is_used(item):
        log.append(f"{item.code} {item.name} — заголовок без разноски, удалён")
        item.delete()
        return log

    old = item.code
    item.parent = parent
    item.number = _free_number(siblings, taken)
    item.save()
    log.append(f"{old} {item.name} → {item.code}")
    return log


@transaction.atomic
def make_top(item: CFItem, activity: int) -> list[str]:
    """Подстатья → самостоятельная статья; статья → другая деятельность."""
    _check(item)
    activity = int(activity)
    if not item.parent_id and item.activity == activity:
        raise ValidationError(f"«{item}» уже самостоятельная статья этой деятельности")

    old = item.code
    siblings = CFItem.objects.filter(
        parent__isnull=True, activity=activity, direction=item.direction,
    ).exclude(pk=item.pk)
    item.parent = None
    item.activity = activity
    item.number = _free_number(siblings, set())
    item.save()
    return [f"{old} {item.name} → {item.code}"]


@transaction.atomic
def merge(item: CFItem, target: CFItem, delete: bool = True) -> list[str]:
    """Правила, разноска и проводки статьи (и её подстатей) → на target."""
    _check(item)
    if target.pk == item.pk:
        raise ValidationError("Нельзя объединить статью саму с собой")
    if target.children.exists():
        raise ValidationError(f"У «{target}» есть подстатьи — выберите подстатью")
    if target.parent_id == item.pk:
        raise ValidationError(f"«{target}» — подстатья «{item}»: сначала вынесите её отдельно")
    if item.direction != target.direction:
        raise ValidationError(
            f"«{item}» — {item.get_direction_display().lower()}, а «{target}» — "
            f"{target.get_direction_display().lower()}: направление должно совпадать"
        )

    sources = [item] + (list(item.children.all()) if not item.parent_id else [])
    ids = [s.pk for s in sources]

    rules = ResolverRule.objects.filter(cf_item__in=ids).update(cf_item=target)
    allocs = BSLineAlloc.objects.filter(cf_item__in=ids).update(cf_item=target)
    journal = _journal_lines().filter(cf_item__in=ids).update(cf_item=target)
    log = [f"{item.code} {item.name} → {target.code} {target.name}: "
           f"правил {rules}, строк {allocs}, проводок {journal}"]

    if delete:
        for source in reversed(sources):   # сначала подстатьи
            source.delete()
        log.append(f"{item.code} {item.name} — удалена")
    return log
